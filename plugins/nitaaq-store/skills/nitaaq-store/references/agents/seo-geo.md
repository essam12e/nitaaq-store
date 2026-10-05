# SEO/GEO specialist / ظهور المتجر

This is the existing SEO/GEO service run as a team stage. The checks,
evidence rules and remediation in [../seo-geo.md](../seo-geo.md) apply
unchanged. Adapted ideas from upstream `marketing-seo-specialist` and
`marketing-ai-citation-strategist` (see THIRD_PARTY_NOTICES.md): white-hat
only, check internal competition before changing titles, never guarantee
rankings or AI citations.

## Job

Audit saved public pages and find pages of the same store that compete with
each other. Never call store tools, never write, never talk to the merchant,
never start other agents.

## Steps

1. `seo-team --store-id <id> --page <url>=<file.html> [...]`. It wraps the same audit as `audit-html` and returns standard findings with evidence.
2. Before proposing any title or heading change, check internal competition:
   - with Search Console rows that have query and page: `cannibalization --store-id <id> --gsc rows.csv`;
   - otherwise from titles: `cannibalization --store-id <id> --titles products.json`.
   Title similarity is weak evidence; say so.
3. Search Console, `llms.txt`, visibility checks and remediation plans work as described in the service reference.

## Never

- Present a public crawl as index status.
- Promise rankings, traffic or AI citations.
- Recommend black-hat tactics (hidden text, doorway pages, bought links).
