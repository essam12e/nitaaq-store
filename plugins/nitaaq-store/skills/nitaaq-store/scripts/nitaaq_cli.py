#!/usr/bin/env python3
"""Nitaaq Store helper CLI (نطاق للمتاجر).

Run from the skill directory:  python3 scripts/nitaaq_cli.py <command> ...
All output is JSON on stdout (use --md for Arabic Markdown where offered).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nitaaq import __version__  # noqa: E402
from nitaaq import activation, capabilities, errors, exports, geo, gsc, products, redact, remediation, reports, seo_audit, tracking, visibility, writes  # noqa: E402
from nitaaq import ads, approvals, contracts, cro, customer_intel, evidence, findings, growth, metrics, pricing, registry, review, routing, seo_team, state  # noqa: E402


def _out(obj):
    def default(o):
        if isinstance(o, Decimal):
            return str(o)
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=default))


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_activation(a):
    r = activation.detect(a.text, a.context_active)
    _out(r.__dict__)


def cmd_cap_build(a):
    raw = _load(a.tools)
    if isinstance(raw, dict):
        raw = raw.get("tools", [])
    cmap = capabilities.build_map(raw)
    if a.out:
        Path(a.out).write_text(json.dumps(cmap, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        print(capabilities.summarize_ar(cmap))
    else:
        _out(cmap if not a.out else {"written": a.out, "connection": cmap["connection"],
                                      "summary_ar": capabilities.summarize_ar(cmap)})


def cmd_cap_diff(a):
    _out(capabilities.diff_maps(_load(a.old), _load(a.new)))


def cmd_cap_evidence(a):
    cmap = _load(a.map)
    entry = capabilities.record_evidence(cmap, a.op, a.level, a.note, a.tool, a.store_id)
    Path(a.map).write_text(json.dumps(cmap, ensure_ascii=False, indent=1), encoding="utf-8")
    _out({"op": a.op, "evidence_level": entry["evidence_level"]})


def cmd_classify_error(a):
    _out(errors.classify(a.text, a.status, a.write, a.idempotent).__dict__)


def cmd_price(a):
    if a.phrase:
        parsed = products.parse_price_phrase(a.phrase)
        _out({"parsed": parsed})
        return
    pc = products.check_prices(a.regular, a.sale, a.cost)
    _out({**pc.__dict__, "discount_percent": pc.discount_percent})


def cmd_stock(a):
    _out(products.plan_stock_change(a.mode, a.current, a.value).__dict__)


def cmd_match(a):
    ref = _load(a.ref)
    cat = _load(a.catalog)
    _out(products.best_matches(ref, cat, a.top))


def cmd_diff(a):
    cur, des = _load(a.current), _load(a.desired)
    fields = a.fields.split(",") if a.fields else None
    patch = writes.build_patch(cur, des, fields)
    if a.md:
        print(writes.diff_table_ar(cur, patch))
    else:
        _out({"patch": patch, "rows": writes.diff_rows(cur, patch)})


def cmd_conflicts(a):
    _out(writes.detect_conflicts(_load(a.snapshot), _load(a.fresh), a.fields.split(",")))


def cmd_verify(a):
    _out(writes.verify_readback(_load(a.patch), _load(a.readback)))


def cmd_ledger(a):
    path = a.ledger or (writes.ledger_path(a.root, a.store_id) if a.store_id else ".nitaaq/ledger.json")
    led = writes.Ledger(path)
    payload = _load(a.payload) if a.payload else {}
    key = a.key or writes.operation_key(a.op, a.entity, payload, a.store_id, a.approval_id)
    if a.action == "check":
        _out({"key": key, "state": led.check(key)})
    elif a.action == "begin":
        _out({"key": key, "state": led.begin(key, a.op, a.entity, a.summary or "", a.store_id, a.approval_id)})
    elif a.action == "finish":
        led.finish(key, a.status, a.result_id)
        _out({"key": key, "status": a.status})
    elif a.action == "reconcile":
        led.reconcile(key, a.result_id)
        _out({"key": key, "state": led.check(key)})


def cmd_export(a):
    info = exports.analyze(a.file)
    if a.report and info["kind"] == "orders":
        orders = exports.to_orders(a.file)
        kept, dropped = reports.filter_orders(orders, a.date_from, a.date_to, a.statuses.split(",") if a.statuses else None)
        meta = reports.ReportMeta(source=f"ملف تصدير {Path(a.file).name}", date_from=a.date_from or info.get("date_min"),
                                  date_to=a.date_to or info.get("date_max"), currency=a.currency,
                                  included_statuses=a.statuses.split(",") if a.statuses else None,
                                  records_fetched=len(orders), records_total=None,
                                  treatment={"cancellations": "مستبعدة" if a.statuses else "غير مستبعدة (لم تُحدد الحالات)"},
                                  missing=info["limitations"])
        info["report"] = {
            "meta": reports.meta_dict(meta), "dropped": dropped,
            "summary": reports.sales_summary(kept), "monthly": reports.by_month(kept),
            "by_city": reports.by_dimension(kept, "city"), "by_payment": reports.by_dimension(kept, "payment_method"),
            "top_by_quantity": reports.top_products(kept, "quantity"), "top_by_revenue": reports.top_products(kept, "revenue"),
            "margins": reports.product_margins(kept),
            "customers": reports.customers_new_returning(kept),
        }
        if a.md:
            r = info["report"]
            s = r["summary"]
            print("## ملخص المبيعات\n")
            print(meta.to_markdown_ar() + "\n")
            print(f"- عدد الطلبات: {s['orders']}\n- المبيعات: {reports.money(s['sales'])} {a.currency}\n- متوسط قيمة الطلب: {reports.money(s['aov'])} {a.currency}\n")
            print("### حسب الشهر\n")
            print(reports.table_ar(r["monthly"], [("month", "الشهر"), ("orders", "الطلبات"), ("sales", "المبيعات"), ("aov", "متوسط الطلب")]))
            print("\n### الأكثر مبيعاً بالكمية\n")
            print(reports.table_ar(r["top_by_quantity"], [("product", "المنتج"), ("quantity", "الكمية"), ("revenue", "الإيراد")]))
            m = r["margins"]
            print(f"\n### {m['label_ar']}\n\nبنود بتكلفة: {m['items_with_cost']} — بدون تكلفة: {m['items_without_cost']} — الهامش: {reports.money(m['margin'])}")
            return
    _out(info)


def cmd_audit_url(a):
    res = seo_audit.crawl(a.url, a.max_pages, a.delay)
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if a.md:
        print(f"## فحص SEO — {res['site']}\n\n{res['coverage_note_ar']}\n\n{res['disclaimer_ar']}\n")
        if not res["reachable"]:
            print("⚠️ تعذر الوصول للموقع من بيئة الفحص؛ النتائج أدناه لا تصف المتجر. استخدم أداة الجلب في المحادثة أو أرسل الصفحات.\n")
        print(seo_audit.findings_markdown_ar(res["findings"]))
    else:
        _out({k: v for k, v in res.items() if k != "pages"} if a.out else res)


def cmd_audit_html(a):
    html = Path(a.file).read_text(encoding="utf-8")
    f = seo_audit.parse_page(html, a.url)
    fs = seo_audit.audit_page(f, a.page_type or seo_audit.guess_page_type(a.url, f))
    if a.md:
        print(seo_audit.findings_markdown_ar(fs))
    else:
        _out({"page": seo_audit.page_summary(f), "findings": fs})


def cmd_robots(a):
    _out(seo_audit.robots_report(a.site, Path(a.file).read_text(encoding="utf-8"), a.page))


def cmd_tracking(a):
    html = Path(a.html).read_text(encoding="utf-8")
    code = tracking.detect_code(html)
    har = tracking.events_from_har(a.har) if a.har else None
    conf = _load(a.confirmations) if a.confirmations else None
    _out(tracking.combine(code, har, conf))


def cmd_geo(a):
    _out(geo.assess(_load(a.crawl), a.store_name, a.llms_status))


def cmd_vis_queries(a):
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else []  # noqa: E731
    _out(visibility.build_queries(a.store, split(a.categories), split(a.products), split(a.cities),
                                  split(a.competitors), split(a.aliases)))


def cmd_vis_summary(a):
    _out(visibility.summarize(_load(a.observations)))


def cmd_redact(a):
    data = Path(a.file).read_text(encoding="utf-8")
    try:
        _out(redact.redact_record(json.loads(data)))
    except json.JSONDecodeError:
        print(redact.redact_text(data))


def cmd_gsc_perf(a):
    brands = [x.strip() for x in (a.brand or "").split(",") if x.strip()]
    _out(gsc.analyze_performance(a.file, brands, a.min_impressions))


def cmd_gsc_index(a):
    _out(gsc.analyze_indexing(a.file))


def cmd_remediation(a):
    crawl = _load(a.crawl)
    cmap = _load(a.capabilities) if a.capabilities else None
    p = remediation.plan(crawl.get("findings", []), cmap)
    if a.md:
        print(remediation.plan_markdown_ar(p))
    else:
        _out(p)


def cmd_recheck(a):
    before, after = _load(a.before), _load(a.after)
    _out(remediation.recheck(before.get("findings", []), after.get("findings", []), after.get("reachable", True)))


# ------------------------------------------------------------------ team (orchestrator, operator, specialists)


def _orders(path):
    """Orders from a CSV export, a JSON list, {"data": [...]}, or a saved evidence file."""
    if str(path).lower().endswith(".csv"):
        return exports.to_orders(path)
    d = _load(path)
    if isinstance(d, dict):
        d = d.get("records") or d.get("data") or d.get("orders") or []
    return d


def _pair(v):
    a, b = v.split(",")
    return [a.strip(), b.strip()]


def _available(a):
    cmap = _load(a.map) if getattr(a, "map", None) else None
    avail = routing.available_from_map(cmap, (a.exports or "").split(",") if a.exports else [], a.public,
                                       (a.external or "").split(",") if a.external else [])
    if a.available:
        avail |= {x.strip() for x in a.available.split(",") if x.strip()}
    return avail


def _findings(path):
    """A list of findings, one finding, or the --out file of sales-change."""
    d = _load(path)
    if isinstance(d, dict):
        d = d["findings"] if "findings" in d else [d]
    return d


def cmd_registry(a):
    if a.sub == "validate":
        errs = registry.validate()
        _out({"valid": not errs, "errors": errs})
        if errs:
            raise ValueError("registry invalid")
    else:
        reg = registry.load()
        _out([{k: x.get(k) for k in ("id", "kind", "status", "phase", "label_ar")} for x in reg["agents"]])


def cmd_route(a):
    _out(routing.route(a.message, _available(a)))


def cmd_followups(a):
    _out(routing.followups(a.signals.split(","), _available(a), (a.already or "").split(",") if a.already else []))


def _now(a):
    from datetime import datetime
    return datetime.fromisoformat(a.now) if a.now else None


def cmd_metrics(a):
    if a.sub == "periods":
        from datetime import date
        _out(metrics.default_periods(a.today or date.today().isoformat(), a.days))
        return
    orders = _orders(a.orders)
    statuses = a.statuses.split(",") if a.statuses else None
    if a.sub == "compare":
        r = metrics.compare_periods(orders, _pair(a.current), _pair(a.baseline), definition=a.definition,
                                    statuses=statuses, now=_now(a), records_total=a.total)
        if a.md:
            print(metrics.comparison_markdown_ar(r, a.currency))
            return
        _out(r)
    else:
        _out(metrics.decompose(orders, _pair(a.current), _pair(a.baseline), a.dimension, definition=a.definition,
                               statuses=statuses, top=a.top))


def cmd_evidence(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    if a.sub == "make":
        records = _orders(a.records)
        ev, unique = evidence.make_evidence(a.store_id, {"kind": a.source, "operation": a.op, "tool": a.tool},
                                            records, period=_pair(a.period) if a.period else None,
                                            records_total=a.total, pages=a.pages, key=a.key,
                                            check_fields=["date", "total", "status"] if a.op.startswith("orders") else None)
        path = store.save(ev, unique)
        _out({"evidence": ev, "path": str(path)})
    else:
        ev, records = store.load(a.id)
        _out({"evidence": ev, "records_count": len(records)})


def cmd_approvals(a):
    st = approvals.ApprovalStore.for_store(a.root, a.store_id)
    if a.sub == "grant":
        _out(st.grant(a.store_id, a.op, _load(a.items), proposal_id=a.proposal, merchant_words=a.words or "",
                      ttl_hours=a.ttl_hours, account_id=a.account_id))
    elif a.sub == "check":
        r = st.check(a.id, a.store_id, a.op, _load(a.items), _load(a.fresh) if a.fresh else None, account_id=a.account_id)
        _out(r)
    elif a.sub == "use":
        st.mark_used(a.id)
        _out(st.get(a.id))
    elif a.sub == "revoke":
        _out({"revoked": st.revoke(a.id)})
    else:
        _out(st.all())


def cmd_state(a):
    if a.sub == "new":
        run = state.Run.create(a.root, a.store_id, a.question or "", mode=a.mode, capability_map_version=a.cmap_version)
        _out(run.data())
        return
    run = state.Run(a.root, a.store_id, a.run)
    if a.sub == "stage":
        tid = run.add_stage(a.agent, depends_on=a.depends.split(",") if a.depends else None,
                            evidence_refs=a.evidence.split(",") if a.evidence else None)
        _out({"task_id": tid})
    elif a.sub == "move":
        _out(run.transition(a.task, a.to, reason_ar=a.reason, outputs=a.outputs.split(",") if a.outputs else None))
    elif a.sub == "review-mode":
        run.set_review_mode(a.mode)
        _out(run.data())
    elif a.sub == "cancel":
        run.request_cancel()
        _out(run.data())
    elif a.sub == "resume":
        _out(run.resume_plan())
    else:
        _out(run.data())


def cmd_validate(a):
    errs = contracts.validate(_load(a.file), a.schema)
    _out({"valid": not errs, "errors": errs})
    if errs:
        raise ValueError("document does not match schema " + a.schema)


def cmd_review(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    _out(review.review_all(_findings(a.findings), store, a.mode))


def cmd_sales_change(a):
    """Store Analytics, deterministic part: evidence -> comparison -> where -> findings (+ self review, report)."""
    from datetime import datetime
    now = _now(a) or datetime.now(reports.RIYADH)
    if now.tzinfo is None:
        now = now.replace(tzinfo=reports.RIYADH)
    if not (a.orders or a.evidence_id):
        raise ValueError("pass --orders or --evidence-id")
    orders = _orders(a.orders) if a.orders else []
    store = evidence.EvidenceStore(a.root, a.store_id)
    if a.current and a.baseline:
        cur, base = _pair(a.current), _pair(a.baseline)
    else:
        p = metrics.default_periods(now.astimezone(reports.RIYADH).date().isoformat(), a.days)
        cur, base = p["current"], p["baseline"]
    if a.evidence_id:
        ev, orders = store.load(a.evidence_id)
    else:
        ev, orders = evidence.make_evidence(a.store_id, {"kind": a.source, "operation": "orders.list", "tool": a.tool or Path(a.orders).name},
                                            orders, period=[base[0], cur[1]], records_total=a.total, pages=a.pages,
                                            check_fields=["date", "total", "status"])
        store.save(ev, orders)
    statuses = a.statuses.split(",") if a.statuses else None
    cmp = metrics.compare_periods(orders, cur, base, statuses=statuses, now=now,
                                  records_total=ev["coverage"]["records_total"])
    fs = [findings.sales_change_finding(cmp, ev)]
    for i, dim in enumerate(("product", "city"), start=2):
        if cmp["status"] in ("decline", "increase"):
            w = findings.where_finding(metrics.decompose(orders, cur, base, dim, statuses=statuses), ev, cmp,
                                       finding_id=f"f{i}")
            if w:
                fs.append(w)
    for f in fs:  # pin the clock so the reviewer recomputes the same partial-day check
        for o in f["observed"]:
            if isinstance(o.get("calc"), dict) and o["calc"]["fn"] == "compare_periods":
                o["calc"]["now"] = now.isoformat()
    rv = review.review_all(fs, store, "self_check")
    out = {"evidence": ev, "comparison": cmp, "findings": fs, "review": rv}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if a.md:
        plan = routing.route(a.question or "ليش المبيعات نازلة؟", {"orders.list"} | ({"export:orders"} if a.source == "export" else set()))
        print(findings.unified_report_ar(a.question or "ليش المبيعات نازلة؟", plan, json.loads(json.dumps(fs, default=str)), rv))
        return
    _out(out)


def _records(path):
    d = _load(path)
    if isinstance(d, dict):
        d = d.get("records") or d.get("data") or d.get("products") or d.get("carts") or []
    return d


def _evidence_or_file(store, store_id, ev_id, path, op, total, source="mcp"):
    """Load saved evidence, or save a file as new evidence. Returns (evidence, records)."""
    if ev_id:
        return store.load(ev_id)
    if not path:
        return None, None
    recs = _orders(path) if op == "orders.list" else _records(path)
    ev, unique = evidence.make_evidence(store_id, {"kind": source, "operation": op, "tool": Path(path).name}, recs,
                                        records_total=total)
    store.save(ev, unique)
    return ev, unique


def _finish(a, fs, props, store, plan_msg):
    fs = [f for f in fs if f]
    rv = review.review_all(fs, store, "self_check")
    out = {"findings": fs, "proposals": props, "review": rv}
    if getattr(a, "out", None):
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if getattr(a, "md", False):
        print(findings.unified_report_ar(a.question or plan_msg, {}, json.loads(json.dumps(fs, default=str)), rv, props))
        return
    _out(out)


def cmd_pricing(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    vat = {"yes": True, "no": False, None: None}[a.prices_include_vat]
    pev, prods = _evidence_or_file(store, a.store_id, a.products_evidence, a.products, "products.list", a.total)
    if pev is None:
        raise ValueError("pass --products or --products-evidence")
    econ = pricing.product_economics(prods, prices_include_vat=vat, deep_discount_pct=a.deep_discount)
    fs = pricing.economics_findings(econ, pev, prices_include_vat=vat)
    signals = []
    oev, orders = _evidence_or_file(store, a.store_id, a.orders_evidence, a.orders, "orders.list", a.orders_total)
    if oev is not None and a.current and a.baseline:
        pc = pricing.price_changes(orders, _pair(a.current), _pair(a.baseline), threshold_pct=a.threshold)
        signals += pc["signals"]
        fs.append(pricing.price_change_finding(pc, oev, _pair(a.current), _pair(a.baseline), fid=f"p{len(fs) + 1}"))
    if econ["counts"]["deep_discount"]:
        signals.append("discount_heavy")
    inv = next((f["finding_id"] for f in fs if f and "سعر التخفيض" in f["interpretation_ar"]), None)
    props = pricing.invalid_sale_proposals(econ, a.store_id, inv) if inv else []
    for f in fs:
        if f:
            f["signals"] = signals
    _finish(a, fs, props, store, "هل أسعاري مناسبة؟")


def cmd_breakeven(a):
    _out(pricing.breakeven(a.price, a.cost, a.new_price))


def cmd_growth(a):
    if a.sub == "sample-size":
        _out(growth.sample_size(a.baseline_rate, a.lift, daily_visitors_per_arm=a.daily))
        return
    if a.sub == "funnel":
        _out(growth.funnel(_load(a.current_steps), _load(a.baseline_steps) if a.baseline_steps else None))
        return
    store = evidence.EvidenceStore(a.root, a.store_id)
    ev, orders = _evidence_or_file(store, a.store_id, a.evidence_id, a.orders, "orders.list", a.total)
    if ev is None:
        raise ValueError("pass --orders or --evidence-id")
    if a.sub == "cohorts":
        _out(growth.cohorts(orders, as_of=a.as_of))
        return
    if a.current and a.baseline:
        cur, base = _pair(a.current), _pair(a.baseline)
    else:
        from datetime import date
        p = metrics.default_periods(a.today or date.today().isoformat(), a.days)
        cur, base = p["current"], p["baseline"]
    fod = _load(a.first_order_dates) if a.first_order_dates else None
    mc = growth.mix_change(orders, cur, base, first_order_dates=fod)
    _finish(a, [growth.mix_finding(mc, ev)], [], store, "مين اللي يشتري من متجري؟")


def cmd_cro(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    products = dict(x.split("=", 1) for x in a.product or [])
    pages = []
    for spec in a.page or []:
        url, path = spec.split("=", 1)
        pages.append((url, Path(path).read_text(encoding="utf-8", errors="replace")))
    for url in a.fetch or []:
        from nitaaq import seo_audit
        r = seo_audit.fetch(url)
        if r.error or not r.body:
            raise ValueError(f"could not fetch {url}: {r.error or r.status}")
        pages.append((url, r.body))
    fs, carts = [], None
    if pages:
        recs = [{"id": u, "url": u, "html": h, "product": _load(products[u]) if u in products else None} for u, h in pages]
        ev, recs = evidence.make_evidence(a.store_id, {"kind": "public", "operation": "public.pages", "tool": "page"},
                                          recs, records_total=len(recs), kind="public_page")
        store.save(ev, recs)
        for i, r in enumerate(recs, start=1):
            fs.append(cro.page_finding(cro.page_checks(r["html"], r["url"], r.get("product")), ev, fid=f"c{i}"))
    if a.carts:
        carts = cro.abandoned_carts(_records(a.carts), _pair(a.period) if a.period else None)
    if not fs and carts is None:
        raise ValueError("pass --page, --fetch or --carts")
    if not fs:
        _out({"abandoned_carts": carts})
        return
    if carts is not None and not a.md:
        rv = review.review_all(fs, store, "self_check")
        _out({"findings": fs, "review": rv, "abandoned_carts": carts})
        return
    _finish(a, fs, [], store, "ليش الزوار ما يشترون؟")


def cmd_seo_team(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    recs = []
    for spec in a.page:
        url, path = spec.split("=", 1)
        recs.append({"id": url, "url": url, "html": Path(path).read_text(encoding="utf-8", errors="replace"),
                     "page_type": a.page_type})
    ev, recs = evidence.make_evidence(a.store_id, {"kind": "public", "operation": "public.pages", "tool": "page"},
                                      recs, records_total=len(recs), kind="public_page")
    store.save(ev, recs)
    fs = []
    for r in recs:
        fs += seo_team.page_findings(r, ev, start=len(fs) + 1)
    _finish(a, fs, [], store, "افحص ظهور متجري في محركات البحث")


def cmd_cannibalization(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    if a.gsc:
        if a.gsc.lower().endswith(".csv"):
            headers, rows = exports.read_csv(a.gsc)
            m = gsc._map(headers)  # same Arabic/English column names as gsc-performance
            rows = [{m[h]: v for h, v in r.items() if h in m} for r in rows]
        else:
            rows = _records(a.gsc)
        rows = [{**r, "id": f"{r.get('query')}|{r.get('page')}"} for r in rows]
        ev, rows = evidence.make_evidence(a.store_id, {"kind": "export", "operation": "export.gsc", "tool": Path(a.gsc).name},
                                          rows, records_total=len(rows), kind="gsc")
        cands, basis = seo_team.cannibalization_gsc(rows), "gsc"
    elif a.titles:
        rows = _records(a.titles)
        ev, rows = evidence.make_evidence(a.store_id, {"kind": "mcp", "operation": "products.list", "tool": Path(a.titles).name},
                                          rows, records_total=len(rows))
        cands, basis = seo_team.cannibalization_titles(rows, threshold=a.threshold), "titles"
    else:
        raise ValueError("pass --gsc or --titles")
    store.save(ev, rows)
    f = seo_team.cannibalization_finding(cands, ev, basis=basis, threshold=a.threshold)
    _out({"basis": basis, "candidates": cands, "findings": [f] if f else [],
          "review": review.review_all([f], store) if f else None})


def cmd_reviews(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    ev, recs = _evidence_or_file(store, a.store_id, a.evidence_id, a.reviews, "reviews.list", a.total)
    if ev is None:
        raise ValueError("pass --reviews or --evidence-id")
    fs, extra = [], {}
    if a.current and a.baseline:
        cmp = customer_intel.compare(recs, _pair(a.current), _pair(a.baseline), records_total=ev["coverage"]["records_total"])
        f = customer_intel.themes_finding(cmp["current"], ev)
        f["signals"] = cmp["signals"]
        f["observed"].append({"label_ar": "نسبة التقييمات السلبية", "current": str(cmp["negative"]["current_rate_pct"]),
                              "baseline": str(cmp["negative"]["baseline_rate_pct"])})
        fs.append(f)
        extra = {"comparison": {k: cmp[k] for k in ("negative", "theme_changes", "enough_sample", "signals")}}
    else:
        fs.append(customer_intel.themes_finding(customer_intel.analyze(recs, records_total=ev["coverage"]["records_total"]), ev))
    if a.md or a.out:
        _finish(a, fs, [], store, "وش يقولون العملاء عن منتجاتي؟")
        return
    _out({"findings": fs, "review": review.review_all(fs, store), **extra})


def cmd_reconcile(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    oev, orders = _evidence_or_file(store, a.store_id, a.orders_evidence, a.orders, "orders.list", a.orders_total)
    if oev is None:
        raise ValueError("pass --orders or --orders-evidence")
    if a.conversions_evidence:
        pev, conv = store.load(a.conversions_evidence)
    elif a.conversions:
        if a.conversions.lower().endswith(".csv"):
            _, conv = exports.read_csv(a.conversions)
        else:
            conv = _records(a.conversions)
        conv = [{**r, "id": f"row-{i}"} for i, r in enumerate(conv)]  # keep repeated transaction ids: they are findings
        pev, conv = evidence.make_evidence(a.store_id, {"kind": "export", "operation": f"export.{a.destination}",
                                                        "tool": Path(a.conversions).name, "platform": a.platform},
                                           conv, records_total=a.conversions_total, kind=a.destination)
        store.save(pev, conv)
    else:
        raise ValueError("pass --conversions or --conversions-evidence")
    fault = None
    if a.fault_evidence:
        fe = _load(a.fault_evidence)
        fault = {k: fe.get(k) for k in ("level", "purchase_event_observed", "purchase_flow_captured")}
    params = {"period": _pair(a.period), "destination": a.destination, "platform": a.platform, "date_basis": a.date_basis,
              "window_days": a.window_days, "lag_days": a.lag_days, "as_of": a.as_of or date.today().isoformat(),
              "store_currency": a.currency, "tolerance_pct": a.tolerance,
              "conversion_actions": a.actions.split(",") if a.actions else None, "fault_evidence": fault}
    params = {k: v for k, v in params.items() if v is not None}
    res = tracking.reconcile_from_calc(orders, conv, params)
    f = tracking.reconciliation_finding(res, oev, pev, params)
    if a.md or a.out:
        _finish(a, [f], [], store, "هل التتبع يسجل طلباتي صح؟")
        return
    _out({"reconciliation": res, "findings": [f], "review": review.review_all([f], store)})


def _csv_or_json(path):
    if str(path).lower().endswith(".csv"):
        return exports.read_csv(path)[1]
    return _records(path)


def _list(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


ADS_PARAMS = {
    "audit": ("min_clicks", "min_conversions", "margin_pct", "store_domains", "brand", "as_of", "lag_days"),
    "structure": ("brand", "competitors", "min_conversions"),
    "search_terms": ("brand", "competitors", "keywords", "negatives", "min_clicks", "as_of", "lag_days"),
    "fatigue": ("min_impressions", "drop_pct"),
    "social": ("frequency_limit", "min_results"),
}


def cmd_ads(a):
    store = evidence.EvidenceStore(a.root, a.store_id)
    kind = a.analysis.replace("-", "_")
    if a.evidence_id:
        ev, rows = store.load(a.evidence_id)
    elif a.file:
        rows = [{**r, "id": f"row-{i}"} for i, r in enumerate(_csv_or_json(a.file))]
        ev, rows = evidence.make_evidence(a.store_id, {"kind": "export", "operation": f"export.{a.platform}",
                                                       "tool": Path(a.file).name}, rows, records_total=a.total, kind="ads")
        store.save(ev, rows)
    else:
        raise ValueError("pass --file (ads export) or --evidence-id")
    given = {"min_clicks": a.min_clicks, "min_conversions": a.min_conversions, "margin_pct": a.margin,
             "store_domains": _list(a.domains), "brand": _list(a.brand), "competitors": _list(a.competitors),
             "keywords": _list(a.keywords), "negatives": _list(a.negatives), "as_of": a.as_of, "lag_days": a.lag_days,
             "min_impressions": a.min_impressions, "drop_pct": a.drop_pct, "frequency_limit": a.frequency_limit,
             "min_results": a.min_results}
    params = {"platform": a.platform, **{k: given[k] for k in ADS_PARAMS[kind] if given[k] is not None}}
    res = ads.run(kind, rows, params)
    f = ads.finding(res, ev, params)
    props = []
    if kind == "search_terms":
        p = ads.negatives_proposal(res, a.store_id, f["finding_id"])
        if p:
            f["proposed_action_ref"] = p["proposal_id"]
            props.append(p)
    if a.md or a.out:
        _finish(a, [f], props, store, "كيف أداء إعلاناتي؟")
        return
    _out({"result": {k: v for k, v in res.items() if k != "metrics"}, "findings": [f], "proposals": props,
          "review": review.review_all([f], store)})


def cmd_ads_copy(a):
    d = _load(a.copy)
    _out(ads.rsa_check(d.get("headlines", []), d.get("descriptions", []), facts=_load(a.facts) if a.facts else d.get("facts")))


def cmd_report(a):
    plan = _load(a.plan) if a.plan else {}
    fs = _findings(a.findings)
    rv = _load(a.review) if a.review else (_load(a.findings).get("review") if isinstance(_load(a.findings), dict) else None)
    props = _load(a.proposals) if a.proposals else None
    run = state.Run(a.root, a.store_id, a.run).data() if a.run else None
    print(findings.unified_report_ar(a.question, plan, fs, rv, props, run))


def main(argv=None):
    p = argparse.ArgumentParser(prog="nitaaq_cli", description="Nitaaq Store helpers")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("activation"); s.add_argument("text"); s.add_argument("--context-active", action="store_true"); s.set_defaults(f=cmd_activation)

    s = sub.add_parser("capabilities", help="build/diff/evidence")
    cs = s.add_subparsers(dest="sub", required=True)
    b = cs.add_parser("build"); b.add_argument("--tools", required=True); b.add_argument("--out"); b.add_argument("--md", action="store_true"); b.set_defaults(f=cmd_cap_build)
    d = cs.add_parser("diff"); d.add_argument("old"); d.add_argument("new"); d.set_defaults(f=cmd_cap_diff)
    e = cs.add_parser("evidence"); e.add_argument("--map", required=True); e.add_argument("--op", required=True)
    e.add_argument("--level", required=True, choices=["tested", "live_verified"]); e.add_argument("--note", required=True)
    e.add_argument("--tool"); e.add_argument("--store-id"); e.set_defaults(f=cmd_cap_evidence)

    s = sub.add_parser("classify-error"); s.add_argument("text"); s.add_argument("--status", type=int); s.add_argument("--write", action="store_true")
    s.add_argument("--idempotent", action="store_true"); s.set_defaults(f=cmd_classify_error)

    s = sub.add_parser("price-check"); s.add_argument("--regular"); s.add_argument("--sale"); s.add_argument("--cost"); s.add_argument("--phrase"); s.set_defaults(f=cmd_price)
    s = sub.add_parser("stock-plan"); s.add_argument("--mode", required=True, choices=["set", "increment", "decrement"])
    s.add_argument("--current", required=True); s.add_argument("--value", required=True); s.set_defaults(f=cmd_stock)
    s = sub.add_parser("match"); s.add_argument("--ref", required=True); s.add_argument("--catalog", required=True); s.add_argument("--top", type=int, default=3); s.set_defaults(f=cmd_match)

    s = sub.add_parser("diff"); s.add_argument("--current", required=True); s.add_argument("--desired", required=True); s.add_argument("--fields"); s.add_argument("--md", action="store_true"); s.set_defaults(f=cmd_diff)
    s = sub.add_parser("conflicts"); s.add_argument("--snapshot", required=True); s.add_argument("--fresh", required=True); s.add_argument("--fields", required=True); s.set_defaults(f=cmd_conflicts)
    s = sub.add_parser("verify"); s.add_argument("--patch", required=True); s.add_argument("--readback", required=True); s.set_defaults(f=cmd_verify)
    s = sub.add_parser("ledger"); s.add_argument("action", choices=["check", "begin", "finish", "reconcile"]); s.add_argument("--ledger")
    s.add_argument("--store-id", help="per-store ledger and key"); s.add_argument("--approval-id"); s.add_argument("--root", default=".nitaaq")
    s.add_argument("--op"); s.add_argument("--entity"); s.add_argument("--payload"); s.add_argument("--key"); s.add_argument("--summary")
    s.add_argument("--status", choices=["succeeded", "failed", "unknown"]); s.add_argument("--result-id"); s.set_defaults(f=cmd_ledger)

    s = sub.add_parser("analyze-export"); s.add_argument("file"); s.add_argument("--report", action="store_true"); s.add_argument("--from", dest="date_from")
    s.add_argument("--to", dest="date_to"); s.add_argument("--statuses"); s.add_argument("--currency", default="SAR"); s.add_argument("--md", action="store_true"); s.set_defaults(f=cmd_export)

    s = sub.add_parser("audit-url"); s.add_argument("url"); s.add_argument("--max-pages", type=int, default=25); s.add_argument("--delay", type=float, default=1.0)
    s.add_argument("--out"); s.add_argument("--md", action="store_true"); s.set_defaults(f=cmd_audit_url)
    s = sub.add_parser("audit-html"); s.add_argument("file"); s.add_argument("--url", required=True); s.add_argument("--page-type"); s.add_argument("--md", action="store_true"); s.set_defaults(f=cmd_audit_html)
    s = sub.add_parser("robots"); s.add_argument("file"); s.add_argument("--site", required=True); s.add_argument("--page"); s.set_defaults(f=cmd_robots)
    s = sub.add_parser("tracking"); s.add_argument("--html", required=True); s.add_argument("--har"); s.add_argument("--confirmations"); s.set_defaults(f=cmd_tracking)
    s = sub.add_parser("geo"); s.add_argument("--crawl", required=True); s.add_argument("--store-name"); s.add_argument("--llms-status", type=int); s.set_defaults(f=cmd_geo)

    s = sub.add_parser("visibility-queries"); s.add_argument("--store", required=True); s.add_argument("--categories", required=True)
    s.add_argument("--products"); s.add_argument("--cities"); s.add_argument("--competitors"); s.add_argument("--aliases"); s.set_defaults(f=cmd_vis_queries)
    s = sub.add_parser("visibility-summary"); s.add_argument("observations"); s.set_defaults(f=cmd_vis_summary)
    s = sub.add_parser("redact"); s.add_argument("file"); s.set_defaults(f=cmd_redact)
    s = sub.add_parser("gsc-performance"); s.add_argument("file"); s.add_argument("--brand", help="كلمات العلامة مفصولة بفواصل")
    s.add_argument("--min-impressions", type=int, default=50); s.set_defaults(f=cmd_gsc_perf)
    s = sub.add_parser("gsc-indexing"); s.add_argument("file"); s.set_defaults(f=cmd_gsc_index)
    s = sub.add_parser("remediation-plan"); s.add_argument("--crawl", required=True); s.add_argument("--capabilities"); s.add_argument("--md", action="store_true"); s.set_defaults(f=cmd_remediation)
    s = sub.add_parser("recheck"); s.add_argument("--before", required=True); s.add_argument("--after", required=True); s.set_defaults(f=cmd_recheck)

    # ---- team: registry, routing, metrics, evidence, approvals, state, review, report
    s = sub.add_parser("registry"); s.add_argument("sub", choices=["validate", "show"]); s.set_defaults(f=cmd_registry)
    for name, fn in (("route", cmd_route), ("followups", cmd_followups)):
        s = sub.add_parser(name)
        if name == "route":
            s.add_argument("message")
        else:
            s.add_argument("--signals", required=True); s.add_argument("--already")
        s.add_argument("--map"); s.add_argument("--available", help="extra capability tokens, comma-separated")
        s.add_argument("--exports", help="orders,products,..."); s.add_argument("--public", action="store_true")
        s.add_argument("--external", help="analytics,google_ads,meta,..."); s.set_defaults(f=fn)
    s = sub.add_parser("metrics"); ms = s.add_subparsers(dest="sub", required=True)
    m = ms.add_parser("periods"); m.add_argument("--today"); m.add_argument("--days", type=int, default=28); m.set_defaults(f=cmd_metrics)
    for name in ("compare", "decompose"):
        m = ms.add_parser(name); m.add_argument("--orders", required=True); m.add_argument("--current", required=True)
        m.add_argument("--baseline", required=True); m.add_argument("--statuses"); m.add_argument("--definition", default="order_total")
        if name == "compare":
            m.add_argument("--total", type=int); m.add_argument("--now"); m.add_argument("--md", action="store_true"); m.add_argument("--currency", default="SAR")
        else:
            m.add_argument("--dimension", required=True); m.add_argument("--top", type=int, default=10)
        m.set_defaults(f=cmd_metrics)
    s = sub.add_parser("evidence"); es = s.add_subparsers(dest="sub", required=True)
    e = es.add_parser("make"); e.add_argument("--store-id", required=True); e.add_argument("--records", required=True)
    e.add_argument("--op", required=True); e.add_argument("--source", default="mcp", choices=["mcp", "export", "public", "report_tool", "merchant"])
    e.add_argument("--tool"); e.add_argument("--period"); e.add_argument("--total", type=int); e.add_argument("--pages", type=int)
    e.add_argument("--key", default="id"); e.add_argument("--root", default=".nitaaq"); e.set_defaults(f=cmd_evidence)
    e = es.add_parser("show"); e.add_argument("--store-id", required=True); e.add_argument("--id", required=True)
    e.add_argument("--root", default=".nitaaq"); e.set_defaults(f=cmd_evidence)
    s = sub.add_parser("approvals"); s.add_argument("sub", choices=["grant", "check", "use", "revoke", "list"])
    s.add_argument("--store-id", required=True); s.add_argument("--account-id"); s.add_argument("--id"); s.add_argument("--op")
    s.add_argument("--items", help="JSON [{entity_id, before, after}]"); s.add_argument("--fresh", help="JSON {entity_id: current object}")
    s.add_argument("--proposal"); s.add_argument("--words"); s.add_argument("--ttl-hours", type=float, default=24)
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_approvals)
    s = sub.add_parser("state"); s.add_argument("sub", choices=["new", "stage", "move", "review-mode", "cancel", "resume", "show"])
    s.add_argument("--store-id", required=True); s.add_argument("--run"); s.add_argument("--question")
    s.add_argument("--mode", default="sequential"); s.add_argument("--cmap-version"); s.add_argument("--agent"); s.add_argument("--depends")
    s.add_argument("--evidence"); s.add_argument("--task"); s.add_argument("--to"); s.add_argument("--reason"); s.add_argument("--outputs")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_state)
    s = sub.add_parser("validate-doc"); s.add_argument("file"); s.add_argument("--schema", required=True,
        choices=["task-envelope", "evidence", "finding", "recommendation", "action-proposal", "approval", "review"]); s.set_defaults(f=cmd_validate)
    s = sub.add_parser("review"); s.add_argument("--store-id", required=True); s.add_argument("--findings", required=True)
    s.add_argument("--mode", default="self_check", choices=["self_check", "independent_context"]); s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_review)
    s = sub.add_parser("sales-change"); s.add_argument("--store-id", required=True); s.add_argument("--orders", help="file; or use --evidence-id")
    s.add_argument("--current"); s.add_argument("--baseline"); s.add_argument("--days", type=int, default=28); s.add_argument("--statuses")
    s.add_argument("--total", type=int, help="total records the source reported"); s.add_argument("--pages", type=int)
    s.add_argument("--source", default="mcp", choices=["mcp", "export"]); s.add_argument("--tool"); s.add_argument("--evidence-id")
    s.add_argument("--now"); s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_sales_change)
    s = sub.add_parser("report"); s.add_argument("--question", required=True); s.add_argument("--findings", required=True)
    s.add_argument("--plan"); s.add_argument("--review"); s.add_argument("--proposals"); s.add_argument("--store-id"); s.add_argument("--run")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_report)

    # ---- phase 2 specialists
    s = sub.add_parser("pricing"); s.add_argument("--store-id", required=True)
    s.add_argument("--products"); s.add_argument("--products-evidence"); s.add_argument("--total", type=int)
    s.add_argument("--orders"); s.add_argument("--orders-evidence"); s.add_argument("--orders-total", type=int)
    s.add_argument("--current"); s.add_argument("--baseline"); s.add_argument("--threshold", type=float, default=5)
    s.add_argument("--prices-include-vat", choices=["yes", "no"]); s.add_argument("--deep-discount", type=float, default=40)
    s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_pricing)
    s = sub.add_parser("pricing-breakeven"); s.add_argument("--price", required=True); s.add_argument("--cost", required=True)
    s.add_argument("--new-price", required=True); s.set_defaults(f=cmd_breakeven)
    s = sub.add_parser("growth"); gs = s.add_subparsers(dest="sub", required=True)
    for name in ("mix", "cohorts"):
        g = gs.add_parser(name); g.add_argument("--store-id", required=True); g.add_argument("--orders")
        g.add_argument("--evidence-id"); g.add_argument("--total", type=int); g.add_argument("--root", default=".nitaaq")
        if name == "mix":
            g.add_argument("--current"); g.add_argument("--baseline"); g.add_argument("--today"); g.add_argument("--days", type=int, default=28)
            g.add_argument("--first-order-dates", help="JSON {customer_id: date} from the store")
            g.add_argument("--question"); g.add_argument("--out"); g.add_argument("--md", action="store_true")
        else:
            g.add_argument("--as-of")
        g.set_defaults(f=cmd_growth)
    g = gs.add_parser("funnel"); g.add_argument("--current-steps", required=True, help="JSON {sessions, product_views, add_to_cart, checkout, purchases}")
    g.add_argument("--baseline-steps"); g.set_defaults(f=cmd_growth)
    g = gs.add_parser("sample-size"); g.add_argument("--baseline-rate", required=True, help="e.g. 2 (percent) or 0.02")
    g.add_argument("--lift", required=True, help="relative lift to detect, e.g. 20 (percent)"); g.add_argument("--daily", type=int, help="visitors per arm per day")
    g.set_defaults(f=cmd_growth)
    s = sub.add_parser("cro"); s.add_argument("--store-id", required=True)
    s.add_argument("--page", action="append", help="URL=saved.html"); s.add_argument("--fetch", action="append", help="public URL")
    s.add_argument("--product", action="append", help="URL=product.json (store price to match)")
    s.add_argument("--carts"); s.add_argument("--period"); s.add_argument("--question"); s.add_argument("--out")
    s.add_argument("--md", action="store_true"); s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_cro)

    # ---- phase 3 specialists
    s = sub.add_parser("seo-team"); s.add_argument("--store-id", required=True)
    s.add_argument("--page", action="append", required=True, help="URL=saved.html"); s.add_argument("--page-type")
    s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_seo_team)
    s = sub.add_parser("cannibalization"); s.add_argument("--store-id", required=True)
    s.add_argument("--gsc", help="Search Console rows with query and page (CSV or JSON)")
    s.add_argument("--titles", help="JSON [{id|url, title|name}]"); s.add_argument("--threshold", type=float, default=0.8)
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_cannibalization)
    s = sub.add_parser("reviews"); s.add_argument("--store-id", required=True); s.add_argument("--reviews")
    s.add_argument("--evidence-id"); s.add_argument("--total", type=int); s.add_argument("--current"); s.add_argument("--baseline")
    s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_reviews)

    s = sub.add_parser("ads", help="ads export analysis: audit, structure, search-terms, fatigue, social")
    s.add_argument("analysis", choices=["audit", "structure", "search-terms", "fatigue", "social"])
    s.add_argument("--store-id", required=True); s.add_argument("--file", help="ads export (CSV or JSON)"); s.add_argument("--evidence-id")
    s.add_argument("--platform", default="google_ads", choices=["google_ads", "meta", "tiktok", "snapchat"])
    s.add_argument("--total", type=int); s.add_argument("--brand"); s.add_argument("--competitors")
    s.add_argument("--keywords"); s.add_argument("--negatives"); s.add_argument("--min-clicks", type=int)
    s.add_argument("--min-conversions", type=int); s.add_argument("--margin", type=float, help="contribution margin %% for break-even ROAS")
    s.add_argument("--domains", help="store domains for landing pages"); s.add_argument("--as-of"); s.add_argument("--lag-days", type=int)
    s.add_argument("--min-impressions", type=int); s.add_argument("--drop-pct", type=int)
    s.add_argument("--frequency-limit", type=float); s.add_argument("--min-results", type=int)
    s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_ads)
    s = sub.add_parser("ads-copy", help="check draft ad copy against limits and store facts")
    s.add_argument("--copy", required=True, help="JSON {headlines: [...], descriptions: [...], facts?: {...}}")
    s.add_argument("--facts"); s.set_defaults(f=cmd_ads_copy)

    s = sub.add_parser("reconcile", help="Salla orders vs a destination's purchases/conversions")
    s.add_argument("--store-id", required=True); s.add_argument("--period", required=True, help="YYYY-MM-DD,YYYY-MM-DD")
    s.add_argument("--orders"); s.add_argument("--orders-evidence"); s.add_argument("--orders-total", type=int)
    s.add_argument("--conversions", help="destination export (CSV or JSON)"); s.add_argument("--conversions-evidence")
    s.add_argument("--conversions-total", type=int)
    s.add_argument("--destination", choices=["analytics", "ads"], default="analytics"); s.add_argument("--platform", default="ga4")
    s.add_argument("--date-basis", choices=["conversion", "click"], default="conversion")
    s.add_argument("--window-days", type=int); s.add_argument("--lag-days", type=int)
    s.add_argument("--as-of", help="date the destination data was exported (default today)")
    s.add_argument("--currency", default="SAR"); s.add_argument("--tolerance", type=int, default=tracking.DEFAULT_TOLERANCE_PCT)
    s.add_argument("--actions", help="conversion action names counted as purchase, comma separated")
    s.add_argument("--fault-evidence", help="JSON: level, purchase_event_observed, purchase_flow_captured for a real authorized purchase")
    s.add_argument("--question"); s.add_argument("--out"); s.add_argument("--md", action="store_true")
    s.add_argument("--root", default=".nitaaq"); s.set_defaults(f=cmd_reconcile)

    a = p.parse_args(argv)
    try:
        a.f(a)
    except (ValueError, KeyError, FileNotFoundError, state.InvalidTransition) as exc:
        _out({"error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
