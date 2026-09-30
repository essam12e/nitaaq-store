# Orders and daily operations / الطلبات والتشغيل اليومي

Operations: `orders.list`, `orders.get`, `orders.statuses`, `orders.history`,
`orders.update_status`, `orders.add_note`, `invoices.list`, `customers.list`,
`carts.abandoned`, `reviews.list`, `reviews.reply`, `coupons.list/create`,
`offers.list`, `shipping.list`.

## Listing and details
- Filters only as the tool supports (dates, statuses, customer reference). Dates in Asia/Riyadh unless the tool states otherwise; say which.
- Details: products, quantities, totals (subtotal, discount, shipping, tax, total as returned), customer, address, payment method, status.
- Follow pagination for "all orders" requests; report fetched vs total.
- Show customer data only to the merchant in the private conversation; redact in anything shareable.

## Statuses
- Read the merchant's own status list (`orders.statuses`). Never guess numeric ids or assume labels map identically across stores (custom sub-statuses exist).
- Changing status: confirm order, current status, target status, and whether it notifies the customer (from the tool description/schema). Ask explicitly when it may notify: «تغيير الحالة إلى "تم الشحن" قد يرسل إشعاراً للعميل. أكمل؟»
- Bulk status changes: review table + one approval + per-order report.
- Verify via readback and status history.

## Notes
Internal notes are not customer messages; confirm the tool's note is internal
(read its description). Adding a note is not idempotent: use the ledger.

## Invoices
Invoice data (issued tax invoices) is separate from order data. Do not
present an order total as an invoice. List/show invoices only from invoice tools or dashboard.

## Customers
Lookup by the references the tool supports. Minimum necessary data. Never
infer age or gender from names.

## Abandoned carts
List carts and items; frequently abandoned products = count carts containing
each product over the period (state period and coverage). Contacting customers
about carts requires explicit authorization of the message and channel.

## Reviews
Read reviews; group recurring complaint themes by reading the text (quote
short anonymized examples). Replies are public: draft them, get approval, then post if a tool exists.

## Not done silently, ever
Refunds, cancellations, shipments/AWB creation, customer messages, coupon
creation, price-affecting offers. Each needs explicit authorization for that action.

## Other merchant actions
Categories, product options, coupons, offers, customer edits, review replies,
shipping, branches, taxes, languages: inspect the capability map first. If
unmapped, prepare the exact change and dashboard steps, and say: «هذا غير متاح
عبر الاتصال الحالي» — not «سلة لا تدعم هذا».
