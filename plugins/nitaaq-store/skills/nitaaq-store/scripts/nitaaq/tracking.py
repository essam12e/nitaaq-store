"""Tracking evidence with explicit levels.

Level 1  tracking code detected in the page source
Level 2  event request observed (from a HAR / network log the host captured)
Level 3  event received in the destination platform (merchant confirms/exports)
Level 4  event values reconciled with a real authorized transaction

This module can establish levels 1 and 2. Levels 3 and 4 are recorded only
from evidence the merchant or an authorized platform tool provides. It never
sends events itself.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

PLATFORMS = {
    "ga4": {
        "label": "Google Analytics 4",
        "code": [r"googletagmanager\.com/gtag/js\?id=G-[A-Z0-9]+", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-[A-Z0-9]+"],
        "gtm": [r"googletagmanager\.com/gtm\.js\?id=GTM-[A-Z0-9]+", r"GTM-[A-Z0-9]{4,}"],
        "ids": r"\bG-[A-Z0-9]{6,}\b",
        "requests": [r"google-analytics\.com/g/collect", r"analytics\.google\.com/g/collect", r"/g/collect\?"],
        "event_param": "en",
    },
    "meta": {
        "label": "Meta Pixel",
        "code": [r"connect\.facebook\.net/[^\"']*/fbevents\.js", r"fbq\(\s*['\"]init['\"]"],
        "ids": r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{8,})",
        "requests": [r"facebook\.com/tr[/?]"],
        "event_param": "ev",
    },
    "tiktok": {
        "label": "TikTok Pixel",
        "code": [r"analytics\.tiktok\.com/i18n/pixel", r"ttq\.load\(", r"ttq\.page\("],
        "ids": r"ttq\.load\(\s*['\"]([A-Z0-9]{10,})",
        "requests": [r"analytics\.tiktok\.com/api/v2/pixel"],
        "event_param": "event",
    },
    "snapchat": {
        "label": "Snap Pixel",
        "code": [r"sc-static\.net/scevent\.min\.js", r"snaptr\(\s*['\"]init['\"]"],
        "ids": r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([a-f0-9\-]{20,})",
        "requests": [r"tr\.snapchat\.com/", r"tr-shadow\.snapchat\.com/"],
        "event_param": "ev",
    },
}

LEVEL_AR = {
    0: "لم يُرصد",
    1: "الكود موجود في الصفحة",
    2: "رُصد إرسال حدث من المتصفح",
    3: "تأكد استلام الحدث في المنصة",
    4: "طُوبقت قيم الحدث مع عملية شراء حقيقية مصرّح بها",
}


def detect_code(html: str, scripts_src: list | None = None) -> dict:
    blob = (html or "") + "\n" + "\n".join(scripts_src or [])
    out = {}
    for key, spec in PLATFORMS.items():
        hits = [p for p in spec["code"] if re.search(p, blob, re.I)]
        via_gtm = key == "ga4" and not hits and any(re.search(p, blob) for p in spec["gtm"])
        ids = sorted(set(re.findall(spec["ids"], blob)))
        level = 1 if hits else 0
        out[key] = {
            "label": spec["label"], "level": level, "ids": ids[:5],
            "note_ar": LEVEL_AR[level] if not via_gtm else "يوجد Google Tag Manager؛ قد يحمّل GA4 ديناميكياً — يحتاج رصد الطلبات للتأكد",
            "via_gtm": via_gtm,
        }
    return out


def _har_urls(har: dict) -> list:
    entries = har.get("log", {}).get("entries", [])
    urls = []
    for e in entries:
        req = e.get("request", {})
        url = req.get("url", "")
        body = (req.get("postData") or {}).get("text", "")
        urls.append((url, body))
    return urls


def events_from_har(har: dict | str | Path) -> dict:
    if not isinstance(har, dict):
        har = json.loads(Path(har).read_text(encoding="utf-8"))
    found: dict = {k: [] for k in PLATFORMS}
    for url, body in _har_urls(har):
        for key, spec in PLATFORMS.items():
            if any(re.search(p, url) for p in spec["requests"]):
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
                ev = q.get(spec["event_param"])
                if not ev and body:
                    try:
                        ev = json.loads(body).get("event")
                    except (ValueError, AttributeError):
                        m = re.search(rf"(?:^|&){spec['event_param']}=([^&]+)", body)
                        ev = urllib.parse.unquote(m.group(1)) if m else None
                found[key].append(ev or "unknown")
    return found


def combine(code: dict, har_events: dict | None = None, confirmations: dict | None = None) -> dict:
    """Merge evidence. confirmations: {platform: {"level": 3|4, "note": "..."}}."""
    result = {}
    for key, c in code.items():
        level = c["level"]
        events = (har_events or {}).get(key) or []
        if events:
            level = max(level, 2)
        conf = (confirmations or {}).get(key)
        if conf and conf.get("level") in (3, 4) and conf.get("note"):
            if conf["level"] == 4 and level < 2:
                conf = {**conf, "level": 3}
            level = max(level, conf["level"])
        purchase_seen = any(str(e).lower() in ("purchase", "completepayment", "placeorder") for e in events)
        result[key] = {
            "label": c["label"], "level": level, "level_ar": LEVEL_AR[level], "ids": c["ids"],
            "events_observed": sorted(set(map(str, events))), "purchase_event_observed": purchase_seen,
            "conclusion_ar": _conclusion(level, purchase_seen),
        }
    return result


def _conclusion(level: int, purchase_seen: bool) -> str:
    if level == 0:
        return "لا دليل على التتبع من الصفحات المفحوصة."
    if level == 1:
        return "وجود الكود لا يثبت أن أحداث الشراء تعمل."
    if level == 2 and not purchase_seen:
        return "تُرسل أحداث، لكن لم نرصد حدث شراء؛ لا نحكم على تتبع المشتريات."
    if level == 2:
        return "رُصد حدث شراء يُرسل من المتصفح؛ الاستلام في المنصة لم يُؤكد بعد."
    if level == 3:
        return "المنصة تستقبل الأحداث؛ لم تُطابق القيم مع عملية حقيقية بعد."
    return "تم التحقق الكامل مع عملية حقيقية مصرّح بها."
