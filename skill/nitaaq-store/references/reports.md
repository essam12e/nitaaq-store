# Reports and business analysis / التقارير والتحليل

## Report families (only what tools/data actually provide)

Operational summary · sales, order count, AOV · monthly sales · sales by
branch, brand, category, city, device, gateway, payment method · products by
quantity and revenue · costs and product margins · coupon and offer
performance · new vs returning customers · demographics (only if the source
provides them) · top-spending customers · conversion and abandoned-cart
metrics · traffic sources and campaign attribution · shipping company
performance, delivery speed, satisfaction · destination cities · stock
levels and composition · returns · reviews and topics · wishlists · wallet
balances and transactions · affiliates · POS sales.

Do not claim a number of "working reports". The available set is whatever the
capability map and the merchant's files support today; list it when asked.

## Sources, in order of preference
1. A report tool from the connector (trusted output; still state its definitions).
2. Raw records from tools (orders, products) aggregated with `nitaaq.reports`.
3. Merchant exports: `analyze-export file.csv --report [--from --to --statuses] --md`.
4. Public data only for public facts (never for sales).

## Every report states
Use `ReportMeta.to_markdown_ar()`:
- Source.
- Date range and timezone (default Asia/Riyadh).
- Currency (default SAR).
- Metric definition (e.g. sales = order total including tax and shipping, or items net of discounts).
- Included order statuses (e.g. excluded cancelled/refunded) when known.
- Treatment of discounts, tax, shipping, refunds, cancellations when known; «غير معروف» otherwise.
- Completeness (fetched vs total) and freshness (fetch time / export date).
- Missing inputs and unavailable dimensions.

## Numbers
- Computed by code (helpers or trusted report outputs). No mental arithmetic.
- `None` = missing ≠ 0. Distinguish: missing data («غير متوفر»), true zero («صفر»), feature unavailable («غير متاح عبر الاتصال»), failed query («فشل الاستعلام»).
- Breakdowns include an explicit «غير محدد» bucket so parts sum to the whole.

## Wording rules
- Product margin (revenue − product cost) is «هامش المنتجات», never «صافي الربح», unless operating expenses are provided.
- No ROAS without ad spend (`reports.roas`).
- Attributed sales are «مبيعات منسوبة», not proven impact.
- New vs returning: needs all-time first-order dates; otherwise label as within-period only.
- No age/gender inference from names.

## Charts
When the host renders charts, chart only retrieved values (bar for categories,
line for time series). Always also give a table fallback.
