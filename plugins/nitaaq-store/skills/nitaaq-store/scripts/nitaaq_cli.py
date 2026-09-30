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
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nitaaq import __version__  # noqa: E402
from nitaaq import activation, capabilities, errors, exports, geo, gsc, products, redact, remediation, reports, seo_audit, tracking, visibility, writes  # noqa: E402


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
    led = writes.Ledger(a.ledger)
    payload = _load(a.payload) if a.payload else {}
    key = a.key or writes.operation_key(a.op, a.entity, payload)
    if a.action == "check":
        _out({"key": key, "state": led.check(key)})
    elif a.action == "begin":
        _out({"key": key, "state": led.begin(key, a.op, a.entity, a.summary or "")})
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
    s = sub.add_parser("ledger"); s.add_argument("action", choices=["check", "begin", "finish", "reconcile"]); s.add_argument("--ledger", default=".nitaaq/ledger.json")
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

    a = p.parse_args(argv)
    try:
        a.f(a)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        _out({"error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
