"""Tracking evidence with explicit levels.

Level 1  tracking code detected in the page source
Level 2  event request observed (from a HAR / network log the host captured)
Level 3  event received in the destination platform (merchant confirms/exports)
Level 4  event values reconciled with a real authorized transaction

This module can establish levels 1 and 2. Levels 3 and 4 are recorded only
from evidence the merchant or an authorized platform tool provides. It never
sends events itself.

The second half reconciles Salla orders with what a destination recorded
(see reconcile()).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .arabic import normalize, parse_amount
from .evidence import coverage_note_ar, dedupe
from .reports import parse_dt

PLATFORMS = {
    "ga4": {
        "label": "Google Analytics 4",
        "code": [r"googletagmanager\.com/gtag/js\?id=G-[A-Z0-9]+", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-[A-Z0-9]+"],
        "gtm": [r"googletagmanager\.com/gtm\.js\?id=GTM-[A-Z0-9]+", r"GTM-[A-Z0-9]{4,}"],
        "ids": r"\bG-[A-Z0-9]{6,}\b",
        "requests": [r"google-analytics\.com/g/collect", r"analytics\.google\.com/g/collect", r"/g/collect\?"],
        "event_param": "en",
    },
    "meta": {
        "label": "Meta Pixel",
        "code": [r"connect\.facebook\.net/[^\"']*/fbevents\.js", r"fbq\(\s*['\"]init['\"]"],
        "ids": r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{8,})",
        "requests": [r"facebook\.com/tr[/?]"],
        "event_param": "ev",
    },
    "tiktok": {
        "label": "TikTok Pixel",
        "code": [r"analytics\.tiktok\.com/i18n/pixel", r"ttq\.load\(", r"ttq\.page\("],
        "ids": r"ttq\.load\(\s*['\"]([A-Z0-9]{10,})",
        "requests": [r"analytics\.tiktok\.com/api/v2/pixel"],
        "event_param": "event",
    },
    "snapchat": {
        "label": "Snap Pixel",
        "code": [r"sc-static\.net/scevent\.min\.js", r"snaptr\(\s*['\"]init['\"]"],
        "ids": r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([a-f0-9\-]{20,})",
        "requests": [r"tr\.snapchat\.com/", r"tr-shadow\.snapchat\.com/"],
        "event_param": "ev",
    },
}

LEVEL_AR = {
    0: "لم يُرصد",
    1: "الكود موجود في الصفحة",
    2: "رُصد إرسال حدث من المتصفح",
    3: "تأكد استلام الحدث في المنصة",
    4: "طُوبقت قيم الحدث مع عملية شراء حقيقية مصرّح بها",
}


def detect_code(html: str, scripts_src: list | None = None) -> dict:
    blob = (html or "") + "\n" + "\n".join(scripts_src or [])
    out = {}
    for key, spec in PLATFORMS.items():
        hits = [p for p in spec["code"] if re.search(p, blob, re.I)]
        via_gtm = key == "ga4" and not hits and any(re.search(p, blob) for p in spec["gtm"])
        ids = sorted(set(re.findall(spec["ids"], blob)))
        level = 1 if hits else 0
        out[key] = {
            "label": spec["label"], "level": level, "ids": ids[:5],
            "note_ar": LEVEL_AR[level] if not via_gtm else "يوجد Google Tag Manager؛ قد يحمّل GA4 ديناميكياً — يحتاج رصد الطلبات للتأكد",
            "via_gtm": via_gtm,
        }
    return out


def _har_urls(har: dict) -> list:
    entries = har.get("log", {}).get("entries", [])
    urls = []
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        body = (req.get("postData") or {}).get("text", "")
        urls.append((url, body))
    return urls


def events_from_har(har: dict | str | Path) -> dict:
    if not isinstance(har, dict):
        har = json.loads(Path(har).read_text(encoding="utf-8"))
    found: dict = {k: [] for k in PLATFORMS}
    for url, body in _har_urls(har):
        for key, spec in PLATFORMS.items():
            if any(re.search(p, url) for p in spec["requests"]):
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
                ev = q.get(spec["event_param"])
                if not ev and body:
                    try:
                        ev = json.loads(body).get("event")
                    except (ValueError, AttributeError):
                        m = re.search(rf"(?:^|&){spec['event_param']}=([^&]+)", body)
                        ev = urllib.parse.unquote(m.group(1)) if m else None
                found[key].append(ev or "unknown")
    return found


def combine(code: dict, har_events: dict | None = None, confirmations: dict | None = None) -> dict:
    """Merge evidence. confirmations: {platform: {"level": 3|4, "note": "..."}}."""
    result = {}
    for key, c in code.items():
        level = c["level"]
        events = (har_events or {}).get(key) or []
        if events:
            level = max(level, 2)
        conf = (confirmations or {}).get(key)
        if conf and conf.get("level") in (3, 4) and conf.get("note"):
            if conf["level"] == 4 and level < 2:
                conf = {**conf, "level": 3}
            level = max(level, conf["level"])
        purchase_seen = any(str(e).lower() in ("purchase", "completepayment", "placeorder") for e in events)
        result[key] = {
            "label": c["label"], "level": level, "level_ar": LEVEL_AR[level], "ids": c["ids"],
            "events_observed": sorted(set(map(str, events))), "purchase_event_observed": purchase_seen,
            "conclusion_ar": _conclusion(level, purchase_seen),
        }
    return result


def _conclusion(level: int, purchase_seen: bool) -> str:
    if level == 0:
        return "لا دليل على التتبع من الصفحات المفحوصة."
    if level == 1:
        return "وجود الكود لا يثبت أن أحداث الشراء تعمل."
    if level == 2 and not purchase_seen:
        return "تُرسل أحداث، لكن لم نرصد حدث شراء؛ لا نحكم على تتبع المشتريات."
    if level == 2:
        return "رُصد حدث شراء يُرسل من المتصفح؛ الاستلام في المنصة لم يُؤكد بعد."
    if level == 3:
        return "المنصة تستقبل الأحداث؛ لم تُطابق القيم مع عملية حقيقية بعد."
    return "تم التحقق الكامل مع عملية حقيقية مصرّح بها."


# ------------------------------------------------------------------ reconciliation (Salla orders vs destination)
#
# Compares Salla orders with what a destination recorded (GA4 purchases, or an
# ad platform's conversions), from an export or an authorized tool. It never
# sends or edits events. A gap is a lead to investigate, never "tracking is
# broken": that verdict needs level-2 evidence that a real, authorized purchase
# fired no purchase event (see `fault_evidence`).
#
# - Record mode (transaction ids on the platform side): each order is matched,
#   explained (date shift, unsettled days, cancelled after the conversion, not
#   attributed to ads, value basis) or left unexplained (missing, not in Salla,
#   duplicate, value mismatch).
# - Totals mode (daily counts only): totals are compared; the result says it
#   cannot prove any single order is missing.

DESTINATIONS = ("analytics", "ads")
# Days after a date before its numbers stop changing in the destination (processing delay).
DEFAULT_LAG_DAYS = {"analytics": 2, "ads": 3}
DEFAULT_TOLERANCE_PCT = 5  # our chosen threshold for "close enough", not an industry figure
_CANCELLED = [normalize(x) for x in ("ملغي", "ملغى", "مسترجع", "مسترد", "استرجاع", "استرداد", "مرفوض",
                                      "cancel", "refund", "return", "declined", "restored")]
_PENDING = [normalize(x) for x in ("بانتظار الدفع", "بإنتظار الدفع", "انتظار الدفع", "pending_payment", "payment_pending",
                                    "awaiting_payment", "pending")]
_PURCHASE_ACTIONS = ("purchase", "شراء", "complete payment", "completepayment", "placeorder", "place order", "order")
_TID_KEYS = ("transaction_id", "transaction", "order_id", "order_number", "order", "reference_id", "رقم_الطلب", "معرف_المعاملة")
_DATE_KEYS = ("date", "conversion_date", "event_date", "day", "time", "التاريخ")
_VALUE_KEYS = ("value", "revenue", "purchase_revenue", "total_revenue", "conversion_value", "conv_value", "conv._value",
               "purchases_conversion_value", "القيمة", "الإيرادات")
_COUNT_KEYS = ("conversions", "conv.", "purchases", "count", "transactions", "events", "التحويلات", "المشتريات")
ONE = Decimal("0.1")
CENT = Decimal("0.01")

STATUS_AR = {
    "matched": "متطابق ضمن الحد المقبول",
    "explained": "فرق مفسَّر",
    "expected_subset": "طبيعي: الإعلانات تسجل الطلبات المنسوبة لها فقط",
    "investigate": "فرق يحتاج تحقيق",
    "event_missing": "حدث الشراء لم يُرسل في عملية حقيقية مرصودة",
    "not_comparable": "غير قابل للمقارنة",
}
CATEGORY_AR = {
    "matched": "مطابق",
    "date_shift": "مسجل في المنصة بتاريخ خارج الفترة (تاريخ النقرة أو فرق توقيت)",
    "unsettled": "طلب في أيام لم تكتمل أرقامها في المنصة بعد",
    "cancelled_after_conversion": "سجلته المنصة ثم أُلغي أو استُرجع في سلة",
    "pending_payment": "سجلته المنصة والطلب بانتظار الدفع في سلة",
    "not_attributed": "طلب لم تنسبه المنصة الإعلانية لإعلاناتها",
    "value_basis": "القيمة مختلفة لأن أساس الحساب مختلف (ضريبة أو شحن)",
    "missing_in_platform": "طلب في سلة غير موجود في المنصة",
    "not_in_salla": "تحويل في المنصة برقم غير موجود في طلبات سلة",
    "duplicate": "نفس رقم المعاملة مسجل أكثر من مرة",
    "value_mismatch": "قيمة مختلفة لا يفسرها أساس الحساب",
}
EXPLAINED = ("date_shift", "unsettled", "cancelled_after_conversion", "pending_payment", "not_attributed", "value_basis")
UNEXPLAINED = ("missing_in_platform", "not_in_salla", "duplicate", "value_mismatch")
POSSIBLE_CAUSES_AR = ["رفض الكوكيز أو وضع الموافقة", "مانعات الإعلانات والمتصفحات", "طرق دفع لا ترجع لصفحة الشكر",
                      "نموذج الإسناد ونافذته", "تأخر معالجة المنصة", "طلبات يدوية أو من قنوات خارج الموقع"]


def _q1(v):
    return v.quantize(ONE, ROUND_HALF_UP)


def _key(k) -> str:
    return re.sub(r"\s+", "_", str(k).strip().lower())


def _pick(row: dict, keys) -> object:
    low = {_key(k): v for k, v in row.items()}
    for k in keys:
        v = low.get(_key(k))
        if v not in (None, ""):
            return v
    return None


def _tid(v) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip().lstrip("#").strip().lower()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or None


def order_state(o: dict) -> str:
    """counted | cancelled | pending, from the Salla status text (name or slug)."""
    st = o.get("status")
    if isinstance(st, dict):
        st = st.get("slug") or st.get("name") or st.get("customized", {}).get("name")
    t = normalize(str(st or ""))
    if any(c in t for c in _CANCELLED):
        return "cancelled"
    if any(p in t for p in _PENDING):
        return "pending"
    return "counted"


def _order_ids(o: dict) -> list[str]:
    return [x for x in (_tid(o.get("id")), _tid(o.get("reference_id")), _tid(o.get("reference"))) if x]


def _order_values(o: dict) -> dict:
    """Candidate order values by basis. Missing parts mean the basis is not offered."""
    total = parse_amount(o.get("total"))
    if total is None:
        return {}
    out = {"total": total}
    tax = parse_amount(o.get("tax") if o.get("tax") is not None else o.get("vat"))
    ship = parse_amount(o.get("shipping") if o.get("shipping") is not None else o.get("shipping_cost"))
    if tax is not None:
        out["ex_tax"] = total - tax
    if ship is not None:
        out["ex_shipping"] = total - ship
    if tax is not None and ship is not None:
        out["ex_tax_shipping"] = total - tax - ship
    return out


def normalize_conversions(rows: list[dict]) -> tuple[str, list[dict]]:
    """('records'|'totals', rows as {tid, date, value, currency, count, event_id, source})."""
    out = []
    for r in rows:
        raw = _pick(r, _DATE_KEYS)
        if isinstance(raw, (str, int)) and re.fullmatch(r"\d{8}", str(raw).strip()):  # GA4 exports use YYYYMMDD
            raw = f"{str(raw).strip()[:4]}-{str(raw).strip()[4:6]}-{str(raw).strip()[6:]}"
        dt = parse_dt(raw)
        cnt = _pick(r, _COUNT_KEYS)
        out.append({"tid": _tid(_pick(r, _TID_KEYS)), "date": dt.date() if dt else None,
                    "value": parse_amount(_pick(r, _VALUE_KEYS)), "currency": (_pick(r, ("currency", "العملة")) or None),
                    "count": parse_amount(cnt) if cnt is not None else None,
                    "event_id": _pick(r, ("event_id",)), "source": _pick(r, ("source", "channel", "event_source")),
                    "action": _pick(r, ("conversion_action", "action", "event_name", "event"))})
    with_id = sum(1 for x in out if x["tid"])
    mode = "records" if out and with_id >= 0.8 * len(out) else "totals"
    return mode, out


def settle_cutoff(period_end: date, as_of: date, *, destination: str, date_basis: str,
                  lag_days: int | None, window_days: int | None) -> tuple[int, date | None]:
    """Days needed before a date's numbers settle, and the first unsettled date in the period (None if all settled)."""
    settle = DEFAULT_LAG_DAYS[destination] if lag_days is None else lag_days
    if date_basis == "click":
        settle += window_days if window_days is not None else 7
    first = as_of - timedelta(days=settle)  # dates after this may still change
    return settle, (first + timedelta(days=1) if first < period_end else None)


def _period(p):
    return date.fromisoformat(str(p[0])[:10]), date.fromisoformat(str(p[1])[:10])


def _same_money(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(CENT, abs(b) * Decimal("0.005"))


def reconcile(orders: list[dict], conversions: list[dict], period, *, destination: str = "analytics",
              platform: str = "ga4", date_basis: str = "conversion", window_days: int | None = None,
              lag_days: int | None = None, as_of=None, store_currency: str = "SAR",
              tolerance_pct: Decimal | int = DEFAULT_TOLERANCE_PCT, conversion_actions: list[str] | None = None,
              fault_evidence: dict | None = None, orders_total: int | None = None) -> dict:
    """Reconcile Salla orders with a destination for one period. See the module notes above."""
    if destination not in DESTINATIONS:
        raise ValueError(f"destination must be one of {DESTINATIONS}")
    p0, p1 = _period(period)
    as_of = date.fromisoformat(str(as_of)[:10]) if as_of else date.today()
    tol = Decimal(str(tolerance_pct))
    settle, unsettled_from = settle_cutoff(p1, as_of, destination=destination, date_basis=date_basis,
                                           lag_days=lag_days, window_days=window_days)
    flags = []

    # Salla side
    unique, dd = dedupe(orders, "id")
    by_id, in_period = {}, {"counted": [], "cancelled": [], "pending": []}
    for o in unique:
        dt = parse_dt(o.get("date"))
        o = {**o, "_date": dt.date() if dt else None, "_state": order_state(o)}
        for k in _order_ids(o):
            by_id[k] = o
        if o["_date"] and p0 <= o["_date"] <= p1:
            in_period[o["_state"]].append(o)
    counted = in_period["counted"]
    if orders_total is not None and len(unique) < orders_total:
        flags.append("orders_incomplete")
    if len(counted) < 30:
        flags.append("small_sample")

    # Destination side
    mode, conv = normalize_conversions(conversions)
    currencies = {str(c["currency"]).upper() for c in conv if c["currency"]}
    values_comparable = not currencies or currencies == {store_currency.upper()}
    if not values_comparable:
        flags.append("currency_mismatch")
    actions = {normalize(str(a)) for a in (conversion_actions or []) + [c["action"] for c in conv if c["action"]]}
    purchase_like = [a for a in actions if any(normalize(p) in a for p in _PURCHASE_ACTIONS)]
    if len(purchase_like) > 1:
        flags.append("multiple_purchase_actions")
    conv_in = [c for c in conv if c["date"] and p0 <= c["date"] <= p1]

    cats: dict = defaultdict(list)
    res = {"mode": mode, "destination": destination, "platform": platform, "period": [p0.isoformat(), p1.isoformat()],
           "as_of": as_of.isoformat(), "date_basis": date_basis, "window_days": window_days, "settle_days": settle,
           "unsettled_from": unsettled_from.isoformat() if unsettled_from else None, "tolerance_pct": tol,
           "salla": {"orders": len(counted), "cancelled": len(in_period["cancelled"]), "pending": len(in_period["pending"]),
                     "duplicates_removed": dd["duplicates_removed"]}}

    def unsettled(d):
        return unsettled_from is not None and d is not None and d >= unsettled_from

    if mode == "records":
        groups: dict = defaultdict(list)
        for c in conv:
            if c["tid"]:
                groups[c["tid"]].append(c)
        seen_in_period = set()
        for tid, rows in groups.items():
            rows_in = [r for r in rows if r["date"] and p0 <= r["date"] <= p1]
            if not rows_in:
                continue
            ev_ids = {r["event_id"] for r in rows_in}
            if len(rows_in) > 1 and not (len(ev_ids) == 1 and None not in ev_ids):
                cats["duplicate"].append(tid)
            o = by_id.get(tid)
            if o is None:
                cats["not_in_salla"].append(tid)
                continue
            seen_in_period.add(id(o))
            if o["_state"] == "cancelled":
                cats["cancelled_after_conversion"].append(tid)
            elif o["_state"] == "pending":
                cats["pending_payment"].append(tid)
            elif not (o["_date"] and p0 <= o["_date"] <= p1):
                cats["date_shift"].append(tid)
            else:
                cats["matched"].append(tid)
                val = rows_in[0]["value"]
                cand = _order_values(o)
                if values_comparable and val is not None and cand and not _same_money(val, cand["total"]):
                    basis = next((k for k, v in cand.items() if k != "total" and _same_money(val, v)), None)
                    cats["value_basis" if basis else "value_mismatch"].append(tid)
                    if basis:
                        res.setdefault("value_bases", {}).setdefault(basis, 0)
                        res["value_bases"][basis] += 1
        all_tids = set(groups)
        for o in counted:
            if id(o) in seen_in_period:
                continue
            oid = (_order_ids(o) or ["?"])[0]
            if any(k in all_tids for k in _order_ids(o)):
                cats["date_shift"].append(oid)  # recorded by the platform on a date outside this period
            elif unsettled(o["_date"]):
                cats["unsettled"].append(oid)
            elif destination == "ads":
                cats["not_attributed"].append(oid)
            else:
                cats["missing_in_platform"].append(oid)
        platform_count = sum(1 for tid, rows in groups.items() if any(r["date"] and p0 <= r["date"] <= p1 for r in rows))
        res["platform_side"] = {"conversions": platform_count, "rows": len(conv_in)}
        base = len(counted) or 1
        unexplained = sum(len(cats[k]) for k in ("missing_in_platform", "not_in_salla"))
        res["categories"] = {k: len(cats[k]) for k in ("matched",) + EXPLAINED + UNEXPLAINED}
        res["examples"] = {k: sorted(v)[:5] for k, v in cats.items() if k != "matched" and v}
        res["unexplained_pct"] = _q1(Decimal(unexplained) / base * 100)
        raw_gap = platform_count - len(counted)
        res["gap"] = {"count": raw_gap, "pct": _q1(Decimal(raw_gap) / base * 100) if counted else None}
        if not counted:
            status = "not_comparable"
        elif cats["duplicate"] or cats["value_mismatch"] or res["unexplained_pct"] > tol:
            status = "investigate"
        elif any(cats[k] for k in EXPLAINED if k != "not_attributed"):
            status = "explained"
        elif cats["not_attributed"]:
            status = "expected_subset"
        else:
            status = "matched"
    else:
        flags.append("totals_only")
        s_day, p_day = defaultdict(int), defaultdict(Decimal)
        s_val, p_val = Decimal(0), Decimal(0)
        for o in counted:
            s_day[o["_date"]] += 1
            s_val += parse_amount(o.get("total")) or 0
        for c in conv_in:
            p_day[c["date"]] += c["count"] if c["count"] is not None else 1
            p_val += c["value"] or 0
        sc, pc = len(counted), sum(p_day.values(), Decimal(0))
        settled_s = sum(n for d, n in s_day.items() if not unsettled(d))
        settled_p = sum((n for d, n in p_day.items() if not unsettled(d)), Decimal(0))
        res["platform_side"] = {"conversions": pc, "value": p_val if values_comparable else None}
        res["salla"]["value"] = s_val
        res["gap"] = {"count": pc - sc, "pct": _q1((pc - sc) / sc * 100) if sc else None}
        res["settled_gap"] = {"salla": settled_s, "platform": settled_p, "count": settled_p - settled_s,
                              "pct": _q1((settled_p - settled_s) / settled_s * 100) if settled_s else None}
        res["categories"] = {"unsettled": sc - settled_s}
        within = res["gap"]["pct"] is not None and abs(res["gap"]["pct"]) <= tol
        settled_within = res["settled_gap"]["pct"] is not None and abs(res["settled_gap"]["pct"]) <= tol
        if not sc:
            status = "not_comparable"
        elif destination == "ads" and pc <= sc:
            status = "expected_subset"
        elif within:
            status = "matched"
        elif unsettled_from and settled_within:
            status = "explained"
        else:
            status = "investigate"

    fe = fault_evidence or {}
    if fe.get("purchase_flow_captured") and fe.get("level", 0) >= 2 and fe.get("purchase_event_observed") is False:
        status = "event_missing"
    res.update({"status": status, "status_ar": STATUS_AR[status], "flags": flags,
                "possible_causes_ar": POSSIBLE_CAUSES_AR if status in ("investigate", "event_missing") else []})
    return res


def reconciliation_finding(res: dict, orders_ev: dict, platform_ev: dict, calc_params: dict, *, fid: str = "t1") -> dict:
    """A standard finding; every number carries a recomputable calc."""
    calc = {"fn": "tracking_reconcile", "evidence": orders_ev["evidence_id"], "platform_evidence": platform_ev["evidence_id"],
            **calc_params, "period": res["period"], "as_of": res["as_of"]}  # pinned so the reviewer recomputes the same day
    obs = [{"label_ar": "طلبات سلة المحتسبة في الفترة", "current": str(res["salla"]["orders"]), "baseline": None,
            "calc": {**calc, "field": "salla_orders"}},
           {"label_ar": f"التحويلات في {res['platform']}", "current": str(res["platform_side"]["conversions"]), "baseline": None,
            "calc": {**calc, "field": "platform_conversions"}}]
    if res["mode"] == "records":
        for k in EXPLAINED + UNEXPLAINED:
            if res["categories"].get(k):
                obs.append({"label_ar": CATEGORY_AR[k], "current": str(res["categories"][k]), "baseline": None,
                            "calc": {**calc, "field": "category", "category": k}})
    elif res.get("settled_gap"):
        obs.append({"label_ar": "الفرق بعد استبعاد الأيام غير المكتملة", "current": str(res["settled_gap"]["count"]),
                    "baseline": None, "calc": {**calc, "field": "settled_gap"}})
    status = res["status"]
    head = {"matched": "الأرقام متطابقة تقريباً بين سلة والمنصة.",
            "explained": "فيه فرق بين سلة والمنصة، لكنه يتفسّر بالكامل بأمور معروفة وما يدل على خلل في التتبع.",
            "expected_subset": "المنصة الإعلانية تسجل أقل من طلبات سلة، وهذا طبيعي لأنها تحسب الطلبات المنسوبة لإعلاناتها فقط.",
            "investigate": "فيه فرق ما قدرنا نفسره؛ هذا دليل للتحقيق وليس إثباتاً لخلل في التتبع.",
            "event_missing": "في عملية شراء حقيقية مرصودة، ما انرسل حدث الشراء للمنصة.",
            "not_comparable": "ما نقدر نقارن هذه الفترة (ما فيه طلبات محتسبة في سلة)."}[status]
    parts = [head]
    if res["mode"] == "records":
        ex = [f"{CATEGORY_AR[k]} ({res['categories'][k]})" for k in EXPLAINED if res["categories"].get(k)]
        un = [f"{CATEGORY_AR[k]} ({res['categories'][k]})" for k in UNEXPLAINED if res["categories"].get(k)]
        if ex:
            parts.append("المفسَّر: " + "، ".join(ex) + ".")
        if un:
            parts.append("غير المفسَّر: " + "، ".join(un) + ".")
    elif res.get("settled_gap") and status == "explained":
        parts.append(f"بعد استبعاد الأيام من {res['unsettled_from']} (أرقامها لم تكتمل في المنصة) صار الفرق "
                     f"{res['settled_gap']['pct']}%.")
    lim = [f"حد التطابق المستخدم ±{res['tolerance_pct']}% وهو اختيارنا للتقرير، وليس رقماً معيارياً."]
    if res["mode"] == "totals":
        lim.append("مقارنة مجاميع — لا تثبت أي طلب مفقود.")
    if res["unsettled_from"]:
        lim.append(f"الأيام من {res['unsettled_from']} قد تتغير أرقامها في المنصة (التأخر{' ونافذة الإسناد' if res['date_basis'] == 'click' else ''}).")
    if "currency_mismatch" in res["flags"]:
        lim.append("عملة المنصة مختلفة عن عملة المتجر؛ ما قارنا القيم.")
    if "multiple_purchase_actions" in res["flags"]:
        lim.append("فيه أكثر من إجراء تحويل للشراء؛ قد تُحسب نفس العملية مرتين.")
    if "small_sample" in res["flags"]:
        lim.append(f"عينة صغيرة ({res['salla']['orders']} طلب).")
    if res["salla"]["cancelled"] or res["salla"]["pending"]:
        lim.append(f"استبعدنا من سلة {res['salla']['cancelled']} ملغي/مسترجع و{res['salla']['pending']} بانتظار الدفع.")
    for ev in (orders_ev, platform_ev):
        if ev["coverage"].get("complete") is not True:
            lim.append(f"بيانات {ev['source'].get('operation')} جزئية أو غير معروفة الاكتمال: {coverage_note_ar(ev)}.")
    level = "medium" if res["mode"] == "records" and status in ("matched", "explained", "expected_subset") else "low"
    return {
        "finding_id": fid, "agent": "tracking", "store_id": orders_ev["store_id"], "account_id": orders_ev.get("account_id"),
        "metric": "tracking_reconciliation", "entity": {"type": "destination", "id": res["platform"], "label": res["platform"]},
        "period": {"current": res["period"], "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [orders_ev["evidence_id"], platform_ev["evidence_id"]],
        "coverage_note_ar": "سلة: " + coverage_note_ar(orders_ev) + "؛ المنصة: " + coverage_note_ar(platform_ev),
        "freshness": min(orders_ev["fetched_at"], platform_ev["fetched_at"]),
        "observed": obs, "interpretation_ar": " ".join(parts),
        "alternatives_ar": res["possible_causes_ar"],
        "confidence": {"level": level, "rationale_ar": "مطابقة برقم المعاملة" if res["mode"] == "records" else "مقارنة مجاميع يومية فقط"},
        "priority": "high" if status in ("investigate", "event_missing") and "small_sample" not in res["flags"] else "medium",
        "proposed_action_ref": None, "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً."},
        "requires": None, "claims_cause": False, "limitations_ar": lim,
        "reconciliation": {k: res[k] for k in ("mode", "status", "flags", "unsettled_from", "settle_days")},
        "examples": res.get("examples", {}),
    }


CALC_KEYS = ("destination", "platform", "date_basis", "window_days", "lag_days", "as_of", "store_currency",
             "tolerance_pct", "conversion_actions", "fault_evidence")


def reconcile_from_calc(orders: list[dict], conversions: list[dict], calc: dict) -> dict:
    return reconcile(orders, conversions, calc["period"], **{k: calc[k] for k in CALC_KEYS if calc.get(k) is not None})


def recompute(orders: list[dict], conversions: list[dict], calc: dict):
    res = reconcile_from_calc(orders, conversions, calc)
    f = calc.get("field")
    if f == "salla_orders":
        return res["salla"]["orders"]
    if f == "platform_conversions":
        return res["platform_side"]["conversions"]
    if f == "settled_gap":
        return res["settled_gap"]["count"]
    if f == "category":
        return res["categories"].get(calc["category"], 0)
    raise ValueError(f)
