# Reviewer / المراجع

Checks findings before the merchant sees them. It does not trust a finding's
text: it reloads the cited evidence and recomputes.

Run `review --store-id <id> --findings findings.json [--mode independent_context]`.
Use `independent_context` only when this review runs as a separate agent;
otherwise it is a `self_check`, and the report says so.

| Issue | Severity |
|---|---|
| Missing required fields, wrong store, missing evidence, number that does not recompute | fail |
| Invalid comparison presented as a real change | revise |
| Cause without mechanism evidence, or with high confidence | revise |
| Market benchmark without a source, guaranteed outcome | revise |
| Partial data not disclosed | revise |
| No alternatives for a stated cause | warning |
| Two agents agreeing on the same evidence | note: not independent |

Failed findings are excluded from facts and listed separately. One review
round only; if a revised finding still fails, report the limitation.

The reviewer never calls store tools, never writes, never contacts the
merchant.
