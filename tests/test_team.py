"""Phase 0/1 team foundation: ledger concurrency, approvals, evidence, metrics,
routing, review, run state, registry and generated agents."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from _path import FIX, ROOT, SKILL

from nitaaq import approvals, contracts, evidence, findings, metrics, registry, review, routing, state, writes

CLI = SKILL / "scripts" / "nitaaq_cli.py"
RIYADH = timezone(timedelta(hours=3))


def orders_history(start=date(2026, 8, 10), days=56, before=6, after=4, out_product=None):
    """Synthetic orders: `before` per day for the first half, `after` per day for the second half."""
    rows, i = [], 0
    for d in range(days):
        day = start + timedelta(days=d)
        n = before if d < days // 2 else after
        for k in range(n):
            i += 1
            pid = "P1" if k % 2 == 0 else "P2"
            if out_product and d >= days // 2 and pid == out_product:
                pid = "P2"
            rows.append({"id": str(i), "date": f"{day.isoformat()}T12:00:00+03:00", "status": "completed",
                         "total": "100", "city": "الرياض" if k < 3 else "جدة",
                         "items": [{"product_id": pid, "name": "عباية سوداء" if pid == "P1" else "شنطة",
                                    "quantity": 1, "price": "100", "total": "100"}]})
    return rows


CUR, BASE = ["2026-09-07", "2026-10-04"], ["2026-08-10", "2026-09-06"]
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=RIYADH)


def cli(*args, cwd):
    r = subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    return r


class LedgerConcurrencyTests(unittest.TestCase):
    CHILD = (
        "import sys; sys.path.insert(0, %r)\n"
        "from nitaaq import writes\n"
        "led = writes.Ledger(sys.argv[1])\n"
        "out = []\n"
        "for k in sys.argv[2].split(','):\n"
        "    out.append(led.begin(k, 'products.update', k))\n"
        "print(','.join(out))\n"
    ) % str(SKILL / "scripts")

    def _run(self, path, keys_per_proc):
        procs = [subprocess.Popen([sys.executable, "-c", self.CHILD, str(path), ",".join(ks)],
                                  stdout=subprocess.PIPE, text=True) for ks in keys_per_proc]
        outs = []
        for p in procs:
            o, _ = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0)
            outs.append(o.strip().split(","))
        return outs

    def test_same_key_only_one_process_goes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.json"
            outs = self._run(path, [["k1"]] * 8)
            self.assertEqual(sum(o.count("go") for o in outs), 1)
            self.assertEqual(sum(o.count("reconcile_first") for o in outs), 7)

    def test_distinct_keys_are_never_lost(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.json"
            keys = [[f"p{p}_{i}" for i in range(15)] for p in range(6)]
            outs = self._run(path, keys)
            self.assertTrue(all(x == "go" for o in outs for x in o))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 90)

    def test_store_scoped_keys_and_paths(self):
        k_old = writes.operation_key("products.update", "1", {"price": 10})
        self.assertEqual(k_old, writes.operation_key("products.update", "1", {"price": 10}, None, None))
        k_a = writes.operation_key("products.update", "1", {"price": 10}, store_id="A")
        k_b = writes.operation_key("products.update", "1", {"price": 10}, store_id="B")
        self.assertNotEqual(k_a, k_b)
        self.assertNotEqual(k_a, k_old)
        self.assertEqual(writes.ledger_path(".nitaaq", "A"), Path(".nitaaq/stores/A/ledger.json"))
        for bad in ("", "..", "a/b", "a\\b", "."):
            with self.assertRaises(ValueError):
                writes.ledger_path(".nitaaq", bad)


class ApprovalTests(unittest.TestCase):
    ITEMS = [{"entity_id": "p1", "before": {"price": "200"}, "after": {"price": "150"}}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = approvals.ApprovalStore.for_store(self.tmp.name, "S1")
        self.ap = self.st.grant("S1", "products.update", self.ITEMS, merchant_words="موافق")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, **kw):
        args = dict(approval_id=self.ap["approval_id"], store_id="S1", operation="products.update",
                    items=[{"entity_id": "p1", "after": {"price": "150"}}], fresh={"p1": {"price": "200.00"}})
        args.update(kw)
        return self.st.check(**args)

    def test_valid(self):
        r = self.check()
        self.assertTrue(r["ok"], r)

    def test_rejects_scope_changes(self):
        self.assertIn("wrong_store", self.check(store_id="S2")["reasons"])
        self.assertIn("wrong_operation", self.check(operation="products.delete")["reasons"])
        self.assertIn("wrong_account", self.check(account_id="acc9")["reasons"])
        self.assertIn("payload_changed", self.check(items=[{"entity_id": "p1", "after": {"price": "140"}}])["reasons"])
        self.assertIn("entity_not_approved", self.check(items=[{"entity_id": "p2", "after": {"price": "150"}}],
                                                        fresh={"p2": {"price": "200"}})["reasons"])

    def test_stale_snapshot_and_missing_snapshot(self):
        self.assertIn("stale_snapshot", self.check(fresh={"p1": {"price": "210"}})["reasons"])
        self.assertIn("snapshot_missing", self.check(fresh=None)["reasons"])

    def test_expiry_replay_revoke(self):
        later = datetime.now(timezone.utc) + timedelta(hours=25)
        self.assertIn("expired", self.check(now=later)["reasons"])
        self.st.mark_used(self.ap["approval_id"])
        r = self.check()
        self.assertFalse(r["ok"])
        self.assertIn("used", r["reasons"])
        self.assertTrue(r["reasons_ar"])
        ap2 = self.st.grant("S1", "products.update", self.ITEMS)
        self.assertTrue(self.st.revoke(ap2["approval_id"]))
        self.assertIn("revoked", self.check(approval_id=ap2["approval_id"])["reasons"])
        self.assertEqual(self.check(approval_id="ap_nope")["reasons"], ["not_found"])

    def test_item_limit(self):
        many = [{"entity_id": f"p{i}", "before": {"q": 1}, "after": {"q": 2}} for i in range(3)]
        with self.assertRaises(ValueError):
            self.st.grant("S1", "inventory.set", many, max_items=2)

    def test_sensitive_ops(self):
        self.assertTrue(approvals.requires_approval({"id": "inventory.set"}, ["quantity"])[0])


class EvidenceTests(unittest.TestCase):
    def test_dedupe_and_coverage(self):
        recs = [{"id": "1", "total": 5}, {"id": "1", "total": 5}, {"id": "2", "total": 7}, {"id": "2", "total": 8}, {"total": 1}]
        ev, unique = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, recs, records_total=4)
        self.assertEqual(ev["coverage"]["duplicates_removed"], 2)
        self.assertEqual(ev["coverage"]["conflicting_duplicates"], ["2"])
        self.assertEqual(ev["coverage"]["records_without_key"], 1)
        self.assertFalse(ev["coverage"]["complete"] and len(unique) < 4)
        ev2, _ = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, recs)
        self.assertIsNone(ev2["coverage"]["complete"])
        self.assertIn("غير معروف", evidence.coverage_note_ar(ev2))

    def test_cross_store_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            ev, recs = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, [{"id": "1"}], records_total=1)
            path = evidence.EvidenceStore(d, "S1").save(ev, recs)
            with self.assertRaises(evidence.CrossStoreError):
                evidence.EvidenceStore(d, "S2").save(ev, recs)
            # a copy placed under another store is still refused on load
            other = Path(d) / "stores" / "S2" / "evidence"
            other.mkdir(parents=True)
            (other / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(evidence.CrossStoreError):
                evidence.EvidenceStore(d, "S2").load(ev["evidence_id"])
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["records"][0]["id"] = "99"
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(ValueError):
                evidence.EvidenceStore(d, "S1").load(ev["evidence_id"])

    def test_cache_key_depends_on_store(self):
        self.assertNotEqual(evidence.cache_key("S1", "orders.list", period=CUR), evidence.cache_key("S2", "orders.list", period=CUR))


class MetricsTests(unittest.TestCase):
    def test_decline_and_exact_decomposition(self):
        orders = orders_history()
        r = metrics.compare_periods(orders, CUR, BASE, now=NOW, records_total=len(orders))
        self.assertEqual(r["status"], "decline")
        self.assertEqual(r["change"]["sales_pct"], Decimal("-33.33"))
        d = r["decomposition"]
        self.assertEqual(d["order_count_effect"] + d["order_value_effect"], r["change"]["sales"])
        dec = metrics.decompose(orders, CUR, BASE, "city")
        self.assertEqual(sum(Decimal(str(x["change"])) for x in dec["rows"]), dec["total_change"])

    def test_invalid_comparisons(self):
        orders = orders_history()
        partial = metrics.compare_periods(orders, CUR, BASE, now=datetime(2026, 10, 4, 9, tzinfo=RIYADH), records_total=len(orders))
        self.assertEqual(partial["status"], "not_comparable")
        self.assertIn("partial_current_day", partial["flags"])
        self.assertIn("unequal_periods", metrics.compare_periods(orders, CUR, ["2026-08-20", "2026-09-06"], now=NOW,
                                                                 records_total=len(orders))["flags"])
        self.assertIn("overlapping_periods", metrics.compare_periods(orders, CUR, ["2026-09-01", "2026-09-28"], now=NOW,
                                                                     records_total=len(orders))["flags"])
        inc = metrics.compare_periods(orders, CUR, BASE, now=NOW, records_total=len(orders) + 50)
        self.assertEqual(inc["status"], "not_comparable")
        self.assertIn("incomplete_data", inc["flags"])
        unk = metrics.compare_periods(orders, CUR, BASE, now=NOW)
        self.assertIn("unknown_completeness", unk["flags"])
        self.assertEqual(unk["status"], "decline")

    def test_default_periods_exclude_today(self):
        p = metrics.default_periods("2026-10-05")
        self.assertEqual(p["current"], CUR)
        self.assertEqual(p["baseline"], BASE)

    def test_dictionary(self):
        self.assertIn("gmv", [m["id"] for m in metrics.load_dictionary()["metrics"]])
        self.assertEqual(metrics.metric("aov")["id"], "aov")


class RoutingTests(unittest.TestCase):
    def test_labelled_cases(self):
        cases = json.loads((FIX / "routing_cases.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 40)
        wrong = [(c["text"], routing.classify(c["text"])) for c in cases
                 if (routing.classify(c["text"])["intent"], routing.classify(c["text"])["kind"]) != (c["intent"], c["kind"])]
        self.assertEqual(wrong, [])

    def test_route_respects_capabilities(self):
        r = routing.route("ليش المبيعات نازلة؟", {"orders.list"})
        self.assertEqual(r["specialists_to_run"], ["store_analytics"])
        self.assertTrue(r["review_required"])
        blocked = routing.route("ليش المبيعات نازلة؟", set())
        self.assertEqual(blocked["specialists_to_run"], [])
        self.assertIn("blocker_ar", blocked)
        self.assertTrue(routing.route("ليش المبيعات نازلة؟", {"export:orders"})["specialists_to_run"])

    def test_planned_and_existing_are_honest(self):
        broad = routing.route("حلل متجري وقل لي وش أحسن", {"orders.list"})
        st = {s["agent"]: s["status"] for s in broad["stages"]}
        self.assertEqual(st["store_analytics"], "run")
        self.assertEqual(st["pricing"], "not_available_yet")
        self.assertEqual(st["seo_geo"], "existing_service")
        self.assertLessEqual(len(broad["specialists_to_run"]), registry.limits()["max_specialists_broad"])
        ads = routing.route("أوقف الحملات اللي ما تجيب مبيعات", {"orders.list"})
        self.assertEqual(ads["specialists_to_run"], [])
        self.assertEqual(routing.route("كم طلب جاني اليوم؟", {"orders.list"})["stages"], [])

    def test_followups(self):
        f = routing.followups(["stock_out", "price_changed"], {"orders.list", "inventory.read"})
        by = {x["agent"]: x["status"] for x in f}
        self.assertEqual(by["salla_operator"], "run")
        self.assertEqual(by["pricing"], "not_available_yet")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = evidence.EvidenceStore(self.tmp.name, "S1")
        orders = orders_history()
        self.ev, recs = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, orders,
                                               period=[BASE[0], CUR[1]], records_total=len(orders))
        self.store.save(self.ev, recs)
        self.cmp = metrics.compare_periods(recs, CUR, BASE, now=NOW, records_total=len(orders))
        self.f = findings.sales_change_finding(self.cmp, self.ev)
        for o in self.f["observed"]:
            o["calc"]["now"] = NOW.isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, f):
        return review.review_finding(f, self.store)

    def test_clean_finding_passes_and_matches_schema(self):
        self.assertEqual(contracts.validate(self.f, "finding"), [])
        self.assertEqual(self.status(self.f)["status"], "pass")

    def test_number_mismatch_fails(self):
        self.f["observed"][0]["current"] = "12000"
        r = self.status(self.f)
        self.assertEqual(r["status"], "fail")
        self.assertIn("number_mismatch", [i["code"] for i in r["issues"]])

    def test_unsupported_cause_benchmark_guarantee(self):
        self.f["interpretation_ar"] = "المبيعات نزلت بسبب ارتفاع الأسعار، والمعدل الطبيعي 3%، ونضمن ترجع."
        self.f["confidence"]["level"] = "high"
        codes = [i["code"] for i in self.status(self.f)["issues"]]
        for c in ("unsupported_cause", "high_confidence_cause", "no_alternatives", "unsourced_benchmark", "guarantee"):
            self.assertIn(c, codes)
        self.assertEqual(self.status(self.f)["status"], "revise")

    def test_limitation_wording_is_not_a_cause(self):
        self.f["limitations_ar"] = ["الأرقام تقريبية بسبب نقص بيانات الزيارات."]
        self.assertEqual(self.status(self.f)["status"], "pass")

    def test_partial_data_must_be_disclosed(self):
        ev, recs = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, orders_history()[:50],
                                          period=[BASE[0], CUR[1]])
        self.store.save(ev, recs)
        f = dict(self.f, evidence_refs=[ev["evidence_id"]], observed=[], limitations_ar=[], coverage_note_ar="كامل",
                 confidence={"level": "high", "rationale_ar": "x"})
        self.assertIn("partial_not_disclosed", [i["code"] for i in self.status(f)["issues"]])

    def test_wrong_store_and_not_independent(self):
        other = evidence.EvidenceStore(self.tmp.name, "S2")
        self.assertEqual(review.review_finding(self.f, other)["status"], "fail")
        g = json.loads(json.dumps(self.f, default=str))
        g["agent"], g["finding_id"] = "pricing", "f9"
        out = review.review_all([self.f, g], self.store)
        self.assertEqual([n["code"] for n in out["batch_notes"]], ["not_independent"])

    def test_report_drops_failed_findings(self):
        bad = json.loads(json.dumps(self.f, default=str))
        bad["finding_id"] = "f2"
        bad["observed"][0]["current"] = "1"
        rv = review.review_all([self.f, bad], self.store)
        text = findings.unified_report_ar("ليش المبيعات نازلة؟", routing.route("ليش المبيعات نازلة؟", {"orders.list"}),
                                          [self.f, bad], rv)
        for h in ("الخلاصة", "الحقائق", "الأسباب المحتملة", "ما لا نعرفه", "المقترحات", "طريقة التحليل"):
            self.assertIn(h, text)
        self.assertIn("لم تجتز المراجعة", text)
        self.assertIn("ليست مستقلة", text)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = state.Run.create(self.tmp.name, "S1", "ليش المبيعات نازلة؟ token=abc123secretvalue")

    def tearDown(self):
        self.tmp.cleanup()

    def test_transitions_dependencies_attempts(self):
        t1 = self.run.add_stage("store_analytics")
        t2 = self.run.add_stage("reviewer", depends_on=[t1])
        with self.assertRaises(state.InvalidTransition):
            self.run.transition(t2, "running")
        with self.assertRaises(state.InvalidTransition):
            self.run.transition(t1, "done")
        self.run.transition(t1, "running")
        self.run.transition(t1, "failed", reason_ar="انقطع الاتصال")
        self.run.transition(t1, "running")
        self.run.transition(t1, "failed")
        with self.assertRaises(state.InvalidTransition):
            self.run.transition(t1, "running")

    def test_cancel_and_resume(self):
        t1 = self.run.add_stage("store_analytics")
        t2 = self.run.add_stage("reviewer", depends_on=[t1])
        self.run.transition(t1, "running")
        self.assertEqual(self.run.resume_plan()["interrupted_or_retryable"], [t1])
        self.run.request_cancel()
        d = self.run.data()
        self.assertEqual({s["task_id"]: s["status"] for s in d["stages"]}[t2], "cancelled")
        with self.assertRaises(state.InvalidTransition):
            self.run.add_stage("pricing")
        self.run.transition(t1, "done")
        self.assertEqual(self.run.data()["status"], "cancelled")

    def test_audit_has_no_secrets_and_store_is_checked(self):
        self.run.add_stage("store_analytics")
        raw = self.run.audit_path.read_text(encoding="utf-8") + self.run.path.read_text(encoding="utf-8")
        self.assertNotIn("abc123secretvalue", raw)
        self.assertEqual(state.scrub({"api_key": "x", "n": 1}), {"api_key": "[removed]", "n": 1})
        self.assertNotIn("ghp_", state.scrub("رمزي ghp_abcdefghijklmnopqrstuvwxyz"))
        self.assertEqual(state.scrub("وقف الحملة"), "وقف الحملة")
        with self.assertRaises((ValueError, FileNotFoundError)):
            state.Run(self.tmp.name, "S2", self.run.run_id).data()


class RegistryAndAgentsTests(unittest.TestCase):
    def test_registry_valid(self):
        self.assertEqual(registry.validate(), [])

    def test_registry_rejects_unsafe_native(self):
        reg = registry.load()
        a = registry.get("store_analytics", reg)
        a["native"]["claude_code"]["tools"].append("Write")
        a["native"]["claude_code"]["disallowedTools"].remove("mcp__*")
        reg["limits"]["max_delegation_depth"] = 2
        errs = " ".join(registry.validate(reg))
        for s in ("Write", "mcp__*", "max_delegation_depth"):
            self.assertIn(s, errs)

    def test_generated_agents_current_and_read_only(self):
        import gen_agents
        self.assertEqual(gen_agents.check(), [])
        for f in (ROOT / "plugins" / "nitaaq-store" / "agents").glob("*.md"):
            text = f.read_text(encoding="utf-8")
            self.assertIn("mcp__*", text.split("---")[1])
            self.assertNotIn("Write,", text.split("---")[1].split("tools:")[1].split("\n")[0])
        try:
            import tomllib
        except ImportError:
            return
        for f in (ROOT / "adapters" / "codex" / "custom-agents").glob("*.toml"):
            d = tomllib.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(d["sandbox_mode"], "read-only")
            self.assertTrue(d["name"].startswith("nitaaq-"))

    def test_install_agents_never_touches_foreign_files(self):
        import install
        with tempfile.TemporaryDirectory() as h:
            home = Path(h)
            foreign = home / ".codex" / "agents" / "nitaaq-reviewer.toml"
            foreign.parent.mkdir(parents=True)
            foreign.write_text("mine", encoding="utf-8")
            res = install.agents_action("codex", "user", None, home, remove=False, dry=False)
            self.assertIn("refused", [r["result"] for r in res])
            self.assertEqual(foreign.read_text(encoding="utf-8"), "mine")
            install.agents_action("codex", "user", None, home, remove=True, dry=False)
            self.assertTrue(foreign.exists())
            self.assertFalse((home / ".codex" / "agents" / "nitaaq-store-analytics.toml").exists())


class SalesDropEndToEndTests(unittest.TestCase):
    """Orders fetched through a real MCP transport from the mock server, then the CLI slice."""

    def fetch(self, rows, faults=()):
        from test_mcp_e2e import StdioMCP
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(rows, fh)
        os.environ["MOCK_ORDERS_FILE"], os.environ["MOCK_PAGE_SIZE"] = fh.name, "50"
        try:
            m = StdioMCP(faults)
            got, page, total, pages = [], 1, None, 0
            while True:
                ok, data = m.call("list_orders", page=page)
                self.assertTrue(ok)
                pages += 1
                got += data["data"]
                total = data["pagination"]["total"]
                if page >= data["pagination"]["total_pages"]:
                    break
                page += 1
            m.close()
        finally:
            del os.environ["MOCK_ORDERS_FILE"], os.environ["MOCK_PAGE_SIZE"]
            os.unlink(fh.name)
        return got, total, pages

    def slice(self, rows, total, pages, now="2026-10-05T10:00:00+03:00", extra=()):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "orders.json").write_text(json.dumps(rows), encoding="utf-8")
            r = cli("sales-change", "--store-id", "S1", "--orders", "orders.json", "--total", str(total),
                    "--pages", str(pages), "--now", now, *extra, cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

    def test_real_drop_with_stockout_signal(self):
        rows, total, pages = self.fetch(orders_history(out_product="P1"))
        self.assertEqual(len(rows), total)
        out = self.slice(rows, total, pages)
        self.assertEqual(out["comparison"]["status"], "decline")
        self.assertEqual(out["review"]["counts"], {"pass": len(out["findings"])})
        prod = next(f for f in out["findings"] if (f.get("entity") or {}).get("type") == "product")
        dec_rows = [{"value": o["label_ar"], "change": Decimal(o["current"]) - Decimal(o["baseline"])} for o in prod["observed"]]
        signals = findings.stock_signals(dec_rows, {"P1": 0, "P2": 12})
        self.assertEqual(signals, ["stock_out"])
        self.assertEqual(routing.followups(signals, {"orders.list", "inventory.read"})[0]["agent"], "salla_operator")

    def test_partial_day_is_not_a_drop(self):
        rows, total, pages = self.fetch(orders_history())
        out = self.slice(rows, total, pages, now="2026-10-04T09:00:00+03:00",
                         extra=("--current", ",".join(CUR), "--baseline", ",".join(BASE)))
        self.assertEqual(out["comparison"]["status"], "not_comparable")
        self.assertIn("لا يمكن الحكم", out["findings"][0]["interpretation_ar"])
        self.assertEqual(len(out["findings"]), 1)

    def test_incomplete_pagination_is_not_a_drop(self):
        rows, total, pages = self.fetch(orders_history(before=6, after=6), faults=("orders_truncated",))
        self.assertLess(len(rows), total)
        out = self.slice(rows, total, pages)
        self.assertEqual(out["comparison"]["status"], "not_comparable")
        self.assertIn("incomplete_data", out["comparison"]["flags"])
        self.assertFalse(out["evidence"]["coverage"]["complete"])

    def test_markdown_report(self):
        rows = orders_history()
        with tempfile.TemporaryDirectory() as d:
            Path(d, "orders.json").write_text(json.dumps(rows), encoding="utf-8")
            r = cli("sales-change", "--store-id", "S1", "--orders", "orders.json", "--total", str(len(rows)),
                    "--now", "2026-10-05T10:00:00+03:00", "--md", cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("انخفضت المبيعات 33.33%", r.stdout)
            self.assertIn("ما لا نعرفه", r.stdout)
            self.assertTrue((Path(d) / ".nitaaq" / "stores" / "S1" / "evidence").is_dir())


if __name__ == "__main__":
    unittest.main()
