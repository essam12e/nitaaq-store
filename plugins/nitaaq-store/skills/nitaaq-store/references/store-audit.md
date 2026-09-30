# Store audit / فحص المتجر

## Coverage
- URLs: duplicates, parameterized URLs (sort/filter/utm), canonical handling, redirect chains, 4xx/5xx.
- Listing pages: empty or weak category/brand/tag pages.
- Product pages: titles, descriptions, images (count, quality, alt), price presentation (regular vs sale shown correctly), mobile layout.
- Structured data vs visible facts (name, price, currency, availability).
- Tracking: GA4, Meta, TikTok, Snapchat.
- Checkout usability; guest checkout where supported.
- Payment methods: display and eligibility.
- Shipping cost visibility before checkout.
- Catalog gaps: products without images, descriptions or categories (needs tools or an export).
- Out-of-stock display vs the merchant's policy (show, hide, allow pre-order).

## Tools
- Public crawl: `audit-url https://store.example --max-pages 25 --out .nitaaq/crawl.json --md`. The crawl is polite (robots.txt, delay) and partial by design; say how many pages.
- One page from saved HTML: `audit-html page.html --url <url> --md`.
- Catalog gaps from tools (mode A) or export (`analyze-export`).
- Mobile layout: only with a browser/screenshot tool at a mobile viewport; otherwise say not checked.

## Tracking evidence levels
1. Tracking code detected (`tracking --html page.html`).
2. Event request observed (`tracking --html page.html --har network.har` from a browser session the host recorded).
3. Event receipt verified in the destination platform (merchant shows the platform's event log/test events, or an authorized tool reads it).
4. Event values reconciled with a real authorized transaction.

Rules: never conclude purchase tracking works from script presence. Never
send fabricated purchase events to production analytics. Level 4 needs a
real transaction the merchant authorized specifically.

## Payments
Payment logos visible ≠ payment works. Eligibility depends on device,
browser, country and account (e.g. Apple Pay appears only on supported
Apple devices/browsers). Do not call Apple Pay broken because a test
environment can't show it. Any real payment test needs specific authorization
(amount, method, and refund plan).

## Checkout
Walk the flow only with a browser tool, up to the step before payment unless
a real payment test is authorized. Note: required fields, forced account
creation, shipping cost first shown at which step, coupon field, errors in Arabic.

## Output
Arabic report: summary (top 5 issues by impact), findings table
(priority, issue, URL, evidence, fix, who can fix: dashboard / theme settings /
app / Salla support), what was not checked and why, and the evidence date.
