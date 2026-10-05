"""Growth specialist, deterministic part.

From orders (and customers when available): new vs returning customers and
their revenue, repeat purchase, cohorts by first-order month, and the sample
size an experiment needs before it starts.

Funnel rates (conversion, add-to-cart) exist only when traffic data is
supplied; orders alone never produce a conversion rate. CAC exists only with
real ad spend. "New" customers are new to the store only when all-time first
order dates are known; otherwise they are "first seen in the data we have",
and the result says so.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import NormalDist

from .arabic import parse_amount
from .evidence import coverage_note_ar, dedupe
from .reports import filter_orders, parse_dt

TWO = Decimal("0.01")
ONE = Decimal("0.1")
FUNNEL_STEPS = ("sessions", "product_views", "add_to_cart", "checkout", "purchases")
FUNNEL_AR = {"sessions": "الزيارات", "product_views": "مشاهدة منتج", "add_to_cart": "إضافة للسلة",
             "checkout": "بدء الدفع", "purchases": "الشراء"}


def _q(v, step=TWO):
    return None if v is None else Decimal(v).quantize(step, ROUND_HALF_UP)


def _pct(a, b):
    return _q(Decimal(a) / Decimal(b) * 100, ONE) if b else None


def _cust(o: dict):
    c = o.get("customer_id")
    if c in (None, ""):
        c = (o.get("customer") or {}).get("id") if isinstance(o.get("customer"), dict) else None
    return str(c) if c not in (None, "") else None


def first_seen(orders: list[dict]) -> tuple[dict, str | None]:
    """First order date per customer within the orders given, and the earliest date in the data."""
    first: dict = {}
    earliest = None
    for o in orders:
        dt = parse_dt(o.get("date"))
        if dt is None:
            continue
        d = dt.date()
        earliest = d if earliest is None or d < earliest else earliest
        c = _cust(o)
        if c and (c not in first or d < first[c]):
            first[c] = d
    return first, earliest.isoformat() if earliest else None


def customer_mix(orders: list[dict], period, *, first_order_dates: dict | None = None,
                 statuses: list[str] | None = None, history: list[dict] | None = None) -> dict:
    """New vs returning customers and their revenue in one period.

    history: all orders available (defaults to `orders`) to find first-seen dates.
    first_order_dates: {customer_id: date} from the store (all-time); preferred.
    """
    unique, _ = dedupe(orders, "id")
    kept, _ = filter_orders(unique, period[0], period[1], statuses)
    if first_order_dates:
        first = {str(k): parse_dt(v).date() for k, v in first_order_dates.items() if parse_dt(v)}
        basis, hist_start = "all_time", None
    else:
        first, hist_start = first_seen(dedupe(history or orders, "id")[0])
        basis = "first_seen_in_data"
    d0 = date.fromisoformat(period[0])
    per: dict = defaultdict(lambda: {"orders": 0, "revenue": Decimal(0)})
    no_customer = 0
    for o in kept:
        c = _cust(o)
        if not c:
            no_customer += 1
            continue
        per[c]["orders"] += 1
        per[c]["revenue"] += parse_amount(o.get("total")) or Decimal(0)
    new = {c for c in per if c in first and first[c] >= d0}
    unknown = {c for c in per if c not in first}
    returning = set(per) - new - unknown
    rev = lambda cs: sum((per[c]["revenue"] for c in cs), Decimal(0))  # noqa: E731
    repeaters = sum(1 for c in per if per[c]["orders"] > 1)
    out = {
        "period": list(period), "basis": basis, "history_start": hist_start,
        "customers": len(per), "new_customers": len(new), "returning_customers": len(returning),
        "unknown_customers": len(unknown), "orders_without_customer": no_customer,
        "new_revenue": rev(new), "returning_revenue": rev(returning),
        "repeat_in_period": repeaters, "repeat_rate_pct": _pct(repeaters, len(per)),
    }
    out["history_days_before_period"] = (d0 - date.fromisoformat(hist_start)).days if hist_start else None
    if basis == "first_seen_in_data" and hist_start and date.fromisoformat(hist_start) >= d0 - timedelta(days=365):
        out["note_ar"] = (f"«جديد» يعني أول طلب له في البيانات المتاحة (من {hist_start})، وقد يكون اشترى قبلها. "
                          "لعدد دقيق نحتاج تاريخ أول طلب لكل عميل من المتجر.")
    return out


def mix_change(orders: list[dict], current, baseline, *, min_history_days: int = 90, **kw) -> dict:
    """Compare the customer mix of two periods.

    With first-seen dates only, a period that starts near the beginning of the
    data counts old customers as new. If the earlier period has less than
    min_history_days of history before it, the new/returning split is flagged
    insufficient_history and must not be compared.
    """
    c, b = customer_mix(orders, current, **kw), customer_mix(orders, baseline, **kw)
    fields = ("customers", "new_customers", "returning_customers", "new_revenue", "returning_revenue")
    flags = []
    if b["basis"] == "first_seen_in_data" and (b["history_days_before_period"] or 0) < min_history_days:
        flags.append("insufficient_history")
    return {"current": c, "baseline": b, "change": {f: c[f] - b[f] for f in fields},
            "change_pct": {f: _pct(c[f] - b[f], b[f]) for f in fields}, "flags": flags,
            "min_history_days": min_history_days}


def cohorts(orders: list[dict], *, windows=(30, 60, 90), as_of: str | None = None) -> list[dict]:
    """Customers by first-order month and how many ordered again within N days of their first order.

    A window that has not fully elapsed for the whole cohort is None, not a low number.
    """
    unique, _ = dedupe(orders, "id")
    by_c: dict = defaultdict(list)
    for o in unique:
        c, dt = _cust(o), parse_dt(o.get("date"))
        if c and dt:
            by_c[c].append(dt.date())
    end = date.fromisoformat(as_of) if as_of else max((d for ds in by_c.values() for d in ds), default=None)
    groups: dict = defaultdict(list)
    for c, ds in by_c.items():
        ds.sort()
        groups[ds[0].strftime("%Y-%m")].append(ds)
    rows = []
    for month in sorted(groups):
        members = groups[month]
        last_first = max(ds[0] for ds in members)
        row = {"cohort": month, "customers": len(members)}
        for w in windows:
            if end is None or last_first + timedelta(days=w) > end:
                row[f"returned_{w}d"] = None
                row[f"returned_{w}d_pct"] = None
                continue
            n = sum(1 for ds in members if any(0 < (d - ds[0]).days <= w for d in ds[1:]))
            row[f"returned_{w}d"] = n
            row[f"returned_{w}d_pct"] = _pct(n, len(members))
        rows.append(row)
    return rows


def funnel(current: dict | None, baseline: dict | None = None, *, drop_threshold_pct: Decimal | int = 10) -> dict:
    """Step rates from traffic data. Without sessions there is no funnel and no conversion rate.

    drop_threshold_pct: relative fall in a step rate that raises funnel_drop (a reporting choice).
    """
    if not current or current.get("sessions") in (None, "", 0):
        return {"available": False, "conversion_rate_pct": None, "steps": [], "signals": [],
                "note_ar": "لا توجد بيانات زيارات، فلا يمكن حساب معدل التحويل أو مراحل القمع. الطلبات وحدها لا تكفي."}

    def rates(d):
        present = [s for s in FUNNEL_STEPS if d.get(s) not in (None, "")]
        out = []
        for a, b in zip(present, present[1:]):
            out.append({"from": a, "to": b, "rate_pct": _pct(parse_amount(d[b]), parse_amount(d[a]))})
        return out, present

    cur, present = rates(current)
    base = rates(baseline)[0] if baseline and baseline.get("sessions") else []
    bmap = {(r["from"], r["to"]): r["rate_pct"] for r in base}
    signals = []
    for r in cur:
        b = bmap.get((r["from"], r["to"]))
        r["baseline_rate_pct"] = b
        r["relative_change_pct"] = _pct(r["rate_pct"] - b, b) if r["rate_pct"] is not None and b else None
        if r["relative_change_pct"] is not None and r["relative_change_pct"] <= -Decimal(str(drop_threshold_pct)):
            r["dropped"] = True
            signals = ["funnel_drop"]
    conv = _pct(parse_amount(current.get("purchases")), parse_amount(current["sessions"])) \
        if current.get("purchases") not in (None, "") else None
    return {"available": True, "steps_present": present, "steps": cur, "conversion_rate_pct": conv,
            "denominator": "sessions", "signals": signals}


def sample_size(baseline_rate, mde_relative, *, alpha: float = 0.05, power: float = 0.8,
                daily_visitors_per_arm=None) -> dict:
    """Visitors per arm to detect a relative lift in a conversion rate (two-sided, two proportions)."""
    p1 = float(parse_amount(baseline_rate))
    m = float(parse_amount(mde_relative))
    if p1 > 1:
        p1 /= 100
    if m > 1:
        m /= 100
    p2 = p1 * (1 + m)
    if not (0 < p1 < 1 and 0 < p2 < 1):
        raise ValueError("baseline_rate and lift must give rates between 0 and 1")
    z = NormalDist()
    za, zb = z.inv_cdf(1 - alpha / 2), z.inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = ((za * (2 * pbar * (1 - pbar)) ** 0.5 + zb * (p1 * (1 - p1) + p2 * (1 - p2)) ** 0.5) ** 2) / (p2 - p1) ** 2
    n = int(n) + 1
    out = {"per_arm": n, "total": 2 * n, "baseline_rate": p1, "target_rate": round(p2, 6), "alpha": alpha, "power": power,
           "rules_ar": ["حدد المدة وحجم العينة قبل البدء، ولا توقف التجربة أول ما تشوف فرق.",
                        "شغّل التجربة أسبوع كامل على الأقل حتى تغطي أيام الأسبوع.",
                        "جهّز طريقة الرجوع قبل الإطلاق، والإطلاق نفسه يحتاج موافقتك."]}
    if daily_visitors_per_arm:
        out["days_needed"] = -(-n // int(daily_visitors_per_arm))
    return out


def cac(ad_spend, new_customers) -> dict:
    s = parse_amount(ad_spend)
    if s is None:
        return {"cac": None, "note_ar": "لا يمكن حساب تكلفة اكتساب العميل بدون بيانات الإنفاق الإعلاني."}
    if not new_customers:
        return {"cac": None, "note_ar": "لا يوجد عملاء جدد في الفترة؛ التكلفة غير معرّفة."}
    return {"cac": _q(s / Decimal(new_customers)),
            "note_ar": "الإنفاق كله مقسوم على كل العملاء الجدد، بما فيهم من جاء بدون إعلان."}


# ------------------------------------------------------------------ findings

def mix_finding(mc: dict, ev: dict, *, fid: str = "g1", statuses: list[str] | None = None,
                first_order_dates_ref: str | None = None) -> dict:
    c, b = mc["current"], mc["baseline"]
    calc = {"fn": "customer_mix", "evidence": ev["evidence_id"], "current": c["period"], "baseline": b["period"],
            "statuses": statuses}
    obs = [{"label_ar": label, "current": str(c[f]), "baseline": str(b[f]),
            "change_pct": str(mc["change_pct"][f]), "calc": {**calc, "field": f}}
           for f, label in (("customers", "العملاء"), ("new_customers", "عملاء جدد"),
                            ("returning_customers", "عملاء عائدون"), ("new_revenue", "مبيعات العملاء الجدد"),
                            ("returning_revenue", "مبيعات العملاء العائدين"))]
    dn, dr = mc["change"]["new_revenue"], mc["change"]["returning_revenue"]
    where = "العملاء الجدد" if abs(dn) >= abs(dr) else "العملاء العائدين"
    lim = [x for x in (c.get("note_ar"),) if x]
    short = "insufficient_history" in mc.get("flags", [])
    if short:
        obs = obs[:1]
        lim.append(f"البيانات تبدأ قبل الفترة السابقة بأقل من {mc['min_history_days']} يوم، فتقسيم جديد/عائد غير صالح للمقارنة.")
    if c["orders_without_customer"] or b["orders_without_customer"]:
        lim.append(f"طلبات بدون رقم عميل: {c['orders_without_customer']} حالياً و{b['orders_without_customer']} سابقاً؛ غير محسوبة هنا.")
    if ev["coverage"].get("complete") is not True:
        lim.append("الطلبات جزئية أو غير معروفة الاكتمال.")
    return {
        "finding_id": fid, "agent": "growth", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "customer_mix", "entity": None,
        "period": {"current": c["period"], "baseline": b["period"], "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]] + ([first_order_dates_ref] if first_order_dates_ref else []),
        "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"], "observed": obs,
        "interpretation_ar": ("لا يمكن مقارنة العملاء الجدد بالعائدين لأن البيانات المتاحة قصيرة؛ "
                              f"عدد العملاء {c['customers']} مقابل {b['customers']}. نحتاج تاريخ أول طلب لكل عميل أو طلبات أقدم.")
        if short else (f"العملاء الجدد {c['new_customers']} مقابل {b['new_customers']}، والعائدون "
                       f"{c['returning_customers']} مقابل {b['returning_customers']}. "
                       + (f"أكبر تغير في المبيعات جاء من {where}." if dn or dr else
                          "ما تغيّرت مبيعات الجدد ولا العائدين بين الفترتين.")),
        "alternatives_ar": [], "confidence": {
            "level": "low" if short else ("high" if c["basis"] == "all_time" and ev["coverage"].get("complete") is True else "medium"),
            "rationale_ar": ("تاريخ أول طلب من المتجر" if c["basis"] == "all_time" else "أول ظهور في البيانات المتاحة") +
                            "؛ " + coverage_note_ar(ev)},
        "priority": "medium", "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً."}, "requires": None,
        "limitations_ar": lim, "claims_cause": False,
    }
