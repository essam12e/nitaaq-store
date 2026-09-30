import unittest
from decimal import Decimal

import _path  # noqa: F401
from nitaaq import products


class PriceTests(unittest.TestCase):
    def test_valid_sale(self):
        pc = products.check_prices("200", "150", "90")
        self.assertTrue(pc.ok)
        self.assertEqual(pc.discount_percent, Decimal("25.0"))

    def test_sale_not_below_regular(self):
        pc = products.check_prices(150, 200)
        self.assertFalse(pc.ok)
        self.assertIn("أقل من السعر الأساسي", pc.errors[0])

    def test_arabic_digits_and_currency(self):
        pc = products.check_prices("٢٠٠ ر.س", "١٥٠")
        self.assertEqual((pc.price, pc.sale_price), (Decimal(200), Decimal(150)))

    def test_cost_warning(self):
        self.assertTrue(products.check_prices(100, 80, 90).warnings)

    def test_missing_price(self):
        self.assertFalse(products.check_prices(None).ok)

    def test_phrase(self):
        self.assertEqual(products.parse_price_phrase("كان 200 وصار 150"), {"price": Decimal(200), "sale_price": Decimal(150)})
        self.assertEqual(products.parse_price_phrase("بدل ٣٠٠ صار ٢٤٩")["sale_price"], Decimal(249))
        self.assertEqual(products.parse_price_phrase("غيّر الوصف"), {})


class StockTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(products.plan_stock_change("set", 5, 20).target, 20)
        self.assertEqual(products.plan_stock_change("increment", 5, 3).target, 8)
        p = products.plan_stock_change("decrement", 5, 7)
        self.assertFalse(p.ok)
        self.assertTrue(products.plan_stock_change("set", 1, 2).safe_to_retry)
        self.assertFalse(products.plan_stock_change("increment", 1, 2).safe_to_retry)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            products.plan_stock_change("add", 1, 1)
        with self.assertRaises(ValueError):
            products.plan_stock_change("set", 1, -2)
        with self.assertRaises(ValueError):
            products.plan_stock_change("set", 1, "1.5")

    def test_concurrent_sales_readback(self):
        plan = products.plan_stock_change("increment", 5, 10)
        self.assertEqual(products.reconcile_stock_readback(plan, 15)["status"], "persisted")
        self.assertEqual(products.reconcile_stock_readback(plan, 13, sold_since=2)["status"], "persisted_with_sales")
        self.assertEqual(products.reconcile_stock_readback(plan, 9)["status"], "mismatch")

    def test_sellability(self):
        self.assertTrue(products.sellability({"status": "sale", "quantity": 4})["sellable"])
        r = products.sellability({"status": "hidden", "quantity": 4})
        self.assertFalse(r["sellable"])
        self.assertFalse(products.sellability({"status": "sale", "quantity": 0})["sellable"])
        self.assertTrue(products.sellability({"status": "sale", "unlimited_quantity": True})["sellable"])
        self.assertIn("غير معروفة", products.sellability({"status": "sale"})["reasons"][0])
        v = products.sellability({"status": "sale"}, {"quantity": 3, "is_available": False})
        self.assertFalse(v["sellable"])


class MatchingTests(unittest.TestCase):
    catalog = [
        {"id": 1, "name": "عطر العود الملكي 100 مل", "brand": "الريحان", "attributes": {"الحجم": "100 مل"},
         "visual": ["زجاجة سوداء", "غطاء ذهبي"]},
        {"id": 2, "name": "عطر العود الملكي 50 مل", "brand": "الريحان", "attributes": {"الحجم": "50 مل"}},
        {"id": 3, "name": "سماعة لاسلكية X200", "brand": "صوتي", "model": "X200"},
    ]

    def test_match_without_sku(self):
        ref = {"name": "عطر عود ملكي ١٠٠ مل", "brand": "الريحان", "attributes": {"الحجم": "100 مل"},
               "visual": ["زجاجة سوداء", "غطاء ذهبي"]}
        best = products.best_matches(ref, self.catalog)
        self.assertEqual(best[0]["candidate"]["id"], 1)
        self.assertEqual(best[0]["decision"], "match")
        self.assertNotEqual(best[1]["decision"], "match")

    def test_model_conflict(self):
        r = products.match_product({"name": "سماعة لاسلكية X300", "brand": "صوتي"}, self.catalog[2])
        self.assertNotEqual(r["decision"], "match")
        self.assertTrue(r["conflicts"])

    def test_identifiers_only_corroborate(self):
        a = {"name": "مسك أبيض", "brand": "الريحان"}
        with_sku = products.match_product({**a, "sku": "M1"}, {**a, "sku": "M1"})
        without = products.match_product(a, a)
        self.assertEqual(without["decision"], "match")
        self.assertGreaterEqual(with_sku["score"], without["score"])

    def test_uncertain_asks_merchant(self):
        r = products.match_product({"name": "عطر عود"}, {"name": "عطر عود ورد فاخر"})
        self.assertIn(r["decision"], ("confirm_with_merchant", "no_match"))
        self.assertNotEqual(r["decision"], "match")


if __name__ == "__main__":
    unittest.main()
