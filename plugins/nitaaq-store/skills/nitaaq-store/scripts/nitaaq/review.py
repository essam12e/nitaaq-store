"""Review findings against the evidence they cite.

The reviewer does not trust a finding's text. It reloads the cited evidence
(same store only), recomputes numbers that declare how they were computed,
and rejects: invalid comparisons presented as real changes, causes stated
without mechanism evidence, invented benchmarks or guarantees, and partial
data that the finding does not disclose. Agreement between findings that
cite the same evidence is reported as not independent.

mode="independent_context" is recorded only when the review really ran in a
separate agent context (a native subagent). Otherwise it is "self_check".
"""

from __future__ import annotations

import re

from . import contracts, metrics
from .arabic import normalize
from .evidence import CrossStoreError, EvidenceStore
from .writes import same_value

CAUSAL = [normalize(x) for x in ("بسبب", "السبب هو", "السبب الرئيسي", "سببه", "أدى إلى", "ادى الى", "نتج عن",
                                 "هو اللي نزل", "هو سبب", "تسبب", "because")]
BENCHMARK = re.compile(r"(المعدل الطبيعي|المعدل المعتاد|متوسط السوق|متوسط القطاع|المعيار|المتاجر المشابهة|benchmark|"
                       r"industry average|عادة ما يكون|النسبة الطبيعية)", re.I)
GUARANTEE = re.compile(r"(مضمون|نضمن|أكيد بي|اكيد بي|بالتأكيد سي|guarantee|guaranteed)", re.I)
PARTIAL_WORDS = ("جزئي", "جزئية", "ناقص", "غير مكتمل", "غير معروفة الاكتمال", "partial")

ISSUES_AR = {
    "schema": "الاستنتاج ناقص الحقول المطلوبة.",
    "wrong_store": "الاستنتاج أو دليله من متجر آخر.",
    "evidence_missing": "الدليل المذكور غير موجود.",
    "number_mismatch": "رقم في الاستنتاج لا يطابق إعادة الحساب من الدليل.",
    "invalid_comparison": "المقارنة غير صالحة (يوم جزئي أو فترات غير متساوية أو بيانات ناقصة) ومع ذلك قُدّمت كتغير حقيقي.",
    "unsupported_cause": "ذُكر سبب بدون دليل على الآلية؛ يجب أن يُكتب كاحتمال مع بدائل.",
    "high_confidence_cause": "ثقة عالية في سبب غير مثبت.",
    "no_alternatives": "لا توجد تفسيرات بديلة.",
    "unsourced_benchmark": "رقم معياري أو «طبيعي» بدون مصدر.",
    "guarantee": "وعد بنتيجة مضمونة.",
    "partial_not_disclosed": "الدليل جزئي ولم يُذكر ذلك في الاستنتاج.",
    "calc_unsupported": "طريقة الحساب المذكورة غير مدعومة لإعادة الحساب.",
    "not_independent": "استنتاجان يتفقان لكنهما يعتمدان على نفس الدليل؛ هذا ليس تأكيداً مستقلاً.",
}
BLOCKING = {"schema", "wrong_store", "evidence_missing", "number_mismatch", "invalid_comparison", "unsupported_cause",
            "high_confidence_cause", "unsourced_benchmark", "guarantee", "partial_not_disclosed"}


def _issue(code: str, detail: str = "") -> dict:
    return {"code": code, "severity": "blocking" if code in BLOCKING else "warning",
            "message_ar": ISSUES_AR[code] + (f" ({detail})" if detail else "")}


def _texts(f: dict) -> str:
    parts = [f.get("interpretation_ar", ""), f.get("coverage_note_ar", "")]
    parts += f.get("alternatives_ar") or []
    parts += f.get("limitations_ar") or []
    parts += [(f.get("expected_effect") or {}).get("range_ar", ""), (f.get("confidence") or {}).get("rationale_ar", "")]
    return " ".join(p for p in parts if p)


def _recompute(calc: dict, store: EvidenceStore):
    ev, records = store.load(calc["evidence"])
    if calc["fn"] == "compare_periods":
        r = metrics.compare_periods(records, calc["current"], calc["baseline"],
                                    definition=calc.get("definition", "order_total"), statuses=calc.get("statuses"),
                                    records_total=ev["coverage"].get("records_total"),
                                    now=_now_or_none(calc.get("now")))
        field = calc.get("field", "sales")
        return r, r["current"][field], r["baseline"][field], ev
    if calc["fn"] == "decompose":
        r = metrics.decompose(records, calc["current"], calc["baseline"], calc["dimension"],
                              definition=calc.get("definition", "order_total"), statuses=calc.get("statuses"), top=10 ** 6)
        row = next((x for x in r["rows"] if x["value"] == calc["value"]), None)
        if row is None:
            return r, None, None, ev
        return r, row["current"], row["baseline"], ev
    raise ValueError(calc["fn"])


def _now_or_none(v):
    from datetime import datetime
    return datetime.fromisoformat(v) if v else None


def review_finding(f: dict, store: EvidenceStore, mode: str = "self_check") -> dict:
    issues: list[dict] = []
    errs = contracts.validate(f, "finding")
    if errs:
        issues.append(_issue("schema", "; ".join(errs[:3])))
    if str(f.get("store_id")) != store.store_id:
        issues.append(_issue("wrong_store"))
    evs = []
    for ref in f.get("evidence_refs") or []:
        try:
            evs.append(store.load(ref)[0])
        except CrossStoreError:
            issues.append(_issue("wrong_store", ref))
        except (FileNotFoundError, ValueError):
            issues.append(_issue("evidence_missing", ref))
    text = _texts(f)
    partial = [e for e in evs if e["coverage"].get("complete") is not True]
    if partial and not any(w in text for w in PARTIAL_WORDS):
        issues.append(_issue("partial_not_disclosed", ", ".join(e["evidence_id"] for e in partial)))
    for ob in f.get("observed") or []:
        calc = ob.get("calc")
        if not isinstance(calc, dict):
            continue
        try:
            result, cur, base, _ = _recompute(calc, store)
        except (ValueError, KeyError, FileNotFoundError, CrossStoreError):
            issues.append(_issue("calc_unsupported", str(calc.get("fn"))))
            continue
        for label, claimed, actual in (("current", ob.get("current"), cur), ("baseline", ob.get("baseline"), base)):
            if claimed is not None and not same_value(claimed, actual if actual is not None else ""):
                issues.append(_issue("number_mismatch", f"{ob.get('label_ar')}: {label} {claimed} ≠ {actual}"))
        if calc["fn"] == "compare_periods" and result["status"] == "not_comparable" and \
                f.get("priority") == "high" and "غير صالحة" not in text and "لا يمكن" not in text:
            issues.append(_issue("invalid_comparison", "، ".join(result["flags"])))
    # causal wording is judged on the interpretation only; limitations may legitimately say "بسبب نقص البيانات"
    causal = bool(f.get("claims_cause")) or any(c in normalize(f.get("interpretation_ar", "")) for c in CAUSAL)
    if causal and not f.get("mechanism_evidence_refs"):
        issues.append(_issue("unsupported_cause"))
        if (f.get("confidence") or {}).get("level") == "high":
            issues.append(_issue("high_confidence_cause"))
    if causal and not f.get("alternatives_ar"):
        issues.append(_issue("no_alternatives"))
    if BENCHMARK.search(text) and not f.get("benchmark_source"):
        issues.append(_issue("unsourced_benchmark"))
    if GUARANTEE.search(text):
        issues.append(_issue("guarantee"))
    blocking = any(i["severity"] == "blocking" for i in issues)
    status = "fail" if any(i["code"] in ("schema", "wrong_store", "evidence_missing", "number_mismatch") for i in issues) \
        else ("revise" if blocking else "pass")
    return {"finding_id": f.get("finding_id"), "status": status, "issues": issues, "mode": mode}


def review_all(findings: list[dict], store: EvidenceStore, mode: str = "self_check") -> dict:
    results = [review_finding(f, store, mode) for f in findings]
    # agreement on the same evidence is not independent corroboration
    by_refs: dict = {}
    for f in findings:
        by_refs.setdefault((f.get("metric"), tuple(sorted(f.get("evidence_refs") or []))), []).append(f)
    notes = []
    for (_, refs), group in by_refs.items():
        agents = {g.get("agent") for g in group}
        if len(group) > 1 and len(agents) > 1:
            notes.append(_issue("not_independent", "، ".join(sorted(a for a in agents if a))))
    counts: dict = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"mode": mode, "results": results, "batch_notes": notes, "counts": counts}

