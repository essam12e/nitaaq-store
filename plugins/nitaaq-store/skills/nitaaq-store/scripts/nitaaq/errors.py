"""Classify tool failures and decide what is safe to do next."""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = {
    "auth_expired": "انتهت صلاحية ربط المتجر. أعد تسجيل الدخول/الربط من إعدادات الاتصال ثم أعد الطلب.",
    "permission_denied": "الصلاحيات الممنوحة لا تسمح بهذه العملية. لا يمكن تجاوزها؛ يمكن منح الصلاحية من إعدادات الربط أو تنفيذها من لوحة تحكم سلة.",
    "not_found": "العنصر غير موجود أو لا يتبع هذا المتجر. تحقق من الاسم أو المعرّف.",
    "validation": "رُفضت القيم المرسلة. راجع الحقول المطلوبة والقيم المسموحة في مخطط الأداة.",
    "rate_limited": "تم تجاوز حد الطلبات مؤقتاً. سننتظر ثم نكمل من حيث توقفنا.",
    "conflict": "تغيّرت البيانات منذ آخر قراءة. أعد القراءة قبل أي تعديل.",
    "unavailable_feature": "هذه الميزة غير مفعّلة في المتجر أو غير مدعومة عبر هذا الاتصال.",
    "server_error": "خطأ مؤقت من الخادم.",
    "network_ambiguous": "انقطع الاتصال ولا نعرف إن كانت العملية نُفّذت. سنتحقق بالقراءة قبل أي إعادة.",
    "partial": "وصلت نتيجة جزئية فقط.",
    "unknown": "خطأ غير مصنّف.",
}

_PATTERNS = [
    ("auth_expired", r"\b401\b|unauthori[sz]ed|token (has )?expired|invalid[_ ]token|expired token|re-?authenticat|login required"),
    ("permission_denied", r"\b403\b|forbidden|permission|not allowed|insufficient scope|scope|access denied"),
    ("rate_limited", r"\b429\b|rate.?limit|too many requests|throttl"),
    ("not_found", r"\b404\b|not found|does not exist|no such"),
    ("conflict", r"\b409\b|conflict|version mismatch|precondition|\b412\b"),
    ("validation", r"\b422\b|\b400\b|validation|invalid|required field|must be|is required"),
    ("unavailable_feature", r"not enabled|feature (is )?disabled|not supported|unsupported|upgrade your plan"),
    ("network_ambiguous", r"timed? ?out|timeout|connection reset|econnreset|socket hang up|broken pipe|network error|gateway time"),
    ("server_error", r"\b5\d\d\b|internal server error|bad gateway|service unavailable"),
]


@dataclass
class Classified:
    kind: str
    message_ar: str
    retry: str  # "no" | "after_wait" | "safe" | "reconcile_first"


def classify(error_text: str | None, status: int | None = None, write: bool = False,
             idempotent: bool | None = None) -> Classified:
    text = f"{status or ''} {error_text or ''}".lower()
    kind = "unknown"
    for k, pat in _PATTERNS:
        if re.search(pat, text):
            kind = k
            break
    if kind == "server_error" and write:
        # A 5xx on a write may still have been applied.
        kind = "network_ambiguous"
    if kind == "rate_limited":
        retry = "after_wait"
    elif kind in ("network_ambiguous",):
        retry = "safe" if (not write or idempotent) else "reconcile_first"
    elif kind == "server_error":
        retry = "safe"
    else:
        retry = "no"
    return Classified(kind, KINDS[kind], retry)
