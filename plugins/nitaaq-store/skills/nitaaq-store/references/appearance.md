# Store appearance and merchandising / مظهر المتجر والعرض

Operations: `theme.list`, `theme.settings.get`, `theme.settings.update`,
`theme.clone`, `theme.preview`, `theme.publish`, `home.sections.list`,
`home.sections.types`, `home.sections.create/update/reorder/delete`,
`pages.list/create/update/publish/delete`, `menus.list/update`,
`assets.upload`, `branding.update`.

Everything here configures the **existing** theme. Cloning an existing theme
configuration for preview is allowed; developing a new theme is not.

## Always first
1. Identify the active theme and which configuration is live (`theme.list`).
2. Read current sections/settings and, when available, the catalog of section types and their editable field schemas (`home.sections.types`). Only add section types the active theme supports; only write fields the schema exposes.
3. Preserve everything not selected: other sections, other items in a slider/collection, other language values, hidden sections.

## Homepage sections
- Add: choose a supported type, fill required fields, link targets (product, category, brand, page, article, external URL) by resolving ids first.
- Edit text in Arabic and English fields separately; do not overwrite one language with the other.
- Colors, toggles, numbers: validate against allowed ranges/enums.
- Rename, reorder (show old → new order), show/hide.
- Delete: only with explicit authorization, after saying: «الحذف نهائي ولا يمكن استرجاع إعدادات القسم إلا يدوياً.» Offer hiding instead. Save the section's settings to a snapshot first.

## Banners
- Concept: message, offer, CTA, destination, dimensions from the section schema/theme recommendation.
- Generation only with an image tool; Arabic text in generated images must be checked letter by letter (RTL, joined letters). If text renders poorly, generate without text and use the section's text fields instead.
- Upload → set in section → verify on preview/public page.

## Landing pages
- Create/edit/reorder/hide/duplicate sections as supported.
- Provide the actual preview link returned by the tool; never construct a URL.
- Publish or schedule: confirm the timezone the tool uses (store time Asia/Riyadh unless the tool says otherwise); show the scheduled time in Riyadh time.
- Deactivate/delete within authorized scope; deletion is irreversible.

## Menus
Read the full menu tree, change only targeted items, keep order and nesting,
resolve link targets to real ids/URLs, verify by reading back.

## Logo and brand colors
- Upload an existing logo as provided. Proposing a generated logo only when asked; present as a concept.
- Some branding fields may require dashboard verification or OTP; hand off honestly (mode C or dashboard steps).

## Theme settings, cloning, publishing
- Read header/footer/product-page settings; patch only chosen settings.
- For larger changes: clone the current configuration (if supported), apply changes to the clone, give the preview link, publish only after approval. Publishing is customer-visible.

## Verify
Read back the configuration; open the preview or public page when the host
can fetch it; mention CDN/cache delays if the public page lags.

## Without tools
Prepare exact settings/text/images and step-by-step dashboard instructions
(«المظهر ← تخصيص القالب ← الصفحة الرئيسية ← إضافة عنصر»), labelled as a proposal.
Dashboard menu names can change; tell the merchant to follow the closest label.
