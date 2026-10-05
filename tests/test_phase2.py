"""Phase 2: pricing, growth and CRO specialists, their review rules and routing."""

import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from _path import ROOT, SKILL

from nitaaq import cro, evidence, growth, pricing, registry, review, routing

CLI = SKILL / "scripts" / "nitaaq_cli.py"
PRODUCTS = [
    {"id": "P1", "name": "عباية سوداء", "price": "200", "sale_price": "150", "cost": "160"},
    {"id": "P2", "name": "شنطة", "price": "100", "sale_price": "120", "cost": "40"},
    {"id": "P3", "name": "طرحة", "price": "80", "sale_price": "40", "cost": "20"},
    {"id": "P4", "name": "بخور", "price": "50"},
    {"id": "P5", "name": "مسك", "price": {"amount": 115, "currency": "SAR"}, "cost": "50"},
]
PAGE = """<html lang="ar"><body><h1>عباية سوداء</h1><img src="a.jpg"><img src="b.jpg">
<p>السعر 150 ر.س</p><button>أضف للسلة</button><p>باقي 2 قطع فقط!</p>
<p>ادفع مع تابي أو تمارا أو مدى</p><p>الشحن خلال 3 أيام</p></body></html>"""


def orders_with_prices():
    """P1 sells at 100 in the first 28 days and 80 after; P2 stays at 50."""
    from datetime import date, timedelta
    rows, i = [], 0
    for d in range(56):
        day = date(2026, 8, 10) + timedelta(days=d)
        for pid, price in (("P1", "100" if d < 28 else "80"), ("P2", "50")):
            i += 1
            rows.append({"id": str(i), "date": f"{day}T12:00:00+03:00", "status": "completed", "total": price,
                         "customer_id": f"c{i % 30}",
                         "items": [{"product_id": pid, "name": "عباية" if pid == "P1" else "شنطة",
                                    "quantity": 1, "price": price, "total": price}]})
    return rows


CUR, BASE = ["2026-09-07", "2026-10-04"], ["2026-08-10", "2026-09-06"]


def save(store, store_id, op, records, kind=None):
    ev, recs = evidence.make_evidence(store_id, {"kind": "mcp", "operation": op}, records, records_total=len(records), kind=kind)
    store.save(ev, recs)
    return ev, recs


class PricingTests(unittest.TestCase):
    def test_economics_and_issues(self):
        e = pricing.product_economics(PRODUCTS, prices_include_vat=False)
        rows = {r["id"]: r for r in e["rows"]}
        self.assertIn("below_cost", rows["P1"]["issues"])
        self.assertEqual(rows["P1"]["unit_margin"], Decimal("-10.00"))
        self.assertIn("invalid_sale", rows["P2"]["issues"])
        self.assertEqual(rows["P2"]["effective_price"], Decimal("100"))  # invalid sale price is ignored
        self.assertIn("deep_discount", rows["P3"]["issues"])
        self.assertIn("cost_missing", rows["P4"]["issues"])
        self.assertIsNone(rows["P4"]["unit_margin"])  # no cost, no margin
        self.assertEqual(rows["P5"]["unit_margin"], Decimal("65.00"))

    def test_vat_basis(self):
        e = pricing.product_economics([{"id": "X", "price": "115", "cost": "50"}], prices_include_vat=True)
        self.assertEqual(e["rows"][0]["net_price"], Decimal("100.00"))
        self.assertEqual(e["rows"][0]["unit_margin"], Decimal("50.00"))
        self.assertEqual(pricing.product_economics(PRODUCTS)["basis"], "price_as_listed_vat_unknown")

    def test_realized_price_change_and_signal(self):
        pc = pricing.price_changes(orders_with_prices(), CUR, BASE)
        moved = {r["product"]: r for r in pc["moved"]}
        self.assertEqual(list(moved), ["عباية (P1)"])
        self.assertEqual(moved["عباية (P1)"]["change_pct"], Decimal("-20.0"))
        self.assertEqual(pc["signals"], ["price_changed"])

    def test_breakeven_is_arithmetic(self):
        b = pricing.breakeven(200, 120, 180)
        self.assertEqual(b["units_change_needed_pct"], Decimal("33.3"))
        self.assertIn("ليس توقعاً", b["note_ar"])
        self.assertIsNone(pricing.breakeven(200, 120, 110)["units_change_needed_pct"])
        self.assertFalse(pricing.breakeven(200, None, 180)["ok"])

    def test_no_elasticity_anywhere(self):
        e = pricing.product_economics(PRODUCTS)
        blob = json.dumps(pricing.price_changes(orders_with_prices(), CUR, BASE), default=str) + json.dumps(e, default=str)
        self.assertNotIn("elasticity", blob)

    def test_findings_review_and_proposals(self):
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, "S1", "products.list", PRODUCTS)
            e = pricing.product_economics(recs, prices_include_vat=False)
            fs = pricing.economics_findings(e, ev, prices_include_vat=False)
            self.assertEqual(review.review_all(fs, store)["counts"], {"pass": len(fs)})
            props = pricing.invalid_sale_proposals(e, "S1", fs[0]["finding_id"])
            self.assertEqual([i["entity_id"] for i in props[0]["items"]], ["P2"])
            self.assertTrue(props[0]["customer_visible"])
            fs[1]["observed"][0]["current"] = "25"
            self.assertEqual(review.review_finding(fs[1], store)["status"], "fail")
            oev, orecs = save(store, "S1", "orders.list", orders_with_prices())
            pf = pricing.price_change_finding(pricing.price_changes(orecs, CUR, BASE), oev, CUR, BASE)
            self.assertEqual(review.review_finding(pf, store)["status"], "pass")


class GrowthTests(unittest.TestCase):
    def test_all_time_basis(self):
        orders = orders_with_prices()
        first = {f"c{i}": "2025-01-01" for i in range(30)}
        first["c3"] = "2026-09-20"
        m = growth.customer_mix(orders, CUR, first_order_dates=first)
        self.assertEqual(m["basis"], "all_time")
        self.assertEqual(m["new_customers"], 1)
        self.assertEqual(m["returning_customers"], 29)

    def test_short_history_is_not_compared(self):
        mc = growth.mix_change(orders_with_prices(), CUR, BASE)
        self.assertIn("insufficient_history", mc["flags"])
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, _ = save(store, "S1", "orders.list", orders_with_prices())
            f = growth.mix_finding(mc, ev)
            self.assertIn("لا يمكن مقارنة", f["interpretation_ar"])
            self.assertEqual(f["confidence"]["level"], "low")
            self.assertEqual(review.review_finding(f, store)["status"], "pass")

    def test_no_funnel_without_traffic(self):
        for steps in (None, {}, {"purchases": 50}, {"sessions": 0, "purchases": 5}):
            r = growth.funnel(steps)
            self.assertFalse(r["available"])
            self.assertIsNone(r["conversion_rate_pct"])

    def test_funnel_drop_signal(self):
        cur = {"sessions": 10000, "product_views": 4000, "add_to_cart": 600, "checkout": 300, "purchases": 150}
        base = {"sessions": 9000, "product_views": 3600, "add_to_cart": 720, "checkout": 360, "purchases": 200}
        r = growth.funnel(cur, base)
        self.assertEqual(r["conversion_rate_pct"], Decimal("1.5"))
        self.assertEqual(r["signals"], ["funnel_drop"])
        self.assertEqual(growth.funnel(cur, cur)["signals"], [])

    def test_sample_size_and_cac(self):
        s = growth.sample_size(2, 20, daily_visitors_per_arm=300)
        self.assertTrue(21000 <= s["per_arm"] <= 21200, s["per_arm"])
        self.assertEqual(s["days_needed"], -(-s["per_arm"] // 300))
        self.assertIsNone(growth.cac(None, 10)["cac"])
        self.assertEqual(growth.cac(1000, 10)["cac"], Decimal("100.00"))

    def test_incomplete_cohort_window_is_empty(self):
        rows = growth.cohorts(orders_with_prices(), as_of="2026-10-04")
        last = rows[-1]
        self.assertIsNone(last["returned_90d"])


class CroTests(unittest.TestCase):
    def test_page_checks(self):
        r = cro.page_checks(PAGE, "https://s.example/p/1", {"price": "200", "sale_price": "150"})
        v = {c["id"]: c["value"] for c in r["checks"]}
        self.assertEqual(v["price_visible"], "نعم")
        self.assertEqual(v["price_matches"], "نعم")
        self.assertEqual(v["buy_button"], "نعم")
        self.assertEqual(v["returns_policy"], "لا")
        self.assertEqual(v["images"], "2")
        self.assertEqual(r["verify_truthful"], ["باقي 2"])
        self.assertIn("returns_policy", r["missing"])
        mism = cro.page_checks(PAGE, "u", {"price": "175"})
        self.assertEqual({c["id"]: c["value"] for c in mism["checks"]}["price_matches"], "لا")

    def test_abandoned_carts(self):
        carts = [{"id": "k1", "date": "2026-10-01", "total": "300", "items": [{"name": "عباية"}]},
                 {"id": "k1", "date": "2026-10-01", "total": "300", "items": [{"name": "عباية"}]},
                 {"id": "k2", "date": "2026-10-02", "total": None, "items": [{"name": "عباية"}, {"name": "شنطة"}]}]
        r = cro.abandoned_carts(carts)
        self.assertEqual((r["carts"], r["value"], r["carts_without_total"]), (2, Decimal("300"), 1))
        self.assertEqual(r["top_products"][0], {"product": "عباية", "carts": 2})

    def test_page_finding_review(self):
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, "S1", "public.pages", [{"id": "u1", "url": "u1", "html": PAGE, "product": None}],
                            kind="public_page")
            f = cro.page_finding(cro.page_checks(PAGE, "u1"), ev)
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            self.assertIn("تأكد", " ".join(f["limitations_ar"]))
            f["observed"][3]["current"] = "لا" if f["observed"][3]["current"] == "نعم" else "نعم"
            self.assertEqual(review.review_finding(f, store)["status"], "fail")


class ReviewRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = evidence.EvidenceStore(self.tmp.name, "S1")
        self.ev, recs = save(self.store, "S1", "products.list", PRODUCTS)
        self.f = pricing.economics_findings(pricing.product_economics(recs), self.ev)[0]

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self, **changes):
        f = dict(self.f, **changes)
        return [i["code"] for i in review.review_finding(f, self.store)["issues"]]

    def test_forecast_needs_assumptions(self):
        text = "إذا نزلت السعر بتزيد المبيعات 30% خلال شهر."
        self.assertIn("unsupported_forecast", self.codes(interpretation_ar=text))
        ok = self.codes(interpretation_ar=text, expected_effect={"kind": "range", "range_ar": "10-30%",
                                                                 "assumptions_ar": ["نفس الزيارات", "تجربة سابقة"]})
        self.assertNotIn("unsupported_forecast", ok)

    def test_elasticity_and_dark_patterns(self):
        self.assertIn("inferred_elasticity", self.codes(interpretation_ar="المرونة السعرية لمنتجاتك عالية."))
        self.assertIn("dark_pattern", self.codes(interpretation_ar="أضف عداد تنازلي على صفحة المنتج."))
        self.assertIn("dark_pattern", self.codes(interpretation_ar="حط باقي 3 قطع فقط فوق زر الشراء."))

    def test_conversion_rate_needs_traffic(self):
        self.assertIn("metric_without_data", self.codes(metric="conversion_rate"))
        tev, _ = save(self.store, "S1", "analytics.sessions", [{"id": "d1", "sessions": 100}], kind="traffic")
        self.assertNotIn("metric_without_data", self.codes(metric="conversion_rate", evidence_refs=[tev["evidence_id"]],
                                                           observed=[{"label_ar": "x"}]))


class Phase2RoutingTests(unittest.TestCase):
    ALL = {"orders.list", "products.list", "public:pages", "customers.list"}

    def test_active_with_data(self):
        for msg, agent in (("هل أسعاري مناسبة؟", "pricing"), ("العملاء الجدد قلوا", "growth"),
                           ("معدل التحويل عندي ضعيف", "cro")):
            self.assertEqual(routing.route(msg, self.ALL)["specialists_to_run"], [agent], msg)

    def test_broad_respects_cap(self):
        r = routing.route("حلل متجري وقل لي وش أحسن", self.ALL)
        self.assertEqual(r["specialists_to_run"], ["store_analytics", "pricing", "growth", "cro"])
        self.assertEqual({s["agent"]: s["status"] for s in r["stages"]}["seo_geo"], "deferred")
        self.assertLessEqual(len(r["specialists_to_run"]), registry.limits()["max_specialists_broad"])
        self.assertTrue(all(s["depends_on"] == ["store_analytics"] for s in r["stages"]
                            if s["status"] == "run" and s["agent"] != "store_analytics"))

    def test_conditional_followups(self):
        f = {x["signal"]: (x["agent"], x["status"]) for x in
             routing.followups(["funnel_drop", "discount_heavy", "traffic_available"], {"public:pages", "orders.list"})}
        self.assertEqual(f["funnel_drop"], ("cro", "run"))
        self.assertEqual(f["discount_heavy"], ("pricing", "blocked"))
        self.assertEqual(f["traffic_available"], ("growth", "run"))
        again = routing.followups(["funnel_drop"], {"public:pages"}, already=["cro"])
        self.assertEqual(again, [])

    def test_cro_blocked_without_pages(self):
        r = routing.route("معدل التحويل عندي ضعيف", {"orders.list"})
        self.assertEqual(r["stages"][0]["status"], "blocked")
        self.assertEqual(r["specialists_to_run"], [])


class Phase2CliTests(unittest.TestCase):
    def run_cli(self, *args, cwd):
        r = subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_commands(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "products.json").write_text(json.dumps(PRODUCTS, ensure_ascii=False), encoding="utf-8")
            Path(d, "orders.json").write_text(json.dumps(orders_with_prices(), ensure_ascii=False), encoding="utf-8")
            Path(d, "page.html").write_text(PAGE, encoding="utf-8")
            out = json.loads(self.run_cli("pricing", "--store-id", "S1", "--products", "products.json", "--total", "5",
                                          "--orders", "orders.json", "--current", ",".join(CUR), "--baseline", ",".join(BASE),
                                          "--prices-include-vat", "no", cwd=d))
            self.assertEqual(out["review"]["counts"], {"pass": len(out["findings"])})
            self.assertIn("price_changed", out["findings"][0]["signals"])
            self.assertEqual(len(out["proposals"]), 1)
            md = self.run_cli("growth", "mix", "--store-id", "S1", "--orders", "orders.json", "--current", ",".join(CUR),
                              "--baseline", ",".join(BASE), "--md", cwd=d)
            self.assertIn("ما لا نعرفه", md)
            out = json.loads(self.run_cli("cro", "--store-id", "S1", "--page", "https://s.example/p/1=page.html", cwd=d))
            self.assertEqual(out["review"]["counts"], {"pass": 1})
            be = json.loads(self.run_cli("pricing-breakeven", "--price", "200", "--cost", "120", "--new-price", "180", cwd=d))
            self.assertEqual(be["units_change_needed_pct"], "33.3")

    def test_generated_agents(self):
        names = sorted(p.stem for p in (ROOT / "plugins" / "nitaaq-store" / "agents").glob("*.md"))
        for n in ("nitaaq-cro", "nitaaq-growth", "nitaaq-pricing", "nitaaq-reviewer", "nitaaq-store-analytics"):
            self.assertIn(n, names)


if __name__ == "__main__":
    unittest.main()
