"""SEO/GEO specialist: a thin wrapper over the existing audit, plus internal competition.

The audit itself is unchanged: page_findings() calls seo_audit.parse_page and
seo_audit.audit_page exactly as the `audit-html` command does and only
re-packages the result as standard findings so the reviewer and the unified
report can use it.

Internal competition (cannibalization) is checked before any title change:
  - from Search Console rows that have both query and page: one query that
    sends impressions to more than one of the store's pages;
  - otherwise from titles: pages or products whose titles are nearly the same.
Both are candidates for the merchant to look at, not proof of lost rankings.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from . import seo_audit
from .arabic import normalize, parse_amount, tokens
from .evidence import coverage_note_ar

SEVERITY_PRIORITY = {"high": "high", "medium": "medium", "low": "low", "info": "low"}


def audit(html: str, url: str, page_type: str | None = None, status: int | None = 200) -> list[dict]:
    """Exactly the existing audit (same call path as `audit-html`)."""
    f = seo_audit.parse_page(html, url, status)
    return seo_audit.audit_page(f, page_type or seo_audit.guess_page_type(url, f))


def page_findings(record: dict, ev: dict, *, start: int = 1) -> list[dict]:
    """Wrap existing audit findings for one saved page as standard findings, one per audit finding."""
    raw = audit(record.get("html") or "", record.get("url") or record["id"], record.get("page_type"), record.get("status", 200))
    out = []
    for i, a in enumerate(raw, start=start):
        out.append({
            "finding_id": f"s{i}", "agent": "seo_geo", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
            "metric": "seo_check", "entity": {"type": "page", "id": a.get("url") or record.get("url"), "label": a["area"]},
            "period": {"current": "now", "baseline": None, "timezone": "Asia/Riyadh"},
            "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
            "observed": [{"label_ar": a["message_ar"], "current": a["id"], "baseline": None,
                          "calc": {"fn": "seo_audit", "evidence": ev["evidence_id"], "url": record.get("url") or record["id"],
                                   "audit_id": a["id"]}}],
            "interpretation_ar": a["message_ar"] + (f" المقترح: {a['fix_ar']}" if a.get("fix_ar") else ""),
            "alternatives_ar": [], "confidence": {"level": "medium",
                                                  "rationale_ar": "فحص عام للصفحة كما وصلت؛ لا يثبت حالة الفهرسة الفعلية."},
            "priority": SEVERITY_PRIORITY.get(a["severity"], "medium"), "proposed_action_ref": None,
            "expected_effect": {"kind": "unknown", "range_ar": "لا نعد بترتيب أو ظهور في أي محرك أو منصة."},
            "requires": None, "claims_cause": False, "audit": a,
            "limitations_ar": ["فحص عام للصفحة؛ حالة الفهرسة الفعلية تأتي من Search Console فقط."],
        })
    return out


def audit_ids(record: dict) -> list[str]:
    return [a["id"] for a in audit(record.get("html") or "", record.get("url") or record["id"],
                                   record.get("page_type"), record.get("status", 200))]


# ------------------------------------------------------------------ internal competition

def cannibalization_gsc(rows: list[dict], *, min_impressions: int = 20, min_share: Decimal | int = 10) -> list[dict]:
    """Queries whose impressions split across 2+ store pages, each with at least min_share% of the query's impressions.

    rows: [{query, page, clicks, impressions, position}] (a Search Console export or API rows with both dimensions).
    """
    by_q: dict = defaultdict(lambda: defaultdict(lambda: {"clicks": Decimal(0), "impressions": Decimal(0), "position": None}))
    for r in rows:
        q, p = (r.get("query") or "").strip(), (r.get("page") or "").strip()
        if not q or not p:
            continue
        a = by_q[normalize(q)][p]
        a["clicks"] += parse_amount(r.get("clicks")) or 0
        a["impressions"] += parse_amount(r.get("impressions")) or 0
        a["position"] = parse_amount(r.get("position")) if r.get("position") not in (None, "") else a["position"]
        a["query"] = q
    out = []
    for q, pages in by_q.items():
        total = sum(v["impressions"] for v in pages.values())
        if total < min_impressions:
            continue
        sig = [(p, v) for p, v in pages.items() if v["impressions"] and v["impressions"] / total * 100 >= Decimal(str(min_share))]
        if len(sig) >= 2:
            out.append({"query": next(iter(pages.values()))["query"], "impressions": total,
                        "pages": [{"page": p, "impressions": v["impressions"], "clicks": v["clicks"], "position": v["position"],
                                   "share_pct": (v["impressions"] / total * 100).quantize(Decimal("0.1"))}
                                  for p, v in sorted(sig, key=lambda x: -x[1]["impressions"])]})
    return sorted(out, key=lambda x: -x["impressions"])


def cannibalization_titles(items: list[dict], *, threshold: float = 0.8) -> list[dict]:
    """Pairs of pages/products whose titles share most of their words (Jaccard >= threshold).

    items: [{id or url, title or name}]. Different sizes or colours of the same product are expected
    pairs; the merchant decides which ones compete.
    """
    toks = []
    for it in items:
        title = it.get("title") or it.get("name") or ""
        t = set(tokens(title))
        if len(t) >= 2:
            toks.append((str(it.get("url") or it.get("id")), title, t))
    out = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i][2], toks[j][2]
            sim = len(a & b) / len(a | b)
            if sim >= threshold:
                out.append({"a": toks[i][0], "a_title": toks[i][1], "b": toks[j][0], "b_title": toks[j][1],
                            "similarity": round(sim, 2)})
    return sorted(out, key=lambda x: -x["similarity"])


def cannibalization_finding(cands: list[dict], ev: dict, *, basis: str, fid: str = "s0",
                            threshold: float = 0.8) -> dict | None:
    if not cands:
        return None
    if basis == "gsc":
        obs = [{"label_ar": c["query"], "current": str(len(c["pages"])), "baseline": None,
                "calc": {"fn": "cannibalization_gsc", "evidence": ev["evidence_id"], "query": c["query"]}} for c in cands[:15]]
        interp = (f"{len(cands)} طلب بحث يتوزع ظهوره على أكثر من صفحة في متجرك. "
                  "قبل تعديل أي عنوان، حدد صفحة وحدة لكل طلب بحث حتى ما تتنافس صفحاتك مع بعض.")
    else:
        obs = [{"label_ar": f"{c['a_title']} / {c['b_title']}", "current": str(c["similarity"]), "baseline": None,
                "calc": {"fn": "cannibalization_titles", "evidence": ev["evidence_id"], "a": c["a"], "b": c["b"],
                         "threshold": threshold}}
               for c in cands[:15]]
        interp = (f"{len(cands)} زوج من الصفحات أو المنتجات عناوينها شبه متطابقة. "
                  "راجعها قبل تعديل العناوين؛ بعضها طبيعي (مقاسات أو ألوان) وبعضها قد يتنافس على نفس البحث.")
    return {
        "finding_id": fid, "agent": "seo_geo", "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "cannibalization", "entity": None,
        "period": {"current": "now", "baseline": None, "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": obs, "interpretation_ar": interp,
        "alternatives_ar": ["صفحات مختلفة تخدم نوايا مختلفة لنفس الكلمات", "تكرار طبيعي لخيارات نفس المنتج"],
        "confidence": {"level": "medium" if basis == "gsc" else "low",
                       "rationale_ar": "من بيانات Search Console" if basis == "gsc" else "تشابه نصي في العناوين فقط، بدون بيانات بحث"},
        "priority": "medium", "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "لا نعد بتحسن الترتيب."}, "requires": None,
        "claims_cause": False,
        "limitations_ar": ["مرشحات للمراجعة وليست إثباتاً لخسارة ترتيب."] +
                          (["بيانات Search Console تخص الفترة والفلاتر المصدّرة فقط."] if basis == "gsc" else
                           ["بدون Search Console ما نعرف هل الصفحتين تظهران فعلاً لنفس البحث."]),
    }
