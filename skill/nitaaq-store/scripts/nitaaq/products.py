"""Product rules: prices, stock changes, sellability, identity matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .arabic import normalize, parse_amount, tokens

# ------------------------------------------------------------------ pricing

# Salla distinguishes the regular price (price) from the discounted price
# (sale_price). Field names from the Merchant API "Create Product" reference
# (docs.salla.dev, checked 2026-09-30); the connector's schema decides the
# actual names used at runtime.


@dataclass
class PriceCheck:
    ok: bool
    price: Decimal | None
    sale_price: Decimal | None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def discount_percent(self) -> Decimal | None:
        if self.price and self.sale_price is not None and self.price > 0:
            return ((self.price - self.sale_price) / self.price * 100).quantize(Decimal("0.1"))
        return None


def check_prices(price, sale_price=None, cost=None) -> PriceCheck:
    p, s, c = parse_amount(price), parse_amount(sale_price), parse_amount(cost)
    pc = PriceCheck(True, p, s)
    if p is None:
        pc.errors.append("السعر الأساسي مطلوب ويجب أن يكون رقماً.")
    elif p <= 0:
        pc.errors.append("السعر الأساسي يجب أن يكون أكبر من صفر.")
    if s is not None:
        if s <= 0:
            pc.errors.append("سعر التخفيض يجب أن يكون أكبر من صفر.")
        elif p is not None and s >= p:
            pc.errors.append("سعر التخفيض يجب أن يكون أقل من السعر الأساسي. هل قصدت تبديلهما؟")
    if c is not None and p is not None:
        effective = s if s is not None else p
        if c >= effective:
            pc.warnings.append("التكلفة تساوي أو تتجاوز سعر البيع الفعلي؛ المنتج سيباع بلا هامش أو بخسارة.")
    pc.ok = not pc.errors
    return pc


_BEFORE_AFTER = re.compile(
    r"(?:بدل|بدلا من|بدلاً من|كان|قبل|السعر الاصلي|الأصلي|الاصلي)\s*[:：]?\s*([\d٠-٩.,]+).{0,25}?"
    r"(?:صار|الحين|الان|بعد|ب|بـ|الى|إلى|سعر التخفيض|العرض)\s*[:：]?\s*([\d٠-٩.,]+)"
)


def parse_price_phrase(text: str) -> dict:
    """Read "بدل 200 صار 150" style phrasing into price / sale_price.

    Returns {} when the phrasing is not recognized; the agent then asks.
    """
    m = _BEFORE_AFTER.search(text or "")
    if not m:
        return {}
    a, b = parse_amount(m.group(1)), parse_amount(m.group(2))
    if a is None or b is None:
        return {}
    if b < a:
        return {"price": a, "sale_price": b}
    return {"price": b, "sale_price": None, "note": "القيمة الثانية أعلى؛ ليس تخفيضاً"}


# ---------------------------------------------------------------- inventory


@dataclass
class StockPlan:
    mode: str
    current: int
    requested: int
    target: int
    delta: int
    ok: bool
    message_ar: str
    safe_to_retry: bool


def plan_stock_change(mode: str, current, value) -> StockPlan:
    """mode: "set" (overwrite), "increment" or "decrement".

    The current value must come from a read made right before the write.
    Increments/decrements are not idempotent: never blindly retry them.
    """
    cur, val = parse_amount(current), parse_amount(value)
    if cur is None or val is None:
        raise ValueError("الكمية الحالية والقيمة المطلوبة يجب أن تكونا أرقاماً")
    if cur != cur.to_integral_value() or val != val.to_integral_value():
        raise ValueError("الكميات أعداد صحيحة")
    cur_i, val_i = int(cur), int(val)
    if val_i < 0:
        raise ValueError("اكتب القيمة موجبة واختر زيادة أو إنقاص")
    if mode == "set":
        target = val_i
    elif mode == "increment":
        target = cur_i + val_i
    elif mode == "decrement":
        target = cur_i - val_i
    else:
        raise ValueError("mode يجب أن يكون set أو increment أو decrement")
    ok = target >= 0
    names = {"set": "تعيين", "increment": "زيادة", "decrement": "إنقاص"}
    msg = (f"{names[mode]}: من {cur_i} إلى {target}" if ok
           else f"لا يمكن إنقاص {val_i} من {cur_i}: الناتج سالب")
    return StockPlan(mode, cur_i, val_i, target, target - cur_i, ok, msg, safe_to_retry=(mode == "set"))


def reconcile_stock_readback(plan: StockPlan, readback, sold_since=None) -> dict:
    """Explain a readback that differs because orders arrived meanwhile."""
    got = int(parse_amount(readback))
    sold = int(parse_amount(sold_since) or 0)
    if got == plan.target:
        return {"status": "persisted", "message_ar": f"الكمية الآن {got} كما هو مطلوب."}
    if sold and got == plan.target - sold:
        return {"status": "persisted_with_sales",
                "message_ar": f"حُفظت الكمية ثم بيعت {sold} قطعة، فأصبحت {got}."}
    return {"status": "mismatch", "message_ar": f"المتوقع {plan.target} لكن القراءة {got}. نحتاج مراجعة قبل أي تعديل آخر."}


def sellability(product: dict, variant: dict | None = None) -> dict:
    """Would a customer be able to buy this after a restock?

    Uses Salla's documented status values (sale / out / hidden) when present;
    unknown statuses are reported, not guessed.
    """
    reasons = []
    status = str(product.get("status", "")).lower() or None
    qty_src = variant if variant is not None else product
    qty = parse_amount(qty_src.get("quantity"))
    unlimited = bool(qty_src.get("unlimited_quantity") or product.get("unlimited_quantity"))
    if status == "hidden":
        reasons.append("المنتج مخفي")
    elif status == "out":
        reasons.append("حالة المنتج «نفد»؛ قد تحتاج تغييرها بعد التعبئة حسب سلوك المتجر")
    elif status not in (None, "sale"):
        reasons.append(f"حالة غير معروفة «{status}» تحتاج تحقق")
    if not unlimited:
        if qty is None:
            reasons.append("الكمية غير معروفة")
        elif qty <= 0:
            reasons.append("الكمية صفر")
    if variant is not None and variant.get("is_available") is False:
        reasons.append("المتغير غير متاح")
    return {"sellable": not reasons, "reasons": reasons}


# ------------------------------------------------------- identity matching

_MODEL = re.compile(r"\b[a-z]{0,4}\d{2,}[a-z0-9\-]*\b", re.I)


def model_codes(text: str) -> set:
    return {m.group(0).lower() for m in _MODEL.finditer(normalize(text))}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def match_product(ref: dict, cand: dict) -> dict:
    """Score whether cand is the same product as ref WITHOUT needing SKU/GTIN.

    ref/cand keys (all optional): name, brand, model, description,
    attributes (dict: material, size, color, shape, use...), visual
    (list of attributes the agent observed in the images), sku, gtin.
    Identifiers only corroborate; their absence never blocks a match.
    """
    score, evidence, conflicts = 0.0, [], []
    nt = _jaccard(set(tokens(ref.get("name"))), set(tokens(cand.get("name"))))
    score += 40 * nt
    if nt:
        evidence.append(f"تشابه الاسم {nt:.0%}")
    if normalize(ref.get("name")) and normalize(ref.get("name")) == normalize(cand.get("name")):
        score += 10
        evidence.append("الاسم مطابق")

    rb, cb = normalize(ref.get("brand")), normalize(cand.get("brand"))
    if rb and cb:
        if rb == cb:
            score += 15; evidence.append("نفس الماركة")
        else:
            score -= 25; conflicts.append(f"ماركة مختلفة: {ref.get('brand')} / {cand.get('brand')}")

    rm = model_codes(f"{ref.get('model', '')} {ref.get('name', '')}")
    cm = model_codes(f"{cand.get('model', '')} {cand.get('name', '')}")
    if rm and cm:
        if rm & cm:
            score += 20; evidence.append("نفس رقم الموديل")
        else:
            score -= 20; conflicts.append("رقم موديل مختلف")

    ra, ca = ref.get("attributes") or {}, cand.get("attributes") or {}
    for k in set(ra) & set(ca):
        if normalize(str(ra[k])) == normalize(str(ca[k])):
            score += 5; evidence.append(f"{k} متطابق")
        else:
            score -= 10; conflicts.append(f"{k}: {ra[k]} / {ca[k]}")

    rv, cv = {normalize(v) for v in ref.get("visual") or []}, {normalize(v) for v in cand.get("visual") or []}
    if rv and cv:
        vj = _jaccard(rv, cv)
        score += 15 * vj
        if vj:
            evidence.append(f"تشابه بصري موصوف {vj:.0%}")

    dj = _jaccard(set(tokens(ref.get("description"))), set(tokens(cand.get("description"))))
    score += 10 * dj

    for ident in ("sku", "gtin"):
        a, b = str(ref.get(ident) or "").strip(), str(cand.get(ident) or "").strip()
        if a and b:
            if a == b:
                score += 10; evidence.append(f"{ident.upper()} متطابق (دليل إضافي)")
            else:
                conflicts.append(f"{ident.upper()} مختلف")
                score -= 5

    score = max(0.0, min(100.0, score))
    if conflicts and score < 60:
        decision = "no_match"
    elif score >= 60 and not conflicts:
        decision = "match"
    elif score >= 35:
        decision = "confirm_with_merchant"
    else:
        decision = "no_match"
    return {"score": round(score, 1), "decision": decision, "evidence": evidence, "conflicts": conflicts}


def best_matches(ref: dict, catalog: list[dict], top: int = 3) -> list[dict]:
    scored = [{**match_product(ref, c), "candidate": c} for c in catalog]
    scored.sort(key=lambda r: -r["score"])
    return scored[:top]
