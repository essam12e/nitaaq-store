# فحص وتحسين ظهور المتجر — SEO وGEO

A diagnosis-and-remediation workflow, not a text rewrite. Four parts:
A. SEO audit · B. GEO readiness · C. Observed AI visibility · D. Remediation and re-check.
Keep the evidence type visible everywhere: public crawl, Search Console data,
merchant files, store tools, or manual observation.

Guidance this workflow relies on (checked 2026-09-30; re-check when unsure):
- Google, "AI features and your website" (developers.google.com/search/docs/appearance/ai-features): no additional requirements, special files, or special schema to appear in AI Overviews/AI Mode; normal SEO best practice applies; controls are `nosnippet`, `data-nosnippet`, `max-snippet`, `noindex`; `Google-Extended` governs use in some other Google AI systems, not Search inclusion.
- Google Search Central docs for canonicalization, robots.txt, sitemaps, Product structured data and merchant listings.
- Crawler documentation published by OpenAI (OAI-SearchBot, GPTBot, ChatGPT-User), Anthropic (ClaudeBot, Claude-SearchBot, Claude-User) and Perplexity (PerplexityBot).
Use the host's web tools to re-check these pages when a decision depends on them; do not invent GEO rules.

## A. SEO audit

Evidence: `audit-url <home> --max-pages N --out .nitaaq/crawl.json --md`, plus
product/category lists from tools or exports, plus Search Console exports or
authorized access when the merchant provides them.

Inspect:
- Crawlability & indexability: status codes, redirect chains, `noindex` (meta and `X-Robots-Tag`), robots.txt rules, sitemap presence and contents.
- Canonicals: present, self-referencing on clean URLs, not pointing to parameterized or other-host URLs.
- Duplicates & parameters: sort/filter/utm URLs, duplicate titles/descriptions.
- SEO titles and descriptions: present, unique, descriptive, sensible length.
- Headings and product naming: one clear H1; names that say what the product is (type + key attribute + brand/model when real).
- Useful original content: category intros, product descriptions grounded in facts; thin or copied manufacturer text flagged.
- Image alt text: descriptive, not keyword stuffing.
- Internal linking & product/category relationships: products reachable from categories; empty/weak categories; breadcrumbs.
- Mobile usability & performance: only with measurements the host can run (e.g. a browser/Lighthouse tool) or merchant-provided PageSpeed/Search Console data. Otherwise «لم يُقَس».
- Product/Offer structured data: name, image, offers.price, priceCurrency, availability; validity of JSON-LD.
- Consistency: structured-data price/availability equals the visible price/availability (`price_mismatch` finding).
- Search Console: indexing (Page indexing report), queries/pages performance — only from authorized access or exports; state the date range.

Limits to state in every report:
- «فحص الزحف العام لا يثبت حالة الفهرسة الفعلية في Google.»
- Never use `site:` result counts as an index census.
- The crawl covers N pages, not the whole store.

## B. GEO readiness

Question: can AI-assisted search systems access, understand, trust and cite
the store's content? Helper: `geo --crawl .nitaaq/crawl.json --store-name "<name>" [--llms-status <code>]`.

Dimensions (0–2 each, with evidence and URLs):
1. Access — search crawlers allowed (Googlebot, Bingbot, OAI-SearchBot, Claude-SearchBot, PerplexityBot). Blocking AI crawlers is the merchant's policy choice; report it, explain the trade-off, don't decide for them. Training crawlers (GPTBot, ClaudeBot, Google-Extended) are separate from search visibility.
2. Store identity — clear name, what the store sells, where it ships, Organization/OnlineStore data, «من نحن».
3. Policies — shipping, returns/exchange, contact: present, specific (durations, costs, conditions), consistent across pages.
4. Product facts — accurate names, attributes, prices, availability; enough factual text to answer questions.
5. Consistency — same store/brand/product naming across titles, schema, and policies.
6. Answers — clear factual answers to real customer questions (FAQ on relevant pages), without inventing claims.

`llms.txt` and "AI schema": optional, never required, never a guaranteed
route to inclusion. Report presence as information only.

## C. Observed AI visibility

Separate from readiness. It measures whether AI answers mention or cite the
store for relevant queries, as a sample.

Query set: `visibility-queries --store "<name>" --categories "<c1,c2>" [--products ..] [--cities الرياض,جدة] [--competitors ..] [--aliases ..]`
covers, in Saudi Arabic:
- Brand/store searches («متجر X», «هل متجر X موثوق؟»).
- Product/model searches.
- Category discovery without the store name («أفضل متجر ... في السعودية»).
- Comparisons («X ولا Y؟», «مقارنة بين أشهر متاجر ...»).
- Informational questions («كيف أختار ...؟»).
- Local («... في الرياض مع توصيل سريع»).
- Trust and policy («سياسة الاسترجاع في متجر X»).

How to test — only where tools, access and platform rules permit:
- Manual checks by the merchant (or you, if the host gives you a normal user browser session) on the platforms they care about (e.g. Google AI Overviews/AI Mode, ChatGPT search, Perplexity, Claude with web search, Gemini).
- Official APIs/tools with web search the host provides, noting that API answers can differ from consumer apps.
- No automated scraping against a platform's terms; no fake accounts; no mass querying.

Record each observation (`platform, date, query, category, mentioned, cited_url, position, accuracy_issues, logged_in, location, evidence_ref`) in a JSON list and run `visibility-summary obs.json`.

Report honestly:
- Answers vary by run, day, account, location and model; this is a sample, not a ranking.
- Show sample sizes; <5 queries in a category is not enough to conclude.
- Mentions are not traffic or sales.
- Record inaccuracies (wrong prices, old policies, wrong city) as remediation items: they usually trace to inconsistent public content.

## D. Remediation and re-check

1. Prioritize by impact × effort: indexability blockers → duplicate/canonical issues → missing/weak titles & descriptions on key pages → product data consistency → content gaps (policies, category intros, FAQs) → images/alt → internal links.
2. For each finding, name who can fix it: store tools (mode A), Salla dashboard, theme settings, an installed app, or Salla support. Platform-level items (e.g. how canonicals or JSON-LD are generated) may not be editable by the merchant; say so.
3. Fix within authorization using the execution protocol: SEO titles/descriptions, product names/descriptions, alt text, category descriptions, menus/internal links, policy pages. Before/after tables; approval for bulk changes; verify via readback and by re-fetching the public page.
4. Never report a finding as fixed until a re-crawl or readback confirms it. Search engines and AI systems update on their own schedules; no promise of ranking, indexing or citation.
5. Deliver an Arabic report: executive summary, SEO findings table, GEO readiness table, visibility sample summary, what was fixed and verified, what is proposed, what needs the dashboard/support, and the next re-check date.
