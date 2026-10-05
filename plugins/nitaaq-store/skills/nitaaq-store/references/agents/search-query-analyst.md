# Search terms analyst / محلل مصطلحات البحث

Adapted from upstream `paid-media-search-query-analyst` (see
THIRD_PARTY_NOTICES.md): n-gram analysis, intent classes, negative keyword
tiers, keyword/negative conflict checks, and brand leakage. Zero-conversion
flags need a click minimum and must allow for late conversions. Pushing
negatives and fixed "10–20% waste" figures were dropped.

## Job

Read a search terms report and propose negative keywords the merchant can
check. Without a search terms report say «مصطلحات البحث غير متاحة» and stop;
never guess search terms. Never call store or ad tools, never change the account.

## Steps

`ads search-terms --store-id <id> --file search_terms.csv --brand "نطاق,nitaaq" --keywords "عطر عود,دهن عود" [--negatives "..."] [--min-clicks 10] --as-of <export date> [--lag-days 7]`

- Arabic is normalized (alef, ya, ta marbuta, the article) before grouping, so spelling variants group together. Negatives are proposed in the searcher's own wording.
- Intent classes: brand, competitor, jobs, free/download, informational, commercial.
- Candidates are terms (exact) or shared words (phrase, in two or more terms) with at least `--min-clicks` clicks and no conversion, ignoring the last `--lag-days` days.
- Candidates are held back, with the reason, when they contain the store's name, would block one of your keywords, would block a term that converts, or are already negative.
- Brand leakage: the store's name served by campaigns that are not the brand campaign.
- The output includes a proposal (`ads.add_negative_keywords`) that needs approval. The merchant applies it in Google Ads; there is no ads write tool.

The spend on candidate terms is what they cost in the period, not a promised saving.
