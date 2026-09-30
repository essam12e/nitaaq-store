"""Remove customer personal data before anything leaves the private context."""

from __future__ import annotations

import re

from .arabic import to_western_digits

_PHONE = re.compile(r"(?<!\d)(?:\+?966|00966|0)?\s?5\d(?:[\s\-]?\d){7}(?!\d)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_IBAN = re.compile(r"\bSA\d{2}[\s]?(?:[A-Z0-9]{4}[\s]?){4}[A-Z0-9]{2,4}\b", re.I)
_NATIONAL_ID = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")

PII_KEYS = {"name", "first_name", "last_name", "customer_name", "full_name", "mobile", "phone", "email",
            "address", "street", "street_number", "block", "postal_code", "national_id", "iban",
            "latitude", "longitude", "birthday", "avatar"}


def redact_text(text: str) -> str:
    t = to_western_digits(text or "")
    t = _EMAIL.sub("[بريد محجوب]", t)
    t = _IBAN.sub("[آيبان محجوب]", t)
    t = _CARD.sub(lambda m: "[رقم محجوب]" if len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0), t)
    t = _PHONE.sub("[جوال محجوب]", t)
    t = _NATIONAL_ID.sub("[هوية محجوبة]", t)
    return t


def redact_record(obj, keep_city: bool = True):
    """Recursively blank PII keys and scrub PII patterns in strings."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in PII_KEYS and not (keep_city and k.lower() == "city"):
                out[k] = "[محجوب]" if v not in (None, "") else v
            else:
                out[k] = redact_record(v, keep_city)
        return out
    if isinstance(obj, list):
        return [redact_record(v, keep_city) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def customer_alias(customer_id) -> str:
    """Stable, non-reversible label for public reports ("عميل #a1b2")."""
    import hashlib
    return "عميل #" + hashlib.sha256(str(customer_id).encode()).hexdigest()[:4]
