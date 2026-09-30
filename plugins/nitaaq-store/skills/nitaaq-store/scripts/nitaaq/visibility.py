"""Observed AI visibility: query sets and honest summaries of observations.

AI answers are non-deterministic, personalized and change over time, so an
observation is a sample, not a ranking. Observations come from:
  - manual checks by the merchant or the agent in a normal user session,
  - official APIs / tools the host provides and the platform permits.
Automated scraping of AI products against their terms is out of scope.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

CATEGORIES = {
    "brand": "بحث باسم المتجر/الماركة",
    "product": "بحث عن منتج أو موديل",
    "category_discovery": "اكتشاف فئة (بدون ذكر المتجر)",
    "comparison": "مقارنة",
    "informational": "سؤال معلوماتي",
    "local": "بحث محلي بالمدينة",
    "trust": "ثقة وسياسات",
}


def build_queries(store_name: str, categories: list[str], products: list[str] | None = None,
                  cities: list[str] | None = None, competitors: list[str] | None = None,
                  brand_aliases: list[str] | None = None) -> list[dict]:
    """Arabic, Saudi-market query set. Returns [{category, query, intent_ar}]."""
    q: list[dict] = []
    names = [store_name] + list(brand_aliases or [])

    def add(cat, text):
        if text and text not in [x["query"] for x in q]:
            q.append({"category": cat, "category_ar": CATEGORIES[cat], "query": text})

    for n in names:
        add("brand", f"متجر {n}")
        add("brand", f"وش هو متجر {n}؟")
    add("trust", f"هل متجر {store_name} موثوق؟")
    add("trust", f"سياسة الاسترجاع في متجر {store_name}")
    add("trust", f"كم يأخذ التوصيل من متجر {store_name}؟")
    for p in products or []:
        add("product", f"{p}")
        add("product", f"وين ألقى {p} في السعودية؟")
        add("product", f"سعر {p} في السعودية")
    for c in categories:
        add("category_discovery", f"أفضل متجر {c} في السعودية")
        add("category_discovery", f"أبغى متجر أونلاين موثوق أشتري منه {c}")
        add("informational", f"كيف أختار {c} المناسب؟")
        add("informational", f"وش الفرق بين أنواع {c}؟")
        for city in cities or []:
            add("local", f"متجر {c} في {city} مع توصيل سريع")
    for comp in competitors or []:
        add("comparison", f"{store_name} ولا {comp}؟ أيهم أفضل")
    for c in categories[:2]:
        add("comparison", f"مقارنة بين أشهر متاجر {c} في السعودية")
    return q


OBS_FIELDS = ["platform", "date", "query", "category", "mentioned", "cited_url", "position",
              "accuracy_issues", "logged_in", "location", "evidence_ref"]


def validate_observation(o: dict) -> list[str]:
    errs = []
    for k in ("platform", "date", "query", "category", "mentioned"):
        if k not in o or o[k] in (None, ""):
            errs.append(f"الحقل {k} مطلوب")
    if o.get("category") and o["category"] not in CATEGORIES:
        errs.append(f"فئة غير معروفة: {o['category']}")
    if "mentioned" in o and not isinstance(o["mentioned"], bool):
        errs.append("mentioned يجب أن يكون true/false")
    try:
        date.fromisoformat(str(o.get("date")))
    except ValueError:
        errs.append("التاريخ بصيغة YYYY-MM-DD")
    return errs


def summarize(observations: list[dict]) -> dict:
    valid = [o for o in observations if not validate_observation(o)]
    by: dict = defaultdict(lambda: {"n": 0, "mentioned": 0, "cited": 0, "accuracy_issues": 0})
    platforms = set()
    for o in valid:
        platforms.add(o["platform"])
        for key in (("all", o["category"]), (o["platform"], o["category"])):
            b = by[key]
            b["n"] += 1
            b["mentioned"] += int(o["mentioned"])
            b["cited"] += int(bool(o.get("cited_url")))
            b["accuracy_issues"] += int(bool(o.get("accuracy_issues")))
    rows = []
    for (plat, cat), b in sorted(by.items()):
        rows.append({"platform": plat, "category": cat, "category_ar": CATEGORIES[cat], **b,
                     "mention_rate": round(b["mentioned"] / b["n"], 2) if b["n"] else None,
                     "small_sample": b["n"] < 5})
    dates = sorted(o["date"] for o in valid)
    return {
        "observations": len(valid), "rejected": len(observations) - len(valid),
        "platforms": sorted(platforms), "period": [dates[0], dates[-1]] if dates else None,
        "rows": rows,
        "caveats_ar": [
            "إجابات الذكاء الاصطناعي تتغير بين المحاولات والأيام والحسابات والمواقع؛ هذه عينة وليست ترتيباً ثابتاً.",
            "نسبة الذكر محسوبة على الأسئلة المختبرة فقط.",
            "العينات أقل من 5 أسئلة في الفئة لا تكفي لاستنتاج.",
            "الذكر لا يعني زيارات أو مبيعات.",
        ],
    }
