"""Analyze Google Search Console exports the merchant downloads.

Supported (English or Arabic UI column names):
  - Performance exports: Queries.csv / Pages.csv
    (query|page, clicks, impressions, CTR, position)
  - Page indexing exports: reasons table (reason, source, validation, pages)

The data covers only the date range and filters the merchant chose when
exporting, and Search Console anonymizes some queries. Both limits are
reported. This is the source for indexing/performance facts; a public crawl
is not.
"""

from __future__ import annotations

from .arabic import normalize, parse_amount
from .exports import read_csv

COLS = {
    "query": ["top queries", "query", "queries", "اهم طلبات البحث", "طلبات البحث", "طلب البحث", "الاستعلام"],
    "page": ["top pages", "page", "pages", "اهم الصفحات", "الصفحات", "الصفحه", "عنوان url"],
    "clicks": ["clicks", "النقرات", "عدد النقرات"],
    "impressions": ["impressions", "مرات الظهور", "عدد مرات الظهور"],
    "ctr": ["ctr", "نسبه النقر الى الظهور", "نسبه النقر الي الظهور", "نسبه النقر"],
    "position": ["position", "الموضع", "متوسط الموضع", "الترتيب"],
    "reason": ["reason", "السبب"],
    "source": ["source", "المصدر"],
    "validation": ["validation", "التحقق", "حاله التحقق"],
    "pages_count": ["pages", "الصفحات", "عدد الصفحات"],
}
_IDX = {normalize(v): k for k, vs in COLS.items() for v in vs}


def _map(headers):
    m, used = {}, set()
    for h in headers:
        k = _IDX.get(normalize(h))
        if k and k not in used:
            m[h] = k
            used.add(k)
    return m


def _num(v):
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace("٪", "")
    return parse_amount(s)


def analyze_performance(path, brand_terms: list[str] | None = None, min_impressions: int = 50) -> dict:
    headers, rows = read_csv(path)
    m = _map(headers)
    dim = "query" if "query" in m.values() else "page" if "page" in m.values() else None
    if dim is None or "impressions" not in m.values():
        return {"kind": "unknown", "error_ar": "الملف ليس تصدير أداء من Search Console (لا يوجد عمود استعلام/صفحة ومرات ظهور)",
                "headers": headers}
    inv = {v: k for k, v in m.items()}
    data = []
    for r in rows:
        item = {"key": r.get(inv[dim], "").strip()}
        for f in ("clicks", "impressions", "ctr", "position"):
            item[f] = _num(r.get(inv[f])) if f in inv else None
        data.append(item)
    tot_c = sum((d["clicks"] or 0) for d in data)
    tot_i = sum((d["impressions"] or 0) for d in data)
    brands = [normalize(b) for b in brand_terms or [] if b]
    out = {
        "kind": f"performance_{dim}", "rows": len(data), "clicks": tot_c, "impressions": tot_i,
        "ctr_percent": round(float(tot_c) / float(tot_i) * 100, 2) if tot_i else None,
        "limitations_ar": [
            "الأرقام تخص الفترة والفلاتر التي اخترتها عند التصدير فقط.",
            "Search Console يخفي بعض طلبات البحث النادرة لحماية الخصوصية؛ مجموع الجدول قد يقل عن الإجمالي.",
            "التصدير يحتوي أعلى الصفوف فقط (حد التصدير من الواجهة).",
        ],
    }
    # Low CTR relative to impressions: title/description candidates.
    ctr_ok = [d for d in data if d["impressions"] and d["impressions"] >= min_impressions and d["ctr"] is not None]
    if ctr_ok:
        ctrs = sorted(float(d["ctr"]) for d in ctr_ok)
        median = ctrs[len(ctrs) // 2]
        out["low_ctr"] = [d for d in sorted(ctr_ok, key=lambda d: -d["impressions"]) if float(d["ctr"]) < median / 2][:20]
        out["median_ctr_percent"] = median
    # Striking distance: average position 4-15 with meaningful impressions.
    out["striking_distance"] = [d for d in sorted(data, key=lambda d: -(d["impressions"] or 0))
                                if d["position"] is not None and 4 <= d["position"] <= 15
                                and (d["impressions"] or 0) >= min_impressions][:20]
    out["zero_click"] = [d for d in data if (d["clicks"] == 0) and (d["impressions"] or 0) >= min_impressions][:20]
    if dim == "query" and brands:
        b = [d for d in data if any(t in normalize(d["key"]) for t in brands)]
        nb = [d for d in data if d not in b]
        out["branded"] = {"queries": len(b), "clicks": sum((d["clicks"] or 0) for d in b)}
        out["non_branded"] = {"queries": len(nb), "clicks": sum((d["clicks"] or 0) for d in nb)}
    return out


def analyze_indexing(path) -> dict:
    headers, rows = read_csv(path)
    m = _map(headers)
    inv = {v: k for k, v in m.items()}
    if "reason" not in inv:
        return {"kind": "unknown", "error_ar": "الملف ليس جدول أسباب عدم الفهرسة من Search Console", "headers": headers}
    reasons = []
    for r in rows:
        reasons.append({"reason": r.get(inv["reason"], "").strip(),
                        "source": r.get(inv["source"], "").strip() if "source" in inv else None,
                        "validation": r.get(inv["validation"], "").strip() if "validation" in inv else None,
                        "pages": _num(r.get(inv["pages_count"])) if "pages_count" in inv else None})
    reasons.sort(key=lambda x: -(x["pages"] or 0))
    for x in reasons:
        x["advice_ar"] = _advice(x["reason"])
    return {"kind": "indexing_reasons", "reasons": reasons,
            "total_not_indexed": sum((x["pages"] or 0) for x in reasons),
            "note_ar": "بعض الأسباب طبيعية (مثل الصفحات المكررة ذات canonical صحيح أو المحوّلة). ركّز على الصفحات المهمة فقط."}


_ADVICE = [
    (("soft 404",), "صفحات تبدو فارغة: صفحات تصنيف أو منتجات بلا محتوى؛ أضف محتوى أو أخفها."),
    (("noindex",), "صفحات عليها noindex: تأكد أنها ليست صفحات منتجات أو تصنيفات مهمة."),
    (("404", "not found", "غير موجود"), "روابط محذوفة: حوّلها لأقرب صفحة بديلة إن كان لها زيارات أو روابط."),
    (("redirect", "إعادة توجيه", "اعاده توجيه"), "صفحات محوّلة: طبيعي غالباً؛ حدّث الروابط الداخلية للوجهة النهائية."),
    (("duplicate", "canonical", "مكرر", "أساسية", "اساسيه"), "نسخ مكررة: تحقق أن canonical يشير للنسخة الصحيحة، وقلّل روابط الفلاتر."),
    (("crawled", "discovered", "الزحف", "اكتشاف"), "زُحفت أو اكتُشفت ولم تُفهرس: حسّن المحتوى الفريد والروابط الداخلية لهذه الصفحات."),
    (("robots",), "ممنوعة في robots.txt: تأكد أن هذا مقصود."),
    (("5xx", "server error", "خطأ في الخادم"), "أخطاء خادم: راجع مع دعم سلة إن استمرت."),
]


def _advice(reason: str) -> str:
    r = (reason or "").lower()
    for keys, text in _ADVICE:
        if any(k in r for k in keys):
            return text
    return "راجع الأمثلة في Search Console لهذا السبب."
