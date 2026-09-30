import json
import unittest

import _path
from nitaaq import gsc, remediation, capabilities


class GscTests(unittest.TestCase):
    def test_performance_arabic_columns(self):
        r = gsc.analyze_performance(_path.FIX / "gsc_queries_ar.csv", ["الريحان"], min_impressions=100)
        self.assertEqual(r["kind"], "performance_query")
        self.assertEqual(r["clicks"], 207)
        self.assertEqual(r["impressions"], 3790)
        keys = [d["key"] for d in r["striking_distance"]]
        self.assertEqual(keys, ["افضل عطر عود", "عطر عود ملكي"])
        self.assertIn("افضل عطر عود", [d["key"] for d in r["low_ctr"]])
        self.assertEqual([d["key"] for d in r["zero_click"]], ["بخور كمبودي"])
        self.assertEqual(r["branded"], {"queries": 2, "clicks": 150})
        self.assertTrue(any("يخفي" in l for l in r["limitations_ar"]))

    def test_not_a_gsc_file(self):
        r = gsc.analyze_performance(_path.FIX / "orders_export.csv")
        self.assertEqual(r["kind"], "unknown")

    def test_indexing(self):
        r = gsc.analyze_indexing(_path.FIX / "gsc_indexing.csv")
        self.assertEqual(r["total_not_indexed"], 87)
        self.assertEqual(r["reasons"][0]["reason"], "Duplicate without user-selected canonical")
        adv = {x["reason"]: x["advice_ar"] for x in r["reasons"]}
        self.assertIn("فارغة", adv["Soft 404"])
        self.assertIn("محذوفة", adv["Not found (404)"])


class RemediationTests(unittest.TestCase):
    findings = [
        {"id": "title_missing", "severity": "high", "message_ar": "عنوان مفقود", "url": "https://a/p1"},
        {"id": "title_missing", "severity": "high", "message_ar": "عنوان مفقود", "url": "https://a/p2"},
        {"id": "price_mismatch", "severity": "high", "message_ar": "سعر مختلف", "url": "https://a/p1"},
        {"id": "img_alt_missing", "severity": "low", "message_ar": "صور بلا نص بديل", "url": "https://a/p1"},
        {"id": "something_new", "severity": "medium", "message_ar": "أخرى", "url": "https://a/x"},
    ]

    def test_plan_depends_on_capabilities(self):
        tools = json.loads((_path.FIX / "tools_merchant.json").read_text(encoding="utf-8"))["tools"]
        p = remediation.plan(self.findings, capabilities.build_map(tools))
        items = {i["finding"]: i for i in p["items"]}
        self.assertEqual(items["title_missing"]["count"], 2)
        self.assertTrue(items["title_missing"]["executable_now"])
        self.assertIn("عبر أدوات المتجر", items["title_missing"]["how_ar"])
        self.assertFalse(items["price_mismatch"]["executable_now"])
        self.assertEqual(items["something_new"]["owner"], "merchant")
        self.assertEqual(p["items"][0]["finding"], "title_missing")
        offline = remediation.plan(self.findings, None)
        t = {i["finding"]: i for i in offline["items"]}["title_missing"]
        self.assertFalse(t["executable_now"])
        self.assertIn("لوحة سلة", t["how_ar"])
        self.assertIn("لا يُعتبر", remediation.plan_markdown_ar(offline))

    def test_recheck(self):
        after = [self.findings[2], {"id": "h1_missing", "severity": "medium", "message_ar": "", "url": "https://a/p3"}]
        r = remediation.recheck(self.findings, after)
        self.assertIn({"id": "title_missing", "url": "https://a/p1"}, r["resolved"])
        self.assertEqual(r["persisting"], [{"id": "price_mismatch", "url": "https://a/p1"}])
        self.assertEqual(r["new"], [{"id": "h1_missing", "url": "https://a/p3"}])
        self.assertEqual(remediation.recheck(self.findings, [], after_reachable=False)["status"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
