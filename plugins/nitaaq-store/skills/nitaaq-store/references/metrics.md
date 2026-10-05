# Metric dictionary / قاموس المقاييس

Definitions live in `assets/metrics.json`. Defaults: timezone Asia/Riyadh,
currency SAR, comparison window 28 complete days (yesterday backwards) vs the
28 days before.

## Comparing periods

`metrics compare --orders orders.json --current 2026-09-07,2026-10-04 --baseline 2026-08-10,2026-09-06 --total <reported> [--md]`

It returns sales, orders and AOV per period, the change, an exact
decomposition (order-count effect + order-value effect = sales change),
a `status` and `flags`.

| Flag | Meaning | Effect |
|---|---|---|
| `partial_current_day` / `partial_baseline_day` | A period includes today | not comparable |
| `unequal_periods` | Different lengths | not comparable |
| `overlapping_periods` | Periods overlap | not comparable |
| `incomplete_data` | Fewer records than the source reported | not comparable |
| `unknown_completeness` | The source reported no total | comparable, stated as unknown |
| `small_sample` | Few orders | comparable, low confidence |
| `duplicates_removed` | Same order seen twice | removed and stated |

`status` is `decline`, `increase`, `flat` (within the reporting threshold,
not a significance test), `not_comparable` or `unknown`. A
`not_comparable` result is never presented as a real change.

## Where the change happened

`metrics decompose --orders orders.json --current ... --baseline ... --dimension product|category|brand|city|payment_method`

Product, category and brand use item revenue; other dimensions use order
totals. Rows include «غير محدد» so the parts sum to the total change. This
says where the change is, not why.

## Rules

- Product margin is not net profit. Historical customer value is not modeled LTV.
- No ROAS or CAC without real spend data. No conversion rate without sessions.
- Unknown is «غير معروف», never zero.
