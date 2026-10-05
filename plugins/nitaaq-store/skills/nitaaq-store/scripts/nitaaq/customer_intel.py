"""Customer intelligence specialist, deterministic part.

Groups reviews and complaints into themes with an Arabic (Gulf and MSA)
keyword codebook, counts them, compares two periods, and ranks themes by
frequency x severity. Every result states the sample size and coverage:
twenty reviews are twenty reviews, and many reviews saying the same thing are
still one theme, not stronger proof.

Rating <= 2 is negative, 3 neutral, >= 4 positive; text without a rating is
coded by theme only. Complaint themes are not counted in positive reviews, and
praise themes are not counted in negative ones. Quotes are redacted before they appear anywhere.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .arabic import normalize, parse_amount
from .evidence import coverage_note_ar, dedupe
from .redact import redact_text
from .reports import filter_orders

ONE = Decimal("0.1")
SMALL_SAMPLE = 30

# theme -> (Arabic label, severity 1..3, phrases). Phrases are normalized before matching.
THEMES = {
    "delivery_delay": ("تأخر التوصيل", 2, ["تاخر", "متاخر", "تأخير", "ما وصل", "ماوصل", "لين الحين ما", "طول التوصيل",
                                          "التوصيل بطيء", "وصل بعد", "late delivery"]),
    "damaged": ("وصل تالف أو مكسور", 3, ["تالف", "مكسور", "مخدوش", "انكسر", "خربان", "damaged", "broken"]),
    "wrong_item": ("وصل منتج غلط أو ناقص", 3, ["غلط", "مو نفس", "مب نفس", "غير اللي طلبت", "ناقص", "نقص", "wrong item"]),
    "quality": ("الجودة", 2, ["جوده", "خامه", "الخامه", "رديء", "رديئ", "سيء", "سيئ", "ما يستاهل", "تقليد", "quality"]),
    "size_fit": ("المقاس", 2, ["مقاس", "المقاس", "صغير", "كبير عليه", "ضيق", "واسع", "size"]),
    "price_value": ("السعر مقابل القيمة", 1, ["غالي", "سعره عالي", "مبالغ", "ما يسوى", "expensive", "overpriced"]),
    "packaging": ("التغليف", 1, ["تغليف", "التغليف", "الكرتون", "packaging"]),
    "service": ("خدمة العملاء", 2, ["خدمه العملاء", "ما ردوا", "ماردوا", "ما احد رد", "تعامل", "الدعم", "support"]),
    "refund": ("الاسترجاع والاستبدال", 2, ["استرجاع", "استرداد", "ارجاع", "استبدال", "refund", "return"]),
    "authenticity": ("أصلي أو تقليد", 3, ["مو اصلي", "مب اصلي", "غير اصلي", "مقلد", "fake"]),
    "praise_quality": ("مدح الجودة", 0, ["ممتاز", "رائع", "جميل", "حلو", "روعه", "يجنن", "excellent", "great"]),
    "praise_delivery": ("مدح سرعة التوصيل", 0, ["توصيل سريع", "وصل بسرعه", "وصل بسرعة", "سريع التوصيل", "fast delivery"]),
}
_TN = {k: [normalize(p) for p in v[2]] for k, v in THEMES.items()}


def _q(v):
    return v.quantize(ONE, ROUND_HALF_UP)


def sentiment(rating) -> str | None:
    r = parse_amount(rating)
    if r is None:
        return None
    return "negative" if r <= 2 else "neutral" if r < 4 else "positive"


def themes_of(text: str) -> list[str]:
    t = normalize(text or "")
    return [k for k, ps in _TN.items() if any(p in t for p in ps)]


def _text(r: dict) -> str:
    return r.get("text") or r.get("content") or r.get("comment") or r.get("body") or ""


def analyze(reviews: list[dict], period=None, *, records_total: int | None = None, top_quotes: int = 2) -> dict:
    """Theme counts, sentiment and priority for one period (or all records)."""
    unique, dd = dedupe(reviews, "id")
    rows = unique
    if period:
        dated = [r if r.get("date") else {**r, "date": r.get("created_at")} for r in unique]
        rows, _ = filter_orders(dated, period[0], period[1])
    n = len(rows)
    sent = defaultdict(int)
    th: dict = defaultdict(lambda: {"count": 0, "negative": 0, "quotes": [], "products": defaultdict(int)})
    uncoded = 0
    for r in rows:
        s = sentiment(r.get("rating"))
        sent[s or "no_rating"] += 1
        ts = themes_of(_text(r))
        if not ts:
            uncoded += 1
        for t in ts:
            # a complaint word in a positive review ("تعامل راقي") is not a complaint, and praise in a negative one is not praise
            if (THEMES[t][1] > 0 and s == "positive") or (THEMES[t][1] == 0 and s == "negative"):
                continue
            a = th[t]
            a["count"] += 1
            a["negative"] += 1 if s == "negative" else 0
            prod = r.get("product_name") or r.get("product_id")
            if prod:
                a["products"][str(prod)] += 1
            quote = redact_text(_text(r))[:140]
            if len(a["quotes"]) < top_quotes and quote and quote not in a["quotes"]:
                a["quotes"].append(quote)
    out_themes = []
    for t, a in th.items():
        label, sev, _ = THEMES[t]
        out_themes.append({
            "theme": t, "label_ar": label, "count": a["count"], "share_pct": _q(Decimal(a["count"]) / n * 100) if n else None,
            "negative": a["negative"], "severity": sev, "priority_score": a["count"] * sev,
            "top_products": sorted(a["products"].items(), key=lambda kv: (-kv[1], kv[0]))[:3], "quotes": a["quotes"]})
    out_themes.sort(key=lambda x: (-x["priority_score"], -x["count"], x["theme"]))
    flags = []
    if n < SMALL_SAMPLE:
        flags.append("small_sample")
    if records_total is not None and len(unique) < records_total:
        flags.append("incomplete_data")
    if records_total is None:
        flags.append("unknown_completeness")
    return {"period": list(period) if period else None, "sample_size": n, "duplicates_removed": dd["duplicates_removed"],
            "sentiment": dict(sent), "uncoded": uncoded, "themes": out_themes, "flags": flags,
            "method_ar": "تصنيف بالكلمات المفتاحية؛ قد يفوّت تعبيرات غير مألوفة. الأولوية = التكرار × الخطورة."}


def compare(reviews: list[dict], current, baseline, *, records_total: int | None = None,
            min_sample: int = 10, rise_pct: Decimal | int = 50) -> dict:
    """Theme counts per period and complaints_up when negatives clearly rose with enough reviews in both periods."""
    c = analyze(reviews, current, records_total=records_total)
    b = analyze(reviews, baseline, records_total=records_total)
    cn, bn = c["sentiment"].get("negative", 0), b["sentiment"].get("negative", 0)
    signals = []
    enough = c["sample_size"] >= min_sample and b["sample_size"] >= min_sample
    cr = Decimal(cn) / c["sample_size"] if c["sample_size"] else None
    br = Decimal(bn) / b["sample_size"] if b["sample_size"] else None
    if enough and cr is not None and br is not None and (
            (br == 0 and cn >= 3) or (br and (cr - br) / br * 100 >= Decimal(str(rise_pct)))):
        signals.append("complaints_up")
    bt = {t["theme"]: t["count"] for t in b["themes"]}
    changes = [{"theme": t["theme"], "label_ar": t["label_ar"], "current": t["count"], "baseline": bt.get(t["theme"], 0)}
               for t in c["themes"]]
    return {"current": c, "baseline": b, "negative": {"current": cn, "baseline": bn,
                                                     "current_rate_pct": _q(cr * 100) if cr is not None else None,
                                                     "baseline_rate_pct": _q(br * 100) if br is not None else None},
            "theme_changes": changes, "enough_sample": enough, "signals": signals}


# ------------------------------------------------------------------ findings

def themes_finding(res: dict, ev: dict, *, fid: str = "r1", top: int = 5) -> dict:
    calc = {"fn": "review_themes", "evidence": ev["evidence_id"], "period": res["period"]}
    neg_themes = [t for t in res["themes"] if t["severity"] > 0][:top]
    obs = [{"label_ar": "عدد التقييمات والشكاوى المحللة", "current": str(res["sample_size"]), "baseline": None,
            "calc": {**calc, "field": "sample_size"}}]
    obs += [{"label_ar": t["label_ar"], "current": str(t["count"]), "baseline": None,
             "calc": {**calc, "theme": t["theme"]}} for t in neg_themes]
    small = "small_sample" in res["flags"]
    if neg_themes:
        interp = (f"من {res['sample_size']} تقييم/شكوى، أكثر المواضيع أولوية: " +
                  "، ".join(f"{t['label_ar']} ({t['count']})" for t in neg_themes[:3]) + ".")
    else:
        interp = f"من {res['sample_size']} تقييم/شكوى، ما ظهر موضوع سلبي متكرر بالكلمات اللي نصنّف بها."
    lim = [res["method_ar"], f"عينة صغيرة ({res['sample_size']} فقط)؛ النتائج مؤشر وليست حكماً." if small else None,
           "التقييمات جزئية أو غير معروفة الاكتمال." if ev["coverage"].get("complete") is not True else None,
           f"{res['uncoded']} نص ما انطبق عليه أي موضوع." if res["uncoded"] else None,
           "كثرة التقييمات اللي تقول نفس الشي لا تعني دليلاً أقوى على السبب."]
    return {
        "finding_id": fid, "agent": "customer_intelligence", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "review_themes", "entity": None,
        "period": {"current": res["period"] or "all", "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": obs, "interpretation_ar": interp, "alternatives_ar": [],
        "confidence": {"level": "low" if small else "medium",
                       "rationale_ar": f"عدّ مواضيع من {res['sample_size']} نص بتصنيف كلمات مفتاحية؛ " + coverage_note_ar(ev)},
        "priority": "high" if any(t["severity"] == 3 and t["count"] >= 3 for t in neg_themes) else "medium",
        "proposed_action_ref": None, "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً."},
        "requires": None, "claims_cause": False, "limitations_ar": [x for x in lim if x],
        "quotes_redacted": {t["theme"]: t["quotes"] for t in neg_themes},
    }


def recompute(records: list[dict], calc: dict):
    res = analyze(records, calc.get("period"))
    if calc.get("field") == "sample_size":
        return res["sample_size"]
    return next((t["count"] for t in res["themes"] if t["theme"] == calc.get("theme")), 0)
