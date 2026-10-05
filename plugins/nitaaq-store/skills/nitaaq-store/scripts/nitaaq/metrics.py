"""Shared metric dictionary and period comparison.

compare_periods() answers "did sales really change, by how much, and through
order count or order value?" with deterministic math, and refuses to call a
change real when the comparison is not valid (partial day, unequal periods,
incomplete data). decompose() splits the change by a dimension so a
specialist can say *where* it happened. Neither says *why*.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .arabic import parse_amount
from .evidence import dedupe
from .reports import RIYADH, filter_orders, parse_dt, sales_summary

METRICS_PATH = Path(__file__).resolve().parents[2] / "assets" / "metrics.json"
TWO = Decimal("0.01")
SMALL_SAMPLE_ORDERS = 30
# Below this total change (as % of the baseline) per-value shares of the change are noise
# (a near-zero denominator gives shares like 2550%), so they are not shown.
SHARE_MIN_PCT = Decimal("3")
SHARES_HIDDEN_AR = "التغير الكلي صغير جداً، فنسبة مساهمة كل بند فيه ما لها معنى؛ نعرض الفرق بالريال فقط."

FLAGS_AR = {
    "partial_current_day": "الفترة الحالية تشمل يوماً لم ينتهِ بعد؛ المقارنة غير عادلة إلا بعد اكتماله.",
    "partial_baseline_day": "فترة المقارنة تشمل يوماً لم ينتهِ.",
    "unequal_periods": "الفترتان مختلفتان في عدد الأيام.",
    "overlapping_periods": "الفترتان متداخلتان.",
    "incomplete_data": "البيانات المجلوبة جزئية؛ لا يمكن الحكم على التغير.",
    "unknown_completeness": "المصدر لم يذكر العدد الكلي؛ اكتمال البيانات غير مؤكد.",
    "small_sample": f"عدد الطلبات قليل (أقل من {SMALL_SAMPLE_ORDERS}) في إحدى الفترتين؛ التغير قد يكون عشوائياً.",
    "missing_values": "بعض الطلبات بدون قيمة؛ لم تُحسب في المبيعات.",
    "orders_without_date": "طلبات بدون تاريخ استُبعدت.",
    "duplicates_removed": "حُذفت طلبات مكررة قبل الحساب.",
}

BLOCKING_FLAGS = {"partial_current_day", "partial_baseline_day", "unequal_periods", "overlapping_periods", "incomplete_data"}


def load_dictionary(path: Path | None = None) -> dict:
    return json.loads(Path(path or METRICS_PATH).read_text(encoding="utf-8"))


def metric(metric_id: str) -> dict:
    for m in load_dictionary()["metrics"]:
        if m["id"] == metric_id:
            return m
    raise KeyError(metric_id)


def _d(s) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s))


def period_days(p) -> int:
    return (_d(p[1]) - _d(p[0])).days + 1


def default_periods(today, days: int = 28) -> dict:
    """Last `days` complete days (ending yesterday) vs the `days` before them."""
    today = _d(today)
    cur_end = today - timedelta(days=1)
    cur_start = cur_end - timedelta(days=days - 1)
    base_end = cur_start - timedelta(days=1)
    base_start = base_end - timedelta(days=days - 1)
    return {"current": [cur_start.isoformat(), cur_end.isoformat()],
            "baseline": [base_start.isoformat(), base_end.isoformat()],
            "timezone": "Asia/Riyadh", "note_ar": f"آخر {days} يوماً مكتملة مقابل {days} يوماً قبلها، بدون اليوم الجاري."}


def _pct(new, old) -> Decimal | None:
    if new is None or old in (None, 0) or old == 0:
        return None
    return ((Decimal(new) - Decimal(old)) / Decimal(old) * 100).quantize(TWO, ROUND_HALF_UP)


def _delta(new, old):
    if new is None or old is None:
        return None
    return Decimal(new) - Decimal(old)


def compare_periods(orders: list[dict], current, baseline, *, definition: str = "order_total",
                    statuses: list[str] | None = None, now: datetime | None = None,
                    records_total: int | None = None, flat_threshold_pct: Decimal | float = 3,
                    key: str = "id") -> dict:
    """Compare two periods of orders.

    orders: all fetched orders for both periods (any order). Deduplicated by `key`.
    now: current time (for the partial-day check). Defaults to the real clock.
    records_total: total the source reported for the fetch (None if unknown).
    flat_threshold_pct: a reporting threshold for calling the change "flat";
    not a statistical significance test.
    """
    flags: list[str] = []
    unique, dd = dedupe(orders, key)
    if dd["duplicates_removed"]:
        flags.append("duplicates_removed")
    if records_total is None:
        flags.append("unknown_completeness")
    elif len(unique) < records_total:
        flags.append("incomplete_data")
    c0, c1, b0, b1 = _d(current[0]), _d(current[1]), _d(baseline[0]), _d(baseline[1])
    today = (now or datetime.now(RIYADH)).astimezone(RIYADH).date()
    if c1 >= today:
        flags.append("partial_current_day")
    if b1 >= today:
        flags.append("partial_baseline_day")
    if period_days(current) != period_days(baseline):
        flags.append("unequal_periods")
    if not (b1 < c0 or c1 < b0):
        flags.append("overlapping_periods")

    cur, dropped_c = filter_orders(unique, c0.isoformat(), c1.isoformat(), statuses)
    base, dropped_b = filter_orders(unique, b0.isoformat(), b1.isoformat(), statuses)
    no_date = sum(1 for o in unique if parse_dt(o.get("date")) is None)
    if no_date:
        flags.append("orders_without_date")
    sc, sb = sales_summary(cur, definition), sales_summary(base, definition)
    if sc["orders_missing_value"] or sb["orders_missing_value"]:
        flags.append("missing_values")
    if min(sc["orders"], sb["orders"]) < SMALL_SAMPLE_ORDERS:
        flags.append("small_sample")

    sales_pct = _pct(sc["sales"], sb["sales"])
    # S = N * A over orders with a known value:  dS = dN * A0 + N1 * dA  (exact)
    n1 = sc["orders"] - sc["orders_missing_value"]
    n0 = sb["orders"] - sb["orders_missing_value"]
    decomposition = None
    if sc["aov"] is not None and sb["aov"] is not None:
        a0, a1 = sb["sales"] / n0, sc["sales"] / n1
        decomposition = {
            "order_count_effect": ((n1 - n0) * a0).quantize(TWO, ROUND_HALF_UP),
            "order_value_effect": (n1 * (a1 - a0)).quantize(TWO, ROUND_HALF_UP),
            "note_ar": "أثر عدد الطلبات = فرق العدد × متوسط الطلب السابق؛ أثر قيمة الطلب = عدد الطلبات الحالي × فرق المتوسط.",
        }
    blocking = [f for f in flags if f in BLOCKING_FLAGS]
    if blocking:
        status = "not_comparable"
    elif sales_pct is None:
        status = "unknown"
    elif abs(sales_pct) < Decimal(str(flat_threshold_pct)):
        status = "flat"
    else:
        status = "decline" if sales_pct < 0 else "increase"
    return {
        "definition": definition, "statuses": statuses, "timezone": "Asia/Riyadh",
        "current": {"period": [c0.isoformat(), c1.isoformat()], **sc},
        "baseline": {"period": [b0.isoformat(), b1.isoformat()], **sb},
        "change": {"sales": _delta(sc["sales"], sb["sales"]), "sales_pct": sales_pct,
                   "orders": sc["orders"] - sb["orders"], "orders_pct": _pct(sc["orders"], sb["orders"]),
                   "aov": _delta(sc["aov"], sb["aov"]), "aov_pct": _pct(sc["aov"], sb["aov"])},
        "decomposition": decomposition,
        "status": status, "flat_threshold_pct": str(flat_threshold_pct),
        "flags": flags, "flags_ar": [FLAGS_AR[f] for f in flags],
        "coverage": {"records_received": len(orders), "records_unique": len(unique), "records_total": records_total,
                     "dropped_current": dropped_c, "dropped_baseline": dropped_b, **dd},
    }


def _item_value(it: dict) -> Decimal | None:
    line = parse_amount(it.get("total"))
    if line is None:
        unit, q = parse_amount(it.get("price")), parse_amount(it.get("quantity")) or Decimal(1)
        line = unit * q if unit is not None else None
    return line


def _buckets(orders: list[dict], dimension: str, definition: str) -> tuple[dict, int]:
    """Value per dimension value. Item-level for product/category/brand."""
    agg: dict = defaultdict(lambda: Decimal(0))
    missing = 0
    item_dims = {"product": ("product_id", "name"), "category": ("category",), "brand": ("brand",)}
    if dimension in item_dims:
        fields = item_dims[dimension]
        for o in orders:
            for it in o.get("items") or []:
                v = _item_value(it)
                if v is None:
                    missing += 1
                    continue
                label = next((str(it[f]) for f in fields if it.get(f) not in (None, "")), "غير محدد")
                if dimension == "product" and it.get("name") and it.get("product_id"):
                    label = f"{it['name']} ({it['product_id']})"
                agg[label] += v
    else:
        for o in orders:
            s = sales_summary([o], definition)["sales"]
            if s is None:
                missing += 1
                continue
            v = o.get(dimension)
            agg[str(v) if v not in (None, "") else "غير محدد"] += s
    return agg, missing


def decompose(orders: list[dict], current, baseline, dimension: str, *, definition: str = "order_total",
              statuses: list[str] | None = None, key: str = "id", top: int = 10) -> dict:
    """Where did the change happen? Contribution of each value of `dimension`.

    Rows sum to the total change of this breakdown (an explicit «غير محدد»
    bucket catches missing values). Product/category/brand use item lines, so
    their total is item revenue, not order totals; the result says which.
    """
    unique, _ = dedupe(orders, key)
    cur, _ = filter_orders(unique, _d(current[0]).isoformat(), _d(current[1]).isoformat(), statuses)
    base, _ = filter_orders(unique, _d(baseline[0]).isoformat(), _d(baseline[1]).isoformat(), statuses)
    ac, mc = _buckets(cur, dimension, definition)
    ab, mb = _buckets(base, dimension, definition)
    basis = "item_revenue" if dimension in ("product", "category", "brand") else definition
    out = _contributions(ac, ab, top)
    out.update({"dimension": dimension, "basis": basis, "lines_without_value": {"current": mc, "baseline": mb}})
    return out


def _contributions(ac: dict, ab: dict, top: int) -> dict:
    total_delta = sum(ac.values(), Decimal(0)) - sum(ab.values(), Decimal(0))
    base_total = sum(ab.values(), Decimal(0))
    shares_ok = bool(total_delta) and (not base_total or abs(total_delta) / abs(base_total) * 100 >= SHARE_MIN_PCT)
    rows = []
    for k in set(ac) | set(ab):
        d = ac.get(k, Decimal(0)) - ab.get(k, Decimal(0))
        share = (d / total_delta * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if shares_ok else None
        rows.append({"value": k, "current": ac.get(k, Decimal(0)), "baseline": ab.get(k, Decimal(0)),
                     "change": d, "share_of_change_pct": share})
    rows.sort(key=lambda r: (r["change"], r["value"]) if total_delta < 0 else (-r["change"], r["value"]))
    out = {"total_change": total_delta, "rows": rows[:top], "rows_total": len(rows), "shares_shown": shares_ok}
    if not shares_ok:
        out["shares_note_ar"] = SHARES_HIDDEN_AR
    return out


def decompose_rows(current_rows: list[dict], baseline_rows: list[dict], label: str, value: str, *,
                   top: int = 10, exclude: list[str] | None = None, overlapping: bool = False) -> dict:
    """Where did the change happen, from two report breakdowns (e.g. Salla's sales by category).

    For sources that already aggregate per value, such as a report row per
    category with its sales. `overlapping` says one sale can sit under several
    rows (a product in two categories), so rows do not add up to the store
    total and shares are not shown.
    """
    skip = set(exclude or [])

    def agg(rows):
        out: dict = defaultdict(lambda: Decimal(0))
        missing = 0
        for r in rows:
            k = str(r.get(label) or "غير محدد")
            if k in skip:
                continue
            v = parse_amount(r.get(value))
            if v is None:
                missing += 1
                continue
            out[k] += Decimal(str(v))
        return out, missing
    ac, mc = agg(current_rows)
    ab, mb = agg(baseline_rows)
    out = _contributions(ac, ab, 10 ** 6)
    # report rows are few and mixed in direction: biggest movers first, up or down
    out["rows"] = sorted(out["rows"], key=lambda r: (-abs(r["change"]), r["value"]))[:top]
    if overlapping:
        for r in out["rows"]:
            r["share_of_change_pct"] = None
        out["shares_shown"] = False
        out["shares_note_ar"] = "البيع الواحد ممكن ينحسب تحت أكثر من بند (منتج في أكثر من تصنيف)، فالبنود ما تنجمع؛ نعرض الفرق بالريال فقط."
    out.update({"dimension": label, "basis": value, "excluded": sorted(skip), "rows_without_value": {"current": mc, "baseline": mb}})
    return out


def reconcile_report(orders: list[dict], period, reported_sales, *, statuses: list[str] | None = None,
                     reported_orders: int | None = None, definition: str = "order_total", key: str = "id",
                     tolerance=Decimal("1")) -> dict:
    """Compare our total for a period with the platform's own report and explain the gap.

    The gap is attributed only when including or excluding whole order
    statuses reproduces the reported figure within `tolerance`; otherwise it
    stays unexplained and is said so.
    """
    unique, _ = dedupe(orders, key)
    rows, _ = filter_orders(unique, _d(period[0]).isoformat(), _d(period[1]).isoformat(), None)
    by_status: dict = defaultdict(lambda: [Decimal(0), 0])
    for o in rows:
        v = sales_summary([o], definition)["sales"]
        if v is None:
            continue
        st = str(o.get("status") or "غير محدد")
        by_status[st][0] += Decimal(str(v))
        by_status[st][1] += 1
    chosen = set(statuses) if statuses else set(by_status)
    ours = sum((by_status[s][0] for s in chosen if s in by_status), Decimal(0))
    ours_n = sum(by_status[s][1] for s in chosen if s in by_status)
    reported = Decimal(str(parse_amount(reported_sales)))
    gap = (ours - reported).quantize(TWO, ROUND_HALF_UP)
    out = {"period": [str(period[0]), str(period[1])], "ours": ours.quantize(TWO, ROUND_HALF_UP), "ours_orders": ours_n,
           "reported": reported, "reported_orders": reported_orders, "gap": gap,
           "by_status": {s: {"sales": v[0].quantize(TWO, ROUND_HALF_UP), "orders": v[1]} for s, v in sorted(by_status.items())},
           "matches": abs(gap) <= tolerance, "explained_by": None}
    if out["matches"]:
        out["explanation_ar"] = "رقمنا يطابق تقرير المنصة."
        return out
    from itertools import combinations
    names = sorted(by_status)
    best = None
    for n in range(1, min(len(names), 6) + 1):
        for combo in combinations(names, n):
            alt = chosen ^ set(combo)  # toggle these statuses in or out
            total = sum((by_status[s][0] for s in alt), Decimal(0))
            if abs(total - reported) <= tolerance:
                best = (combo, alt)
                break
        if best:
            break
    if best:
        combo, alt = best
        out_s = [s for s in combo if s in chosen]
        in_s = [s for s in combo if s not in chosen]
        parts = []
        if out_s:
            parts.append("التقرير ما يحسب الطلبات بحالة " + "، ".join(f"«{s}»" for s in out_s))
        if in_s:
            parts.append("التقرير يحسب الطلبات بحالة " + "، ".join(f"«{s}»" for s in in_s))
        out["explained_by"] = {"excluded_in_report": out_s, "included_in_report": in_s}
        out["explanation_ar"] = f"الفرق {abs(gap):,} يساوي مجموع طلبات بحالات محددة: " + "؛ و".join(parts) + "."
    else:
        out["explanation_ar"] = f"الفرق {abs(gap):,} ما قدرنا نفسره بحالات الطلبات؛ ممكن يكون تعريف مختلف (ضريبة، شحن، خصم) أو توقيت تحديث التقرير."
    return out


def comparison_markdown_ar(cmp: dict, currency: str = "SAR") -> str:
    def m(v):
        return "غير متوفر" if v is None else f"{Decimal(v).quantize(TWO, ROUND_HALF_UP):,}"

    status_ar = {"decline": "انخفاض", "increase": "ارتفاع", "flat": "ثابت تقريباً",
                 "not_comparable": "المقارنة غير صالحة", "unknown": "غير معروف"}[cmp["status"]]
    c, b, ch = cmp["current"], cmp["baseline"], cmp["change"]
    lines = [
        f"**النتيجة:** {status_ar}",
        "",
        "| البند | الفترة الحالية | فترة المقارنة | التغير |",
        "|---|---|---|---|",
        f"| الفترة | {c['period'][0]} إلى {c['period'][1]} | {b['period'][0]} إلى {b['period'][1]} | — |",
        f"| المبيعات ({currency}) | {m(c['sales'])} | {m(b['sales'])} | {m(ch['sales'])} ({ch['sales_pct'] if ch['sales_pct'] is not None else '—'}%) |",
        f"| عدد الطلبات | {c['orders']} | {b['orders']} | {ch['orders']} ({ch['orders_pct'] if ch['orders_pct'] is not None else '—'}%) |",
        f"| متوسط قيمة الطلب | {m(c['aov'])} | {m(b['aov'])} | {m(ch['aov'])} |",
    ]
    if cmp.get("decomposition"):
        d = cmp["decomposition"]
        lines += ["", f"- أثر عدد الطلبات: {m(d['order_count_effect'])}", f"- أثر قيمة الطلب: {m(d['order_value_effect'])}"]
    if cmp["flags_ar"]:
        lines += ["", "**تنبيهات:**"] + [f"- {f}" for f in cmp["flags_ar"]]
    lines += ["", f"التوقيت: Asia/Riyadh. تعريف المبيعات: {cmp['definition']}. "
                  f"«ثابت تقريباً» يعني تغيراً أقل من {cmp['flat_threshold_pct']}% (حد عرض، ليس اختباراً إحصائياً)."]
    return "\n".join(lines)
