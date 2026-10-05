"""Capability-aware routing: which specialists a request needs, and which it may not use.

The model still reads the merchant's message; this router is the deterministic,
tested part of the decision:

  1. classify the request (intent) and its kind: read / analysis / recommendation / execution
  2. pick specialists for the intent from the registry
  3. keep only specialists that are active AND whose required capabilities exist now
  4. report everything else as blocked or not available yet, with the reason
  5. after a first stage, followups() turns evidence signals into conditional stages

Simple reads get no specialists at all.
"""

from __future__ import annotations

from .arabic import normalize
from . import registry

# Ordered: the first intent whose phrase matches wins. Phrases are normalized
# (alef/ya/ta-marbuta unified) before matching, so spelling variants match.
INTENT_PHRASES: list[tuple[str, list[str]]] = [
    ("ads", ["حمله", "حملات", "اعلان", "اعلانات", "اعلاني", "google ads", "قوقل ادز", "جوجل ادز", "ميتا", "سناب", "تيك توك",
             "تيكتوك", "كلمات سلبيه", "مصطلحات البحث", "ميزانيه الاعلان", "roas"]),
    ("tracking", ["تتبع", "بكسل", "pixel", "ga4", "تحليلات جوجل", "قوقل اناليتكس", "google analytics", "التحويلات مو مسجله",
                  "capi", "تاق مانجر", "gtm"]),
    ("seo", ["seo", "سيو", "السيو", "geo", "ما يطلع في قوقل", "ما يطلع في جوجل", "محركات البحث", "ظهور المتجر", "الظهور في جوجل", "الظهور في قوقل", "نتايج البحث",
             "عناوين seo", "ميتا تايتل", "search console", "سيرش كونسول"]),
    ("sales_change", ["المبيعات نازله", "المبيعات نزلت", "المبيعات قلت", "المبيعات قليله", "المبيعات طاحت", "المبيعات تراجعت",
                      "انخفاض المبيعات", "نزول المبيعات", "تراجع المبيعات", "ليش المبيعات", "ليه المبيعات", "ليش الطلبات قلت",
                      "الطلبات قلت", "الطلبات نازله", "الطلبات نزلت", "مبيعاتي نازله", "مبيعاتي نزلت", "ليش ما فيه طلبات",
                      "المبيعات زادت", "ليش زادت المبيعات"]),
    ("broad_improvement", ["حلل المتجر", "حلل متجري", "حسن المبيعات", "حسن مبيعات", "زود المبيعات", "ارفع المبيعات",
                           "طور المتجر", "طور متجري", "كيف ازيد مبيعاتي", "كيف ازود المبيعات", "خطه نمو", "افحص المتجر كامل"]),
    ("reviews", ["تقييمات", "التقييمات", "مراجعات العملاء", "شكاوي", "شكاوى", "شكوى", "اراء العملاء", "تعليقات العملاء"]),
    ("retention", ["رسايل للعملاء", "رساله للعملاء", "واتساب للعملاء", "ايميل للعملاء", "حمله بريد", "العملاء القدام",
                   "استرجاع العملاء", "برنامج ولاء", "العملاء المميزين", "win back"]),
    ("cro", ["صفحه المنتج ما تبيع", "معدل التحويل", "السلات المتروكه", "السلات المهجوره", "صفحه الدفع", "زر الشراء", "conversion"]),
    ("pricing", ["التسعير", "سعر مناسب", "اسعاري", "اسعار المنافسين", "هامش الربح", "الخصومات تضرني", "هل الخصم", "كم اسعر"]),
    ("strategy", ["استراتيجيه", "توسع", "منتج جديد يناسب", "سوق جديد", "خطه سنويه"]),
    ("sales_report", ["تقرير المبيعات", "مبيعات الشهر", "مبيعات الاسبوع", "اكثر المنتجات مبيعا", "اكثر منتج مبيعا",
                      "المبيعات حسب", "متوسط قيمه الطلب", "مبيعات امس", "تقرير الطلبات"]),
]

# Avoid bare words with other meanings (غير = "other/not", حدث = "happened").
EXECUTION_VERBS = ["عدل", "غير السعر", "غير الاسم", "غير الحاله", "غير الوصف", "غيرها", "ضيف", "اضف", "احذف", "امسح",
                   "اوقف", "وقف", "فعل", "زود الكميه", "نقص الكميه", "حدث السعر", "حدثها", "انشر", "خفض السعر",
                   "نزل السعر", "ارفع السعر", "خل السعر", "سو خصم", "ارسل", "طبق"]
RECOMMEND_WORDS = ["حسن", "اقترح", "وش تنصح", "وش الافضل", "كيف ازيد", "كيف ازود", "خطه"]
SIMPLE_READ = ["كم طلب", "كم عدد", "وش الطلبات", "اعرض", "ورني", "وريني", "عطني قائمه", "كم منتج", "وش المنتجات",
               "حاله الطلب", "وين الطلب", "كم الكميه", "كم باقي"]

# Specialists to consider per intent (in dependency order). Planned ones are
# listed so the plan can say honestly that they are not available yet.
INTENT_AGENTS = {
    "sales_change": ["store_analytics"],
    "sales_report": ["store_analytics"],
    "broad_improvement": ["store_analytics", "pricing", "cro", "seo_geo", "tracking", "growth"],
    "seo": ["seo_geo"],
    "pricing": ["pricing"],
    "ads": ["paid_media_auditor", "ppc", "search_query_analyst", "ad_creative", "paid_social"],
    "tracking": ["tracking"],
    "reviews": ["customer_intelligence"],
    "retention": ["email_retention"],
    "cro": ["cro"],
    "strategy": ["store_analytics", "business_strategist"],
    "growth": ["growth"],
    "simple_read": [],
    "operation": [],
}

# Existing services that keep working exactly as before (not routed to a new specialist yet).
EXISTING_SERVICE = {"seo": "references/seo-geo.md", "tracking": "references/store-audit.md"}
EXISTING_AGENT_SERVICE = {"seo_geo": "references/seo-geo.md", "tracking": "references/store-audit.md"}

# Conditional routing after the first stage. signal -> (agent, Arabic reason)
SIGNAL_ROUTES = {
    "stock_out": ("salla_operator", "منتجات مؤثرة في الانخفاض نفد مخزونها أو قلّ؛ نتحقق من المخزون الحالي."),
    "price_changed": ("pricing", "تغيّر سعر أو انتهى عرض على منتجات مؤثرة."),
    "offer_ended": ("pricing", "انتهى عرض أو كوبون في فترة المقارنة."),
    "complaints_up": ("customer_intelligence", "زادت الشكاوى أو التقييمات السلبية على منتجات مؤثرة."),
    "traffic_available": ("growth", "تتوفر بيانات زيارات لتحليل القمع."),
    "funnel_drop": ("cro", "يوجد هبوط في خطوة من القمع."),
    "ads_available": ("paid_media_auditor", "تتوفر بيانات حملات إعلانية."),
    "tracking_discrepancy": ("tracking", "فرق بين طلبات سلة والتحويلات المسجلة."),
    "search_terms_available": ("search_query_analyst", "يتوفر تقرير مصطلحات البحث."),
}

# What we cannot know without each source; stated instead of guessed.
UNKNOWN_WITHOUT = {
    "external:analytics": "لا نعرف هل تغيّرت الزيارات أو معدل التحويل (لا توجد بيانات زيارات).",
    "external:ads": "لا نعرف هل تغيّر الإنفاق أو أداء الإعلانات (لا يوجد اتصال أو ملف إعلانات).",
    "reviews.list": "لا نعرف هل زادت الشكاوى (لا يوجد مصدر تقييمات).",
}


def _norm_list(xs):
    return [normalize(x) for x in xs]


_INTENTS_N = [(i, _norm_list(p)) for i, p in INTENT_PHRASES]
_EXEC_N, _REC_N, _SIMPLE_N = _norm_list(EXECUTION_VERBS), _norm_list(RECOMMEND_WORDS), _norm_list(SIMPLE_READ)


def _has(text: str, phrases: list[str]) -> bool:
    padded = f" {text} "
    return any((f" {p} " in padded) if len(p) <= 3 else (p in text) for p in phrases)


def _best_intent(t: str) -> str | None:
    """The intent with the longest matching phrase; earlier intents win ties.
    So «ميتا تايتل» is SEO, not ads, and «حمله بريد» is retention, not ads."""
    best, best_len = None, 0
    for intent, phrases in _INTENTS_N:
        for p in phrases:
            if _has(t, [p]) and len(p) > best_len:
                best, best_len = intent, len(p)
    return best


def classify(message: str) -> dict:
    t = normalize(message)
    intent = _best_intent(t)
    execution = _has(t, _EXEC_N)
    if intent is None:
        intent = "simple_read" if _has(t, _SIMPLE_N) or not execution else "operation"
    if execution and intent not in ("broad_improvement",):
        kind = "execution"
    elif intent in ("simple_read",):
        kind = "read"
    elif intent == "broad_improvement" or _has(t, _REC_N):
        kind = "recommendation"
    elif intent == "sales_report":
        kind = "read"
    else:
        kind = "analysis"
    return {"intent": intent, "kind": kind}


def available_from_map(cmap: dict | None, exports: list[str] | None = None, public: bool = False,
                       external: list[str] | None = None) -> set[str]:
    """Capability tokens: mapped operation ids + export:<kind> + public:pages + external:<name>."""
    out: set[str] = set()
    for op_id, op in ((cmap or {}).get("operations") or {}).items():
        if op.get("status") == "mapped":
            out.add(op_id)
    out |= {f"export:{e}" for e in exports or []}
    if public:
        out.add("public:pages")
    for e in external or []:
        out.add(f"external:{e}")
        if e in ("google_ads", "meta", "tiktok", "snapchat"):
            out.add("external:ads")
    return out


def _stage(agent_id: str, available: set[str], reg: dict, reason_ar: str = "") -> dict:
    a = registry.get(agent_id, reg)
    if not registry.is_active(a) and agent_id in EXISTING_AGENT_SERVICE:
        return {"agent": agent_id, "label_ar": a["label_ar"], "status": "existing_service",
                "reference": EXISTING_AGENT_SERVICE[agent_id],
                "reason_ar": "تعمل الخدمة الحالية كما هي في نفس الجلسة (ليست تخصصاً منفصلاً بعد)."}
    if not registry.is_active(a):
        return {"agent": agent_id, "label_ar": a["label_ar"], "status": "not_available_yet",
                "phase": a.get("phase"), "reason_ar": (reason_ar + " " if reason_ar else "") +
                f"هذا التخصص مخطط للمرحلة {a.get('phase')} ولم يُفعّل بعد."}
    ok, missing = registry.capability_ok(a, available)
    if not ok:
        return {"agent": agent_id, "label_ar": a["label_ar"], "status": "blocked", "missing": missing,
                "reason_ar": "البيانات اللازمة غير متاحة الآن: " + "، ".join(missing)}
    return {"agent": agent_id, "label_ar": a["label_ar"], "status": "run", "reason_ar": reason_ar}


def route(message: str, available: set[str], reg: dict | None = None) -> dict:
    reg = reg or registry.load()
    lim = registry.limits(reg)
    c = classify(message)
    stages = [_stage(a, available, reg) for a in INTENT_AGENTS.get(c["intent"], [])]
    runnable = [s for s in stages if s["status"] == "run"]
    cap = lim["max_specialists_broad"] if c["intent"] == "broad_improvement" else lim["max_specialists_simple"]
    cap = min(cap, lim["max_specialists_absolute"])
    for s in runnable[cap:]:
        s["status"], s["reason_ar"] = "deferred", "تجاوز حد التخصصات لهذا الطلب؛ يُقترح لاحقاً."
    runnable = runnable[:cap]
    # dependency order: store_analytics is the shared base when present; others can run in parallel after it
    base = "store_analytics" if any(s["agent"] == "store_analytics" for s in runnable) else None
    for s in runnable:
        s["depends_on"] = [base] if base and s["agent"] != base else []
        s["parallel_group"] = 0 if s["agent"] == base or not base else 1
    material = c["intent"] in ("sales_change", "broad_improvement", "strategy") or c["kind"] in ("recommendation",)
    reads = _operator_reads(c["intent"], available)
    unknown = [msg for tok, msg in UNKNOWN_WITHOUT.items()
               if c["intent"] in ("sales_change", "broad_improvement") and tok not in available]
    plan = {
        **c,
        "operator_reads": reads,
        "stages": stages,
        "specialists_to_run": [s["agent"] for s in runnable],
        "review_required": bool(runnable) and material,
        "existing_service": EXISTING_SERVICE.get(c["intent"]),
        "unknowns_ar": unknown,
        "execution_path": "salla_operator_with_approval" if c["kind"] == "execution" else None,
        "limits": {k: lim[k] for k in ("max_delegation_depth", "max_review_rounds", "max_stage_attempts", "stage_timeout_minutes")},
    }
    if c["intent"] in ("sales_change", "sales_report", "broad_improvement") and not any(
            t in available for t in ("orders.list", "reports.sales", "export:orders")):
        plan["blocker_ar"] = "لا يمكن تحليل المبيعات بدون طلبات: اربط أداة التاجر في سلة أو أرسل ملف تصدير الطلبات."
    return plan


def _operator_reads(intent: str, available: set[str]) -> list[str]:
    wanted = {
        "sales_change": ["orders.list", "orders.statuses", "reports.sales"],
        "sales_report": ["orders.list", "reports.sales", "reports.summary"],
        "broad_improvement": ["store.info", "orders.list", "products.list"],
        "simple_read": [],
    }.get(intent, [])
    return [w for w in wanted if w in available]


def followups(signals: list[str], available: set[str], already: list[str] | None = None,
              reg: dict | None = None) -> list[dict]:
    """Conditional stages from evidence signals found by the first stage. Never runs anything twice."""
    reg = reg or registry.load()
    done = set(already or [])
    out, seen = [], set()
    for sig in signals:
        if sig not in SIGNAL_ROUTES:
            continue
        agent, why = SIGNAL_ROUTES[sig]
        if agent in done or agent in seen:
            continue
        seen.add(agent)
        if agent == "salla_operator":
            ok = any(t in available for t in ("inventory.read", "products.get", "products.list"))
            out.append({"agent": agent, "label_ar": "مشغّل سلة", "signal": sig,
                        "status": "run" if ok else "blocked", "reason_ar": why if ok else "لا توجد أداة لقراءة المخزون."})
            continue
        st = _stage(agent, available, reg, why)
        st["signal"] = sig
        out.append(st)
    return out
