"""Pricing specialist, deterministic part.

Shows the math and nothing more: unit economics from the prices and costs the
store actually holds, realized prices from orders, price changes between two
periods, and break-even arithmetic for a price the merchant is considering.

It never infers price elasticity or predicts sales from a price change. A
break-even figure says how many more units a cut would need to keep the same
gross margin; it does not say whether those units will come.

Margins need cost. Without cost there is no margin, only price checks.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .arabic import parse_amount
from .evidence import coverage_note_ar, dedupe
from .reports import filter_orders

TWO = Decimal("0.01")
ONE = Decimal("0.1")
VAT_DEFAULT = Decimal("0.15")

ISSUES_AR = {
    "price_missing": "السعر الأساسي غير موجود أو ليس رقماً.",
    "invalid_sale": "سعر التخفيض يساوي أو يتجاوز السعر الأساسي.",
    "below_cost": "السعر الفعلي (بعد التخفيض) لا يغطي التكلفة.",
    "cost_missing": "التكلفة غير مسجلة؛ لا يمكن حساب الهامش.",
    "deep_discount": "تخفيض أكبر من الحد الذي اخترته للتنبيه.",
}


def _amount(v):
    if isinstance(v, dict):
        v = v.get("amount", v.get("value"))
    return parse_amount(v)


def _q(v, step=TWO):
    return None if v is None else v.quantize(step, ROUND_HALF_UP)


def product_label(p: dict) -> str:
    pid = p.get("id", p.get("product_id"))
    name = p.get("name") or ""
    return f"{name} ({pid})" if name else str(pid)


def product_economics(products: list[dict], *, prices_include_vat: bool | None = None,
                      vat_rate: Decimal = VAT_DEFAULT, deep_discount_pct: Decimal | int = 40) -> dict:
    """Per-product price checks and unit margin.

    prices_include_vat: True if listed prices include VAT, False if not, None if
    unknown. When unknown, margin is computed on the price as listed and the
    result says so. deep_discount_pct is the merchant's alert threshold, not a
    market norm.
    """
    rows = []
    unique, _ = dedupe(products, "id")
    for p in unique:
        price = _amount(p.get("price"))
        sale = _amount(p.get("sale_price"))
        cost = _amount(p.get("cost", p.get("cost_price")))
        issues = []
        if price is None or price <= 0:
            issues.append("price_missing")
        valid_sale = sale is not None and sale > 0 and price is not None and sale < price
        if sale is not None and sale > 0 and price is not None and sale >= price:
            issues.append("invalid_sale")
        effective = sale if valid_sale else price
        discount = _q((price - sale) / price * 100, ONE) if valid_sale else None
        if discount is not None and discount >= Decimal(str(deep_discount_pct)):
            issues.append("deep_discount")
        net = None
        if effective is not None:
            net = effective / (1 + vat_rate) if prices_include_vat else effective
        margin = margin_pct = None
        if cost is None:
            issues.append("cost_missing")
        elif net is not None:
            margin = net - cost
            margin_pct = _q(margin / net * 100, ONE) if net else None
            if margin <= 0:
                issues.append("below_cost")
        rows.append({"id": str(p.get("id")), "label": product_label(p), "price": price, "sale_price": sale,
                     "effective_price": effective, "discount_pct": discount, "net_price": _q(net), "cost": cost,
                     "unit_margin": _q(margin), "margin_pct": margin_pct, "issues": issues})
    basis = {True: "price_ex_vat", False: "price_as_listed_ex_vat", None: "price_as_listed_vat_unknown"}[prices_include_vat]
    return {"basis": basis, "vat_rate": vat_rate, "rows": rows,
            "counts": {k: sum(1 for r in rows if k in r["issues"]) for k in ISSUES_AR},
            "products": len(rows)}


def realized_prices(orders: list[dict], period, *, statuses: list[str] | None = None) -> dict:
    """Units, item revenue and average realized unit price per product in a period."""
    unique, _ = dedupe(orders, "id")
    kept, _ = filter_orders(unique, period[0], period[1], statuses)
    agg: dict = defaultdict(lambda: {"units": Decimal(0), "revenue": Decimal(0), "lines": 0, "lines_without_value": 0})
    for o in kept:
        for it in o.get("items") or []:
            key = product_label(it)
            q = parse_amount(it.get("quantity"))
            line = parse_amount(it.get("total"))
            if line is None and parse_amount(it.get("price")) is not None and q is not None:
                line = parse_amount(it.get("price")) * q
            a = agg[key]
            a["lines"] += 1
            if q is None or line is None or q <= 0:
                a["lines_without_value"] += 1
                continue
            a["units"] += q
            a["revenue"] += line
    out = {}
    for k, a in agg.items():
        out[k] = {**a, "avg_unit_price": _q(a["revenue"] / a["units"]) if a["units"] else None}
    return out


def price_changes(orders: list[dict], current, baseline, *, statuses: list[str] | None = None,
                  threshold_pct: Decimal | int = 5) -> dict:
    """Products whose average realized unit price moved by at least threshold_pct between periods.

    The threshold is a reporting choice. A moved average can come from a
    price edit, a coupon, an offer or a different variant mix; the result does
    not say which.
    """
    cur, base = realized_prices(orders, current, statuses=statuses), realized_prices(orders, baseline, statuses=statuses)
    rows = []
    for k in sorted(set(cur) | set(base)):
        c, b = (cur.get(k) or {}).get("avg_unit_price"), (base.get(k) or {}).get("avg_unit_price")
        pct = _q((c - b) / b * 100, ONE) if c is not None and b else None
        rows.append({"product": k, "current": c, "baseline": b, "change_pct": pct,
                     "units_current": (cur.get(k) or {}).get("units", Decimal(0)),
                     "units_baseline": (base.get(k) or {}).get("units", Decimal(0)),
                     "moved": pct is not None and abs(pct) >= Decimal(str(threshold_pct))})
    moved = [r for r in rows if r["moved"]]
    return {"rows": rows, "moved": moved, "threshold_pct": Decimal(str(threshold_pct)),
            "signals": ["price_changed"] if moved else []}


def breakeven(price, cost, new_price) -> dict:
    """Units change needed for a new price to keep the same gross margin. Arithmetic only."""
    p, c, n = _amount(price), _amount(cost), _amount(new_price)
    if None in (p, c, n):
        return {"ok": False, "note_ar": "يلزم السعر الحالي والتكلفة والسعر المقترح."}
    m0, m1 = p - c, n - c
    if m0 <= 0:
        return {"ok": False, "unit_margin_now": m0, "note_ar": "الهامش الحالي صفر أو سالب؛ لا معنى لحساب التعادل."}
    if m1 <= 0:
        return {"ok": True, "unit_margin_now": m0, "unit_margin_new": m1, "units_change_needed_pct": None,
                "note_ar": "السعر المقترح لا يغطي التكلفة؛ كل قطعة تباع بخسارة مهما زادت الكمية."}
    pct = _q((m0 / m1 - 1) * 100, ONE)
    return {"ok": True, "unit_margin_now": m0, "unit_margin_new": m1, "units_change_needed_pct": pct,
            "note_ar": (f"لتحافظ على نفس إجمالي الهامش تحتاج تغيّر الكمية المباعة بنسبة {pct}%. "
                        "هذا حساب فقط، وليس توقعاً بأن الكمية ستتغير.")}


# ------------------------------------------------------------------ findings and proposals

def _base(ev: dict, fid: str, metric: str) -> dict:
    return {"finding_id": fid, "agent": "pricing", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
            "metric": metric, "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev),
            "freshness": ev["fetched_at"], "alternatives_ar": [], "proposed_action_ref": None,
            "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً."}, "requires": None,
            "claims_cause": False}


def economics_findings(econ: dict, ev: dict, *, prices_include_vat: bool | None = None, start: int = 1) -> list[dict]:
    """One finding per issue type that occurs, listing the affected products."""
    out = []
    partial = ev["coverage"].get("complete") is not True
    lim_cov = ["قائمة المنتجات جزئية أو غير معروفة الاكتمال؛ قد توجد منتجات أخرى بنفس المشكلة."] if partial else []
    vat_note = {None: ["لا نعرف هل الأسعار تشمل الضريبة؛ الهامش محسوب على السعر كما هو، وقد يكون أقل فعلياً."],
                True: ["الهامش محسوب بعد خصم ضريبة القيمة المضافة 15% من السعر."],
                False: []}[prices_include_vat]
    calc = {"fn": "product_economics", "evidence": ev["evidence_id"], "prices_include_vat": prices_include_vat}
    n = start
    for code, field, label in (("invalid_sale", "sale_price", "سعر التخفيض"), ("below_cost", "unit_margin", "هامش القطعة"),
                               ("deep_discount", "discount_pct", "نسبة التخفيض")):
        rows = [r for r in econ["rows"] if code in r["issues"]]
        if not rows:
            continue
        f = _base(ev, f"p{n}", "product_margin" if code == "below_cost" else "price")
        f.update({
            "entity": {"type": "product", "id": None, "label": f"{len(rows)} منتج"},
            "period": {"current": "now", "baseline": None, "timezone": "Asia/Riyadh"},
            "observed": [{"label_ar": f"{r['label']}: {label}", "current": str(r[field]), "baseline": None,
                          "calc": {**calc, "entity": r["id"], "field": field}} for r in rows[:20]],
            "interpretation_ar": f"{ISSUES_AR[code]} عدد المنتجات: {len(rows)}.",
            "confidence": {"level": "high" if not partial else "medium",
                           "rationale_ar": "مقارنة مباشرة بين الحقول المسجلة في المتجر؛ " + coverage_note_ar(ev)},
            "priority": "high" if code in ("invalid_sale", "below_cost") else "medium",
            "limitations_ar": lim_cov + (vat_note if code == "below_cost" else []) +
                              (["الحد 40% (أو ما اخترته) للتنبيه فقط وليس معياراً للسوق."] if code == "deep_discount" else []),
        })
        out.append(f)
        n += 1
    missing = econ["counts"]["cost_missing"]
    if missing:
        f = _base(ev, f"p{n}", "product_margin")
        f.update({
            "entity": {"type": "product", "id": None, "label": f"{missing} منتج"},
            "period": {"current": "now", "baseline": None, "timezone": "Asia/Riyadh"},
            "observed": [{"label_ar": "منتجات بدون تكلفة", "current": str(missing), "baseline": None,
                          "calc": {**calc, "entity": None, "field": "cost_missing_count"}}],
            "interpretation_ar": f"منتجات بدون تكلفة مسجلة: {missing} من {econ['products']}، فما نقدر نحسب هامشها.",
            "confidence": {"level": "high", "rationale_ar": "عدّ مباشر للحقول الفارغة؛ " + coverage_note_ar(ev)},
            "priority": "medium", "limitations_ar": lim_cov + ["بدون التكلفة لا نقدر نحكم إن كان السعر مربحاً."],
        })
        out.append(f)
    return out


def price_change_finding(pc: dict, ev: dict, current, baseline, *, fid: str = "p9",
                         statuses: list[str] | None = None) -> dict | None:
    if not pc["moved"]:
        return None
    f = _base(ev, fid, "avg_unit_price")
    calc = {"fn": "realized_price", "evidence": ev["evidence_id"], "current": list(current), "baseline": list(baseline),
            "statuses": statuses}
    f.update({
        "entity": {"type": "product", "id": None, "label": f"{len(pc['moved'])} منتج"},
        "period": {"current": list(current), "baseline": list(baseline), "timezone": "Asia/Riyadh"},
        "observed": [{"label_ar": r["product"], "current": str(r["current"]), "baseline": str(r["baseline"]),
                      "change_pct": str(r["change_pct"]), "calc": {**calc, "product": r["product"]}}
                     for r in pc["moved"][:20]],
        "interpretation_ar": (f"متوسط سعر البيع الفعلي تغيّر {pc['threshold_pct']}% أو أكثر في {len(pc['moved'])} منتج بين الفترتين. "
                              "هذا ما دفعه العملاء فعلاً، ولا يحدد هل التغير من تعديل السعر أو كوبون أو عرض."),
        "alternatives_ar": ["تعديل السعر في المتجر", "كوبون أو عرض في إحدى الفترتين", "اختلاف المقاسات أو الخيارات المباعة"],
        "confidence": {"level": "medium", "rationale_ar": "متوسطات محسوبة من بنود الطلبات؛ " + coverage_note_ar(ev)},
        "priority": "medium",
        "limitations_ar": ["لا نستنتج من هذا كيف تتأثر الكمية بالسعر؛ يحتاج تجربة مصممة."] +
                          ([] if ev["coverage"].get("complete") is True else ["الطلبات جزئية أو غير معروفة الاكتمال."]),
    })
    return f


def invalid_sale_proposals(econ: dict, store_id: str, source_finding: str) -> list[dict]:
    """A data fix the merchant must approve: remove a sale price that is not below the regular price."""
    items = [{"entity_id": r["id"], "label_ar": r["label"], "before": {"sale_price": str(r["sale_price"])},
              "after": {"sale_price": None}} for r in econ["rows"] if "invalid_sale" in r["issues"]]
    if not items:
        return []
    return [{"proposal_id": "pp1", "store_id": str(store_id), "operation": "products.update", "items": items,
             "sensitivity": "high", "reversible": True, "customer_visible": True, "source_findings": [source_finding],
             "proposed_by": "pricing",
             "note_ar": "إلغاء سعر التخفيض غير الصحيح (أو تصحيحه للقيمة اللي تبيها). يحتاج موافقتك قبل أي تعديل."}]
