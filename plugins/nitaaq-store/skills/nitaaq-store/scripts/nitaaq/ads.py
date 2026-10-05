"""Ads specialists, deterministic part: account audit, Google Ads structure,
search terms, creative checks and paid social.

Everything here reads an ads export the merchant gives (Google Ads, Meta,
TikTok, Snapchat) or rows an authorized tool returned. Nothing runs without
ads evidence, and nothing changes an ad account: negatives, pauses and budget
moves come out as proposals that need the merchant's approval and are applied
by the merchant (no ads write tool is assumed).

Platform conversions and ROAS are what the platform attributed to its ads.
ROAS is not profit; profit needs the product margin. Thresholds such as
min_clicks or the frequency limit are parameters we choose and state, not
industry figures.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse

from .arabic import normalize, parse_amount, to_western_digits, tokens
from .evidence import coverage_note_ar
from .reports import parse_dt

TWO = Decimal("0.01")
ONE = Decimal("0.1")

# canonical field -> export column names (lowercase, spaces as underscores)
COLUMNS = {
    "platform": ("platform", "المنصة"),
    "campaign": ("campaign", "campaign_name", "الحملة", "اسم_الحملة"),
    "campaign_type": ("campaign_type", "advertising_channel_type", "نوع_الحملة"),
    "objective": ("objective", "campaign_objective", "الهدف"),
    "bid_strategy": ("bid_strategy", "bid_strategy_type", "استراتيجية_عروض_الأسعار"),
    "ad_group": ("ad_group", "ad_group_name", "ad_set", "ad_set_name", "adset", "ad_squad", "المجموعة_الإعلانية"),
    "ad": ("ad", "ad_name", "ad_id", "الإعلان"),
    "keyword": ("keyword", "الكلمة_المفتاحية"),
    "search_term": ("search_term", "search_query", "query", "مصطلح_البحث"),
    "match_type": ("match_type", "نوع_المطابقة"),
    "date": ("day", "date", "reporting_starts", "start_date", "التاريخ", "اليوم"),
    "impressions": ("impressions", "impr.", "مرات_الظهور"),
    "clicks": ("clicks", "link_clicks", "clicks_(all)", "swipe_ups", "النقرات"),
    "cost": ("cost", "spend", "amount_spent", "amount_spent_(sar)", "amount_spent_(usd)", "التكلفة", "المبلغ_المنفق"),
    "conversions": ("conversions", "purchases", "results", "website_purchases", "التحويلات", "المشتريات", "النتائج"),
    "value": ("conv._value", "conversion_value", "conversions_value", "purchase_conversion_value",
              "purchases_conversion_value", "website_purchases_conversion_value", "total_purchase_value", "القيمة"),
    "reach": ("reach", "الوصول"),
    "frequency": ("frequency", "التكرار"),
    "attribution": ("attribution_setting", "attribution", "attribution_window", "نافذة_الإسناد"),
    "currency": ("currency", "العملة"),
    "status": ("status", "campaign_status", "الحالة"),
    "final_url": ("final_url", "landing_page", "website_url", "destination_url", "الرابط"),
}
NUMERIC = ("impressions", "clicks", "cost", "conversions", "value", "reach", "frequency")
_ALIAS = {a: k for k, names in COLUMNS.items() for a in names}
SMART_BIDDING = re.compile(r"(target|maximi[sz]e conversion|tcpa|troas|max(imize)? conversion value|cost cap|bid cap|"
                           r"تحقيق أقصى|عائد مستهدف|تكلفة مستهدفة)", re.I)
INFO_WORDS = [normalize(x) for x in ("كيف", "طريقه", "طريقة", "وش", "ما هو", "ما هي", "شرح", "فوائد", "اضرار", "تجربتي",
                                       "how", "what", "diy", "recipe")]
JOB_WORDS = [normalize(x) for x in ("وظائف", "وظيفه", "توظيف", "راتب", "jobs", "job", "career")]
FREE_WORDS = [normalize(x) for x in ("مجانا", "مجاني", "ببلاش", "تحميل", "free", "download", "pdf")]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _q(v, step=TWO):
    return v.quantize(step, ROUND_HALF_UP) if v is not None else None


def _key(k) -> str:
    return re.sub(r"\s+", "_", str(k).strip().lower())


def _num(v) -> Decimal | None:
    if v in (None, "", "--", "—", " --"):
        return None
    if isinstance(v, str):
        v = v.replace("%", "").strip()
    return parse_amount(v)


def normalize_rows(rows: list[dict], platform: str | None = None) -> list[dict]:
    """Rows with canonical field names; numbers as Decimal (None when missing, never zero)."""
    out = []
    for r in rows:
        n: dict = {}
        for k, v in r.items():
            canon = _ALIAS.get(_key(k))
            if canon is None:
                m = re.fullmatch(r"(headline|description)_?(\d+)", _key(k))
                if m and v not in (None, ""):
                    n.setdefault(m.group(1) + "s", []).append(str(v))
                continue
            if canon not in n or n[canon] in (None, ""):
                n[canon] = v
        for f in NUMERIC:
            n[f] = _num(n.get(f))
        dt = parse_dt(n.get("date"))
        n["date"] = dt.date() if dt else None
        n["platform"] = (n.get("platform") or platform or "unknown").lower()
        for f in ("campaign", "ad_group", "ad", "keyword", "search_term", "bid_strategy", "campaign_type", "objective",
                  "attribution", "currency", "final_url", "status", "match_type"):
            n[f] = str(n[f]).strip() if n.get(f) not in (None, "") else None
        out.append(n)
    return out


def _sum(rows, f):
    vals = [r[f] for r in rows if r.get(f) is not None]
    return sum(vals, Decimal(0)) if vals else None


def totals(rows: list[dict]) -> dict:
    t = {f: _sum(rows, f) for f in ("impressions", "clicks", "cost", "conversions", "value")}
    t["ctr_pct"] = _q(t["clicks"] / t["impressions"] * 100) if t["clicks"] is not None and t["impressions"] else None
    t["cpc"] = _q(t["cost"] / t["clicks"]) if t["cost"] is not None and t["clicks"] else None
    t["cpa"] = _q(t["cost"] / t["conversions"]) if t["cost"] is not None and t["conversions"] else None
    t["platform_roas"] = _q(t["value"] / t["cost"]) if t["value"] is not None and t["cost"] else None
    return t


def by(rows: list[dict], key: str) -> dict:
    g: dict = defaultdict(list)
    for r in rows:
        g[r.get(key) or "(غير محدد)"].append(r)
    return {k: totals(v) for k, v in g.items()}


def _has_any(text: str, words: list[str]) -> bool:
    t = f" {normalize(text)} "
    return any(f" {w} " in t or (len(w) > 3 and w in t) for w in words)


def _norm_list(xs) -> list[str]:
    return [normalize(x) for x in xs or [] if str(x).strip()]


def _settled(rows, as_of, lag_days):
    """Rows old enough for late conversions to have arrived (rows without a date are kept and flagged)."""
    if as_of is None:
        return rows, False
    cut = date.fromisoformat(str(as_of)[:10]) - timedelta(days=lag_days)
    return [r for r in rows if r["date"] is None or r["date"] <= cut], any(r["date"] and r["date"] > cut for r in rows)


def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _common(rows: list[dict]) -> list[str]:
    flags = []
    cur = {r["currency"].upper() for r in rows if r.get("currency")}
    if len(cur) > 1:
        flags.append("currency_mixed")
    if all(r.get("conversions") is None for r in rows):
        flags.append("no_conversions_column")
    atts = {r["attribution"] for r in rows if r.get("attribution")}
    if len(atts) > 1:
        flags.append("mixed_attribution")
    return flags


# ------------------------------------------------------------------ auditor

def audit(rows: list[dict], *, min_clicks: int = 30, min_conversions: int = 15, margin_pct=None,
          store_domains: list[str] | None = None, brand: list[str] | None = None, as_of=None, lag_days: int = 7) -> dict:
    """Account checks across platforms. Each check names its rule and the numbers behind it."""
    flags = _common(rows)
    issues = []
    total = totals(rows)
    settled, recent = _settled(rows, as_of, lag_days)
    if recent:
        flags.append("recent_days_excluded_from_zero_conversion_checks")
    camps = defaultdict(list)
    for r in rows:
        camps[r["campaign"] or "(غير محدد)"].append(r)
    s_camps = by(settled, "campaign")
    if total["cost"] and "no_conversions_column" not in flags and not total["conversions"]:
        issues.append({"check": "no_conversions_recorded", "severity": "high", "entity": "account",
                       "detail": {"cost": total["cost"]}})
    breakeven = (Decimal(100) / Decimal(str(margin_pct))) if margin_pct else None
    brand_n = _norm_list(brand)
    for name, rs in sorted(camps.items()):
        t = totals(rs)
        st = s_camps.get(name) or {}
        if "no_conversions_column" not in flags and st.get("cost") and st.get("clicks") is not None and \
                st["clicks"] >= min_clicks and not st.get("conversions"):
            issues.append({"check": "zero_conversion_campaign", "severity": "medium", "entity": name,
                           "detail": {"cost": st["cost"], "clicks": st["clicks"]}})
        if breakeven and t["platform_roas"] is not None and t["conversions"] and t["platform_roas"] < breakeven:
            issues.append({"check": "below_breakeven_roas", "severity": "high", "entity": name,
                           "detail": {"platform_roas": t["platform_roas"], "breakeven_roas": _q(breakeven)}})
        strategies = {r["bid_strategy"] for r in rs if r.get("bid_strategy")}
        if any(SMART_BIDDING.search(s) for s in strategies) and (t["conversions"] or 0) < min_conversions:
            issues.append({"check": "smart_bidding_low_volume", "severity": "medium", "entity": name,
                           "detail": {"conversions": t["conversions"] or 0, "min_conversions": min_conversions}})
        if brand_n:
            texts = [r.get("keyword") or r.get("search_term") for r in rs if r.get("keyword") or r.get("search_term")]
            b = [x for x in texts if _has_any(x, brand_n)]
            if b and len(b) < len(texts):
                issues.append({"check": "brand_mixed", "severity": "low", "entity": name,
                               "detail": {"brand_rows": len(b), "other_rows": len(texts) - len(b)}})
    if store_domains:
        doms = {_host(d if "//" in d else f"https://{d}") for d in store_domains}
        off = sorted({r["final_url"] for r in rows if r.get("final_url") and _host(r["final_url"]) not in doms})
        if off:
            issues.append({"check": "landing_outside_store", "severity": "medium", "entity": "account",
                           "detail": {"urls": off[:10], "count": len(off)}})
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["check"], str(i["entity"])))
    counts = defaultdict(int)
    for i in issues:
        counts[i["check"]] += 1
    metrics = {"cost": total["cost"], "conversions": total["conversions"], "platform_roas": total["platform_roas"],
               **{f"issues:{k}": v for k, v in counts.items()}}
    return {"kind": "audit", "totals": total, "campaigns": by(rows, "campaign"), "issues": issues, "flags": flags,
            "metrics": metrics, "breakeven_roas": _q(breakeven) if breakeven else None}


# ------------------------------------------------------------------ Google Ads structure (PPC)

def segment(text: str | None, campaign: str | None, brand_n: list[str], comp_n: list[str]) -> str:
    probe = text or campaign or ""
    if brand_n and _has_any(probe, brand_n):
        return "brand"
    if comp_n and _has_any(probe, comp_n):
        return "competitor"
    return "non_brand"


def structure(rows: list[dict], *, brand: list[str] | None = None, competitors: list[str] | None = None,
              min_conversions: int = 15) -> dict:
    """Brand / non-brand / competitor split, campaign types, bid strategy vs conversion volume."""
    brand_n, comp_n = _norm_list(brand), _norm_list(competitors)
    seg = defaultdict(list)
    for r in rows:
        seg[segment(r.get("search_term") or r.get("keyword"), r.get("campaign"), brand_n, comp_n)].append(r)
    split = {k: totals(v) for k, v in seg.items()}
    types = by([r for r in rows if r.get("campaign_type")], "campaign_type")
    strategies = []
    for name, rs in sorted(_group(rows, "campaign").items()):
        t = totals(rs)
        bs = sorted({r["bid_strategy"] for r in rs if r.get("bid_strategy")})
        smart = any(SMART_BIDDING.search(s) for s in bs)
        strategies.append({"campaign": name, "bid_strategies": bs, "conversions": t["conversions"], "smart": smart,
                           "low_volume_for_smart": smart and (t["conversions"] or 0) < min_conversions})
    total_cost = sum((v["cost"] or 0 for v in split.values()), Decimal(0))
    share = {k: _q(v["cost"] / total_cost * 100, ONE) if total_cost and v["cost"] is not None else None for k, v in split.items()}
    metrics = {f"cost:{k}": v["cost"] for k, v in split.items()}
    metrics.update({f"share:{k}": s for k, s in share.items()})
    metrics["low_volume_smart_campaigns"] = sum(1 for s in strategies if s["low_volume_for_smart"])
    flags = _common(rows) + ([] if brand_n else ["brand_terms_not_given"])
    return {"kind": "structure", "split": split, "share_pct": share, "campaign_types": types, "bidding": strategies,
            "flags": flags, "metrics": metrics, "min_conversions": min_conversions}


def _group(rows, key):
    g: dict = defaultdict(list)
    for r in rows:
        g[r.get(key) or "(غير محدد)"].append(r)
    return g


# ------------------------------------------------------------------ search terms

def classify_term(term: str, brand_n: list[str], comp_n: list[str]) -> str:
    if brand_n and _has_any(term, brand_n):
        return "brand"
    if comp_n and _has_any(term, comp_n):
        return "competitor"
    if _has_any(term, JOB_WORDS):
        return "jobs"
    if _has_any(term, FREE_WORDS):
        return "free"
    if _has_any(term, INFO_WORDS):
        return "informational"
    return "commercial"


def _ngrams(term: str, sizes=(1, 2)) -> dict:
    """{normalized n-gram: the merchant's own wording}, so a negative is proposed as people typed it."""
    norm, orig = tokens(term), term.split()
    if len(orig) != len(norm):
        orig = norm
    out = {}
    for n in sizes:
        for i in range(len(norm) - n + 1):
            g = " ".join(norm[i:i + n])
            if len(g) >= 3:
                out.setdefault(g, " ".join(orig[i:i + n]))
    return out


def _blocks(neg: str, text: str) -> bool:
    """Would a phrase negative `neg` block `text`? (word sequence match on normalized tokens)."""
    return f" {neg} " in f" {' '.join(tokens(text))} "


def search_terms(rows: list[dict], *, brand: list[str] | None = None, competitors: list[str] | None = None,
                 keywords: list[str] | None = None, negatives: list[str] | None = None, min_clicks: int = 10,
                 as_of=None, lag_days: int = 7, top: int = 30) -> dict:
    """Aggregate search terms, classify intent, and list negative-keyword candidates with conflicts removed."""
    brand_n, comp_n = _norm_list(brand), _norm_list(competitors)
    rows = [r for r in rows if r.get("search_term")]
    flags = _common(rows)
    settled, recent = _settled(rows, as_of, lag_days)
    if recent:
        flags.append("recent_days_excluded")
    if as_of is None:
        flags.append("conversion_lag_unknown")
    terms: dict = {}
    for r in settled:
        k = " ".join(tokens(r["search_term"]))
        a = terms.setdefault(k, {"term": r["search_term"], "cost": Decimal(0), "clicks": Decimal(0), "conversions": Decimal(0),
                                 "value": Decimal(0), "campaigns": set()})
        for f in ("cost", "clicks", "conversions", "value"):
            a[f] += r.get(f) or 0
        if r.get("campaign"):
            a["campaigns"].add(r["campaign"])
    for k, a in terms.items():
        a["intent"] = classify_term(a["term"], brand_n, comp_n)
    kw_n = [" ".join(tokens(k)) for k in keywords or []]
    neg_n = [" ".join(tokens(k)) for k in negatives or []]
    converting = [k for k, a in terms.items() if a["conversions"] > 0]

    def conflicts(cand: str, match: str) -> list[str]:
        hits = (lambda text: cand == text) if match == "exact" else (lambda text: _blocks(cand, text))
        c = []
        if brand_n and _has_any(cand, brand_n):
            c.append("brand")
        if any(hits(kw) for kw in kw_n):
            c.append("blocks_keyword")
        if any(hits(t) for t in converting):
            c.append("blocks_converting_term")
        if any(_blocks(n, cand) for n in neg_n):
            c.append("already_negative")
        return c

    cands, held = [], []
    for k, a in terms.items():
        if a["clicks"] >= min_clicks and a["conversions"] == 0 and a["cost"] > 0:
            item = {"negative": a["term"], "key": k, "match": "exact", "cost": a["cost"], "clicks": a["clicks"],
                    "intent": a["intent"], "terms": 1}
            c = conflicts(k, "exact")
            (held if c else cands).append({**item, "conflicts": c} if c else item)
    grams: dict = defaultdict(lambda: {"cost": Decimal(0), "clicks": Decimal(0), "conversions": Decimal(0), "terms": 0})
    for k, a in terms.items():
        for g, shown in _ngrams(a["term"]).items():
            G = grams[g]
            G.setdefault("shown", shown)
            G["cost"] += a["cost"]
            G["clicks"] += a["clicks"]
            G["conversions"] += a["conversions"]
            G["terms"] += 1
    exact = {c["key"] for c in cands} | {c["key"] for c in held}
    for g, G in grams.items():
        if G["terms"] >= 2 and G["clicks"] >= min_clicks and G["conversions"] == 0 and G["cost"] > 0 and g not in exact:
            item = {"negative": G["shown"], "key": g, "match": "phrase", "cost": G["cost"], "clicks": G["clicks"],
                    "intent": classify_term(g, brand_n, comp_n), "terms": G["terms"]}
            c = conflicts(g, "phrase")
            (held if c else cands).append({**item, "conflicts": c} if c else item)
    # an exact candidate already covered by a phrase candidate is not proposed twice
    phrases = [c["key"] for c in cands if c["match"] == "phrase"]
    cands = [c for c in cands if c["match"] == "phrase" or not any(_blocks(p, c["key"]) for p in phrases)]
    cands.sort(key=lambda x: (-x["cost"], x["negative"]))
    held.sort(key=lambda x: (-x["cost"], x["negative"]))
    # brand terms served by campaigns that are not brand campaigns
    leak = [a for a in terms.values() if a["intent"] == "brand" and a["campaigns"] and
            not any(_has_any(c, brand_n + [normalize("brand"), normalize("علامه")]) for c in a["campaigns"])]
    by_intent: dict = defaultdict(lambda: {"cost": Decimal(0), "clicks": Decimal(0), "conversions": Decimal(0), "terms": 0})
    for a in terms.values():
        b = by_intent[a["intent"]]
        b["cost"] += a["cost"]
        b["clicks"] += a["clicks"]
        b["conversions"] += a["conversions"]
        b["terms"] += 1
    exact_cost = sum((c["cost"] for c in cands if c["match"] == "exact"), Decimal(0))
    metrics = {"terms": len(terms), "candidates": len(cands), "held_conflicts": len(held),
               "candidate_exact_cost": exact_cost, "brand_leak_cost": sum((a["cost"] for a in leak), Decimal(0))}
    if not brand_n:
        flags.append("brand_terms_not_given")
    return {"kind": "search_terms", "by_intent": dict(by_intent), "candidates": cands[:top], "held": held[:top],
            "brand_leak": [{"term": a["term"], "cost": a["cost"], "campaigns": sorted(a["campaigns"])} for a in leak][:top],
            "flags": flags, "metrics": metrics, "min_clicks": min_clicks}


def negatives_proposal(res: dict, store_id: str, source_finding: str, *, limit: int = 20) -> dict | None:
    """A proposal only: there is no authorized ads write tool, so the merchant applies it after approving."""
    items = [{"entity_id": c["negative"], "label_ar": f"كلمة سلبية ({'مطابقة تامة' if c['match'] == 'exact' else 'عبارة'})",
              "before": {"negative": False}, "after": {"negative": True, "match": c["match"]},
              "cost_in_period": str(c["cost"]), "clicks_in_period": str(c["clicks"])} for c in res["candidates"][:limit]]
    if not items:
        return None
    return {"proposal_id": "ap1", "store_id": str(store_id), "operation": "ads.add_negative_keywords", "items": items,
            "sensitivity": "high", "reversible": True, "customer_visible": False, "source_findings": [source_finding],
            "proposed_by": "search_query_analyst", "execution": "merchant_in_platform",
            "note_ar": "اقتراح فقط. ما عندنا أداة مصرّح لها تعدّل حسابك الإعلاني؛ بعد موافقتك تضيفها أنت من المنصة، "
                       "وراجع القائمة قبلها لأن بعض البحث بدون تحويل اليوم قد يتحول لاحقاً."}


# ------------------------------------------------------------------ creative

RSA_LIMITS = {"headline": 30, "description": 90, "min_headlines": 3, "max_headlines": 15, "min_descriptions": 2,
              "max_descriptions": 4}
CLAIMS = re.compile(r"(أصلي|اصلي|الأفضل|الافضل|رقم\s*1|الأول|الاول|مضمون|ضمان|مجاني|مجانا|شحن مجاني|توصيل مجاني|"
                    r"أرخص|ارخص|best|original|guarantee|free)", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def fact_issues(texts: list[str], facts: dict | None) -> list[dict]:
    """Claims and numbers in customer-facing text that the store's own data does not back."""
    facts = facts or {}
    allowed = {str(parse_amount(facts[k])) for k in ("price", "sale_price") if parse_amount(facts.get(k)) is not None}
    if parse_amount(facts.get("price")) and parse_amount(facts.get("sale_price")):
        p, s = parse_amount(facts["price"]), parse_amount(facts["sale_price"])
        allowed.add(str(((p - s) / p * 100).quantize(Decimal(1), ROUND_HALF_UP)))
    allowed |= {str(parse_amount(x)) for x in facts.get("numbers_verified", [])}
    verified = _norm_list(facts.get("claims_verified"))
    issues = []
    for t in texts:
        for m in CLAIMS.finditer(t):
            word = normalize(m.group(0))
            if ("شحن" in word or "توصيل" in word) and facts.get("free_shipping") is True:
                continue
            if not any(v in word or word in v for v in verified):
                issues.append({"check": "claim_needs_proof", "text": t, "claim": m.group(0)})
        for num in NUMBER.findall(to_western_digits(t)):
            if str(parse_amount(num)) not in allowed:
                issues.append({"check": "unverified_number", "text": t, "number": num})
    return issues


def rsa_check(headlines: list[str], descriptions: list[str], *, facts: dict | None = None) -> dict:
    """Responsive search ad text against platform limits and store facts.

    facts: {"price": "199", "sale_price": "149", "free_shipping": true, "claims_verified": ["أصلي"]} from Salla data.
    Ad strength is the platform's indicator, not a target, and is not computed here.
    """
    issues = []
    for kind, items, limit in (("headline", headlines, RSA_LIMITS["headline"]),
                               ("description", descriptions, RSA_LIMITS["description"])):
        for t in items:
            if len(t) > limit:
                issues.append({"check": f"{kind}_too_long", "text": t, "length": len(t), "limit": limit})
        seen = {}
        for t in items:
            n = normalize(t)
            if n in seen:
                issues.append({"check": f"duplicate_{kind}", "text": t})
            seen[n] = 1
    if not RSA_LIMITS["min_headlines"] <= len(headlines) <= RSA_LIMITS["max_headlines"]:
        issues.append({"check": "headline_count", "count": len(headlines)})
    if not RSA_LIMITS["min_descriptions"] <= len(descriptions) <= RSA_LIMITS["max_descriptions"]:
        issues.append({"check": "description_count", "count": len(descriptions)})
    issues += fact_issues(headlines + descriptions, facts)
    counts = defaultdict(int)
    for i in issues:
        counts[i["check"]] += 1
    return {"kind": "rsa_check", "issues": issues, "metrics": {f"issues:{k}": v for k, v in counts.items()},
            "status_ar": "مسودة نص فقط؛ ليست إعلاناً منشوراً."}


def fatigue(rows: list[dict], *, min_impressions: int = 1000, drop_pct=25) -> dict:
    """Ads whose CTR fell between the first and second half of their dated rows (with frequency when present)."""
    out, skipped = [], 0
    for name, rs in sorted(_group([r for r in rows if r.get("date")], "ad").items()):
        rs = sorted(rs, key=lambda r: r["date"])
        days = sorted({r["date"] for r in rs})
        if len(days) < 4:
            skipped += 1
            continue
        mid = days[len(days) // 2]
        a, b = totals([r for r in rs if r["date"] < mid]), totals([r for r in rs if r["date"] >= mid])
        if (a["impressions"] or 0) < min_impressions or (b["impressions"] or 0) < min_impressions or not a["ctr_pct"]:
            skipped += 1
            continue
        change = _q((b["ctr_pct"] - a["ctr_pct"]) / a["ctr_pct"] * 100, ONE)
        fa = [r["frequency"] for r in rs if r["date"] < mid and r.get("frequency") is not None]
        fb = [r["frequency"] for r in rs if r["date"] >= mid and r.get("frequency") is not None]
        item = {"ad": name, "ctr_first": a["ctr_pct"], "ctr_second": b["ctr_pct"], "ctr_change_pct": change,
                "frequency_first": _q(max(fa)) if fa else None, "frequency_second": _q(max(fb)) if fb else None,
                "split_date": mid.isoformat()}
        if change <= -Decimal(str(drop_pct)):
            out.append(item)
    flags = _common(rows) + (["too_little_data_for_some_ads"] if skipped else [])
    return {"kind": "fatigue", "fatigued": out, "skipped": skipped, "flags": flags, "drop_pct": drop_pct,
            "min_impressions": min_impressions, "metrics": {"fatigued": len(out), "skipped": skipped}}


# ------------------------------------------------------------------ paid social

def social(rows: list[dict], *, frequency_limit=4, min_results: int = 10) -> dict:
    """Meta / TikTok / Snapchat ad sets: frequency, cost per result within the same objective, attribution settings."""
    flags = _common(rows)
    sets = []
    for name, rs in sorted(_group(rows, "ad_group").items()):
        t = totals(rs)
        freq = [r["frequency"] for r in rs if r.get("frequency") is not None]
        objs = sorted({r["objective"] for r in rs if r.get("objective")})
        sets.append({"ad_set": name, "objective": objs[0] if len(objs) == 1 else ("mixed" if objs else None),
                     "cost": t["cost"], "results": t["conversions"], "cost_per_result": t["cpa"],
                     "frequency_max": _q(max(freq)) if freq else None,
                     "frequency_high": bool(freq) and max(freq) >= Decimal(str(frequency_limit)),
                     "enough_results": (t["conversions"] or 0) >= min_results})
    # compare cost per result only inside one objective and only with enough results
    ranked = defaultdict(list)
    for s in sets:
        if s["enough_results"] and s["cost_per_result"] is not None and s["objective"] not in (None, "mixed"):
            ranked[s["objective"]].append(s)
    comparisons = {o: sorted(v, key=lambda s: s["cost_per_result"]) for o, v in ranked.items() if len(v) >= 2}
    if len({s["objective"] for s in sets if s["objective"]}) > 1:
        flags.append("several_objectives")
    metrics = {"ad_sets": len(sets), "frequency_high": sum(1 for s in sets if s["frequency_high"]),
               "not_enough_results": sum(1 for s in sets if not s["enough_results"])}
    return {"kind": "social", "ad_sets": sets, "comparisons": comparisons, "flags": flags, "metrics": metrics,
            "frequency_limit": frequency_limit, "min_results": min_results}


# ------------------------------------------------------------------ findings

ANALYSES = {"audit": audit, "structure": structure, "search_terms": search_terms, "fatigue": fatigue, "social": social}
AGENT_OF = {"audit": "paid_media_auditor", "structure": "ppc", "search_terms": "search_query_analyst",
            "fatigue": "ad_creative", "social": "paid_social"}
METRIC_AR = {
    "cost": "الإنفاق في الفترة", "conversions": "تحويلات منسوبة حسب المنصة", "platform_roas": "عائد الإنفاق حسب المنصة (ليس ربحاً)",
    "issues:no_conversions_recorded": "حساب فيه إنفاق بدون أي تحويل مسجل",
    "issues:zero_conversion_campaign": "حملات بإنفاق ونقرات وبدون تحويل",
    "issues:below_breakeven_roas": "حملات عائدها حسب المنصة تحت عائد التعادل",
    "issues:smart_bidding_low_volume": "حملات بمزايدة ذكية وتحويلات قليلة",
    "issues:brand_mixed": "حملات تخلط بحث اسم المتجر مع غيره",
    "issues:landing_outside_store": "روابط هبوط خارج نطاق المتجر",
    "cost:brand": "إنفاق بحث اسم المتجر", "cost:non_brand": "إنفاق البحث العام", "cost:competitor": "إنفاق بحث المنافسين",
    "share:brand": "نسبة إنفاق اسم المتجر %", "share:non_brand": "نسبة إنفاق البحث العام %",
    "share:competitor": "نسبة إنفاق المنافسين %",
    "low_volume_smart_campaigns": "حملات بمزايدة ذكية وتحويلات أقل من الحد",
    "terms": "مصطلحات بحث محللة", "candidates": "مرشحات كلمات سلبية", "held_conflicts": "مرشحات أوقفناها لتعارض",
    "candidate_exact_cost": "إنفاق المصطلحات المرشحة (مطابقة تامة) في الفترة", "brand_leak_cost": "إنفاق اسم المتجر خارج حملة الاسم",
    "fatigued": "إعلانات نزلت نسبة النقر فيها", "skipped": "إعلانات بيانات غير كافية",
    "ad_sets": "مجموعات إعلانية", "frequency_high": "مجموعات تكرار الظهور فيها فوق الحد",
    "not_enough_results": "مجموعات نتائجها قليلة للمقارنة",
}
THRESHOLDS_AR = {"min_clicks": "أقل عدد نقرات للحكم", "min_conversions": "أقل عدد تحويلات للمزايدة الذكية",
                 "frequency_limit": "حد تكرار الظهور", "drop_pct": "نسبة نزول النقر المعتبرة",
                 "min_results": "أقل عدد نتائج للمقارنة"}
_INTERP = {
    "audit": "تدقيق الحساب الإعلاني من الملف: {n} ملاحظة. الأرقام منسوبة حسب المنصة وليست أرباحاً.",
    "structure": "توزيع إنفاق Google بين اسم المتجر والبحث العام والمنافسين، ونوع المزايدة مقابل عدد التحويلات.",
    "search_terms": "{c} مرشح كلمة سلبية من مصطلحات بحث بنقرات وبدون تحويل، بعد استبعاد ما يتعارض مع اسم المتجر أو كلماتك أو بحث يحوّل.",
    "fatigue": "{f} إعلان نزلت نسبة النقر عليه بين نصفي الفترة؛ مرشحات لتجديد الإبداع وليست حكماً.",
    "social": "{h} مجموعة إعلانية تكرار الظهور فيها فوق الحد المختار، والمقارنة بين المجموعات داخل نفس الهدف فقط.",
}


def finding(res: dict, ev: dict, params: dict, *, fid: str = "a1") -> dict:
    kind = res["kind"]
    calc = {"fn": "ads", "analysis": kind, "evidence": ev["evidence_id"], "params": params}
    obs = [{"label_ar": METRIC_AR.get(k, k), "current": str(v), "baseline": None, "calc": {**calc, "metric": k}}
           for k, v in res["metrics"].items() if v is not None]
    m = res["metrics"]
    interp = _INTERP[kind].format(n=sum(v for k, v in m.items() if k.startswith("issues:")),
                                  c=m.get("candidates", 0), f=m.get("fatigued", 0), h=m.get("frequency_high", 0))
    lim = ["من ملف تصدير أو أداة مصرّحة؛ ما عدّلنا شي في حسابك الإعلاني.",
           "التحويلات والعائد حسب نموذج الإسناد في المنصة؛ العائد على الإنفاق ليس ربحية."]
    for p, label in THRESHOLDS_AR.items():
        if p in params or p in res:
            lim.append(f"{label} = {params.get(p, res.get(p))}: حد اخترناه للتقرير وليس رقماً معيارياً.")
    flags_ar = {"currency_mixed": "عملات مختلفة في الملف.", "no_conversions_column": "الملف ما فيه عمود تحويلات.",
                "mixed_attribution": "إعدادات إسناد مختلفة بين الصفوف؛ المقارنة بينها غير دقيقة.",
                "recent_days_excluded": "استبعدنا الأيام الأخيرة من الحكم بعدم التحويل (تأخر التحويلات).",
                "recent_days_excluded_from_zero_conversion_checks": "استبعدنا الأيام الأخيرة من الحكم بعدم التحويل (تأخر التحويلات).",
                "conversion_lag_unknown": "ما نعرف تاريخ التصدير؛ تحويلات متأخرة قد تغيّر النتيجة.",
                "brand_terms_not_given": "ما عندنا اسم المتجر كبحث؛ ما فصلنا بحث الاسم عن غيره.",
                "several_objectives": "أهداف حملات مختلفة؛ ما قارنا تكلفة النتيجة بينها.",
                "too_little_data_for_some_ads": "بعض الإعلانات بياناتها قليلة وما حكمنا عليها."}
    lim += [flags_ar[f] for f in res.get("flags", []) if f in flags_ar]
    if ev["coverage"].get("complete") is not True:
        lim.append("البيانات جزئية أو غير معروفة الاكتمال: " + coverage_note_ar(ev))
    high = any(i.get("severity") == "high" for i in res.get("issues", []))
    return {
        "finding_id": fid, "agent": AGENT_OF[kind], "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": f"ads_{kind}", "entity": None,
        "period": {"current": ev.get("period") or "export", "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": obs, "interpretation_ar": interp, "alternatives_ar": [],
        "confidence": {"level": "medium" if ev["coverage"].get("complete") is True else "low",
                       "rationale_ar": "حساب حتمي من ملف الإعلانات؛ " + coverage_note_ar(ev)},
        "priority": "high" if high else "medium", "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً؛ لا نعد بتحسن العائد."},
        "requires": None, "claims_cause": False, "limitations_ar": lim,
        "details": {k: v for k, v in res.items() if k not in ("metrics", "kind")},
    }


def run(kind: str, rows: list[dict], params: dict) -> dict:
    if kind not in ANALYSES:
        raise ValueError(kind)
    return ANALYSES[kind](normalize_rows(rows, params.get("platform")),
                          **{k: v for k, v in params.items() if k != "platform"})


def recompute(records: list[dict], calc: dict):
    return run(calc["analysis"], records, calc.get("params") or {})["metrics"].get(calc["metric"])
