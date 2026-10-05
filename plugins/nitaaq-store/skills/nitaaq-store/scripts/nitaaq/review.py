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

from . import ads, contracts, cro, customer_intel, growth, metrics, pricing, retention, seo_team, tracking
from .arabic import normalize
from .evidence import CrossStoreError, EvidenceStore
from .writes import same_value

CAUSAL = [normalize(x) for x in ("بسبب", "السبب هو", "السبب الرئيسي", "سببه", "أدى إلى", "ادى الى", "نتج عن",
                                 "هو اللي نزل", "هو سبب", "تسبب", "because")]
BENCHMARK = re.compile(r"(المعدل الطبيعي|المعدل المعتاد|متوسط السوق|متوسط القطاع|المعيار|المتاجر المشابهة|benchmark|"
                       r"industry average|عادة ما يكون|النسبة الطبيعية)", re.I)
GUARANTEE = re.compile(r"(مضمون|نضمن|أكيد بي|اكيد بي|بالتأكيد سي|guarantee|guaranteed)", re.I)
# A predicted effect stated as a number needs stated assumptions (expected_effect.kind == "range" with assumptions_ar).
FORECAST = re.compile(r"(ستزيد|سيزيد|بتزيد|بيزيد|راح تزيد|راح يزيد|سترتفع|سيرتفع|بترتفع|بيرتفع|ستتضاعف|will increase|will grow)"
                      r"[^.؟!\n]{0,40}?\d+\s*%", re.I)
ELASTICITY = re.compile(r"(مرونه سعريه|مرونة سعرية|المرونة السعرية|elasticity)", re.I)
DARK_PATTERN = re.compile(r"(اضف|أضف|حط|ضع|استخدم|فعّل|فعل)[^.\n]{0,25}(عداد تنازلي|عدادا تنازليا|باقي \d+ قطع|كمية محدودة|"
                          r"تقييمات (?:إضافية|اضافية|وهمية|مكتوبة)|ندرة|fake urgency|countdown)", re.I)
# metric -> evidence kinds it cannot exist without
METRIC_NEEDS = {"conversion_rate": {"traffic", "analytics"}, "funnel": {"traffic", "analytics"},
                "cac": {"ads"}, "roas": {"ads"}, "tracking_reconciliation": {"analytics", "ads"},
                **{f"ads_{k}": {"ads"} for k in ads.ANALYSES}}
ROAS_WORD = re.compile(r"(roas|العائد على الإنفاق|العائد على الانفاق|عائد الإنفاق|عائد الانفاق)", re.I)
PROFIT_CLAIM = re.compile(r"(مربح|مربحة|ربحانة|ربحانه|تربح|profitable)", re.I)
# Ad account changes are proposals; a finding never says they were made.
ADS_EXECUTED = re.compile(r"(أوقفنا|اوقفنا|أوقفت الحمل|اوقفت الحمل|أضفنا الكلمات|اضفنا الكلمات|طبقنا|غيرنا الميزانية|"
                          r"غيّرنا الميزانية|رفعنا الميزانية|نزلنا الميزانية)", re.I)
# Messages to customers are drafts until the send gate passes; a finding never says they went out.
MESSAGE_SENT = re.compile(r"(أرسلنا|ارسلنا|تم إرسال|تم ارسال|انرسلت|راحت الرسالة|وصلت الرسالة للعملاء)", re.I)
# "Tracking is broken" needs a captured real purchase that fired no purchase event (reconcile status event_missing).
TRACKING_WORD = re.compile(r"(تتبع|التتبع|بكسل|البكسل|pixel|ga4|capi|tracking)", re.I)
TRACKING_FAULT = re.compile(r"(خربان|خربانه|خربانة|معطل|متعطل|عطل|لا يعمل|ما يشتغل|مايشتغل|broken|not working)", re.I)
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
    "unsupported_forecast": "توقع رقمي للنتيجة بدون افتراضات معلنة.",
    "inferred_elasticity": "مرونة سعرية مذكورة بدون تجربة أو مصدر؛ لا نستنتجها من البيانات المتاحة.",
    "dark_pattern": "اقتراح استعجال أو ندرة أو تقييمات غير حقيقية؛ ممنوع.",
    "metric_without_data": "مقياس لا يمكن حسابه بدون بياناته (مثل معدل التحويل بدون زيارات).",
    "small_sample_not_disclosed": "العينة صغيرة ولم يُذكر ذلك.",
    "tracking_fault_unsupported": "حكم بأن التتبع معطل بدون رصد عملية شراء حقيقية لم يُرسل فيها الحدث؛ الفرق دليل للتحقيق فقط.",
    "totals_not_disclosed": "مقارنة مجاميع بدون ذكر أنها لا تثبت أي طلب مفقود.",
    "roas_as_profit": "العائد على الإنفاق الإعلاني مقدم كربح؛ الربحية تحتاج الهامش.",
    "claimed_ads_execution": "يقول إن تغييراً نُفذ في الحساب الإعلاني؛ تغييرات الإعلانات اقتراحات بموافقة فقط.",
    "claimed_message_sent": "يقول إن رسائل أُرسلت للعملاء؛ الرسائل مسودات حتى تمر بالموافقة وموافقة العميل.",
}
BLOCKING = {"schema", "wrong_store", "evidence_missing", "number_mismatch", "invalid_comparison", "unsupported_cause",
            "high_confidence_cause", "unsourced_benchmark", "guarantee", "partial_not_disclosed",
            "unsupported_forecast", "inferred_elasticity", "dark_pattern", "metric_without_data",
            "small_sample_not_disclosed", "tracking_fault_unsupported", "totals_not_disclosed",
            "roas_as_profit", "claimed_ads_execution", "claimed_message_sent"}


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
    if calc["fn"] == "reconcile_report":
        res = {k: metrics.reconcile_report(records, calc[k], calc[f"reported_{k}"], statuses=calc.get("statuses"))
               for k in ("current", "baseline")}
        field = calc.get("field", "gap")
        return res, res["current"][field], res["baseline"][field], ev
    if calc["fn"] == "decompose":
        r = metrics.decompose(records, calc["current"], calc["baseline"], calc["dimension"],
                              definition=calc.get("definition", "order_total"), statuses=calc.get("statuses"), top=10 ** 6)
        row = next((x for x in r["rows"] if x["value"] == calc["value"]), None)
        if row is None:
            return r, None, None, ev
        return r, row["current"], row["baseline"], ev
    if calc["fn"] == "product_economics":
        r = pricing.product_economics(records, prices_include_vat=calc.get("prices_include_vat"))
        if calc.get("field") == "cost_missing_count":
            return r, r["counts"]["cost_missing"], None, ev
        row = next((x for x in r["rows"] if x["id"] == str(calc.get("entity"))), None)
        return r, (row or {}).get(calc["field"]), None, ev
    if calc["fn"] == "realized_price":
        c = pricing.realized_prices(records, calc["current"], statuses=calc.get("statuses")).get(calc["product"], {})
        b = pricing.realized_prices(records, calc["baseline"], statuses=calc.get("statuses")).get(calc["product"], {})
        return None, c.get("avg_unit_price"), b.get("avg_unit_price"), ev
    if calc["fn"] == "customer_mix":
        r = growth.mix_change(records, calc["current"], calc["baseline"], statuses=calc.get("statuses"))
        return r, r["current"][calc["field"]], r["baseline"][calc["field"]], ev
    if calc["fn"] == "page_checks":
        rec = next((x for x in records if (x.get("url") or x.get("id")) == calc["url"]), None)
        if rec is None:
            raise KeyError(calc["url"])
        return None, cro.check_value(rec, calc["check"]), None, ev
    if calc["fn"] == "seo_audit":
        rec = next((x for x in records if (x.get("url") or x.get("id")) == calc["url"]), None)
        if rec is None:
            raise KeyError(calc["url"])
        return None, calc["audit_id"] if calc["audit_id"] in seo_team.audit_ids(rec) else None, None, ev
    if calc["fn"] == "cannibalization_gsc":
        c = next((x for x in seo_team.cannibalization_gsc(records) if x["query"] == calc["query"]), None)
        return None, len(c["pages"]) if c else None, None, ev
    if calc["fn"] == "cannibalization_titles":
        c = next((x for x in seo_team.cannibalization_titles(records, threshold=calc.get("threshold", 0.8)) if {x["a"], x["b"]} == {calc["a"], calc["b"]}), None)
        return None, c["similarity"] if c else None, None, ev
    if calc["fn"] == "review_themes":
        return None, customer_intel.recompute(records, calc), None, ev
    if calc["fn"] == "retention_segments":
        return None, retention.recompute(records, calc), None, ev
    if calc["fn"] == "ads":
        return None, ads.recompute(records, calc), None, ev
    if calc["fn"] == "tracking_reconcile":
        _, conv = store.load(calc["platform_evidence"])
        return None, tracking.recompute(records, conv, calc), None, ev
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
    eff = f.get("expected_effect") or {}
    if FORECAST.search(text) and not (eff.get("kind") == "range" and eff.get("assumptions_ar")):
        issues.append(_issue("unsupported_forecast"))
    if ELASTICITY.search(text) and not f.get("elasticity_source"):
        issues.append(_issue("inferred_elasticity"))
    if DARK_PATTERN.search(text):
        issues.append(_issue("dark_pattern"))
    if f.get("metric") == "review_themes":
        n = next((o.get("current") for o in f.get("observed") or [] if (o.get("calc") or {}).get("field") == "sample_size"), None)
        if n is not None and int(str(n)) < customer_intel.SMALL_SAMPLE and "عينة صغيرة" not in text:
            issues.append(_issue("small_sample_not_disclosed", str(n)))
    truth = _tracking_truth(f, store)
    interp = f.get("interpretation_ar", "")
    if TRACKING_WORD.search(interp) and TRACKING_FAULT.search(interp) and (truth or {}).get("status") != "event_missing":
        issues.append(_issue("tracking_fault_unsupported"))
    if truth and truth["mode"] == "totals" and "لا تثبت أي طلب مفقود" not in text:
        issues.append(_issue("totals_not_disclosed"))
    if ROAS_WORD.search(interp) and PROFIT_CLAIM.search(interp) and not f.get("margin_ref"):
        issues.append(_issue("roas_as_profit"))
    if ADS_EXECUTED.search(interp):
        issues.append(_issue("claimed_ads_execution"))
    if MESSAGE_SENT.search(interp):
        issues.append(_issue("claimed_message_sent"))
    need = METRIC_NEEDS.get(f.get("metric"))
    if need and not any(e.get("kind") in need for e in evs):
        issues.append(_issue("metric_without_data", f.get("metric")))
    blocking = any(i["severity"] == "blocking" for i in issues)
    status = "fail" if any(i["code"] in ("schema", "wrong_store", "evidence_missing", "number_mismatch") for i in issues) \
        else ("revise" if blocking else "pass")
    return {"finding_id": f.get("finding_id"), "status": status, "issues": issues, "mode": mode}


def _tracking_truth(f: dict, store: EvidenceStore) -> dict | None:
    """The reconciliation recomputed from the finding's own evidence, so its status and mode are not taken on trust."""
    calc = next((o["calc"] for o in f.get("observed") or []
                 if isinstance(o.get("calc"), dict) and o["calc"].get("fn") == "tracking_reconcile"), None)
    if not calc:
        return None
    try:
        _, orders = store.load(calc["evidence"])
        _, conv = store.load(calc["platform_evidence"])
        return tracking.reconcile_from_calc(orders, conv, calc)
    except (ValueError, KeyError, FileNotFoundError, CrossStoreError):
        return None


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

