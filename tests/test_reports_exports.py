import unittest
from decimal import Decimal

import _path
from nitaaq import exports, reports


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.info = exports.analyze(_path.FIX / "orders_export.csv")

    def test_detects_orders_and_columns(self):
        self.assertEqual(self.info["kind"], "orders")
        self.assertEqual(self.info["rows"], 5)
        self.assertEqual(self.info["orders"], 4)
        self.assertEqual(self.info["multi_row_orders"], 1)
        self.assertEqual(self.info["unmapped"], [])
        self.assertEqual((self.info["date_min"], self.info["date_max"]), ("2026-08-02", "2026-09-03"))

    def test_limitations_always_stated(self):
        self.assertTrue(any("لقطة" in l for l in self.info["limitations"]))

    def test_unmapped_columns_reported(self):
        headers = ["رقم الطلب", "عمود غريب"]
        mapping, unmapped = exports.map_columns(headers)
        self.assertEqual(unmapped, ["عمود غريب"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.orders = exports.to_orders(_path.FIX / "orders_export.csv")

    def test_grouping(self):
        o = {x["id"]: x for x in self.orders}
        self.assertEqual(len(o["1004"]["items"]), 2)

    def test_status_filter_and_summary(self):
        kept, dropped = reports.filter_orders(self.orders, statuses=["مكتمل"])
        self.assertEqual(dropped["status"], 1)
        s = reports.sales_summary(kept)
        self.assertEqual(s["orders"], 3)
        self.assertEqual(s["sales"], Decimal("827.00"))
        self.assertEqual(s["aov"], Decimal("275.67"))

    def test_date_range(self):
        kept, dropped = reports.filter_orders(self.orders, "2026-09-01", "2026-09-30")
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped["out_of_range"], 2)

    def test_missing_is_not_zero(self):
        s = reports.sales_summary([{"id": 1}, {"id": 2, "total": "10"}])
        self.assertEqual(s["orders_missing_value"], 1)
        self.assertEqual(s["sales"], Decimal(10))
        s = reports.sales_summary([{"id": 1}])
        self.assertIsNone(s["sales"])
        self.assertIsNone(s["aov"])
        self.assertEqual(reports.sales_summary([])["sales"], Decimal(0))

    def test_dimension_has_unknown_bucket(self):
        kept, _ = reports.filter_orders(self.orders, statuses=["مكتمل"])
        rows = reports.by_dimension(kept, "city")
        self.assertIn("غير محدد", [r["city"] for r in rows])
        self.assertEqual(sum(r["orders"] for r in rows), 3)

    def test_monthly(self):
        m = reports.by_month(self.orders)
        self.assertEqual([r["month"] for r in m], ["2026-08", "2026-09"])

    def test_top_products(self):
        kept, _ = reports.filter_orders(self.orders, statuses=["مكتمل"])
        top = reports.top_products(kept, "quantity")
        self.assertEqual((top[0]["product"], top[0]["quantity"]), ("عطر عود ملكي", 3))
        by_rev = reports.top_products(kept, "revenue")
        self.assertEqual(by_rev[0]["revenue"], Decimal(577))

    def test_margin_is_not_net_profit(self):
        kept, _ = reports.filter_orders(self.orders, statuses=["مكتمل"])
        m = reports.product_margins(kept)
        self.assertIn("ليس صافي الربح", m["label_ar"])
        self.assertEqual(m["items_without_cost"], 1)
        self.assertEqual(m["margin"], Decimal(577 + 150 - 360 - 60))

    def test_customers_within_period_labelled(self):
        c = reports.customers_new_returning(self.orders)
        self.assertEqual(c["basis"], "within_period_only")
        c = reports.customers_new_returning(self.orders, {"C1": "2025-01-01", "C2": "2026-08-15"}, "2026-08-01")
        self.assertEqual((c["new"], c["returning"], c["unknown"]), (1, 1, 1))

    def test_roas_needs_spend(self):
        self.assertIsNone(reports.roas(1000, None)["roas"])
        self.assertEqual(reports.roas(1000, 250)["roas"], Decimal("4.00"))
        self.assertIn("منسوبة", reports.roas(1000, 250)["note_ar"])

    def test_meta_markdown(self):
        meta = reports.ReportMeta(source="ملف", date_from="2026-08-01", date_to="2026-08-31",
                                  records_fetched=50, records_total=120)
        self.assertFalse(meta.complete)
        md = meta.to_markdown_ar()
        for s in ("Asia/Riyadh", "SAR", "جزئية (50 من 120)", "الخصومات", "غير معروف"):
            self.assertIn(s, md)
        self.assertIsNone(reports.ReportMeta(source="x").complete)

    def test_timezone(self):
        dt = reports.parse_dt("2026-09-30T22:30:00Z")
        self.assertEqual(dt.date().isoformat(), "2026-10-01")


if __name__ == "__main__":
    unittest.main()
