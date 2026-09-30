"""Arabic text normalization and number parsing."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def to_western_digits(text: str) -> str:
    """Convert Arabic-Indic and Persian digits to ASCII digits."""
    return text.translate(_DIGITS)


def normalize(text: str | None) -> str:
    """Normalize Arabic/English text for matching (not for display).

    Removes diacritics and tatweel, unifies alef/ya/ta-marbuta forms,
    lowercases Latin letters and collapses whitespace.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = _DIACRITICS.sub("", t).replace(_TATWEEL, "")
    t = re.sub("[إأآٱ]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = to_western_digits(t).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str | None) -> list[str]:
    """Normalized tokens, dropping the Arabic definite article prefix."""
    out = []
    for tok in normalize(text).split():
        if tok.startswith("ال") and len(tok) > 4:
            tok = tok[2:]
        out.append(tok)
    return out


def parse_amount(value) -> Decimal | None:
    """Parse a money/quantity value written in Arabic or Western digits.

    Accepts "١٥٠", "150.00", "1,250 ر.س", "SAR 99". Returns None when the
    value is empty or not a number (missing data is not zero).
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(str(value))
    s = to_western_digits(str(value)).strip()
    if not s:
        return None
    s = s.replace("٬", ",").replace("٫", ".")
    s = re.sub(r"(?i)(sar|ر\.?\s*س\.?|ريال|﷼)", "", s)
    s = s.replace(",", "").replace(" ", "")
    m = re.fullmatch(r"-?\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None
