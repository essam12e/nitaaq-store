"""Phase 5: ads specialists (auditor, Google Ads, search terms, creative, paid social) from exports only."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _path import ROOT, SKILL

from nitaaq import ads, evidence, review, routing

CLI = SKILL / "scripts" / "nitaaq_cli.py"
ADS_AGENTS = ["paid_media_auditor", "ppc", "search_query_analyst", "ad_creative", "paid_social"]

SEARCH_TERMS = [
    {"Campaign": "Search - عطور", "Search term": "عطر عود ملكي", "Clicks": "40", "Cost": "120", "Conversions": "3",
     "Conv. value": "900", "Day": "2026-09-05"},
    {"Campaign": "Search - عطور", "Search term": "كيف اسوي عطر عود", "Clicks": "25", "Cost": "60", "Conversions": "0",
     "Day": "2026-09-06"},
    {"Campaign": "Search - عطور", "Search term": "عطر عود مجاني", "Clicks": "12", "Cost": "30", "Conversions": "0",
     "Day": "2026-09-07"},
    {"Campaign": "Search - عطور", "Search term": "عطور نطاق", "Clicks": "15", "Cost": "20", "Conversions": "2",
     "Day": "2026-09-07"},
    {"Campaign": "Search - عطور", "Search term": "وظائف عطور", "Clicks": "11", "Cost": "25", "Conversions": "0",
     "Day": "2026-09-08"},
    {"Campaign": "Search - عطور", "Search term": "وظائف محل عطور", "Clicks": "4", "Cost": "8", "Conversions": "0",
     "Day": "2026-09-08"},
    {"Campaign": "Search - عطور", "Search term": "دهن عود", "Clicks": "30", "Cost": "70", "Conversions": "0",
     "Day": "2026-09-09"},
    {"Campaign": "Search - عطور", "Search term": "عطر عود رخيص", "Clicks": "20", "Cost": "50", "Conversions": "0",
     "Day": "2026-09-29"},
]
ST_PARAMS = {"platform": "google_ads", "brand": ["نطاق"], "keywords": ["عطر عود", "دهن عود"], "min_clicks": 10,
             "as_of": "2026-10-01", "lag_days": 7}


def campaigns():
    rows = []
    for d in range(28):
        day = (date(2026, 9, 1) + timedelta(days=d)).isoformat()
        rows += [
            {"Campaign": "Brand", "Day": day, "Clicks": "20", "Impressions": "200", "Cost": "10", "Conversions": "2",
             "Conv. value": "400", "Bid strategy type": "Manual CPC", "Final URL": "https://nitaaq.sa/"},
            {"Campaign": "Generic - عود", "Day": day, "Clicks": "30", "Impressions": "3000", "Cost": "60", "Conversions": "0.5",
             "Conv. value": "100", "Bid strategy type": "Target ROAS", "Final URL": "https://www.nitaaq.sa/oud"},
            {"Campaign": "Display", "Day": day, "Clicks": "10", "Impressions": "9000", "Cost": "15", "Conversions": "0",
             "Conv. value": "0", "Bid strategy type": "Manual CPC", "Final URL": "https://linktr.ee/nitaaq"},
        ]
    return rows


def save(store, rows, total=None):
    rows = [{**r, "id": f"row-{i}"} for i, r in enumerate(rows)]
    ev, recs = evidence.make_evidence(store.store_id, {"kind": "export", "operation": "export.google_ads"}, rows,
                                      records_total=len(rows) if total is None else total, kind="ads")
    store.save(ev, recs)
    return ev, recs


class SearchTermsTests(unittest.TestCase):
    def test_candidates_conflicts_and_wording(self):
        r = ads.run("search_terms", SEARCH_TERMS, ST_PARAMS)
        got = {(c["negative"], c["match"]) for c in r["candidates"]}
        self.assertIn(("كيف اسوي عطر عود", "exact"), got)
        self.assertIn(("وظائف", "phrase"), got)  # the searcher's spelling, not the normalized «وظايف»
        self.assertNotIn(("وظائف عطور", "exact"), got)  # covered by the phrase negative
        held = {c["negative"]: c["conflicts"] for c in r["held"]}
        self.assertIn("blocks_keyword", held["دهن عود"])  # an exact keyword is never proposed as a negative
        self.assertFalse(any("عطر عود رخيص" == c["negative"] for c in r["candidates"]))  # inside the conversion lag
        self.assertNotIn(("عطر", "phrase"), got)  # would block a converting term
        self.assertEqual(r["metrics"]["brand_leak_cost"], 20)
        self.assertEqual(r["by_intent"]["jobs"]["terms"], 2)
        self.assertEqual(ads.classify_term("وظائف عطور", [], []), "jobs")

    def test_proposal_needs_approval_and_review_passes(self):
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, SEARCH_TERMS)
            res = ads.run("search_terms", recs, ST_PARAMS)
            f = ads.finding(res, ev, ST_PARAMS)
            self.assertEqual(f["agent"], "search_query_analyst")
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            p = ads.negatives_proposal(res, "S1", f["finding_id"])
            self.assertEqual((p["operation"], p["sensitivity"], p["execution"]),
                             ("ads.add_negative_keywords", "high", "merchant_in_platform"))
            f["observed"][1]["current"] = "99"
            self.assertEqual(review.review_finding(f, store)["status"], "fail")


class AuditStructureTests(unittest.TestCase):
    def test_audit_checks(self):
        p = {"platform": "google_ads", "margin_pct": 30, "store_domains": ["nitaaq.sa"], "brand": ["nitaaq"],
             "min_clicks": 30, "min_conversions": 15, "as_of": "2026-10-20", "lag_days": 7}
        r = ads.run("audit", campaigns(), p)
        checks = {(i["check"], i["entity"]) for i in r["issues"]}
        self.assertIn(("zero_conversion_campaign", "Display"), checks)
        self.assertIn(("below_breakeven_roas", "Generic - عود"), checks)  # 100/30 = 3.33 > 1.67 attributed
        self.assertIn(("smart_bidding_low_volume", "Generic - عود"), checks)  # 14 conversions on target ROAS
        self.assertIn(("landing_outside_store", "account"), checks)  # linktr.ee, www. is the same store
        self.assertNotIn(("below_breakeven_roas", "Brand"), checks)
        self.assertEqual(r["issues"][0]["severity"], "high")

    def test_no_conversions_and_no_column(self):
        rows = [dict(r, Conversions="0", **{"Conv. value": "0"}) for r in campaigns()]
        self.assertEqual(ads.run("audit", rows, {})["issues"][0]["check"], "no_conversions_recorded")
        rows = [{k: v for k, v in r.items() if k not in ("Conversions", "Conv. value")} for r in campaigns()]
        r = ads.run("audit", rows, {})
        self.assertIn("no_conversions_column", r["flags"])
        self.assertFalse(any(i["check"] in ("no_conversions_recorded", "zero_conversion_campaign") for i in r["issues"]))

    def test_structure_split(self):
        r = ads.run("structure", SEARCH_TERMS, {"brand": ["نطاق"], "competitors": ["عبدالصمد"]})
        self.assertEqual(r["split"]["brand"]["cost"], 20)
        self.assertIn("non_brand", r["split"])
        self.assertIn("brand_terms_not_given", ads.run("structure", SEARCH_TERMS, {})["flags"])

    def test_review_rules(self):
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, campaigns())
            f = ads.finding(ads.run("audit", recs, {"margin_pct": 30}), ev, {"margin_pct": 30})
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            codes = lambda x: [i["code"] for i in review.review_finding(x, store)["issues"]]
            self.assertIn("roas_as_profit", codes(dict(f, interpretation_ar="الحملة مربحة لأن العائد على الإنفاق 4.")))
            self.assertIn("claimed_ads_execution", codes(dict(f, interpretation_ar="أوقفنا الحملات اللي ما تحوّل.")))
            orders_ev, _ = evidence.make_evidence("S1", {"kind": "mcp", "operation": "orders.list"}, [{"id": "1"}],
                                                  records_total=1)
            store.save(orders_ev, [{"id": "1"}])
            self.assertIn("metric_without_data", codes(dict(f, evidence_refs=[orders_ev["evidence_id"]], observed=[])))


class CreativeSocialTests(unittest.TestCase):
    def test_rsa_check(self):
        facts = {"price": "199", "sale_price": "149", "free_shipping": True}
        r = ads.rsa_check(["عطر عود ملكي بـ 149 ريال", "خصم 25% لفترة", "شحن مجاني لكل المملكة",
                           "الأفضل في السعودية والأصلي 100%", "عطر عود ملكي بـ 149 ريال"],
                          ["عطر عود ملكي ثابت طول اليوم، اطلبه الحين والتوصيل لباب البيت." * 2], facts=facts)
        got = [(i["check"], i.get("claim") or i.get("number")) for i in r["issues"]]
        self.assertIn(("duplicate_headline", None), got)
        self.assertIn(("headline_too_long", None), got)
        self.assertIn(("description_too_long", None), got)
        self.assertIn(("description_count", None), got)
        self.assertIn(("claim_needs_proof", "الأفضل"), got)
        self.assertIn(("unverified_number", "100"), got)
        self.assertNotIn(("unverified_number", "149"), got)
        self.assertNotIn(("unverified_number", "25"), got)  # (199-149)/199 = 25%
        self.assertFalse(any(c == "claim_needs_proof" and "شحن" in (x or "") for c, x in got))

    def test_fatigue(self):
        rows = []
        for d in range(14):
            day = (date(2026, 9, 1) + timedelta(days=d)).isoformat()
            rows.append({"Ad name": "A", "Day": day, "Impressions": "1000", "Clicks": "30" if d < 7 else "15", "Frequency": str(1 + d / 5)})
            rows.append({"Ad name": "B", "Day": day, "Impressions": "1000", "Clicks": "20"})
            rows.append({"Ad name": "C", "Day": day, "Impressions": "50", "Clicks": "1"})
        r = ads.run("fatigue", rows, {"min_impressions": 1000})
        self.assertEqual([x["ad"] for x in r["fatigued"]], ["A"])
        self.assertEqual(r["fatigued"][0]["ctr_change_pct"], -50)
        self.assertEqual(r["skipped"], 1)

    def test_social(self):
        rows = [{"Ad set name": "Prospecting", "Objective": "Sales", "Amount spent (SAR)": "500", "Results": "20", "Frequency": "1.8",
                 "Attribution setting": "7-day click"},
                {"Ad set name": "Retargeting", "Objective": "Sales", "Amount spent (SAR)": "300", "Results": "25", "Frequency": "6.2",
                 "Attribution setting": "7-day click"},
                {"Ad set name": "Video", "Objective": "Awareness", "Amount spent (SAR)": "200", "Results": "4", "Frequency": "2"}]
        r = ads.run("social", rows, {"platform": "meta", "frequency_limit": 4, "min_results": 10})
        self.assertEqual(r["metrics"]["frequency_high"], 1)
        self.assertEqual([s["ad_set"] for s in r["comparisons"]["Sales"]], ["Retargeting", "Prospecting"])
        self.assertNotIn("Awareness", r["comparisons"])
        self.assertIn("several_objectives", r["flags"])


class Phase5RoutingCliTests(unittest.TestCase):
    def test_no_ads_evidence_no_run(self):
        r = routing.route("وش رايك في حملاتي الإعلانية؟", {"orders.list", "products.list"})
        st = {s["agent"]: s["status"] for s in r["stages"]}
        self.assertEqual(st["paid_media_auditor"], "blocked")
        self.assertEqual(r["specialists_to_run"], [])  # no ads evidence: no ads specialist runs
        copy = routing.route("اكتب لي نص الاعلان لعطر العود", {"products.list"})
        self.assertEqual(copy["specialists_to_run"], ["ad_creative"])  # drafts from store facts, on request only

    def test_planned_agents_stay_honest(self):
        import copy
        from nitaaq import registry
        reg = copy.deepcopy(registry.load())
        next(a for a in reg["agents"] if a["id"] == "email_retention")["status"] = "planned"
        st = routing.route("أبي رسايل للعملاء القدام", {"orders.list", "customers.list"}, reg)["stages"]
        self.assertEqual(st[0]["status"], "not_available_yet")

    def test_focus_and_execution_path(self):
        avail = routing.available_from_map(None, ["google_ads", "search_terms", "social_ads"])
        self.assertIn("export:ads", avail)
        self.assertEqual(routing.route("أبي كلمات سلبية للحملة", avail)["specialists_to_run"], ["search_query_analyst"])
        self.assertEqual(routing.route("اعلانات سناب غالية علي", avail)["specialists_to_run"], ["paid_social"])
        self.assertEqual(routing.route("وش رايك في حملاتي", avail)["specialists_to_run"], ["paid_media_auditor"])
        stop = routing.route("أوقف الحملات اللي ما تجيب مبيعات", avail)
        self.assertEqual(stop["execution_path"], "ads_proposal_only")

    def test_cli_search_terms(self):
        with tempfile.TemporaryDirectory() as d:
            cols = list(SEARCH_TERMS[0])
            Path(d, "st.csv").write_text("\n".join([",".join(cols)] + [",".join(r.get(c, "") for c in cols) for r in SEARCH_TERMS]),
                                         encoding="utf-8")
            r = subprocess.run([sys.executable, str(CLI), "ads", "search-terms", "--store-id", "S1", "--file", "st.csv",
                                "--brand", "نطاق", "--keywords", "عطر عود,دهن عود", "--min-clicks", "10",
                                "--as-of", "2026-10-01", "--total", str(len(SEARCH_TERMS))],
                               cwd=d, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["review"]["counts"], {"pass": 1})
            self.assertEqual(out["proposals"][0]["operation"], "ads.add_negative_keywords")
            self.assertEqual(out["findings"][0]["proposed_action_ref"], "ap1")

    def test_generated_agents_read_only(self):
        for name in ("paid-media-auditor", "ppc", "search-query-analyst", "ad-creative", "paid-social"):
            md = (ROOT / "plugins" / "nitaaq-store" / "agents" / f"nitaaq-{name}.md").read_text(encoding="utf-8")
            self.assertIn("mcp__*", md, name)
            toml = (ROOT / "adapters" / "codex" / "custom-agents" / f"nitaaq-{name}.toml").read_text(encoding="utf-8")
            self.assertIn('sandbox_mode = "read-only"', toml, name)

    def test_never_calls_network(self):
        import ast
        tree = ast.parse((SKILL / "scripts" / "nitaaq" / "ads.py").read_text(encoding="utf-8"))
        mods = {n.name for x in ast.walk(tree) if isinstance(x, ast.Import) for n in x.names}
        mods |= {x.module or "" for x in ast.walk(tree) if isinstance(x, ast.ImportFrom)}
        self.assertFalse({m for m in mods if m.split(".")[0] in ("http", "socket", "requests", "ssl") or m == "urllib.request"})


if __name__ == "__main__":
    unittest.main()
