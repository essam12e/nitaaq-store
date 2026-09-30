import json
import unittest

import _path
from nitaaq import geo, seo_audit, tracking, visibility
from nitaaq.seo_audit import FetchResult


def html(name):
    return (_path.FIX / name).read_text(encoding="utf-8")


class PageAuditTests(unittest.TestCase):
    def setUp(self):
        self.f = seo_audit.parse_page(html("product_page.html"), "https://rayhan.example/p12345")
        self.ids = {x["id"] for x in seo_audit.audit_page(self.f, "product")}

    def test_facts(self):
        self.assertEqual(self.f.h1, ["عطر عود ملكي 100 مل"])
        self.assertEqual(self.f.canonical, "https://rayhan.example/p12345")
        self.assertIn("Organization", {t for n in self.f.jsonld for t in seo_audit._types(n)})
        self.assertEqual([str(p) for p in self.f.prices_visible], ["249", "179"])

    def test_price_mismatch_detected(self):
        self.assertIn("price_mismatch", self.ids)

    def test_alt_missing(self):
        self.assertIn("img_alt_missing", self.ids)

    def test_clean_things_not_flagged(self):
        for good in ("title_missing", "canonical_missing", "h1_missing", "noindex", "meta_description_missing"):
            self.assertNotIn(good, self.ids)

    def test_parameterized_and_noindex(self):
        page = '<html><head><title>عطور رجالية فاخرة | الريحان</title><meta name="robots" content="noindex,follow"></head><body></body></html>'
        f = seo_audit.parse_page(page, "https://rayhan.example/c100?sort=price&utm_source=x")
        ids = {x["id"] for x in seo_audit.audit_page(f)}
        self.assertTrue({"noindex", "parameterized_url", "canonical_missing", "h1_missing"} <= ids)

    def test_invalid_jsonld(self):
        f = seo_audit.parse_page('<script type="application/ld+json">{bad</script>', "https://a.example/")
        self.assertEqual(f.jsonld_errors, 1)

    def test_findings_are_public_crawl_evidence(self):
        for x in seo_audit.audit_page(self.f, "product"):
            self.assertEqual(x["evidence_level"], "public_crawl")

    def test_markdown(self):
        md = seo_audit.findings_markdown_ar(seo_audit.audit_page(self.f, "product"))
        self.assertTrue(md.splitlines()[2].startswith("| 🔴"))


class RobotsTests(unittest.TestCase):
    def test_bots(self):
        r = seo_audit.robots_report("https://rayhan.example", html("robots.txt"))
        c = r["crawlers"]
        self.assertTrue(c["Googlebot"]["allowed"])
        self.assertFalse(c["GPTBot"]["allowed"])
        self.assertFalse(c["Google-Extended"]["allowed"])
        self.assertTrue(c["OAI-SearchBot"]["allowed"])
        self.assertIn("لا يؤثر على الظهور في بحث Google", c["Google-Extended"]["purpose_ar"])
        self.assertEqual(r["sitemaps"], ["https://rayhan.example/sitemap.xml"])

    def test_sitemap(self):
        s = seo_audit.parse_sitemap("<urlset><url><loc>https://a/p1</loc></url><url><loc>https://a/p2</loc></url></urlset>")
        self.assertEqual(s["count"], 2)


class FakeSite:
    def __init__(self):
        prod = html("product_page.html")
        self.pages = {
            "https://rayhan.example/robots.txt": (200, html("robots.txt"), "text/plain"),
            "https://rayhan.example/sitemap.xml": (200, "<urlset><url><loc>https://rayhan.example/p12345</loc></url></urlset>", "application/xml"),
            "https://rayhan.example/": (200, '<html lang="ar"><head><title>متجر الريحان للعطور</title><meta name="description" content="عطور"></head><body><h1>الريحان</h1><a href="/p12345">عود</a><a href="/c100">عطور</a><a href="/shipping-policy">سياسة الشحن</a><a href="/old">قديم</a></body></html>', "text/html"),
            "https://rayhan.example/p12345": (200, prod, "text/html"),
            "https://rayhan.example/p12346": (200, prod.replace("p12345", "p12346"), "text/html"),
            "https://rayhan.example/c100": (200, html("category_empty.html"), "text/html"),
            "https://rayhan.example/shipping-policy": (200, '<html><head><title>سياسة الشحن | متجر الريحان</title></head><body><h1>سياسة الشحن</h1></body></html>', "text/html"),
        }
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url == "https://rayhan.example/old":
            return FetchResult(url, "https://rayhan.example/p12345", 200,
                               [(url, 301), ("https://rayhan.example/old2", 301), ("https://rayhan.example/p12345", 200)],
                               {"Content-Type": "text/html"}, self.pages["https://rayhan.example/p12345"][1])
        if url in self.pages:
            st, body, ct = self.pages[url]
            return FetchResult(url, url, st, [(url, st)], {"Content-Type": ct}, body)
        return FetchResult(url, url, 404, [(url, 404)], {"Content-Type": "text/html"}, "")


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.res = seo_audit.crawl("https://rayhan.example/", max_pages=10, delay=0, fetcher=self.site)
        self.ids = [x["id"] for x in self.res["findings"]]

    def test_crawl_findings(self):
        for fid in ("redirect_chain", "empty_listing", "price_mismatch", "title_duplicate"):
            self.assertIn(fid, self.ids)
        self.assertIn("صفحة عامة فقط", self.res["coverage_note_ar"])
        self.assertIn("لا يثبت حالة الفهرسة", self.res["disclaimer_ar"])

    def test_respects_limits(self):
        res = seo_audit.crawl("https://rayhan.example/", max_pages=2, delay=0, fetcher=FakeSite())
        self.assertEqual(res["pages_crawled"], 2)

    def test_geo(self):
        g = geo.assess(self.res, store_name="متجر الريحان", llms_txt_status=404)
        dims = {d["id"]: d for d in g["dimensions"]}
        self.assertEqual(dims["access"]["score"], 2)  # search crawlers allowed; GPTBot is training only
        self.assertEqual(dims["policies"]["score"], 1)  # shipping only
        self.assertEqual(dims["identity"]["score"], 1)  # Organization schema, no about page
        self.assertIn("لا يضمن الظهور", g["info"][0])
        self.assertIn("لا تضمن الظهور", g["disclaimer_ar"])

    def test_geo_blocked_search(self):
        crawl = {"pages": [], "robots": seo_audit.robots_report("https://a.example", "User-agent: *\nDisallow: /")}
        g = geo.assess(crawl)
        self.assertEqual(g["dimensions"][0]["score"], 0)


class TrackingTests(unittest.TestCase):
    def test_levels(self):
        code = tracking.detect_code(html("product_page.html"))
        self.assertEqual(code["ga4"]["level"], 1)
        self.assertEqual(code["meta"]["level"], 1)
        self.assertEqual(code["tiktok"]["level"], 0)
        self.assertIn("G-ABC1234XYZ", code["ga4"]["ids"])
        only_code = tracking.combine(code)
        self.assertIn("لا يثبت", only_code["ga4"]["conclusion_ar"])
        har = tracking.events_from_har(json.loads(html("network.har")))
        comb = tracking.combine(code, har)
        self.assertEqual(comb["ga4"]["level"], 2)
        self.assertTrue(comb["ga4"]["purchase_event_observed"])
        self.assertFalse(comb["meta"]["purchase_event_observed"])
        self.assertIn("لم نرصد حدث شراء", comb["meta"]["conclusion_ar"])

    def test_confirmations_need_notes_and_order(self):
        code = tracking.detect_code(html("product_page.html"))
        c = tracking.combine(code, None, {"ga4": {"level": 4, "note": "order 1004 matched in GA4 DebugView"}})
        self.assertEqual(c["ga4"]["level"], 3)  # level 4 needs observed requests first
        c = tracking.combine(code, None, {"meta": {"level": 3}})
        self.assertEqual(c["meta"]["level"], 1)  # no note, no upgrade


class VisibilityTests(unittest.TestCase):
    def test_queries(self):
        q = visibility.build_queries("الريحان", ["عطور", "بخور"], products=["عود ملكي"], cities=["الرياض"], competitors=["متجر س"])
        cats = {x["category"] for x in q}
        self.assertEqual(cats, set(visibility.CATEGORIES))
        self.assertEqual(len(q), len({x["query"] for x in q}))

    def test_summary(self):
        obs = [{"platform": "chatgpt", "date": "2026-09-30", "query": f"q{i}", "category": "brand", "mentioned": i % 2 == 0} for i in range(4)]
        obs.append({"platform": "x", "date": "bad", "query": "q", "category": "brand", "mentioned": "yes"})
        s = visibility.summarize(obs)
        self.assertEqual((s["observations"], s["rejected"]), (4, 1))
        row = [r for r in s["rows"] if r["platform"] == "all"][0]
        self.assertEqual(row["mention_rate"], 0.5)
        self.assertTrue(row["small_sample"])
        self.assertTrue(any("عينة" in c for c in s["caveats_ar"]))


if __name__ == "__main__":
    unittest.main()


class UnreachableTests(unittest.TestCase):
    def test_network_failure_is_not_a_store_finding(self):
        def down(url):
            return FetchResult(url, url, None, [], {}, "", "URLError: tunnel 403")
        res = seo_audit.crawl("https://blocked.example/", max_pages=3, delay=0, fetcher=down)
        ids = {x["id"] for x in res["findings"]}
        self.assertFalse(res["reachable"])
        self.assertIn("fetch_failed", ids)
        self.assertIn("robots_unreachable", ids)
        self.assertNotIn("sitemap_missing", ids)
        self.assertTrue(all(x["severity"] == "info" for x in res["findings"]))


class LocalHttpTests(unittest.TestCase):
    """Real HTTP through urllib against a local server (redirects, 404, robots)."""

    def test_fetch_and_crawl(self):
        import http.server
        import os
        import threading

        pages = {
            "/": (200, '<html lang="ar"><head><title>متجر تجريبي للعطور</title></head><body><h1>مرحبا</h1><a href="/old">x</a><a href="/missing">y</a></body></html>'),
            "/p1": (200, html("product_page.html")),
            "/robots.txt": (200, "User-agent: *\nAllow: /\n"),
        }

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/old":
                    self.send_response(301); self.send_header("Location", "/p1"); self.end_headers(); return
                st, body = pages.get(self.path, (404, "nope"))
                data = body.encode("utf-8")
                self.send_response(st)
                self.send_header("Content-Type", "text/plain" if self.path.endswith(".txt") else "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = {k: os.environ.get(k) for k in ("no_proxy", "NO_PROXY")}
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            r = seo_audit.fetch(base + "/old")
            self.assertEqual(r.status, 200)
            self.assertEqual([s for _, s in r.chain], [301, 200])
            res = seo_audit.crawl(base + "/", max_pages=5, delay=0)
            self.assertTrue(res["reachable"])
            self.assertIn("http_error", {x["id"] for x in res["findings"]})
            self.assertTrue(res["robots"]["available"])
        finally:
            srv.shutdown()
            srv.server_close()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
