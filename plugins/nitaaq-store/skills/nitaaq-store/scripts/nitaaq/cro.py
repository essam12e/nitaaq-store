"""Conversion (CRO) specialist, deterministic part.

Checks what a shopper can see on a public product page: the price, a clear
buy button, images, a description, shipping and returns information,
payment options, reviews and a way to ask. Also summarises abandoned carts
when the connector exposes them.

A page check says what is present or missing. It never claims a conversion
effect, and it never recommends fake urgency, invented scarcity or made-up
reviews. Urgency or low-stock text found on a page is flagged for the
merchant to confirm it is true.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from .arabic import normalize, parse_amount
from .evidence import coverage_note_ar, dedupe
from .reports import filter_orders
from .seo_audit import _PageParser, visible_prices

PATTERNS = {
    "buy_button": r"(اضف للسله|اضف الى السله|اضافه للسله|اضافه الى السله|اشتر الان|اشتري الان|اطلب الان|add to cart|buy now)",
    "shipping_info": r"(الشحن|شحن|التوصيل|توصيل)",
    "returns_policy": r"(الاسترجاع|استرجاع|الاستبدال|استبدال|الارجاع|ارجاع)",
    "payment_options": r"(تابي|tabby|تمارا|tamara|مدى|mada|apple ?pay|ابل باي|stc ?pay|فيزا|visa|ماستر|mastercard)",
    "reviews": r"(تقييم|تقييمات|مراجعات|اراء العملاء|reviews?)",
    "contact": r"(واتساب|whatsapp|wa\.me|تواصل معنا|اتصل بنا)",
    "out_of_stock": r"(نفدت الكميه|نفذت الكميه|غير متوفر|نفد المخزون|out of stock|sold out)",
    "urgency": r"(باقي \d+|متبقي \d+|ينتهي العرض خلال|ينتهي خلال|العرض ينتهي|اخر \d+ قطع|countdown|كميه محدوده)",
}
_RX = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}

LABELS_AR = {
    "price_visible": "السعر ظاهر في الصفحة",
    "price_matches": "السعر الظاهر يطابق سعر المنتج في المتجر",
    "buy_button": "زر شراء واضح",
    "images": "عدد الصور",
    "description_words": "عدد كلمات الصفحة",
    "shipping_info": "معلومات الشحن أو التوصيل",
    "returns_policy": "سياسة الاسترجاع أو الاستبدال",
    "payment_options": "طرق الدفع ظاهرة",
    "reviews": "تقييمات العملاء ظاهرة",
    "contact": "طريقة تواصل (واتساب أو غيره)",
    "out_of_stock": "المنتج يظهر غير متوفر",
    "urgency": "عبارات استعجال أو كمية محدودة",
}
MISSING_AR = {
    "price_visible": "سعر ظاهر", "price_matches": "سعر مطابق لسعر المتجر", "buy_button": "زر شراء واضح",
    "shipping_info": "معلومات الشحن", "returns_policy": "سياسة الاسترجاع", "payment_options": "طرق الدفع",
    "reviews": "تقييمات العملاء", "contact": "طريقة تواصل", "out_of_stock": "توفر المنتج (يظهر غير متوفر)",
}
YES, NO = "نعم", "لا"


def _text(html: str) -> tuple[str, _PageParser]:
    p = _PageParser()
    p.feed(html or "")
    links = " ".join(p.links)
    return " ".join(p.text) + " " + links, p


def page_checks(html: str, url: str, product: dict | None = None) -> dict:
    """What a shopper can see on one product page. Values are facts about the HTML, not judgments of impact."""
    raw, p = _text(html)
    t = normalize(raw)
    prices = visible_prices(raw)
    checks = []

    def add(cid, value, *, ok=None, evidence=""):
        checks.append({"id": cid, "label_ar": LABELS_AR[cid], "value": value, "ok": ok, "evidence": evidence[:160]})

    add("price_visible", YES if prices else NO, ok=bool(prices),
        evidence="، ".join(str(x) for x in prices[:5]))
    if product:
        price = parse_amount(product.get("price"))
        sale = parse_amount(product.get("sale_price"))
        expected = sale if sale is not None and price is not None and 0 < sale < price else price
        if expected is not None and prices:
            match = any(abs(x - expected) < Decimal("0.01") for x in prices)
            add("price_matches", YES if match else NO, ok=match, evidence=f"المتوقع {expected}")
    for cid in ("buy_button", "shipping_info", "returns_policy", "payment_options", "reviews", "contact"):
        m = _RX[cid].search(t) or _RX[cid].search(raw)
        add(cid, YES if m else NO, ok=bool(m), evidence=m.group(0) if m else "")
    imgs = [i for i in p.images if i.get("src")]
    add("images", str(len(imgs)), ok=None)
    add("description_words", str(len(" ".join(p.text).split())), ok=None)
    m = _RX["out_of_stock"].search(t) or _RX["out_of_stock"].search(raw)
    add("out_of_stock", YES if m else NO, ok=not m, evidence=m.group(0) if m else "")
    m = _RX["urgency"].search(t) or _RX["urgency"].search(raw)
    add("urgency", YES if m else NO, ok=None, evidence=m.group(0) if m else "")
    return {"url": url, "checks": checks, "missing": [c["id"] for c in checks if c["ok"] is False],
            "verify_truthful": [c["evidence"] for c in checks if c["id"] == "urgency" and c["value"] == YES]}


def check_value(record: dict, check_id: str) -> str | None:
    """Recompute one check from a saved page record ({"id": url, "html": ..., "product": ...})."""
    r = page_checks(record.get("html") or "", record.get("url") or record.get("id"), record.get("product"))
    return next((c["value"] for c in r["checks"] if c["id"] == check_id), None)


def abandoned_carts(carts: list[dict], period=None) -> dict:
    """Count, value and the products most often left in carts. No recovery rate is claimed."""
    unique, dd = dedupe(carts, "id")
    if period:
        rows = [c if c.get("date") else {**c, "date": c.get("created_at") or c.get("updated_at")} for c in unique]
        unique, _ = filter_orders(rows, period[0], period[1])
    total = Decimal(0)
    missing_total = 0
    prods: dict = defaultdict(int)
    for c in unique:
        v = parse_amount(c.get("total"))
        if v is None:
            missing_total += 1
        else:
            total += v
        for it in c.get("items") or []:
            prods[it.get("name") or str(it.get("product_id"))] += 1
    top = sorted(prods.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {"carts": len(unique), "value": total, "carts_without_total": missing_total,
            "top_products": [{"product": k, "carts": v} for k, v in top],
            "note_ar": "السلات المتروكة تشمل من كان يتصفح فقط؛ لا نفترض نسبة استرجاع."}


# ------------------------------------------------------------------ findings

def page_finding(res: dict, ev: dict, *, fid: str = "c1") -> dict:
    calc = {"fn": "page_checks", "evidence": ev["evidence_id"], "url": res["url"]}
    obs = [{"label_ar": c["label_ar"], "current": c["value"], "baseline": None, "calc": {**calc, "check": c["id"]}}
           for c in res["checks"]]
    missing = [MISSING_AR.get(c["id"], c["label_ar"]) for c in res["checks"] if c["ok"] is False]
    interp = (f"في صفحة المنتج ({res['url']}) ينقص: " + "، ".join(missing) + "."
              if missing else f"صفحة المنتج ({res['url']}) فيها العناصر الأساسية اللي فحصناها.")
    lim = ["فحص لنص الصفحة كما وصلنا؛ عناصر تظهر بالجافاسكربت فقط قد لا تُرى.",
           "الفحص يبين الموجود والناقص، ولا يقيس أثره على المبيعات."]
    if res["verify_truthful"]:
        lim.append("في الصفحة عبارات استعجال أو كمية محدودة (" + "، ".join(res["verify_truthful"]) +
                   "). تأكد إنها صحيحة؛ الاستعجال غير الحقيقي يضر الثقة وقد يخالف أنظمة حماية المستهلك.")
    return {
        "finding_id": fid, "agent": "cro", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "page_checks", "entity": {"type": "page", "id": res["url"], "label": res["url"]},
        "period": {"current": "now", "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": obs, "interpretation_ar": interp, "alternatives_ar": [],
        "confidence": {"level": "medium", "rationale_ar": "فحص نصي للصفحة العامة؛ لا يشمل سلوك الزوار."},
        "priority": "medium" if missing else "low", "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "لا نقدر أثر هذه التحسينات بدون بيانات زيارات وتجربة."},
        "requires": None, "limitations_ar": lim, "claims_cause": False,
    }
