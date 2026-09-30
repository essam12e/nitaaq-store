import json
import unittest

import _path
from nitaaq import capabilities as cap


def load(name):
    return json.loads((_path.FIX / name).read_text(encoding="utf-8"))["tools"]


class CapabilityMapTests(unittest.TestCase):
    def setUp(self):
        self.m = cap.build_map(load("tools_merchant.json"))
        self.ops = self.m["operations"]

    def top(self, op):
        c = self.ops[op]["candidates"]
        return c[0]["tool"].split("__")[-1] if c else None

    def test_merchant_connection(self):
        self.assertEqual(self.m["connection"]["kind"], "merchant")

    def test_core_mappings(self):
        expected = {
            "products.list": "list_products", "products.get": "get_product",
            "products.create": "create_product", "products.update": "update_product",
            "inventory.set": "update_product_quantity", "orders.list": "list_orders",
            "orders.get": "get_order", "orders.history": "get_order_status_history",
            "orders.statuses": "list_order_statuses", "orders.update_status": "update_order_status",
            "carts.abandoned": "list_abandoned_carts", "reports.sales": "get_sales_report",
            "products.best_sellers": "get_best_selling_products", "categories.list": "list_categories",
        }
        for op, tool in expected.items():
            self.assertEqual(self.top(op), tool, op)

    def test_no_invented_tools(self):
        names = {t["name"] for t in load("tools_merchant.json")}
        for v in self.ops.values():
            for c in v["candidates"]:
                self.assertIn(c["tool"], names)

    def test_absent_operations_are_unavailable(self):
        for op in ("products.delete", "theme.publish", "home.sections.delete", "reviews.reply", "coupons.create"):
            self.assertEqual(self.ops[op]["status"], "unavailable", op)

    def test_increment_not_mapped_to_set_tool(self):
        self.assertEqual(self.ops["inventory.adjust"]["status"], "unavailable")

    def test_description_mentions_do_not_create_mappings(self):
        # get_order mentions "customer" in its description; that is not a customers tool.
        self.assertEqual(self.ops["customers.list"]["status"], "unavailable")

    def test_read_only_tool_never_mapped_to_write(self):
        for op, v in self.ops.items():
            if v["access"] != "read":
                for c in v["candidates"]:
                    self.assertNotEqual(c["annotations"].get("readOnlyHint"), True, op)

    def test_schema_details_recorded(self):
        c = self.ops["products.list"]["candidates"][0]
        self.assertEqual(c["pagination_params"], ["page", "per_page"])
        self.assertEqual(self.ops["products.list"]["evidence_level"], "schema_validated")
        self.assertIn("status_id", self.ops["orders.update_status"]["candidates"][0]["required_params"])

    def test_partners_only(self):
        m = cap.build_map(load("tools_partners.json"))
        self.assertEqual(m["connection"]["kind"], "partners_only")
        self.assertTrue(all(v["status"] == "unavailable" for v in m["operations"].values()))
        self.assertIn("شركاء", m["connection"]["note_ar"])

    def test_mixed_excludes_partner_tools(self):
        m = cap.build_map(load("tools_merchant.json") + load("tools_partners.json"))
        self.assertEqual(m["connection"]["kind"], "mixed")
        for v in m["operations"].values():
            for c in v["candidates"]:
                self.assertNotIn("salla-partners", c["tool"])

    def test_multiplexed_action_enum(self):
        m = cap.build_map(load("tools_multiplexed.json"))
        c = m["operations"]["products.create"]["candidates"][0]
        self.assertEqual((c["tool"], c["via_action"]), ("products", "create"))
        self.assertEqual(m["operations"]["orders.update_status"]["status"], "unavailable")

    def test_empty(self):
        m = cap.build_map([])
        self.assertEqual(m["connection"]["kind"], "none")

    def test_evidence_levels_require_proof(self):
        with self.assertRaises(ValueError):
            cap.record_evidence(self.m, "products.update", "live_verified", "readback matched on product 5")
        with self.assertRaises(ValueError):
            cap.record_evidence(self.m, "products.update", "live_verified", "mock server returned ok", store_id="1")
        with self.assertRaises(ValueError):
            cap.record_evidence(self.m, "products.delete", "tested", "deleted a test product in sandbox")
        with self.assertRaises(ValueError):
            cap.record_evidence(self.m, "products.update", "discovered", "anything goes here")
        e = cap.record_evidence(self.m, "products.update", "tested", "patched name on sandbox store product")
        self.assertEqual(e["evidence_level"], "tested")
        e = cap.record_evidence(self.m, "products.update", "live_verified", "metadata_title readback matched", store_id="777")
        self.assertEqual(e["evidence_level"], "live_verified")
        e = cap.record_evidence(self.m, "products.update", "tested", "re-ran controlled test later")
        self.assertEqual(e["evidence_level"], "live_verified")

    def test_build_never_claims_tested(self):
        for v in self.ops.values():
            self.assertIn(v["evidence_level"], ("unavailable", "discovered", "schema_validated"))

    def test_diff_detects_new_connection(self):
        before = cap.build_map(load("tools_partners.json"))
        d = cap.diff_maps(before, self.m)
        self.assertEqual((d["connection_before"], d["connection_after"]), ("partners_only", "merchant"))
        self.assertIn("orders.list", d["added"])

    def test_names_only_stay_discovered(self):
        m = cap.build_map([{"name": "list_orders"}])
        self.assertEqual(m["operations"]["orders.list"]["evidence_level"], "discovered")

    def test_summary_arabic(self):
        s = cap.summarize_ar(self.m)
        self.assertIn("متاح", s)
        self.assertIn("ليس بالضرورة قيداً في سلة", s)


if __name__ == "__main__":
    unittest.main()
