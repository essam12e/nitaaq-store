# Product images and assets / الصور والملفات

Four separate steps. Report each separately; never merge them into «تم».

1. **Generate / edit** (only if the host has an image tool).
2. **Merchant review** when the image represents a real product.
3. **Upload** through a mapped tool (`assets.upload` / `products.images.attach`) or the dashboard.
4. **Associate and verify**: the image is attached to the right product/page and loads from its permanent URL.

## Editing existing product photos

Preserve exactly: shape, proportions, colors, logos, printed text,
materials, structure. Do not add accessories, props that could be mistaken
for included items, or features the product doesn't have.

Standard cleanup (when requested): clean white background, better lighting
and clarity, centered and aligned, suitable dimensions (square is common for
product grids; follow the theme's recommendation when known).

After generation, inspect the output against the original: silhouette,
colors, text/logo legibility, missing or added parts. If anything changed,
say so and offer to upload the original instead:
«الصورة المعدلة غيّرت شكل الغطاء؛ أقترح نرفع الصورة الأصلية بدلاً منها.»
Never claim perfect identity preservation without inspecting.

## Upload mechanics

Discover what the tool accepts from its schema: public URL, base64,
multipart file, or an existing media id. Do not assume a local path or a
chat attachment is accepted.

- Validate type (jpg/png/webp as the tool allows), size limits, reachability of URLs.
- Temporary links from image generators or chat uploads may expire; never treat them as permanent hosting. Upload to Salla (or a merchant-approved host) and use the resulting URL.
- Uploads that create new media are not idempotent: use the ledger; reconcile by listing the product's images before retrying.

## Verify
Read the product/page again and check the image list contains the new
image; when possible fetch the public page and confirm it renders.

## Without tools
Mode B: deliver the image files or prompts, plus dashboard steps:
«من لوحة التحكم: المنتجات ← [اسم المنتج] ← الصور ← إضافة صورة». State that the image is ready, not uploaded.
