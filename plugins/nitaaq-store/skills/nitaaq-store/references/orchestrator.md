# Orchestrator / المنسّق

The orchestrator is the main conversation. It owns the merchant, the plan,
the run state and the final answer. Specialists and the reviewer never talk
to the merchant and never write to the store.

## When to use the team

Run `python3 scripts/nitaaq_cli.py route "<merchant message>" --map .nitaaq/capabilities.json [--exports orders] [--public] [--external analytics,google_ads]`.

| `kind` | What you do |
|---|---|
| `read` (simple_read) | Answer directly through the [Salla operator](salla-operator.md). No specialists, no run state. |
| `analysis` | Operator reads evidence, then run the listed specialists, review, one report. |
| `recommendation` | Same as analysis; proposals go in «المقترحات» and still need approval. |
| `execution` | No specialists. Follow [execution-protocol.md](execution-protocol.md) through the operator. |

Read each stage's `status`:

- `run`: the specialist is active and its required data exists.
- `blocked`: active but its data is missing. Say what is missing in one line.
- `not_available_yet`: the specialist is planned for a later phase. Say so honestly. Never imitate it.
- `existing_service`: use the existing reference it points to. No current specialist uses it; it stays for services not yet wrapped as a stage.

Simple questions get one specialist at most. Broad questions get up to the
`limits.max_specialists_broad` in `assets/agent-registry.json`. Specialists
never start other specialists (delegation depth 1).

## Flow (sequential, works in every host)

1. **Plan.** `route`, then `state new --store-id <id> --question "<q>"` and one `state stage` per stage.
2. **Evidence.** The operator fetches the reads the plan lists (`operator_reads`), paginating to the end, and saves them: `evidence make --store-id <id> --records orders.json --op orders.list --total <reported> --pages <n> --period <from>,<to>`.
3. **Specialists.** For each `run` stage, move it to `running`, read its reference, produce findings that cite evidence ids, then move it to `done` (or `failed` with an Arabic reason).
4. **Follow-ups.** Turn concrete signals into at most one extra round: `followups --signals stock_out,...`. Only a signal backed by evidence counts.
5. **Review.** `review --store-id <id> --findings findings.json`. Findings with status `fail` are dropped from facts; `revise` must be fixed or shown with the issue.
6. **Report.** One Arabic answer with the unified sections (`report ...` renders them): الخلاصة، الحقائق، الأسباب المحتملة، ما لا نعرفه، المقترحات، طريقة التحليل.

For "why did sales drop" the deterministic part is one command:
`sales-change --store-id <id> --orders orders.json --total <reported> --md`.

Active specialists and their commands:

| Specialist | Needs | Command |
|---|---|---|
| [Store analytics](agents/store-analytics.md) | orders | `sales-change`, `metrics` |
| [Pricing](agents/pricing.md) | products (orders optional) | `pricing`, `pricing-breakeven` |
| [Growth](agents/growth.md) | orders (traffic for the funnel) | `growth mix`, `growth cohorts`, `growth funnel`, `growth sample-size` |
| [Conversion](agents/cro.md) | public product pages (carts optional) | `cro` |
| [SEO/GEO](agents/seo-geo.md) | public pages (Search Console optional) | `seo-team`, `cannibalization` (plus the existing SEO/GEO commands) |
| [Customer intelligence](agents/customer-intelligence.md) | reviews or complaints | `reviews` |
| [Tracking](agents/tracking.md) | public pages for levels 1–2; orders plus a GA4 or ads export for reconciliation | `tracking`, `reconcile` |
| [Ads auditor](agents/paid-media-auditor.md) | any ads export or authorized ads tool | `ads audit` |
| [Google Ads](agents/ppc.md) | Google Ads export or tool | `ads structure` |
| [Search terms](agents/search-query-analyst.md) | search terms report | `ads search-terms` |
| [Ad creative](agents/ad-creative.md) | products or public pages (ads data for fatigue) | `ads-copy`, `ads fatigue` |
| [Paid social](agents/paid-social.md) | Meta, TikTok or Snapchat export or tool | `ads social` |

Signals from one stage start a follow-up: `price_changed`/`discount_heavy` go to pricing,
`funnel_drop`/`abandoned_carts_up` to conversion, `traffic_available` to growth,
`stock_out` to the operator, `complaints_up` to customer intelligence, `tracking_discrepancy` to tracking, `ads_available` to the ads auditor, `search_terms_available` to the search terms analyst.

Ads questions run the specialist the message is about (search terms, copy, social, Google) or the auditor. Ads changes (pause, negatives, budgets) are never executed: the route's `execution_path` is `ads_proposal_only`, and the merchant applies an approved proposal in the platform.

## Native subagents (optional)

When the host offers subagents and the generated `nitaaq-*` agents are
installed, you may run a specialist or the reviewer as a separate agent.
Give it the task envelope (`assets/schemas/task-envelope.json`) and the
evidence file paths. It has no store tools (`mcp__*` is disallowed) and no
write tools. Record the mode with `state review-mode --mode independent_context`
only when the reviewer really ran in its own context; otherwise the report
says the review was a self-check. If anything fails, fall back to the
sequential flow; never block the merchant on it.

## Rules

- Per store: every path, evidence id, approval and run is scoped by `store_id`. Never mix stores in one analysis.
- Numbers come from helpers or trusted report tools, never from model arithmetic.
- A cause is a possibility unless mechanism evidence exists; list alternatives and confidence.
- Missing data is stated in «ما لا نعرفه»; never fill it with benchmarks or guesses.
- Cancel: `state cancel` stops new stages; finished outputs stay. Resume with `state resume`.
