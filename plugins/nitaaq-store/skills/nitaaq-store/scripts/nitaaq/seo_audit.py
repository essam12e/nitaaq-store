"""Public SEO audit: HTTP, robots/sitemap, on-page facts, structured data.

Everything here is evidence from a public crawl at a point in time. It is
not proof of Google's indexing state; Search Console data (when the
merchant grants it or exports it) is the source for that.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .arabic import parse_amount, to_western_digits

USER_AGENT = "NitaaqStoreAudit/1.0 (+merchant-requested audit)"
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "ttclid", "sclid"}
FACET_PARAMS = {"sort", "order", "filter", "filters", "page", "price", "min_price", "max_price", "color", "size", "brand", "q", "search", "view", "limit"}

# Crawlers whose access a merchant may want to know about. Being allowed or
# blocked is the merchant's policy choice; we only report it.
CRAWLERS = {
    "Googlebot": "بحث Google (والذي تعتمد عليه ميزات AI Overviews / AI Mode)",
    "Bingbot": "بحث Bing (ويُستخدم في منتجات بحث أخرى)",
    "Google-Extended": "استخدام المحتوى لتدريب/تأريض نماذج Gemini — لا يؤثر على الظهور في بحث Google",
    "OAI-SearchBot": "ظهور الصفحات في نتائج بحث ChatGPT",
    "GPTBot": "تدريب نماذج OpenAI",
    "ChatGPT-User": "زيارات يطلبها مستخدم ChatGPT مباشرة",
    "ClaudeBot": "تدريب نماذج Anthropic",
    "Claude-SearchBot": "تحسين نتائج البحث في Claude",
    "Claude-User": "زيارات يطلبها مستخدم Claude مباشرة",
    "PerplexityBot": "فهرسة Perplexity للبحث",
}


# ------------------------------------------------------------------ fetching

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    chain: list = field(default_factory=list)  # [(url, status)]
    headers: dict = field(default_factory=dict)
    body: str = ""
    error: str | None = None


def fetch(url: str, timeout: float = 20, max_redirects: int = 8) -> FetchResult:
    opener = urllib.request.build_opener(_NoRedirect)
    chain, cur = [], url
    for _ in range(max_redirects + 1):
        req = urllib.request.Request(cur, headers={"User-Agent": USER_AGENT, "Accept-Language": "ar,en;q=0.8"})
        try:
            resp = opener.open(req, timeout=timeout)
            status, headers = resp.status, dict(resp.headers)
            body = resp.read(3_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
            chain.append((cur, status))
            return FetchResult(url, cur, status, chain, headers, body)
        except urllib.error.HTTPError as e:
            chain.append((cur, e.code))
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                cur = urllib.parse.urljoin(cur, e.headers["Location"])
                continue
            body = ""
            try:
                body = e.read(200_000).decode("utf-8", "replace")
            except Exception:
                pass
            return FetchResult(url, cur, e.code, chain, dict(e.headers or {}), body)
        except Exception as e:  # DNS, TLS, timeout
            return FetchResult(url, cur, None, chain, {}, "", f"{type(e).__name__}: {e}")
    return FetchResult(url, cur, None, chain, {}, "", "too_many_redirects")


# ------------------------------------------------------------------- parsing

class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict = {}
        self.links: list = []
        self.canonical = None
        self.hreflang: list = []
        self.headings: dict = {"h1": [], "h2": [], "h3": []}
        self._heading = None
        self.images: list = []
        self.scripts_src: list = []
        self._script_type = None
        self._script_buf: list = []
        self.jsonld_raw: list = []
        self.inline_scripts: list = []
        self.text: list = []
        self._skip = 0
        self.html_attrs: dict = {}

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.html_attrs = a
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if "canonical" in rel.split():
                self.canonical = a.get("href")
            if "alternate" in rel.split() and a.get("hreflang"):
                self.hreflang.append((a["hreflang"], a.get("href")))
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.images.append({"src": a.get("src") or a.get("data-src"), "alt": a.get("alt"), "has_alt_attr": "alt" in a})
        elif tag in self.headings:
            self._heading = tag
            self.headings[tag].append("")
        elif tag == "script":
            self._script_type = a.get("type", "").lower()
            self._script_buf = []
            if a.get("src"):
                self.scripts_src.append(a["src"])
            self._skip += 1
        elif tag in ("style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == self._heading:
            self._heading = None
        elif tag == "script":
            body = "".join(self._script_buf)
            if self._script_type == "application/ld+json":
                self.jsonld_raw.append(body)
            elif body.strip():
                self.inline_scripts.append(body)
            self._script_type = None
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._script_type is not None:
            self._script_buf.append(data)
            return
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._heading:
            self.headings[self._heading][-1] += data
        if data.strip():
            self.text.append(data.strip())


def _flatten_jsonld(obj) -> list:
    out = []
    if isinstance(obj, list):
        for o in obj:
            out += _flatten_jsonld(o)
    elif isinstance(obj, dict):
        if "@graph" in obj:
            out += _flatten_jsonld(obj["@graph"])
        out.append(obj)
    return out


def _types(node: dict) -> set:
    t = node.get("@type")
    return set(t) if isinstance(t, list) else ({t} if t else set())


_PRICE_RE = re.compile(r"(?:(?:ر\.?\s?س\.?|SAR|ريال|﷼)\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:ر\.?\s?س\.?|SAR|ريال|﷼))")


def visible_prices(text: str) -> list:
    out = []
    for m in _PRICE_RE.finditer(to_western_digits(text)):
        v = parse_amount(m.group(1) or m.group(2))
        if v is not None:
            out.append(v)
    return out


@dataclass
class PageFacts:
    url: str
    status: int | None
    title: str
    meta_description: str | None
    meta_robots: str
    canonical: str | None
    hreflang: list
    h1: list
    h2_count: int
    images: list
    internal_links: list
    external_links: int
    jsonld: list
    jsonld_errors: int
    lang: str
    dir: str
    word_count: int
    prices_visible: list
    scripts_src: list
    inline_scripts: list
    og: dict
    x_robots: str = ""


def parse_page(html: str, url: str, status: int | None = 200, headers: dict | None = None) -> PageFacts:
    p = _PageParser()
    p.feed(html or "")
    host = urllib.parse.urlparse(url).netloc
    internal = []
    ext = 0
    for href in p.links:
        full = urllib.parse.urljoin(url, href)
        u = urllib.parse.urlparse(full)
        if u.scheme not in ("http", "https"):
            continue
        if u.netloc == host:
            internal.append(urllib.parse.urlunparse(u._replace(fragment="")))
        else:
            ext += 1
    nodes, errs = [], 0
    for raw in p.jsonld_raw:
        try:
            nodes += _flatten_jsonld(json.loads(raw))
        except json.JSONDecodeError:
            errs += 1
    text = " ".join(p.text)
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    return PageFacts(
        url=url, status=status, title=" ".join(p.title.split()),
        meta_description=p.meta.get("description"), meta_robots=p.meta.get("robots", "").lower(),
        canonical=urllib.parse.urljoin(url, p.canonical) if p.canonical else None,
        hreflang=p.hreflang, h1=[" ".join(h.split()) for h in p.headings["h1"]],
        h2_count=len(p.headings["h2"]), images=p.images, internal_links=sorted(set(internal)),
        external_links=ext, jsonld=nodes, jsonld_errors=errs,
        lang=p.html_attrs.get("lang", ""), dir=p.html_attrs.get("dir", ""),
        word_count=len(text.split()), prices_visible=visible_prices(text),
        scripts_src=p.scripts_src, inline_scripts=p.inline_scripts,
        og={k: v for k, v in p.meta.items() if k.startswith("og:")},
        x_robots=hdrs.get("x-robots-tag", "").lower(),
    )


# ------------------------------------------------------------------ findings

def finding(fid, severity, area, message_ar, evidence="", fix_ar="", url=""):
    return {"id": fid, "severity": severity, "area": area, "message_ar": message_ar,
            "evidence": evidence, "fix_ar": fix_ar, "url": url, "evidence_level": "public_crawl"}


def url_params(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query, keep_blank_values=True))


def product_nodes(f: PageFacts) -> list:
    return [n for n in f.jsonld if "Product" in _types(n) or "ProductGroup" in _types(n)]


def _offers(node: dict) -> list:
    o = node.get("offers")
    if isinstance(o, dict):
        if "AggregateOffer" in _types(o):
            return [o]
        return [o]
    if isinstance(o, list):
        return [x for x in o if isinstance(x, dict)]
    return []


def audit_page(f: PageFacts, page_type: str | None = None) -> list:
    out = []
    u = f.url
    if f.status is None:
        return [finding("fetch_failed", "high", "crawl", "تعذر جلب الصفحة", "", "تحقق من الرابط والاستضافة", u)]
    if f.status >= 400:
        out.append(finding("http_error", "high", "crawl", f"الصفحة تعيد رمز {f.status}", str(f.status), "أصلح الرابط أو أعد توجيهه لصفحة مناسبة", u))
    robots = f"{f.meta_robots} {f.x_robots}"
    if "noindex" in robots:
        out.append(finding("noindex", "high", "indexability", "الصفحة تمنع الفهرسة (noindex)", robots.strip(), "أزل noindex إن كانت الصفحة مهمة للبحث", u))
    params = url_params(u)
    if params:
        kinds = set(params) & (TRACKING_PARAMS | FACET_PARAMS)
        out.append(finding("parameterized_url", "medium" if not f.canonical else "low", "duplication",
                           "رابط يحتوي معاملات قد تنشئ نسخاً مكررة", ", ".join(sorted(params)),
                           "تأكد أن canonical يشير للرابط النظيف" if kinds else "راجع ضرورة المعاملات", u))
    if not f.canonical:
        out.append(finding("canonical_missing", "medium", "duplication", "لا يوجد رابط canonical", "", "أضف canonical يشير للنسخة الأساسية (غالباً من إعدادات القالب/المنصة)", u))
    else:
        cu = urllib.parse.urlparse(f.canonical)
        if cu.query and set(url_params(f.canonical)) & (TRACKING_PARAMS | FACET_PARAMS):
            out.append(finding("canonical_parameterized", "medium", "duplication", "الـcanonical نفسه يحتوي معاملات", f.canonical, "اجعل canonical الرابط النظيف", u))
        if cu.netloc and cu.netloc != urllib.parse.urlparse(u).netloc:
            out.append(finding("canonical_cross_host", "medium", "duplication", "الـcanonical يشير لنطاق آخر", f.canonical, "تحقق أن هذا مقصود", u))
    t = f.title
    if not t:
        out.append(finding("title_missing", "high", "metadata", "عنوان الصفحة (title) مفقود", "", "أضف عنوان SEO واضح يتضمن اسم المنتج/التصنيف والمتجر", u))
    elif len(t) > 65:
        out.append(finding("title_long", "low", "metadata", f"العنوان طويل ({len(t)} حرفاً) وقد يُقتطع", t, "اختصره إلى ~50–60 حرفاً مع إبقاء الكلمات المهمة أولاً", u))
    elif len(t) < 15:
        out.append(finding("title_short", "medium", "metadata", "العنوان قصير وغير وصفي", t, "اجعله يصف محتوى الصفحة بدقة", u))
    d = (f.meta_description or "").strip()
    if not d:
        out.append(finding("meta_description_missing", "medium", "metadata", "وصف SEO مفقود", "", "اكتب وصفاً فريداً (~120–155 حرفاً) يلخص الصفحة", u))
    elif len(d) > 170:
        out.append(finding("meta_description_long", "low", "metadata", f"وصف SEO طويل ({len(d)} حرفاً)", d[:80] + "…", "اختصره", u))
    if not f.h1:
        out.append(finding("h1_missing", "medium", "content", "لا يوجد عنوان H1", "", "اجعل اسم المنتج/التصنيف عنوان H1 (من إعدادات القالب إن أمكن)", u))
    elif len([h for h in f.h1 if h]) > 1:
        out.append(finding("h1_multiple", "low", "content", f"أكثر من H1 ({len(f.h1)})", " | ".join(f.h1[:3]), "يفضّل H1 واحد واضح", u))
    imgs = [i for i in f.images if i.get("src")]
    no_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
    if imgs and no_alt:
        sev = "medium" if len(no_alt) / len(imgs) > 0.3 else "low"
        out.append(finding("img_alt_missing", sev, "images", f"{len(no_alt)} من {len(imgs)} صورة بدون نص بديل", no_alt[0]["src"][:120], "أضف نصاً بديلاً يصف المنتج فعلاً (ليس حشو كلمات)", u))
    if f.lang and not f.lang.lower().startswith("ar") and f.dir != "rtl":
        out.append(finding("lang_not_ar", "low", "content", f"لغة الصفحة المعلنة «{f.lang}»", f.lang, "تحقق من إعداد اللغة للنسخة العربية", u))
    if f.jsonld_errors:
        out.append(finding("jsonld_invalid", "high", "structured_data", f"{f.jsonld_errors} كتلة JSON-LD غير صالحة", "", "أصلح صياغة JSON-LD (غالباً من القالب أو تطبيق)", u))

    prods = product_nodes(f)
    if page_type == "product" and not prods:
        out.append(finding("product_schema_missing", "medium", "structured_data", "لا توجد بيانات منظمة من نوع Product", "", "تحقق من دعم القالب لـ Product/Offer", u))
    for n in prods:
        offers = _offers(n)
        if not n.get("name"):
            out.append(finding("product_schema_name", "medium", "structured_data", "Product بدون name", "", "", u))
        if not n.get("image"):
            out.append(finding("product_schema_image", "low", "structured_data", "Product بدون image", "", "", u))
        if not offers:
            out.append(finding("offer_missing", "medium", "structured_data", "Product بدون offers (السعر والتوفر)", "", "تحقق من إعدادات القالب", u))
        for o in offers:
            price = parse_amount(o.get("price") if o.get("price") is not None else o.get("lowPrice"))
            cur = o.get("priceCurrency")
            if price is None:
                out.append(finding("offer_price_missing", "medium", "structured_data", "Offer بدون سعر", json.dumps(o, ensure_ascii=False)[:120], "", u))
            elif f.prices_visible and price not in f.prices_visible:
                out.append(finding("price_mismatch", "high", "consistency",
                                   "السعر في البيانات المنظمة لا يطابق أي سعر ظاهر في الصفحة",
                                   f"schema={price} visible={[str(p) for p in f.prices_visible[:5]]}",
                                   "تحقق يدوياً؛ اختلاف السعر يضر بالثقة وقد يسبب رفضاً في Merchant Center", u))
            if cur and cur.upper() != "SAR":
                out.append(finding("offer_currency", "low", "structured_data", f"العملة في Offer «{cur}»", cur, "تحقق أنها العملة المعروضة فعلاً", u))
            if not o.get("availability"):
                out.append(finding("offer_availability", "low", "structured_data", "Offer بدون availability", "", "", u))
    return out


# ------------------------------------------------------------ robots/sitemap

def robots_report(site: str, robots_txt: str | None, page_url: str | None = None) -> dict:
    base = f"{urllib.parse.urlparse(site).scheme}://{urllib.parse.urlparse(site).netloc}"
    target = page_url or base + "/"
    if robots_txt is None:
        return {"available": False, "note_ar": "تعذر قراءة robots.txt", "crawlers": {}, "sitemaps": []}
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())
    crawlers = {}
    for bot, purpose in CRAWLERS.items():
        crawlers[bot] = {"allowed": rp.can_fetch(bot, target), "purpose_ar": purpose}
    sitemaps = [l.split(":", 1)[1].strip() for l in robots_txt.splitlines() if l.lower().startswith("sitemap:")]
    return {"available": True, "target": target, "crawlers": crawlers, "sitemaps": sitemaps}


def parse_sitemap(xml: str) -> dict:
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "")
    is_index = "<sitemapindex" in (xml or "")
    return {"is_index": is_index, "count": len(locs), "locs": locs}


# ------------------------------------------------------------------- site

def guess_page_type(url: str, f: PageFacts | None = None) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if f and product_nodes(f):
        return "product"
    if re.search(r"/p\d+|/product", path):
        return "product"
    if re.search(r"/c\d+|/category|/categories", path):
        return "category"
    if path in ("", "/"):
        return "home"
    if re.search(r"/brands?/", path):
        return "brand"
    if re.search(r"/tags?/", path):
        return "tag"
    return "other"


def site_findings(pages: list[PageFacts]) -> list:
    out = []
    titles = Counter(p.title for p in pages if p.title)
    for t, n in titles.items():
        if n > 1:
            urls = [p.url for p in pages if p.title == t][:5]
            out.append(finding("title_duplicate", "medium", "duplication", f"العنوان نفسه في {n} صفحات", t, "اجعل لكل صفحة عنواناً فريداً", ", ".join(urls)))
    descs = Counter((p.meta_description or "").strip() for p in pages if (p.meta_description or "").strip())
    for d, n in descs.items():
        if n > 1:
            out.append(finding("description_duplicate", "low", "duplication", f"وصف SEO مكرر في {n} صفحات", d[:80], "", ""))
    for p in pages:
        pt = guess_page_type(p.url, p)
        if pt in ("category", "brand", "tag"):
            prod_links = [l for l in p.internal_links if guess_page_type(l) == "product"]
            if len(prod_links) == 0:
                out.append(finding("empty_listing", "high", "content", "صفحة تصنيف/ماركة/وسم بلا منتجات ظاهرة", "", "أضف منتجات أو أخفِ الصفحة/امنع فهرستها", p.url))
            elif len(prod_links) < 3 and p.word_count < 150:
                out.append(finding("weak_listing", "medium", "content", "صفحة تصنيف ضعيفة (منتجات قليلة ونص قليل)", f"products={len(prod_links)} words={p.word_count}", "أضف وصفاً مفيداً وروابط لتصنيفات قريبة", p.url))
    return out


def crawl(start_url: str, max_pages: int = 25, delay: float = 1.0, fetcher=fetch) -> dict:
    """Polite same-host BFS crawl of public pages (robots.txt respected)."""
    parsed = urllib.parse.urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rob = fetcher(base + "/robots.txt")
    robots_txt = rob.body if rob.status == 200 else None
    rp = urllib.robotparser.RobotFileParser()
    rp.parse((robots_txt or "").splitlines())
    queue, seen, pages, fetches = [start_url], set(), [], []
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        key = url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        if robots_txt and not rp.can_fetch(USER_AGENT, url):
            continue
        r = fetcher(url)
        fetches.append({"url": url, "status": r.status, "chain": r.chain, "error": r.error})
        if r.status and r.status < 400 and "html" in (r.headers.get("Content-Type", r.headers.get("content-type", "text/html"))):
            f = parse_page(r.body, r.final_url, r.status, r.headers)
            pages.append(f)
            for l in f.internal_links:
                if l.split("#")[0] not in seen and not url_params(l):
                    queue.append(l)
        if delay:
            time.sleep(delay)
    findings = []
    if rob.status is None:
        findings.append(finding("robots_unreachable", "info", "crawl", "تعذر الوصول إلى robots.txt من بيئة الفحص",
                                rob.error or "", "قد يكون قيداً في شبكة بيئة الفحص وليس في المتجر", base + "/robots.txt"))
    for fr in fetches:
        if fr["status"] is None:
            findings.append(finding("fetch_failed", "info", "crawl", "تعذر جلب الصفحة من بيئة الفحص",
                                    fr["error"] or "", "أعد المحاولة من بيئة أخرى أو أرسل الصفحة؛ قد لا تكون مشكلة في المتجر", fr["url"]))
        elif fr["status"] >= 400:
            findings.append(finding("http_error", "high", "crawl", f"الصفحة تعيد رمز {fr['status']}", str(fr["status"]),
                                    "أصلح الرابط أو أعد توجيهه لصفحة مناسبة", fr["url"]))
        if len(fr["chain"]) > 2:
            findings.append(finding("redirect_chain", "medium", "crawl", f"سلسلة تحويلات من {len(fr['chain']) - 1} خطوات", " → ".join(f"{u} ({s})" for u, s in fr["chain"]), "اجعل التحويل مباشراً للوجهة النهائية", fr["url"]))
    for f in pages:
        findings += audit_page(f, guess_page_type(f.url, f))
    findings += site_findings(pages)
    sm_urls = robots_report(base, robots_txt)["sitemaps"] if robots_txt else []
    sitemap = None
    for sm in (sm_urls or [base + "/sitemap.xml"])[:1]:
        s = fetcher(sm)
        sitemap = {"url": sm, "status": s.status, **(parse_sitemap(s.body) if s.status == 200 else {"count": 0})}
    if sitemap and sitemap.get("status") is None:
        pass  # unreachable from here: already reported, not evidence the sitemap is missing
    elif not sitemap or sitemap.get("status") != 200:
        findings.append(finding("sitemap_missing", "medium", "crawl", "لم نجد خريطة موقع قابلة للقراءة", "", "تحقق من رابط sitemap في robots.txt", base))
    return {
        "site": base, "pages_crawled": len(pages), "max_pages": max_pages,
        "coverage_note_ar": f"تم فحص {len(pages)} صفحة عامة فقط (الحد {max_pages})؛ ليست كل صفحات المتجر.",
        "robots": robots_report(base, robots_txt), "sitemap": sitemap,
        "fetches": fetches, "findings": findings,
        "reachable": any(f["status"] is not None for f in fetches),
        "pages": [page_summary(p) for p in pages],
        "disclaimer_ar": "فحص زحف عام بتاريخ محدد؛ لا يثبت حالة الفهرسة الفعلية في Google.",
    }


def page_summary(f: PageFacts) -> dict:
    return {"url": f.url, "status": f.status, "type": guess_page_type(f.url, f), "title": f.title,
            "meta_description": f.meta_description, "canonical": f.canonical, "h1": f.h1,
            "word_count": f.word_count, "images": len(f.images),
            "images_without_alt": sum(1 for i in f.images if i.get("src") and not (i.get("alt") or "").strip()),
            "jsonld_types": sorted({t for n in f.jsonld for t in _types(n)}),
            "prices_visible": [str(p) for p in f.prices_visible[:5]]}


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def findings_markdown_ar(findings: list, limit: int = 60) -> str:
    fs = sorted(findings, key=lambda x: (SEVERITY_ORDER.get(x["severity"], 9), x["id"]))
    sev = {"high": "🔴 عالية", "medium": "🟠 متوسطة", "low": "🟡 منخفضة", "info": "ℹ️ معلومة"}
    rows = ["| الأولوية | المشكلة | الرابط | الإصلاح المقترح |", "|---|---|---|---|"]
    for x in fs[:limit]:
        rows.append(f"| {sev.get(x['severity'], x['severity'])} | {x['message_ar']} | {x['url'][:70] or '—'} | {x['fix_ar'] or '—'} |")
    if len(fs) > limit:
        rows.append(f"| … | و{len(fs) - limit} ملاحظة أخرى في ملف JSON | | |")
    return "\n".join(rows)
