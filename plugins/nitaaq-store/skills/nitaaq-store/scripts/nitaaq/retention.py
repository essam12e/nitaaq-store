"""Retention and customer messages, deterministic part.

Segments customers from Salla orders on two attributes (recency and order
count) plus spend for VIPs, keeps only customers with recorded consent for the
chosen channel, checks draft messages, and gates sending.

Drafts are always allowed. Sending needs three things: a sending tool the host
actually has, the merchant's approval bound to this exact message and these
recipients, and each recipient's recorded consent at send time. Unknown consent
is treated as no consent. Order notifications (transactional) are Salla's own;
this module handles marketing messages only. Output carries customer ids, never
phone numbers or emails.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .ads import fact_issues
from .approvals import ApprovalStore
from .arabic import normalize, parse_amount
from .evidence import coverage_note_ar, dedupe
from .reports import parse_dt
from .tracking import order_state

CHANNELS = ("email", "sms", "whatsapp")
SEGMENT_AR = {
    "new": "عملاء جدد (طلب واحد حديث)",
    "one_time": "اشتروا مرة وحدة وما رجعوا",
    "active_repeat": "عملاء متكررون نشطون",
    "at_risk": "عملاء متكررون بدأوا يغيبون",
    "lapsed": "عملاء انقطعوا",
}
EXCLUDED_AR = {"no_consent": "رافض للرسائل التسويقية", "unknown_consent": "موافقته غير مسجلة",
               "unsubscribed": "ألغى الاشتراك", "no_contact": "ما عنده وسيلة تواصل لهذه القناة",
               "not_in_customers": "غير موجود في بيانات العملاء"}
OPT_OUT = re.compile(r"(إلغاء الاشتراك|الغاء الاشتراك|للإلغاء|للالغاء|لإيقاف الرسائل|لايقاف الرسائل|ايقاف|إيقاف|"
                     r"stop|unsubscribe|\{unsubscribe\})", re.I)
URGENCY = re.compile(r"(آخر فرصة|اخر فرصة|ينتهي اليوم|ينتهي الليلة|باقي \d+ (?:قطع|حبات)|الكمية محدودة|الكميه محدوده|"
                     r"لفترة محدودة|لفتره محدوده|العرض ينتهي)", re.I)
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {"name", "store", "unsubscribe", "link"}
YES = {"true", "yes", "1", "نعم", "موافق", "opted_in", "subscribed"}
NO = {"false", "no", "0", "لا", "opted_out", "unsubscribed", "رافض"}


def _cust(o: dict) -> str | None:
    c = o.get("customer_id")
    if c in (None, ""):
        c = (o.get("customer") or {}).get("id") if isinstance(o.get("customer"), dict) else None
    return str(c) if c not in (None, "") else None


def segments(orders: list[dict], *, as_of, new_days: int = 30, active_days: int = 60, risk_days: int = 120,
             vip_share: int = 10) -> dict:
    """One segment per customer from recency and order count; VIP (top spenders with 2+ orders) is an extra tag."""
    as_of = date.fromisoformat(str(as_of)[:10])
    unique, dd = dedupe(orders, "id")
    per: dict = defaultdict(lambda: {"orders": 0, "spend": Decimal(0), "last": None, "first": None})
    no_id, earliest = 0, None
    for o in unique:
        if order_state(o) != "counted":
            continue
        dt = parse_dt(o.get("date"))
        if dt is None or dt.date() > as_of:
            continue
        d = dt.date()
        earliest = d if earliest is None or d < earliest else earliest
        c = _cust(o)
        if c is None:
            no_id += 1
            continue
        p = per[c]
        p["orders"] += 1
        p["spend"] += parse_amount(o.get("total")) or 0
        p["last"] = d if p["last"] is None or d > p["last"] else p["last"]
        p["first"] = d if p["first"] is None or d < p["first"] else p["first"]
    seg_of = {}
    for c, p in per.items():
        days = (as_of - p["last"]).days
        if days > risk_days:
            seg_of[c] = "lapsed"
        elif p["orders"] == 1:
            seg_of[c] = "new" if days <= new_days else "one_time"
        else:
            seg_of[c] = "active_repeat" if days <= active_days else "at_risk"
    repeat = sorted((c for c, p in per.items() if p["orders"] >= 2), key=lambda c: (-per[c]["spend"], c))
    vip = repeat[:max(1, len(repeat) * vip_share // 100)] if repeat else []
    out: dict = {k: {"customers": 0, "spend": Decimal(0), "ids": []} for k in SEGMENT_AR}
    for c, s in sorted(seg_of.items()):
        out[s]["customers"] += 1
        out[s]["spend"] += per[c]["spend"]
        out[s]["ids"].append(c)
    flags = []
    if earliest is None or (as_of - earliest).days < risk_days:
        flags.append("insufficient_history")  # "lapsed" and "one_time" need history longer than risk_days
    if no_id:
        flags.append("orders_without_customer_id")
    metrics = {f"segment:{k}": v["customers"] for k, v in out.items()}
    metrics["customers"] = len(seg_of)
    metrics["vip"] = len(vip)
    return {"kind": "segments", "as_of": as_of.isoformat(), "segments": out, "vip_ids": vip, "flags": flags,
            "orders_without_customer_id": no_id, "duplicates_removed": dd["duplicates_removed"], "metrics": metrics,
            "rules": {"new_days": new_days, "active_days": active_days, "risk_days": risk_days, "vip_share": vip_share}}


def consent(customer: dict, channel: str) -> str:
    """'yes' | 'no' | 'unsubscribed' | 'unknown' for marketing on this channel, from what the store recorded."""
    def val(v):
        if isinstance(v, bool):
            return "yes" if v else "no"
        t = normalize(str(v)) if v not in (None, "") else ""
        return "yes" if t in YES else "no" if t in NO else None
    for k in (f"{channel}_unsubscribed", "unsubscribed"):
        if val(customer.get(k)) == "yes":
            return "unsubscribed"
    cons = customer.get("consent")
    for v in ((cons or {}).get(channel) if isinstance(cons, dict) else None, customer.get(f"{channel}_consent"),
              customer.get(f"accepts_{channel}_marketing"), customer.get("marketing_consent"),
              customer.get("accepts_marketing")):
        r = val(v)
        if r:
            return r
    return "unknown"


def _contact(customer: dict, channel: str) -> bool:
    if channel == "email":
        return bool(customer.get("email"))
    return bool(customer.get("mobile") or customer.get("phone"))


def audience(seg: dict, segment: str, customers: list[dict], channel: str, *, vip_only: bool = False) -> dict:
    """Customers of a segment who can receive marketing on this channel; everyone else is counted by reason."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    if segment not in seg["segments"]:
        raise ValueError(f"segment must be one of {tuple(seg['segments'])}")
    ids = seg["segments"][segment]["ids"]
    if vip_only:
        ids = [i for i in ids if i in set(seg["vip_ids"])]
    by_id = {str(c.get("id")): c for c in customers}
    eligible, excluded = [], defaultdict(list)
    for i in ids:
        c = by_id.get(i)
        if c is None:
            excluded["not_in_customers"].append(i)
            continue
        state = consent(c, channel)
        if state != "yes":
            excluded["no_consent" if state == "no" else state if state == "unsubscribed" else "unknown_consent"].append(i)
        elif not _contact(c, channel):
            excluded["no_contact"].append(i)
        else:
            eligible.append(i)
    metrics = {"segment_size": len(ids), "eligible": len(eligible), **{f"excluded:{k}": len(v) for k, v in excluded.items()}}
    return {"kind": "audience", "segment": segment, "channel": channel, "vip_only": vip_only, "eligible_ids": eligible,
            "excluded": {k: len(v) for k, v in excluded.items()}, "metrics": metrics}


def sms_parts(text: str) -> int:
    """SMS parts for Arabic (UCS-2): 70 characters in one part, 67 per part when split."""
    n = len(text)
    return 1 if n <= 70 else -(-n // 67)


def check_message(text: str, channel: str, *, facts: dict | None = None) -> dict:
    """A marketing draft against consent-era rules: opt-out, honest urgency, claims and numbers from store data."""
    issues = []
    if not OPT_OUT.search(text):
        issues.append({"check": "no_opt_out", "message_ar": "الرسالة التسويقية لازم توضح طريقة إيقاف الرسائل."})
    if URGENCY.search(text) and not (facts or {}).get("offer_ends"):
        issues.append({"check": "urgency_needs_proof", "message_ar": "استعجال بدون تاريخ انتهاء عرض حقيقي من المتجر."})
    for p in PLACEHOLDER.findall(text):
        if p not in ALLOWED_PLACEHOLDERS:
            issues.append({"check": "unknown_placeholder", "placeholder": p})
    filled = PLACEHOLDER.sub("", text)
    issues += fact_issues([filled], facts)
    parts = sms_parts(text) if channel == "sms" else None
    return {"kind": "message_check", "channel": channel, "issues": issues, "sms_parts": parts,
            "status_ar": "مسودة رسالة فقط؛ لم تُرسل."}


def message_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _items(ids, channel, text):
    h = message_hash(text)
    return [{"entity_id": i, "before": {"consent": "yes"}, "after": {"channel": channel, "message_hash": h}} for i in ids]


def send_proposal(aud: dict, text: str, *, store_id: str, source_finding: str | None = None) -> dict | None:
    if not aud["eligible_ids"]:
        return None
    return {"proposal_id": "mp1", "store_id": str(store_id), "operation": "messages.send",
            "items": _items(aud["eligible_ids"], aud["channel"], text), "sensitivity": "high", "reversible": False,
            "customer_visible": True, "source_findings": [source_finding] if source_finding else [],
            "proposed_by": "email_retention", "channel": aud["channel"], "segment": aud["segment"],
            "message_hash": message_hash(text),
            "note_ar": "إرسال لعملاء حقيقيين لا يمكن التراجع عنه. يحتاج موافقتك على هذا النص وهؤلاء المستلمين، "
                       "ويُرسل فقط لمن موافقته مسجلة وقت الإرسال، وعن طريق أداة إرسال مصرّح لها."}


def send_gate(approvals: ApprovalStore, approval_id: str, *, store_id: str, channel: str, text: str,
              recipient_ids: list[str], customers_now: list[dict], sender_available: bool) -> dict:
    """Who may receive this message now. Nothing passes without a sender, a matching approval and current consent."""
    if not sender_available:
        return {"ok": False, "allowed": [], "reasons": ["no_sender"],
                "message_ar": "ما فيه أداة إرسال متاحة؛ الرسالة تبقى مسودة."}
    by_id = {str(c.get("id")): c for c in customers_now}
    fresh = {i: {"consent": consent(by_id[i], channel)} if i in by_id else {"consent": "unknown"} for i in recipient_ids}
    # whoever withdrew consent since the approval drops out; the rest must still match the approval exactly
    ready = [i for i in recipient_ids if fresh[i]["consent"] == "yes" and _contact(by_id.get(i, {}), channel)]
    blocked = sorted(set(recipient_ids) - set(ready))
    if not ready:
        return {"ok": False, "allowed": [], "reasons": ["no_consenting_recipient"], "blocked": blocked}
    chk = approvals.check(approval_id, store_id, "messages.send", _items(ready, channel, text), fresh=fresh)
    if not chk["ok"]:
        return {"ok": False, "allowed": [], "reasons": chk["reasons"], "blocked": blocked,
                "message_ar": "الموافقة لا تغطي هذا الإرسال."}
    return {"ok": True, "allowed": ready, "reasons": [], "blocked": blocked}


# ------------------------------------------------------------------ findings

def segments_finding(seg: dict, ev: dict, *, fid: str = "rt1") -> dict:
    calc = {"fn": "retention_segments", "evidence": ev["evidence_id"], "as_of": seg["as_of"], "rules": seg["rules"]}
    obs = [{"label_ar": SEGMENT_AR[k], "current": str(v["customers"]), "baseline": None,
            "calc": {**calc, "metric": f"segment:{k}"}} for k, v in seg["segments"].items()]
    obs.append({"label_ar": "عملاء VIP (الأعلى إنفاقاً بين المتكررين)", "current": str(len(seg["vip_ids"])), "baseline": None,
                "calc": {**calc, "metric": "vip"}})
    r = seg["rules"]
    lim = [f"التقسيم بقواعد اخترناها: جديد خلال {r['new_days']} يوم، نشط خلال {r['active_days']}، "
           f"منقطع بعد {r['risk_days']} يوم، وVIP أعلى {r['vip_share']}% إنفاقاً؛ ليست أرقاماً معيارية.",
           "التقسيم للتخطيط والمسودات؛ أي إرسال يحتاج موافقتك وموافقة العميل المسجلة."]
    if "insufficient_history" in seg["flags"]:
        lim.append(f"التاريخ المتوفر أقصر من {r['risk_days']} يوم؛ «منقطع» و«ما رجعوا» غير مكتملة.")
    if seg["orders_without_customer_id"]:
        lim.append(f"{seg['orders_without_customer_id']} طلب بدون رقم عميل ما دخل التقسيم.")
    if ev["coverage"].get("complete") is not True:
        lim.append("الطلبات جزئية أو غير معروفة الاكتمال: " + coverage_note_ar(ev))
    big = max(seg["segments"].items(), key=lambda kv: kv[1]["customers"])[0] if seg["metrics"]["customers"] else None
    interp = (f"قسّمنا {seg['metrics']['customers']} عميل حسب آخر طلب وعدد الطلبات. أكبر شريحة: {SEGMENT_AR[big]}."
              if big else "ما فيه عملاء بأرقام تعريف في الطلبات المتاحة.")
    return {
        "finding_id": fid, "agent": "email_retention", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "retention_segments", "entity": None,
        "period": {"current": seg["as_of"], "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": obs, "interpretation_ar": interp, "alternatives_ar": [],
        "confidence": {"level": "medium" if ev["coverage"].get("complete") is True and not seg["flags"] else "low",
                       "rationale_ar": "تقسيم حتمي من الطلبات؛ " + coverage_note_ar(ev)},
        "priority": "medium", "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "تقسيم وليس توقعاً؛ لا نعد بعودة العملاء."},
        "requires": None, "claims_cause": False, "limitations_ar": lim,
    }


def recompute(records: list[dict], calc: dict):
    seg = segments(records, as_of=calc["as_of"], **(calc.get("rules") or {}))
    return seg["metrics"].get(calc["metric"])

