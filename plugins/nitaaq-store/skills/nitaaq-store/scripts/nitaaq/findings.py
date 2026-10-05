"""Build standard findings from deterministic analysis, and render one Arabic report.

The merchant gets one answer: facts, likely causes (as possibilities with
alternatives and confidence), what we do not know, priorities and proposals
that still need approval, and how the analysis was done. Findings that failed
review are not presented as facts.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .evidence import coverage_note_ar

TWO = Decimal("0.01")
STATUS_AR = {"decline": "انخفاض", "increase": "ارتفاع", "flat": "ثابت تقريباً", "not_comparable": "مقارنة غير صالحة",
             "unknown": "غير معروف"}


def _m(v) -> str:
    return "غير متوفر" if v is None else f"{Decimal(str(v)).quantize(TWO, ROUND_HALF_UP):,}"


def _p(period) -> str:
    return f"{period[0]} إلى {period[1]}" if isinstance(period, (list, tuple)) and len(period) == 2 else str(period)


def sales_change_finding(cmp: dict, ev: dict, *, finding_id: str = "f1", agent: str = "store_analytics",
                         definition: str = "order_total") -> dict:
    """Finding for "did sales change?" from metrics.compare_periods() and its evidence."""
    status = cmp["status"]
    ch = cmp["change"]
    limitations = list(cmp["flags_ar"])
    if ev["coverage"].get("complete") is not True:
        limitations.append("البيانات جزئية أو غير معروفة الاكتمال: " + coverage_note_ar(ev))
    if status == "not_comparable":
        interp = "لا يمكن الحكم على تغير المبيعات من هذه المقارنة؛ المقارنة غير صالحة حتى تُصحَّح الفترات أو تكتمل البيانات."
        conf = {"level": "low", "rationale_ar": "المقارنة غير صالحة: " + "، ".join(cmp["flags"])}
        priority = "medium"
    else:
        verb = {"decline": "انخفضت", "increase": "ارتفعت"}.get(status)
        if ch["sales_pct"] is None:
            interp = "لا تتوفر قيمة كافية للمقارنة."
        elif verb:
            interp = f"{verb} المبيعات {abs(Decimal(str(ch['sales_pct'])))}% مقارنة بالفترة السابقة."
        else:
            interp = f"المبيعات ثابتة تقريباً (تغير {ch['sales_pct']}%) مقارنة بالفترة السابقة."
        d = cmp.get("decomposition")
        if d:
            interp += f" حسابياً: {_m(d['order_count_effect'])} من تغير عدد الطلبات، و{_m(d['order_value_effect'])} من تغير متوسط قيمة الطلب."
        level = "high" if not cmp["flags"] or cmp["flags"] == ["duplicates_removed"] else "medium"
        conf = {"level": level, "rationale_ar": "أرقام محسوبة حتمياً من طلبات " + coverage_note_ar(ev) +
                ("؛ مع تنبيهات: " + "، ".join(cmp["flags"]) if cmp["flags"] else "")}
        priority = "high" if status == "decline" else "medium"
    calc = {"fn": "compare_periods", "evidence": ev["evidence_id"], "current": cmp["current"]["period"],
            "baseline": cmp["baseline"]["period"], "definition": definition, "statuses": cmp.get("statuses")}
    return {
        "finding_id": finding_id, "agent": agent, "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "gmv" if definition == "order_total" else "net_sales", "entity": None,
        "period": {"current": cmp["current"]["period"], "baseline": cmp["baseline"]["period"], "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": [
            {"label_ar": "المبيعات", "current": str(cmp["current"]["sales"]), "baseline": str(cmp["baseline"]["sales"]),
             "change_pct": str(ch["sales_pct"]), "calc": {**calc, "field": "sales"}},
            {"label_ar": "عدد الطلبات", "current": cmp["current"]["orders"], "baseline": cmp["baseline"]["orders"],
             "change_pct": str(ch["orders_pct"]), "calc": {**calc, "field": "orders"}},
        ],
        "interpretation_ar": interp,
        "alternatives_ar": [],
        "confidence": conf, "priority": priority, "proposed_action_ref": None,
        "expected_effect": {"kind": "unknown", "range_ar": "هذا قياس لما حدث وليس توقعاً."},
        "requires": None, "limitations_ar": limitations, "claims_cause": False, "comparison_status": status,
    }


def where_finding(dec: dict, ev: dict, cmp: dict, *, finding_id: str = "f2", agent: str = "store_analytics",
                  top: int = 3) -> dict | None:
    """Finding for "where did the change happen?" from metrics.decompose()."""
    rows = [r for r in dec["rows"] if r["change"] != 0][:top]
    if not rows or dec["total_change"] == 0:
        return None
    dim_ar = {"product": "المنتجات", "category": "التصنيفات", "city": "المدن", "payment_method": "طرق الدفع",
              "brand": "الماركات"}.get(dec["dimension"], dec["dimension"])
    parts = [f"{r['value']} ({_m(r['change'])}، {r['share_of_change_pct']}% من التغير)" for r in rows]
    calc = {"fn": "decompose", "evidence": ev["evidence_id"], "current": cmp["current"]["period"],
            "baseline": cmp["baseline"]["period"], "dimension": dec["dimension"], "statuses": cmp.get("statuses")}
    return {
        "finding_id": finding_id, "agent": agent, "store_id": ev["store_id"], "account_id": ev.get("account_id"),
        "metric": "item_revenue" if dec["basis"] == "item_revenue" else "gmv",
        "entity": {"type": dec["dimension"], "id": None, "label": dim_ar},
        "period": {"current": cmp["current"]["period"], "baseline": cmp["baseline"]["period"], "timezone": "Asia/Riyadh"},
        "evidence_refs": [ev["evidence_id"]], "coverage_note_ar": coverage_note_ar(ev), "freshness": ev["fetched_at"],
        "observed": [{"label_ar": str(r["value"]), "current": str(r["current"]), "baseline": str(r["baseline"]),
                      "change_pct": str(r["share_of_change_pct"]), "calc": {**calc, "value": r["value"]}} for r in rows],
        "interpretation_ar": f"التغير يتركز في {dim_ar}: " + "؛ ".join(parts) + ". هذا يحدد أين حدث التغير، لا لماذا.",
        "alternatives_ar": [],
        "confidence": {"level": "high" if ev["coverage"].get("complete") is True else "medium",
                       "rationale_ar": "تفكيك حسابي مباشر من نفس الطلبات؛ " + coverage_note_ar(ev)},
        "priority": "high" if cmp["status"] == "decline" else "medium",
        "proposed_action_ref": None, "expected_effect": {"kind": "unknown", "range_ar": "قياس وليس توقعاً."},
        "requires": None,
        "limitations_ar": ([] if ev["coverage"].get("complete") is True else ["البيانات جزئية أو غير معروفة الاكتمال."]) +
                          (["أساس الحساب: إيراد بنود المنتجات وليس إجمالي الطلبات."] if dec["basis"] == "item_revenue" else []),
        "claims_cause": False,
    }


def stock_signals(product_rows: list[dict], inventory: dict) -> list[str]:
    """stock_out when a product among the biggest drops has no stock now. inventory: {label or id: qty}."""
    for r in product_rows:
        if r["change"] >= 0:
            continue
        for k, q in inventory.items():
            if str(k) in str(r["value"]) and q is not None and Decimal(str(q)) <= 0:
                return ["stock_out"]
    return []


def unified_report_ar(question: str, plan: dict, findings: list[dict], review: dict | None,
                      proposals: list[dict] | None = None, run: dict | None = None) -> str:
    rev = {r["finding_id"]: r for r in (review or {}).get("results", [])}
    ok = [f for f in findings if rev.get(f["finding_id"], {}).get("status", "pass") != "fail"]
    failed = [f for f in findings if rev.get(f["finding_id"], {}).get("status") == "fail"]
    out = [f"## {question.strip()}", ""]
    lead = next((f for f in ok if f.get("priority") == "high"), ok[0] if ok else None)
    if plan.get("blocker_ar"):
        out += [f"**ما نقدر نكمل:** {plan['blocker_ar']}", ""]
    if lead:
        out += ["**الخلاصة:** " + lead["interpretation_ar"], ""]
    out += ["### الحقائق", ""]
    for f in ok:
        flag = " ⚠️ تحتاج مراجعة" if rev.get(f["finding_id"], {}).get("status") == "revise" else ""
        out.append(f"**{f['interpretation_ar']}**{flag}")
        out.append(f"الفترة: {_p(f['period'].get('current'))} مقابل {_p(f['period'].get('baseline'))}. التغطية: {f['coverage_note_ar']}")
        out += ["", "| البند | الحالي | السابق |", "|---|---|---|"]
        for o in f["observed"]:
            out.append(f"| {o['label_ar']} | {o.get('current', '—')} | {o.get('baseline', '—')} |")
        out.append("")
    causes = [f for f in ok if f.get("alternatives_ar") or f.get("claims_cause")]
    out += ["### الأسباب المحتملة", ""]
    if causes:
        for f in causes:
            out.append(f"- {f['interpretation_ar']} (الثقة: {f['confidence']['level']} — {f['confidence']['rationale_ar']})")
            for a in f.get("alternatives_ar") or []:
                out.append(f"  - احتمال آخر: {a}")
    else:
        out.append("- ما عندنا دليل كافٍ يحدد السبب؛ الأرقام أعلاه توضح أين حدث التغير فقط.")
    out += ["", "### ما لا نعرفه", ""]
    unknowns = list(plan.get("unknowns_ar") or [])
    for f in ok:
        unknowns += [x for x in f.get("limitations_ar") or [] if x not in unknowns]
    for s in plan.get("stages", []) + plan.get("followups", []):
        if s.get("status") in ("blocked", "not_available_yet"):
            unknowns.append(f"{s['label_ar']}: {s['reason_ar']}")
    out += [f"- {u}" for u in unknowns] or ["- لا شيء جوهري."]
    out += ["", "### المقترحات", ""]
    if proposals:
        out += ["| المقترح | العناصر | يحتاج موافقتك |", "|---|---|---|"]
        for p in proposals:
            names = "، ".join(i.get("label_ar") or i["entity_id"] for i in p["items"])
            out.append(f"| {p.get('note_ar') or p['operation']} | {names} | نعم |")
        out += ["", "ما نفّذنا أي تغيير. إذا تبي تعتمد مقترح، قل لي أي واحد ونعرض لك جدول قبل/بعد."]
    else:
        out.append("- لا توجد مقترحات تنفيذ مبنية على دليل كافٍ حالياً.")
    if failed:
        out += ["", "### استنتاجات لم تجتز المراجعة (لم نعتمدها)", ""]
        for f in failed:
            msgs = "؛ ".join(i["message_ar"] for i in rev[f["finding_id"]]["issues"])
            out.append(f"- {f['finding_id']}: {msgs}")
    notes = (review or {}).get("batch_notes") or []
    if notes:
        out += [""] + [f"- {n['message_ar']}" for n in notes]
    mode = (run or {}).get("mode", "sequential")
    rmode = (review or {}).get("mode", "self_check")
    out += ["", "### طريقة التحليل", "",
            f"- التنفيذ: {'وكلاء منفصلون' if mode == 'native' else 'مراحل متسلسلة في نفس الجلسة'}.",
            f"- المراجعة: {'مراجع مستقل في سياق منفصل' if rmode == 'independent_context' else 'مراجعة ذاتية بقائمة فحص (ليست مستقلة)'}.",
            "- الأدلة: " + "، ".join(sorted({r for f in ok for r in f['evidence_refs']})) if ok else "- الأدلة: لا يوجد",
            "- التوقيت: Asia/Riyadh. الأرقام محسوبة بكود حتمي."]
    return "\n".join(out)
