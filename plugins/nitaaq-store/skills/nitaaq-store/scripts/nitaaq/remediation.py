"""Turn audit findings into a remediation plan, and compare re-checks.

Each finding id maps to: what to do, who can do it (store tools, dashboard,
theme settings, installed app, Salla support, or the merchant's decision),
and which catalog operation would apply it when a tool exists. Whether it can
be executed now depends on the capability map, never on assumption.
"""

from __future__ import annotations

from collections import defaultdict

OWNER_AR = {
    "tools": "عبر أدوات المتجر المتصلة",
    "dashboard": "من لوحة تحكم سلة",
    "theme": "من إعدادات القالب",
    "app": "من التطبيق المثبت المسؤول",
    "support": "يحتاج دعم سلة (إعداد على مستوى المنصة)",
    "merchant": "قرار يعود لك",
}

# finding id -> (action_ar, owner, catalog operation or None, field or None, impact 1-5, effort 1-5)
RULES = {
    "http_error": ("أصلح الرابط أو أعد توجيهه لأقرب صفحة بديلة", "dashboard", None, None, 5, 2),
    "noindex": ("أزل منع الفهرسة عن الصفحة إن كانت مهمة", "support", None, None, 5, 3),
    "fetch_failed": ("أعد الفحص من بيئة أخرى (قد يكون قيد شبكة)", "merchant", None, None, 1, 1),
    "robots_unreachable": ("أعد الفحص من بيئة أخرى", "merchant", None, None, 1, 1),
    "redirect_chain": ("اجعل التحويل مباشراً وحدّث الروابط الداخلية", "dashboard", "menus.update", None, 3, 2),
    "parameterized_url": ("قلّل روابط الفلاتر والتتبع في الروابط الداخلية وتأكد من canonical", "theme", None, None, 3, 3),
    "canonical_missing": ("تأكد من توليد canonical (إعداد منصة/قالب)", "support", None, None, 4, 3),
    "canonical_parameterized": ("اجعل canonical يشير للرابط النظيف", "support", None, None, 4, 3),
    "canonical_cross_host": ("تحقق أن canonical يشير للنطاق الصحيح", "support", None, None, 4, 3),
    "title_missing": ("اكتب عنوان SEO", "tools", "products.update", "metadata_title", 4, 1),
    "title_long": ("اختصر عنوان SEO", "tools", "products.update", "metadata_title", 2, 1),
    "title_short": ("اكتب عنوان SEO وصفياً", "tools", "products.update", "metadata_title", 3, 1),
    "title_duplicate": ("اجعل لكل صفحة عنوان SEO فريداً", "tools", "products.update", "metadata_title", 3, 2),
    "meta_description_missing": ("اكتب وصف SEO", "tools", "products.update", "metadata_description", 3, 1),
    "meta_description_long": ("اختصر وصف SEO", "tools", "products.update", "metadata_description", 1, 1),
    "description_duplicate": ("اجعل أوصاف SEO فريدة", "tools", "products.update", "metadata_description", 2, 2),
    "h1_missing": ("اجعل الاسم عنوان H1 (إعداد القالب)", "theme", "theme.settings.update", None, 3, 3),
    "h1_multiple": ("قلّل عناوين H1 لواحد (إعداد القالب)", "theme", "theme.settings.update", None, 1, 3),
    "img_alt_missing": ("أضف نصاً بديلاً يصف الصور", "tools", "products.update", "images.alt", 2, 2),
    "lang_not_ar": ("راجع إعداد لغة المتجر", "dashboard", None, None, 2, 1),
    "jsonld_invalid": ("أصلح البيانات المنظمة المعطوبة (قالب أو تطبيق)", "app", None, None, 4, 3),
    "product_schema_missing": ("فعّل بيانات Product المنظمة من القالب/الدعم", "support", None, None, 3, 3),
    "product_schema_name": ("تحقق من اسم المنتج في البيانات المنظمة", "support", None, None, 2, 3),
    "product_schema_image": ("أضف صورة للمنتج", "tools", "products.images.attach", None, 2, 2),
    "offer_missing": ("تحقق من السعر والتوفر في البيانات المنظمة", "support", None, None, 3, 3),
    "offer_price_missing": ("تحقق من السعر في البيانات المنظمة", "support", None, None, 3, 3),
    "price_mismatch": ("طابق السعر الظاهر مع البيانات المنظمة (تحقق يدوياً أولاً)", "support", None, None, 5, 3),
    "offer_currency": ("تحقق من العملة", "support", None, None, 2, 2),
    "offer_availability": ("تحقق من حالة التوفر", "tools", "products.set_status", "status", 2, 2),
    "empty_listing": ("أضف منتجات للتصنيف أو أخفه", "tools", "products.update", "categories", 4, 2),
    "weak_listing": ("أضف وصفاً مفيداً للتصنيف وروابط لتصنيفات قريبة", "dashboard", None, None, 3, 2),
    "sitemap_missing": ("تحقق من خريطة الموقع مع دعم سلة", "support", None, None, 3, 2),
}

SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1, "info": 0}


def plan(findings: list[dict], cmap: dict | None = None) -> dict:
    groups: dict = defaultdict(lambda: {"urls": [], "severity": "info", "count": 0})
    for f in findings:
        g = groups[f["id"]]
        g["count"] += 1
        if f.get("url"):
            g["urls"].append(f["url"])
        if SEV_WEIGHT.get(f["severity"], 0) > SEV_WEIGHT.get(g["severity"], 0):
            g["severity"] = f["severity"]
        g["message_ar"] = f["message_ar"]
    items = []
    for fid, g in groups.items():
        action, owner, op, field, impact, effort = RULES.get(
            fid, ("راجع الملاحظة يدوياً", "merchant", None, None, 2, 2))
        executable = False
        if op and cmap:
            e = cmap.get("operations", {}).get(op)
            executable = bool(e and e["status"] == "mapped")
        if owner == "tools" and not executable:
            how = "جهّز التعديل كمقترح ونفّذه من لوحة سلة (لا توجد أداة متصلة لهذه العملية)"
        elif owner == "tools":
            how = OWNER_AR["tools"] + " (بعد عرض الفروقات والاعتماد)"
        else:
            how = OWNER_AR[owner]
        score = (impact * (SEV_WEIGHT.get(g["severity"], 0) + 1)) / effort
        items.append({"finding": fid, "message_ar": g["message_ar"], "severity": g["severity"], "count": g["count"],
                      "urls": sorted(set(g["urls"]))[:10], "action_ar": action, "owner": owner, "how_ar": how,
                      "operation": op, "field": field, "executable_now": executable, "priority_score": round(score, 2)})
    items.sort(key=lambda x: (-x["priority_score"], x["finding"]))
    return {"items": items,
            "note_ar": "الخطة مقترحة. لا يُعتبر أي بند «تم إصلاحه» قبل إعادة الفحص أو القراءة بعد الحفظ."}


def plan_markdown_ar(p: dict, limit: int = 30) -> str:
    rows = ["| # | المشكلة | العدد | الإجراء | من ينفّذه |", "|---|---|---|---|---|"]
    for i, it in enumerate(p["items"][:limit], 1):
        rows.append(f"| {i} | {it['message_ar']} | {it['count']} | {it['action_ar']} | {it['how_ar']} |")
    return "\n".join(rows) + "\n\n" + p["note_ar"]


def _keys(findings):
    return {(f["id"], f.get("url", "")) for f in findings}


def recheck(before: list[dict], after: list[dict], after_reachable: bool = True) -> dict:
    """Compare two audits: which findings are gone, persisting or new."""
    b, a = _keys(before), _keys(after)
    if not after_reachable:
        return {"status": "inconclusive", "note_ar": "إعادة الفحص لم تصل للموقع؛ لا يمكن الحكم بالإصلاح."}
    fixed = sorted(b - a)
    return {"status": "compared", "resolved": [{"id": i, "url": u} for i, u in fixed],
            "persisting": [{"id": i, "url": u} for i, u in sorted(b & a)],
            "new": [{"id": i, "url": u} for i, u in sorted(a - b)],
            "note_ar": "«اختفت» تعني أن الفحص العام الجديد لم يرصدها؛ قد يتأخر تحديث محركات البحث ومنصات الذكاء الاصطناعي."}
