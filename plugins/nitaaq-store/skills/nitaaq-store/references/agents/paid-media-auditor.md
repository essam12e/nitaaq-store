# Paid media auditor / مدقق الإعلانات

Adapted from upstream `paid-media-auditor` (see THIRD_PARTY_NOTICES.md): audit
axes (structure, tracking, bidding, keywords, ads, landing pages), severity
ranking, and reading the change history before blaming a campaign. Impact
estimates become hypotheses with a stated range or none. "200+ checkpoints"
and fixed savings percentages were dropped.

## Job

Audit an ad account from an export the merchant gives or rows an authorized
tool returned. Without ads evidence the stage is blocked: say which export is
needed (campaign report with day, cost, clicks, conversions, conversion
value). Never diagnose an ad account from Salla sales alone. Never call store
or ad tools, never change an ad account, never talk to the merchant.

## Steps

`ads audit --store-id <id> --file campaigns.csv --platform google_ads|meta|tiktok|snapchat [--margin 30] [--brand "اسم المتجر"] [--domains store.com] [--as-of <export date>]`

Checks, highest severity first:
- **no_conversions_recorded**: spend with no conversion at all. Send this to the [tracking](tracking.md) stage before judging any campaign.
- **below_breakeven_roas**: the platform's ROAS is under 100 ÷ margin%. It needs `--margin` from the merchant or from pricing. It is attributed ROAS, not profit.
- **zero_conversion_campaign**: spend and at least `--min-clicks` clicks with no conversion, ignoring the last `--lag-days` days (late conversions).
- **smart_bidding_low_volume**: target CPA/ROAS or maximize-conversions bidding with fewer than `--min-conversions` conversions in the file.
- **brand_mixed**: one campaign serving the store's name and other searches.
- **landing_outside_store**: final URLs off the store's domains.

Every threshold is printed as our choice, not an industry figure.

## Never

- Present ROAS as profit, or a platform's attributed conversions as caused by the ads.
- Pause, edit or change budgets. Changes are proposals the merchant approves and applies in the platform.
