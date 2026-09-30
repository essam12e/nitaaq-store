"""GEO readiness: can AI-assisted search find, understand and cite the store?

Grounded in published platform guidance (see references/seo-geo.md):
Google states there are no extra requirements, special files or special
schema for AI Overviews / AI Mode, and normal SEO best practice applies.
So this assessment checks accessibility, clarity, factual coverage and
consistency, not "AI tricks". llms.txt is reported as optional information,
never as a requirement.
"""

from __future__ import annotations

import re
from collections import Counter

from .arabic import normalize

POLICY_PATTERNS = {
    "shipping": [r"shipping", r"delivery", r"الشحن", r"التوصيل"],
    "returns": [r"return", r"refund", r"exchange", r"الاسترجاع", r"الاستبدال", r"الارجاع", r"الإرجاع"],
    "contact": [r"contact", r"تواصل", r"اتصل"],
    "about": [r"about", r"من نحن", r"عن المتجر", r"قصتنا"],
    "privacy": [r"privacy", r"الخصوصية"],
    "faq": [r"faq", r"الأسئلة", r"الاسئلة الشائعة", r"اسئلة"],
}

SEARCH_CRAWLERS = ["Googlebot", "Bingbot", "OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]


def _pages_matching(pages: list, pats: list) -> list:
    out = []
    for p in pages:
        hay = f"{p.get('url', '')} {p.get('title', '')} {' '.join(p.get('h1') or [])}".lower()
        if any(re.search(x, hay, re.I) for x in pats):
            out.append(p["url"])
    return out


def assess(crawl_result: dict, store_name: str | None = None, llms_txt_status: int | None = None) -> dict:
    pages = crawl_result.get("pages", [])
    robots = crawl_result.get("robots", {})
    dims = []

    # 1. Accessibility
    crawlers = robots.get("crawlers", {})
    blocked = [b for b in SEARCH_CRAWLERS if b in crawlers and not crawlers[b]["allowed"]]
    if not robots.get("available"):
        dims.append(_dim("access", "إمكانية الوصول", 1, "تعذر قراءة robots.txt؛ لم نتحقق من سماح الزواحف", []))
    elif "Googlebot" in blocked or "Bingbot" in blocked:
        dims.append(_dim("access", "إمكانية الوصول", 0, "زواحف البحث الأساسية ممنوعة: " + "، ".join(blocked), blocked))
    elif blocked:
        dims.append(_dim("access", "إمكانية الوصول", 1, "بعض زواحف بحث الذكاء الاصطناعي ممنوعة (قرار يعود للتاجر): " + "، ".join(blocked), blocked))
    else:
        dims.append(_dim("access", "إمكانية الوصول", 2, "زواحف البحث المعروفة مسموحة للصفحة الرئيسية", []))

    # 2. Store identity
    org = [p["url"] for p in pages if {"Organization", "OnlineStore", "Store", "LocalBusiness"} & set(p.get("jsonld_types", []))]
    about = _pages_matching(pages, POLICY_PATTERNS["about"])
    score = 2 if (org and about) else 1 if (org or about) else 0
    dims.append(_dim("identity", "وضوح هوية المتجر", score,
                     "بيانات Organization: " + ("موجودة" if org else "غير موجودة") + "؛ صفحة «من نحن»: " + ("موجودة" if about else "لم تُرصد"),
                     org[:2] + about[:2]))

    # 3. Policies
    found = {k: _pages_matching(pages, v) for k, v in POLICY_PATTERNS.items() if k in ("shipping", "returns", "contact")}
    have = [k for k, v in found.items() if v]
    names = {"shipping": "الشحن", "returns": "الاسترجاع", "contact": "التواصل"}
    dims.append(_dim("policies", "سياسات الشحن والاسترجاع والتواصل", 2 if len(have) == 3 else 1 if have else 0,
                     "رُصد: " + ("، ".join(names[k] for k in have) or "لا شيء") + "؛ الناقص: " + ("، ".join(names[k] for k in found if k not in have) or "لا شيء"),
                     [u for v in found.values() for u in v[:1]]))

    # 4. Product facts
    prods = [p for p in pages if p.get("type") == "product"]
    if not prods:
        dims.append(_dim("product_facts", "دقة معلومات المنتجات", None, "لم تُفحص صفحات منتجات؛ وسّع الفحص", []))
    else:
        thin = [p["url"] for p in prods if p.get("word_count", 0) < 120]
        schema = [p["url"] for p in prods if "Product" in p.get("jsonld_types", [])]
        ok = len(prods) - len(thin)
        score = 2 if (not thin and len(schema) == len(prods)) else 1 if ok or schema else 0
        dims.append(_dim("product_facts", "دقة معلومات المنتجات", score,
                         f"{len(prods)} صفحة منتج: {len(thin)} بمحتوى قليل، {len(schema)} فيها Product schema", thin[:3]))

    # 5. Naming consistency
    if store_name and pages:
        sn = normalize(store_name)
        with_name = sum(1 for p in pages if sn and sn in normalize(p.get("title")))
        score = 2 if with_name >= 0.8 * len(pages) else 1 if with_name else 0
        dims.append(_dim("consistency", "اتساق اسم المتجر", score, f"اسم المتجر يظهر في عنوان {with_name} من {len(pages)} صفحة", []))
    else:
        suffixes = Counter(re.split(r"[|\-–—]", p.get("title") or "")[-1].strip() for p in pages if p.get("title"))
        dims.append(_dim("consistency", "اتساق اسم المتجر", None,
                         "حدّد اسم المتجر لقياس الاتساق" + (f"؛ أكثر لاحقة في العناوين: «{suffixes.most_common(1)[0][0]}»" if suffixes else ""), []))

    # 6. Answerable questions (FAQ / helpful content)
    faq = _pages_matching(pages, POLICY_PATTERNS["faq"])
    faq_schema = [p["url"] for p in pages if "FAQPage" in p.get("jsonld_types", [])]
    dims.append(_dim("answers", "إجابات واضحة لأسئلة العملاء", 2 if faq else 1 if faq_schema else 0,
                     "صفحة أسئلة شائعة: " + ("موجودة" if faq else "لم تُرصد"), faq[:2]))

    scored = [d for d in dims if d["score"] is not None]
    info = []
    if llms_txt_status is not None:
        info.append("llms.txt " + ("موجود" if llms_txt_status == 200 else "غير موجود") +
                    " — ملف اختياري؛ Google تنص أنه لا حاجة لملفات خاصة للظهور في ميزات AI، ووجوده لا يضمن الظهور.")
    return {
        "dimensions": dims,
        "readiness_points": sum(d["score"] for d in scored), "max_points": 2 * len(scored),
        "unassessed": [d["id"] for d in dims if d["score"] is None],
        "info": info,
        "disclaimer_ar": "جاهزية GEO تقيس وضوح المحتوى وإمكانية الوصول إليه، ولا تضمن الظهور أو الاستشهاد في أي منصة ذكاء اصطناعي.",
    }


def _dim(id_, label, score, evidence, urls):
    return {"id": id_, "label_ar": label, "score": score, "evidence_ar": evidence, "urls": urls}
