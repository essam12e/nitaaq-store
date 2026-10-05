"""Phase 3: SEO/GEO as a team stage (behaviour unchanged), internal competition, customer intelligence."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _path import FIX, ROOT, SKILL

from nitaaq import customer_intel, evidence, review, routing, seo_audit, seo_team

CLI = SKILL / "scripts" / "nitaaq_cli.py"
PAGES = [("product_page.html", "https://shop.example/products/oud"), ("category_empty.html", "https://shop.example/category/oud")]


def save(store, op, records, kind=None):
    ev, recs = evidence.make_evidence(store.store_id, {"kind": "mcp", "operation": op}, records,
                                      records_total=len(records), kind=kind)
    store.save(ev, recs)
    return ev, recs


def cli(*args, cwd):
    r = subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class SeoWrapTests(unittest.TestCase):
    def test_wrapped_audit_is_identical_to_existing(self):
        for name, url in PAGES:
            html = (FIX / name).read_text(encoding="utf-8")
            f = seo_audit.parse_page(html, url)
            before = seo_audit.audit_page(f, seo_audit.guess_page_type(url, f))
            self.assertEqual(seo_team.audit(html, url), before, name)
            with tempfile.TemporaryDirectory() as d:
                Path(d, name).write_text(html, encoding="utf-8")
                old_cli = cli("audit-html", name, "--url", url, cwd=d)["findings"]
                new_cli = cli("seo-team", "--store-id", "S1", "--page", f"{url}={name}", cwd=d)["findings"]
                self.assertEqual([x["audit"] for x in new_cli], old_cli, name)

    def test_findings_review(self):
        html = (FIX / "product_page.html").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, "public.pages", [{"id": PAGES[0][1], "url": PAGES[0][1], "html": html}], kind="public_page")
            fs = seo_team.page_findings(recs[0], ev)
            self.assertTrue(fs)
            out = review.review_all(fs, store)
            self.assertEqual(out["counts"], {"pass": len(fs)})
            fs[0]["observed"][0]["current"] = "made_up_check"
            self.assertEqual(review.review_finding(fs[0], store)["status"], "fail")
            self.assertTrue(all("لا نعد" in f["expected_effect"]["range_ar"] for f in fs))


class CannibalizationTests(unittest.TestCase):
    ROWS = [{"query": "عطر عود", "page": "/p/oud-1", "clicks": 10, "impressions": 500, "position": 5},
            {"query": "عطر عود", "page": "/p/oud-2", "clicks": 4, "impressions": 300, "position": 9},
            {"query": "عطر عود", "page": "/c/oud", "clicks": 0, "impressions": 20, "position": 30},
            {"query": "مسك ابيض", "page": "/p/musk", "clicks": 40, "impressions": 600, "position": 3},
            {"query": "بخور", "page": "/p/a", "clicks": 0, "impressions": 5}, {"query": "بخور", "page": "/p/b", "impressions": 5}]

    def test_gsc(self):
        c = seo_team.cannibalization_gsc(self.ROWS)
        self.assertEqual([x["query"] for x in c], ["عطر عود"])  # musk has one page; bukhoor is below min impressions
        self.assertEqual([p["page"] for p in c[0]["pages"]], ["/p/oud-1", "/p/oud-2"])  # the 2.4% page is below min share

    def test_titles_and_review(self):
        items = [{"id": "1", "name": "عطر عود ملكي 100 مل"}, {"id": "2", "name": "عطر عود ملكي 100 مل اصلي"},
                 {"id": "3", "name": "مسك ابيض"}]
        c = seo_team.cannibalization_titles(items)
        self.assertEqual([(x["a"], x["b"]) for x in c], [("1", "2")])
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, "products.list", items)
            f = seo_team.cannibalization_finding(c, ev, basis="titles")
            self.assertEqual(f["confidence"]["level"], "low")
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
        self.assertIsNone(seo_team.cannibalization_finding([], ev, basis="titles"))


class CustomerIntelTests(unittest.TestCase):
    def reviews(self, neg_every_current=2, neg_every_base=7):
        from datetime import date, timedelta
        neg = ["التوصيل متأخر وما وصل إلا بعد أسبوعين", "وصل مكسور", "المنتج مو اصلي",
               "خدمة العملاء ما ردوا علي، جوالي 0551234567"]
        pos = ["ممتاز وتوصيل سريع", "جميل والتعامل راقي"]
        out = []
        for d in range(56):
            cur = d >= 28
            bad = d % (neg_every_current if cur else neg_every_base) == 0
            out.append({"id": f"r{d}", "date": (date(2026, 8, 10) + timedelta(days=d)).isoformat(), "rating": 1 if bad else 5,
                        "text": neg[d % 4] if bad else pos[d % 2], "product_name": "عطر عود"})
        return out

    def test_themes_and_positive_context(self):
        self.assertEqual(customer_intel.themes_of("التوصيل متأخر مرة"), ["delivery_delay"])
        r = customer_intel.analyze([{"id": "1", "rating": 5, "text": "التعامل راقي"},
                                    {"id": "2", "rating": 1, "text": "التعامل سيء وما ردوا"}])
        service = next(t for t in r["themes"] if t["theme"] == "service")
        self.assertEqual(service["count"], 1)  # the positive "تعامل" is not a complaint
        self.assertIn("small_sample", r["flags"])
        self.assertIn("unknown_completeness", r["flags"])

    def test_quotes_are_redacted(self):
        r = customer_intel.analyze(self.reviews())
        quotes = " ".join(q for t in r["themes"] for q in t["quotes"])
        self.assertNotIn("0551234567", quotes)

    def test_complaints_up_needs_enough_sample(self):
        rows = self.reviews()
        c = customer_intel.compare(rows, ["2026-09-07", "2026-10-04"], ["2026-08-10", "2026-09-06"], records_total=56)
        self.assertEqual(c["signals"], ["complaints_up"])
        small = customer_intel.compare(rows, ["2026-10-01", "2026-10-04"], ["2026-09-27", "2026-09-30"])
        self.assertFalse(small["enough_sample"])
        self.assertEqual(small["signals"], [])

    def test_finding_states_sample_and_review_checks_it(self):
        with tempfile.TemporaryDirectory() as d:
            store = evidence.EvidenceStore(d, "S1")
            ev, recs = save(store, "reviews.list", self.reviews())
            f = customer_intel.themes_finding(customer_intel.analyze(recs, ["2026-09-07", "2026-10-04"]), ev)
            self.assertEqual(f["observed"][0]["current"], "28")
            self.assertIn("عينة صغيرة", " ".join(f["limitations_ar"]))
            self.assertEqual(review.review_finding(f, store)["status"], "pass")
            hidden = dict(f, limitations_ar=[x for x in f["limitations_ar"] if "عينة صغيرة" not in x])
            self.assertIn("small_sample_not_disclosed", [i["code"] for i in review.review_finding(hidden, store)["issues"]])
            f["observed"][1]["current"] = "99"
            self.assertEqual(review.review_finding(f, store)["status"], "fail")

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "reviews.json").write_text(json.dumps(self.reviews(), ensure_ascii=False), encoding="utf-8")
            out = cli("reviews", "--store-id", "S1", "--reviews", "reviews.json", "--total", "56",
                      "--current", "2026-09-07,2026-10-04", "--baseline", "2026-08-10,2026-09-06", cwd=d)
            self.assertEqual(out["comparison"]["signals"], ["complaints_up"])
            self.assertEqual(out["review"]["counts"], {"pass": 1})


class Phase3RoutingTests(unittest.TestCase):
    def test_routes(self):
        r = routing.route("وش تقول التقييمات عن منتجاتي؟", {"export:reviews"})
        self.assertEqual(r["specialists_to_run"], ["customer_intelligence"])
        self.assertEqual(routing.route("وش تقول التقييمات عن منتجاتي؟", {"orders.list"})["stages"][0]["status"], "blocked")
        self.assertEqual(routing.route("افحص السيو", {"public:pages"})["specialists_to_run"], ["seo_geo"])
        f = routing.followups(["complaints_up"], {"reviews.list"})
        self.assertEqual((f[0]["agent"], f[0]["status"]), ("customer_intelligence", "run"))
        u = routing.route("ليش المبيعات نازلة؟", {"orders.list", "export:reviews"})["unknowns_ar"]
        self.assertFalse(any("الشكاوى" in x for x in u))

    def test_generated_agents(self):
        names = sorted(p.stem for p in (ROOT / "plugins" / "nitaaq-store" / "agents").glob("*.md"))
        for n in ["nitaaq-cro", "nitaaq-customer-intelligence", "nitaaq-growth", "nitaaq-pricing", "nitaaq-reviewer",
                  "nitaaq-seo-geo", "nitaaq-store-analytics"]:
            self.assertIn(n, names)


if __name__ == "__main__":
    unittest.main()
