"""Phase 4: tracking reconciliation (Salla orders vs GA4 / ad platform conversions)."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _path import ROOT, SKILL

from nitaaq import evidence, review, routing, tracking

CLI = SKILL / "scripts" / "nitaaq_cli.py"
PERIOD = ["2026-09-01", "2026-09-30"]


def orders(n=60, status="تم التنفيذ"):
    return [{"id": str(1000 + i), "date": f"{date(2026, 9, 1) + timedelta(days=i % 30)} 12:00", "total": "115.00",
             "tax": "15.00", "shipping": "0", "status": status} for i in range(n)]


def conv_for(os_, skip=(), dates=None, value="115.00"):
    out = []
    for o in os_:
        if o["id"] in skip:
            continue
        d = (dates or {}).get(o["id"], o["date"][:10])
        out.append({"transaction_id": o["id"], "date": d, "value": value, "currency": "SAR"})
    return out


def save(store, op, rows, kind=None, total=None):
    ev, recs = evidence.make_evidence(store.store_id, {"kind": "export", "operation": op}, rows,
                                      records_total=len(rows) if total is None else total, kind=kind)
    store.save(ev, recs)
    return ev, recs


def finding(store, os_, cv, **params):
    oev, o = save(store, "orders.list", os_)
    pev, c = save(store, f"export.{params.get('destination', 'analytics')}",
                  [{**r, "id": f"row-{i}"} for i, r in enumerate(cv)], kind=params.get("destination", "analytics"))
    params = {"period": PERIOD, "as_of": "2026-10-20", **params}
    res = tracking.reconcile_from_calc(o, c, params)
    return res, tracking.reconciliation_finding(res, oev, pev, params)


class ReconcileRecordsTests(unittest.TestCase):
    def test_attribution_window_gap_is_explained_not_a_fault(self):
        """Acceptance (plan section 13): a gap explained by the attribution window is 'explained', never 'broken'."""
        os_ = orders()
        # Meta dates conversions by click: 8 orders were credited to clicks in August, and the export was
        # pulled 3 days after the period, so orders from the last days are not credited yet (7-day click window).
        shifted = {o["id"]: "2026-08-29" for o in os_[:8]}
        late = {o["id"] for o in os_ if o["date"][:10] >= "2026-09-24"}
        cv = conv_for(os_, skip=late, dates=shifted)
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            res, f = finding(store, os_, cv, destination="ads", platform="meta", date_basis="click", window_days=7,
                             as_of="2026-10-03")
            self.assertEqual(res["status"], "explained")
            self.assertGreater(abs(res["gap"]["pct"]), 5)
            self.assertEqual(res["categories"]["date_shift"], 8)
            self.assertEqual(res["categories"]["unsettled"], len(late))
            self.assertEqual(res["categories"]["missing_in_platform"], 0)
            self.assertNotIn("معطل", f["interpretation_ar"])
            self.assertEqual(review.review_finding(f, store)["status"], "pass")

    def test_unexplained_missing_is_investigate_with_alternatives(self):
        os_ = orders()
        cv = conv_for(os_, skip={o["id"] for o in os_[:12]})
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            res, f = finding(store, os_, cv)
            self.assertEqual(res["status"], "investigate")
            self.assertEqual(res["categories"]["missing_in_platform"], 12)
            self.assertTrue(f["alternatives_ar"])
            self.assertIn("دليل للتحقيق", f["interpretation_ar"])
            self.assertEqual(review.review_finding(f, store)["status"], "pass")

    def test_broken_wording_needs_a_captured_real_purchase(self):
        os_ = orders()
        cv = conv_for(os_, skip={o["id"] for o in os_[:12]})
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            _, f = finding(store, os_, cv)
            bad = dict(f, interpretation_ar="التتبع معطل في GA4 ولازم يتصلح.")
            self.assertIn("tracking_fault_unsupported", [i["code"] for i in review.review_finding(bad, store)["issues"]])
            fault = {"level": 2, "purchase_event_observed": False, "purchase_flow_captured": True}
            res, f = finding(store, os_, cv, fault_evidence=fault)
            self.assertEqual(res["status"], "event_missing")
            ok = dict(f, interpretation_ar=f["interpretation_ar"] + " تتبع الشراء في GA4 معطل في هذه العملية.")
            self.assertNotIn("tracking_fault_unsupported", [i["code"] for i in review.review_finding(ok, store)["issues"]])

    def test_duplicates_and_shared_event_id(self):
        os_ = orders()
        cv = conv_for(os_)
        dup = cv + [dict(cv[0])]
        res = tracking.reconcile(os_, dup, PERIOD, as_of="2026-10-20")
        self.assertEqual((res["status"], res["categories"]["duplicate"]), ("investigate", 1))
        paired = cv[1:] + [dict(cv[0], event_id="e1", source="browser"), dict(cv[0], event_id="e1", source="server")]
        res = tracking.reconcile(os_, paired, PERIOD, as_of="2026-10-20")
        self.assertEqual((res["status"], res["categories"]["duplicate"]), ("matched", 0))

    def test_value_basis_and_mismatch(self):
        os_ = orders()
        res = tracking.reconcile(os_, conv_for(os_, value="100.00"), PERIOD, as_of="2026-10-20")
        self.assertEqual((res["status"], res["categories"]["value_basis"], res["value_bases"]), ("explained", 60, {"ex_tax": 60}))
        res = tracking.reconcile(os_, conv_for(os_, value="77.00"), PERIOD, as_of="2026-10-20")
        self.assertEqual((res["status"], res["categories"]["value_mismatch"]), ("investigate", 60))

    def test_cancelled_pending_and_unknown_ids(self):
        os_ = orders() + [dict(orders(1)[0], id="2001", status="ملغي"), dict(orders(1)[0], id="2002", status="بإنتظار الدفع")]
        cv = conv_for(os_)
        res = tracking.reconcile(os_, cv, PERIOD, as_of="2026-10-20")
        self.assertEqual((res["categories"]["cancelled_after_conversion"], res["categories"]["pending_payment"]), (1, 1))
        self.assertEqual(res["salla"]["orders"], 60)
        self.assertEqual(res["status"], "explained")
        cv += [{"transaction_id": f"T-{i}", "date": "2026-09-10", "value": "50"} for i in range(5)]
        self.assertEqual(tracking.reconcile(os_, cv, PERIOD, as_of="2026-10-20")["status"], "investigate")

    def test_no_orders_is_not_comparable(self):
        res = tracking.reconcile(orders(), conv_for(orders()), ["2026-11-01", "2026-11-30"], as_of="2026-12-20")
        self.assertEqual(res["status"], "not_comparable")

    def test_ads_subset_is_expected(self):
        os_ = orders()
        res = tracking.reconcile(os_, conv_for(os_[:20]), PERIOD, destination="ads", platform="google_ads", as_of="2026-10-20")
        self.assertEqual((res["status"], res["categories"]["not_attributed"]), ("expected_subset", 40))

    def test_flags(self):
        os_ = orders()
        cv = [dict(c, currency="USD", conversion_action="Purchase") for c in conv_for(os_)]
        res = tracking.reconcile(os_, cv, PERIOD, as_of="2026-10-20", conversion_actions=["Purchase (CAPI)"])
        self.assertIn("currency_mismatch", res["flags"])
        self.assertIn("multiple_purchase_actions", res["flags"])
        self.assertEqual(res["categories"]["value_mismatch"], 0)  # values are not compared across currencies


class ReconcileTotalsTests(unittest.TestCase):
    def daily(self, os_, drop_from=None):
        by = {}
        for o in os_:
            by[o["date"][:10]] = by.get(o["date"][:10], 0) + 1
        return [{"date": d.replace("-", ""), "purchases": str(n)} for d, n in sorted(by.items())
                if not (drop_from and d >= drop_from)]

    def test_unsettled_days_explain_the_gap(self):
        os_ = orders(90)
        rows = self.daily(os_, drop_from="2026-09-29")  # exported on 30 Sep: GA4 has not processed the last two days
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            res, f = finding(store, os_, rows, as_of="2026-09-30")
            self.assertEqual(res["mode"], "totals")
            self.assertEqual(res["status"], "explained")
            self.assertEqual(res["settled_gap"]["count"], 0)
            self.assertIn("مقارنة مجاميع — لا تثبت أي طلب مفقود.", f["limitations_ar"])
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            hidden = dict(f, limitations_ar=[x for x in f["limitations_ar"] if "لا تثبت أي طلب مفقود" not in x])
            self.assertIn("totals_not_disclosed", [i["code"] for i in review.review_finding(hidden, store)["issues"]])
            f["observed"][1]["current"] = "999"
            self.assertEqual(review.review_finding(f, store)["status"], "fail")

    def test_settled_gap_is_investigated(self):
        os_ = orders(90)
        rows = self.daily(os_)
        rows[3]["purchases"] = rows[4]["purchases"] = "0"
        self.assertEqual(tracking.reconcile(os_, rows, PERIOD, as_of="2026-10-20")["status"], "investigate")


class Phase4CliRoutingTests(unittest.TestCase):
    def test_cli_with_ga4_csv(self):
        os_ = orders()
        with tempfile.TemporaryDirectory() as d:
            Path(d, "orders.json").write_text(json.dumps(os_, ensure_ascii=False), encoding="utf-8")
            lines = ["Transaction ID,Date,Purchase revenue"] + \
                    [f"{o['id']},{o['date'][:10].replace('-', '')},115" for o in os_[:55]]
            Path(d, "ga4.csv").write_text("\n".join(lines), encoding="utf-8")
            r = subprocess.run([sys.executable, str(CLI), "reconcile", "--store-id", "S1", "--period", ",".join(PERIOD),
                                "--orders", "orders.json", "--orders-total", "60", "--conversions", "ga4.csv",
                                "--as-of", "2026-10-20"], cwd=d, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["reconciliation"]["mode"], "records")
            self.assertEqual(out["reconciliation"]["categories"]["missing_in_platform"], 5)
            self.assertEqual(out["reconciliation"]["status"], "investigate")
            self.assertEqual(out["review"]["counts"], {"pass": 1})

    def test_routing(self):
        self.assertEqual(routing.route("البكسل ما يسجل المشتريات", {"orders.list", "export:analytics"})["specialists_to_run"],
                         ["tracking"])
        self.assertEqual(routing.route("البكسل ما يسجل المشتريات", {"public:pages"})["specialists_to_run"], ["tracking"])
        st = routing.route("البكسل ما يسجل المشتريات", {"orders.list"})["stages"][0]
        self.assertEqual(st["status"], "blocked")
        f = routing.followups(["tracking_discrepancy"], {"orders.list", "export:ads"})
        self.assertEqual((f[0]["agent"], f[0]["status"]), ("tracking", "run"))

    def test_generated_agents_read_only(self):
        md = (ROOT / "plugins" / "nitaaq-store" / "agents" / "nitaaq-tracking.md").read_text(encoding="utf-8")
        self.assertIn("mcp__*", md)
        toml = (ROOT / "adapters" / "codex" / "custom-agents" / "nitaaq-tracking.toml").read_text(encoding="utf-8")
        self.assertIn('sandbox_mode = "read-only"', toml)

    def test_never_sends_events(self):
        import ast
        tree = ast.parse((SKILL / "scripts" / "nitaaq" / "tracking.py").read_text(encoding="utf-8"))
        mods = {n.name for x in ast.walk(tree) if isinstance(x, ast.Import) for n in x.names}
        mods |= {x.module or "" for x in ast.walk(tree) if isinstance(x, ast.ImportFrom)}
        self.assertFalse({m for m in mods if m.split(".")[0] in ("http", "socket", "requests", "ssl")
                          or m == "urllib.request"})


if __name__ == "__main__":
    unittest.main()
