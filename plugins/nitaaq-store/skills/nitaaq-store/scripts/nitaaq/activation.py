"""Decide whether a message addresses Nitaaq Store.

Hosts decide activation themselves (from the skill description, a slash
command or an explicit mention). This detector is the documented, tested
version of the same rules, used by the agent to resolve ambiguous cases
and by tests to keep the description's trigger list honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .arabic import normalize

# Explicit phrases: activate regardless of context.
EXPLICIT = [
    "نطاق للمتاجر",
    "نطاق المتاجر",
    "نطاق للمتجر",
    "مهاره نطاق",
    "مهارة نطاق",
    "مهاره استور",
    "مهارة استور",
    "nitaaq store",
    "nitaq store",
    "nitaaq-store",
    "nitaaq_store",
    "/nitaaq-store",
    "$nitaaq-store",
]

# "استور" and its spelling variants: a trigger only when addressed to the
# skill or used in a Salla/store-operations context.
STORE_WORD = re.compile(r"(?<!\w)(استور|ستور|اِستور|إستور|استوور)(?!\w)")

COMMERCE_CONTEXT = [
    "سله", "متجري", "متجر", "منتج", "منتجات", "طلب", "طلبات", "مخزون",
    "كميه", "عملاء", "بنر", "ثيم", "تقرير", "مبيعات", "سيو", "seo", "geo",
    "salla", "خصم", "كوبون", "تصنيف", "فاتوره", "سلات",
]

# Phrases where "استور"/"store" clearly means something else.
NEGATIVE_CONTEXT = [
    "اب ستور", "ابل ستور", "app store", "play store", "بلاي ستور",
    "جوجل بلاي", "ستور روم", "storage", "restore", "store procedure",
    "stored procedure",
]

VOCATIVE = re.compile(r"^\s*(يا\s+)?(استور|ستور|إستور)\s*[،,:!؟?]?")


@dataclass
class ActivationResult:
    activate: bool
    confidence: str  # "explicit" | "contextual" | "ambiguous" | "none"
    reason: str
    matched: list[str] = field(default_factory=list)


def detect(message: str, context_active: bool = False) -> ActivationResult:
    """Classify a message.

    context_active: True when earlier turns already use Nitaaq Store, so
    a bare follow-up ("استور، كم طلب اليوم؟") continues the same skill.
    """
    raw = message or ""
    norm = normalize(raw)
    low = raw.lower()

    for phrase in EXPLICIT:
        p = normalize(phrase) if not phrase.startswith(("/", "$")) else phrase
        hay = low if phrase.startswith(("/", "$")) else norm
        if p and p in hay:
            return ActivationResult(True, "explicit", "عبارة تفعيل صريحة", [phrase])

    for neg in NEGATIVE_CONTEXT:
        if normalize(neg) in norm:
            return ActivationResult(False, "none", "كلمة store بمعنى آخر", [neg])

    m = STORE_WORD.search(raw) or STORE_WORD.search(norm)
    if not m:
        # English "store" alone never activates.
        return ActivationResult(False, "none", "لا توجد إشارة للمهارة")

    word = m.group(1)
    if VOCATIVE.match(raw) or VOCATIVE.match(norm):
        return ActivationResult(True, "contextual", "مخاطبة المهارة باسمها", [word])
    ctx = [c for c in COMMERCE_CONTEXT if re.search(rf"(?<!\w)(?:و|ف|ب|ل|ال|بال|لل|وال)?{re.escape(c)}", norm)]
    if ctx:
        return ActivationResult(True, "contextual", "سياق تشغيل متجر", [word, *ctx])
    if context_active:
        return ActivationResult(True, "contextual", "استمرار محادثة المهارة", [word])
    return ActivationResult(False, "ambiguous", "كلمة استور بدون سياق متجر؛ اسأل أو تجاهل", [word])
