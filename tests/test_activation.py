import unittest

import _path  # noqa: F401
from nitaaq.activation import detect


class ActivationTests(unittest.TestCase):
    def test_explicit_phrases(self):
        for msg in ["نطاق للمتاجر ضيف منتج", "استخدم مهارة نطاق", "استخدم مهارة استور",
                    "ابغى استخدم مهاره استور", "Use Nitaaq Store please", "/nitaaq-store", "$nitaaq-store اعرض الطلبات"]:
            r = detect(msg)
            self.assertTrue(r.activate, msg)
            self.assertEqual(r.confidence, "explicit", msg)

    def test_store_word_addressed(self):
        self.assertTrue(detect("استور، كم طلب جاني اليوم؟").activate)
        self.assertTrue(detect("يا استور اعرض لي المنتجات").activate)

    def test_store_word_with_commerce_context(self):
        self.assertTrue(detect("ابغى من استور يعدل سعر المنتج").activate)
        self.assertTrue(detect("شغل استور على متجري في سلة").activate)

    def test_unrelated_store(self):
        for msg in ["نزلت التطبيق من اب ستور", "the store closes at 9", "app store review", "restore my backup",
                    "حطيته في الستور روم"]:
            self.assertFalse(detect(msg).activate, msg)

    def test_bare_store_word_is_ambiguous_without_context(self):
        r = detect("رحت استور امس")
        self.assertFalse(r.activate)
        self.assertEqual(r.confidence, "ambiguous")
        self.assertTrue(detect("رحت استور امس", context_active=True).activate)

    def test_spelling_variants(self):
        self.assertTrue(detect("نطاق المتاجر").activate)
        self.assertTrue(detect("إستور، طلعلي تقرير المبيعات").activate)


if __name__ == "__main__":
    unittest.main()
