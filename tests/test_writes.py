import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from nitaaq import writes


class PatchTests(unittest.TestCase):
    current = {"id": 5, "name": "عطر عود", "price": "199.00", "description": "وصف قديم",
               "seo": {"title": "قديم", "description": "د"}, "images": [{"id": 1}], "sku": "00123"}

    def test_only_changed_selected_fields(self):
        desired = {"price": 199, "name": "عطر عود ملكي", "description": "وصف جديد"}
        patch = writes.build_patch(self.current, desired, ["price", "name"])
        self.assertEqual(patch, {"name": "عطر عود ملكي"})

    def test_nested_and_preservation(self):
        patch = writes.build_patch(self.current, {"seo": {"title": "جديد", "description": "د"}})
        self.assertEqual(patch, {"seo": {"title": "جديد"}})
        full = writes.merge_for_full_replace(self.current, patch)
        self.assertEqual(full["seo"], {"title": "جديد", "description": "د"})
        self.assertEqual(full["images"], [{"id": 1}])
        self.assertEqual(full["description"], "وصف قديم")

    def test_codes_are_not_numbers(self):
        self.assertFalse(writes.same_value("00123", "123"))
        self.assertTrue(writes.same_value("100.00", 100))
        self.assertTrue(writes.same_value("١٠٠", "100"))

    def test_diff_table_arabic(self):
        t = writes.diff_table_ar(self.current, {"price": 150}, {"price": "السعر"})
        self.assertIn("| السعر | 199.00 | 150 |", t)
        self.assertIn("لا توجد فروقات", writes.diff_table_ar(self.current, {}))

    def test_conflicts(self):
        fresh = dict(self.current, price="189.00")
        c = writes.detect_conflicts(self.current, fresh, ["price", "name"])
        self.assertEqual([x["field"] for x in c], ["price"])
        self.assertEqual(writes.detect_conflicts(self.current, dict(self.current), ["price"]), [])

    def test_readback(self):
        r = writes.verify_readback({"price": 150, "name": "x"}, {"price": "150.00", "name": "y"})
        self.assertFalse(r["verified"])
        self.assertEqual([f["status"] for f in r["fields"]], ["persisted", "mismatch"])
        r = writes.verify_readback({"seo": {"title": "t"}}, {"name": "x"})
        self.assertTrue(r["unverified_fields"])
        self.assertTrue(writes.verify_readback({"price": 1}, {"price": 1})["verified"])


class LedgerTests(unittest.TestCase):
    def test_duplicate_and_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            led = writes.Ledger(Path(d) / "l.json")
            key = writes.operation_key("products.create", None, {"name": "مسك"})
            self.assertEqual(led.begin(key, "products.create", None), "go")
            led.finish(key, "unknown")  # timeout after sending
            again = writes.Ledger(Path(d) / "l.json")  # survives restarts
            self.assertEqual(again.begin(key, "products.create", None), "reconcile_first")
            again.reconcile(key, "991")
            self.assertEqual(again.check(key), "done")
            self.assertEqual(again.begin(key, "products.create", None), "done")

    def test_failed_can_retry(self):
        with tempfile.TemporaryDirectory() as d:
            led = writes.Ledger(Path(d) / "l.json")
            k = writes.operation_key("orders.add_note", "7", {"note": "x"})
            led.begin(k, "orders.add_note", "7")
            led.finish(k, "failed")
            self.assertEqual(led.check(k), "retry_allowed")
            led.reconcile(k, None)
            self.assertEqual(led.check(k), "retry_allowed")

    def test_key_stable(self):
        a = writes.operation_key("x", "1", {"a": 1, "b": 2})
        b = writes.operation_key("x", "1", {"b": 2, "a": 1})
        self.assertEqual(a, b)

    def test_find_existing_by_name(self):
        items = [{"id": 1, "name": "عطر العود الملكي"}, {"id": 2, "name": "مسك"}]
        self.assertEqual(writes.find_existing_by_name(items, "عطر العود الملكى")[0]["id"], 1)


class AuthorizationTests(unittest.TestCase):
    def test_scope(self):
        a = writes.Authorization(ops={"products.update"}, entities={"1", "2"}, fields={"metadata_title"})
        self.assertTrue(a.covers("products.update", 1, ["metadata_title"]))
        self.assertFalse(a.covers("products.update", 3, ["metadata_title"]))
        self.assertFalse(a.covers("products.update", 1, ["price"]))
        self.assertFalse(a.covers("products.delete", 1, ["metadata_title"]))

    def test_needs_approval(self):
        self.assertTrue(writes.needs_approval("destructive", "high", [])[0])
        self.assertTrue(writes.needs_approval("write", "medium", ["price"])[0])
        self.assertTrue(writes.needs_approval("write", "low", ["metadata_title"], item_count=12)[0])
        self.assertTrue(writes.needs_approval("write", "low", ["note"], customer_visible=True)[0])
        self.assertFalse(writes.needs_approval("write", "medium", ["metadata_title"])[0])

    def test_batch_report(self):
        b = writes.BatchReport()
        b.add("منتج 1", "succeeded")
        b.add("منتج 2", "failed", "السعر غير صالح")
        b.add("منتج 3", "unverified")
        self.assertEqual(b.counts(), {"succeeded": 1, "failed": 1, "unverified": 1})
        md = b.to_markdown_ar()
        self.assertIn("السعر غير صالح", md)
        with self.assertRaises(ValueError):
            b.add("x", "done")


if __name__ == "__main__":
    unittest.main()
