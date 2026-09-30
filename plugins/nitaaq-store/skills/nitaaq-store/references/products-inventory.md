# Products and inventory / المنتجات والمخزون

Operations (catalog ids): `products.list`, `products.get`, `products.create`,
`products.update`, `products.set_status`, `products.delete`,
`products.trash_list`, `products.variants`, `products.best_sellers`,
`products.images.attach`, `categories.list`, `brands.list`, `tags.list`,
`inventory.read`, `inventory.set`, `inventory.adjust`, `inventory.branches`.
Use only those mapped in the capability map; field names come from the tool schema.

Reference field names from Salla's Merchant API "Create Product"
(docs.salla.dev, checked 2026-09-30) — useful for understanding, not proof
that a connector exposes them: `name`, `price`, `product_type`
(`product`, `service`, `group_products`, `codes`, `digital`, `food`,
`donating`), `quantity`, `status` (`sale`, `out`, `hidden`), `description`,
`categories`, `sale_price`, `cost_price`, `sale_end`, `weight`,
`weight_type`, `sku`, `mpn`, `gtin`, `brand_id`, `tags`, `images`,
`options`, `metadata_title`, `metadata_description`, `subtitle`,
`promotion_title`, `require_shipping`, `hide_quantity`.

## Identifying a product (no SKU/GTIN required)

SKU and GTIN are **never** required to find, research or improve a product.
Match by:
- product name (Arabic normalization handles أ/ا, ة/ه, ى/ي, diacritics, Arabic digits),
- product images (describe what you see: shape, color, material, printed text, logo),
- brand and model when present,
- existing description,
- size, material, intended use and other visible attributes.

Helper: `match --ref ref.json --catalog candidates.json`. `ref.json` fields:
`name, brand, model, description, attributes{}, visual[]`, optional `sku, gtin`.
Decisions: `match` / `confirm_with_merchant` / `no_match`. Identifiers only
add or subtract corroboration.

Never infer technical specifications (capacity, wattage, fabric %, compatibility)
from a photo. If an attribute is uncertain, ask before saving:
«من الصورة يبدو أن الخامة جلد، تؤكد؟».

## Creating products

1. Collect: name, product type, regular price, optional sale price, cost, description, quantity, weight, brand, tags, categories (existing ones — look them up by name), visibility, SEO title/description, images.
2. `price-check --regular R --sale S --cost C`. Sale price must be below the regular price. Phrases like «كان 200 وصار 150» → `price-check --phrase "..."`.
3. Descriptions: original Arabic, grounded only in actual attributes the merchant gave or that are visible. No invented claims (مضمون، أصلي 100%، طبي) unless the merchant supplies evidence. Structure: opening benefit line, key attributes list, usage/care, what's in the box (only if known).
4. SEO title ≈ 50–60 chars (product + key attribute + store name if room), SEO description ≈ 120–155 chars, unique per product.
5. Images: attach existing images as-is (see images-assets.md); do not regenerate them unless asked.
6. Duplicate prevention: search by normalized name first; if a match exists ask whether to update it instead.
7. Execute via ledger (`products.create` is not idempotent). Verify by reading the created product.

### Bulk creation
Show a review table (name, type, price, sale price, quantity, category, status)
and get one approval for the table. Create one by one with the ledger;
report each row with `BatchReport`. A failure in one row doesn't stop others
unless it's auth/permission.

## Updating products
- Read now, patch only requested fields, show differences for sensitive fields, re-read before write, verify after.
- Preserve omitted fields. Never send an empty description/images array unless the merchant asked to clear it.

## Publishing, hiding, availability
Use the status semantics the tool exposes (Salla documents `sale` / `out` / `hidden`).
«أخفِ المنتج» → hidden. «خلّه نفد» → out. Don't use quantity 0 to hide.

## Browsing and search
- Show products with image thumbnails when the host renders images; else a table with image links.
- Search by name/category/status/SKU when the tool supports those filters; otherwise fetch pages and filter locally, stating how many pages were scanned.
- Best sellers: state metric (quantity sold vs revenue) and period; use `products.best_sellers` or compute from orders with `reports.top_products`.

## Variants
Read options/variants and their combinations before any stock or price change.
Resolve the exact variant («المقاس L اللون أسود»). If the connector cannot
address variants, say so and give dashboard steps.

## Stock
- Modes: set (overwrite), increment, decrement: `stock-plan --mode ... --current N --value M`.
- «زود 5» = increment; «خلّها 20» = set; «انقص 3» = decrement. Ambiguous «حط 5» → ask once which.
- Read current quantity immediately before writing. For increment/decrement, prefer a native increment tool; if only "set" exists, compute from the fresh read and write immediately, then read back. If the readback is lower because orders arrived, `reconcile_stock_readback` explains it.
- Branch-level stock only when the tool supports branches; resolve the branch by name.
- Restock: after writing, check sellability (`sellability()`: status, quantity, variant availability) and say whether customers can buy now.
- Never claim a full inventory audit when you read only some pages; state pages fetched vs total.

## Trash
List trashed products only if a tool exists. Restoration/deletion needs explicit approval; permanent deletion is irreversible.
