# Operating modes / أوضاع التشغيل

Pick per operation from the capability map. Tell the merchant which mode
applies in one line.

## A. Connected merchant mode — «وضع الاتصال بالمتجر»

- Requires mapped merchant tools for the specific operation (`status: mapped`, level ≥ `schema_validated`).
- Reads and writes go through those tools only, within the merchant's authorization and the connector's permissions. MCP never bypasses Salla permissions.
- Missing operation → switch that operation to B/C/D and say it is a limit of the current connection.

Merchant line: «متصل بمتجرك عبر الأدوات المتاحة: أقدر أقرأ وأعدّل [..]. العمليات غير المتاحة سأجهزها لك كمقترح.»

## B. Public audit and content mode — «وضع الفحص العام والمحتوى»

- Analyze public pages (with the host's web fetch or `audit-url`), merchant-provided texts and images.
- Produce: product descriptions, names, SEO titles/descriptions, banner concepts (and images if an image tool exists), public SEO audit, GEO readiness, before/after proposals, dashboard steps for writes.
- Never claim anything was saved to Salla.

Merchant line: «لا يوجد اتصال بلوحة متجرك؛ سأعمل على الصفحات العامة وما ترسله لي، وأجهز التعديلات لتطبيقها من لوحة سلة.»

## C. Assisted browser mode — «وضع المتصفح بمساعدتك»

Only when the host lists browser tools **and** the merchant asked for an
action in the Salla dashboard.

- Use the merchant's own session. Never ask for or store passwords in files or chat.
- Login, OTP, CAPTCHA, 2FA: hand off to the merchant («سجّل دخولك في النافذة ثم أخبرني»), then continue.
- Session expiry: stop, report what was done per item, ask the merchant to log in again.
- Same protocol: read current values on screen, minimal change, confirm sensitive changes, verify after saving by reloading.
- If the host has no browser tools, say so and use mode B with dashboard steps.

## D. File analysis mode — «وضع تحليل الملفات»

- `python3 scripts/nitaaq_cli.py analyze-export file.csv [--report --from --to --statuses --md]`.
- Always report: export date if known (ask the merchant if not), date coverage (`date_min`/`date_max`), row counts, unmapped columns, and limitations the helper lists.
- A file is a snapshot; it may not match the store now.

## What is never inferred in B or D

Private orders, hidden products, customer data, live inventory, private
reports. Say: «هذي بيانات خاصة لا تظهر في الصفحات العامة؛ أحتاج اتصالاً بالمتجر أو ملف تصدير.»

## Capability changes

When the merchant connects or reconnects MCP: rebuild the map, `capabilities diff`,
and announce new abilities in one line. When a call fails with
`auth_expired`, mark the operation unavailable for now and tell the merchant how to reconnect.
