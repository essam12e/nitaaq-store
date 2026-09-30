import unittest

import _path  # noqa: F401
from nitaaq import errors, redact


class ErrorTests(unittest.TestCase):
    def test_kinds(self):
        cases = {
            ("Unauthorized: token expired", 401): "auth_expired",
            ("Forbidden: insufficient scope", 403): "permission_denied",
            ("Too Many Requests", 429): "rate_limited",
            ("Product not found", 404): "not_found",
            ("The price field is required", 422): "validation",
            ("feature not enabled for this store", None): "unavailable_feature",
        }
        for (text, status), kind in cases.items():
            self.assertEqual(errors.classify(text, status).kind, kind, text)

    def test_ambiguous_write_needs_reconcile(self):
        c = errors.classify("read timed out", write=True, idempotent=False)
        self.assertEqual((c.kind, c.retry), ("network_ambiguous", "reconcile_first"))
        self.assertEqual(errors.classify("read timed out", write=True, idempotent=True).retry, "safe")
        self.assertEqual(errors.classify("Internal Server Error", 500, write=True).retry, "reconcile_first")
        self.assertEqual(errors.classify("Internal Server Error", 500).retry, "safe")

    def test_no_retry_on_permission(self):
        self.assertEqual(errors.classify("forbidden", 403).retry, "no")

    def test_arabic_messages(self):
        for k, msg in errors.KINDS.items():
            self.assertRegex(msg, "[؀-ۿ]", k)


class RedactTests(unittest.TestCase):
    def test_text(self):
        t = redact.redact_text("جوال العميل 0551234567 وإيميله a.b@example.com وجواله الثاني +966 55 123 4567 وهويته 1023456789")
        self.assertNotIn("0551234567", t)
        self.assertNotIn("a.b@example.com", t)
        self.assertNotIn("1023456789", t)
        self.assertNotIn("123 4567", t)

    def test_arabic_digits(self):
        self.assertIn("محجوب", redact.redact_text("٠٥٥١٢٣٤٥٦٧"))

    def test_record(self):
        r = redact.redact_record({"id": 7, "total": "199.00", "customer": {"name": "سارة", "mobile": "0551234567", "city": "الرياض"},
                                  "note": "اتصل على 0551234567"})
        self.assertEqual(r["total"], "199.00")
        self.assertEqual(r["customer"]["name"], "[محجوب]")
        self.assertEqual(r["customer"]["city"], "الرياض")
        self.assertNotIn("0551234567", r["note"])

    def test_alias_stable(self):
        self.assertEqual(redact.customer_alias(5), redact.customer_alias("5"))
        self.assertNotIn("0551234567", redact.customer_alias("0551234567"))


if __name__ == "__main__":
    unittest.main()
