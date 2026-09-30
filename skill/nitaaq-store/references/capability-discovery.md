# Capability discovery / اكتشاف القدرات

## 1. Dump the tools you can see

Write every tool visible in this session (not only ones you think are
relevant) to `.nitaaq/tools.json`:

```json
{"tools": [
  {"name": "<exact tool name>", "description": "<exact description>",
   "inputSchema": {<exact JSON schema>}, "annotations": {<if shown>}}
]}
```

Copy names, descriptions and schemas exactly as the host shows them. If the
host only shows names, include them; mapping will stay at `discovered`.

## 2. Build the map

```
python3 scripts/nitaaq_cli.py capabilities build --tools .nitaaq/tools.json --out .nitaaq/capabilities.json --md
```

The helper maps tools onto the stable operations in
`assets/operation-catalog.json` (e.g. `products.update`, `inventory.adjust`,
`orders.update_status`). It never creates a tool that is not in your list.

Per operation it records: status (`mapped` / `ambiguous` / `unavailable`),
candidate tools with score, whether access is read/write/destructive,
required params, pagination params, store-targeting params, MCP annotations,
and evidence level.

Connection kinds:

| kind | meaning |
|---|---|
| `merchant` | merchant-operating tools found |
| `mixed` | merchant + Salla Partners tools; partners tools are excluded from merchant operations |
| `partners_only` | Salla Partners MCP only: no access to products, orders, inventory or reports |
| `no_merchant_tools` | tools exist but none operate a store |
| `none` | no MCP tools |

## 3. Confirm before you rely on a mapping

For each operation you are about to use, answer from the tool's own
description and schema:

1. Does the tool do this operation (not something adjacent)?
2. Read or write? Does it replace the whole object or patch fields?
3. Which parameters, which are required, which enums?
4. Which store/account does it target? (store params, or the connection's authorized store). Confirm the store name with a read (`store.info`) before writes when possible.
5. Pagination: how do you get all pages? How many exist? Record fetched vs total.
6. Does it support the specific field or action (e.g. branch quantity, sale end date)?
7. Is it idempotent? (annotation `idempotentHint`, or semantics like "set" vs "increment").

If two tools are `ambiguous`, read both descriptions and choose, or ask the
merchant only if the choice changes the outcome.

Inventory may be readable from product details even if no dedicated stock
tool exists; check the product schema/response before declaring it unavailable.

## 4. Evidence levels

| Level | Set by | Meaning |
|---|---|---|
| discovered | helper | name/description matches |
| schema_validated | helper | schema contains the fields the operation needs |
| tested | you, with a note | exercised in a controlled environment (test store, dry run the tool supports) |
| live_verified | you, with store id + readback note | a real authorized write was read back on the merchant's store |

```
python3 scripts/nitaaq_cli.py capabilities evidence --map .nitaaq/capabilities.json \
  --op products.update --level live_verified --store-id <id> \
  --note "updated metadata_title on product 123, readback matched"
```

The helper refuses `live_verified` without a store id or with a note that
mentions mocks. Unit tests in this repository use synthetic fixtures and never
count as live verification.

## 5. Errors and partial access

Use `classify-error "<error text>" [--status N] [--write] [--idempotent]`:

| kind | what to do |
|---|---|
| auth_expired | stop; ask the merchant to reconnect; keep the plan |
| permission_denied | stop that operation; explain the missing permission; offer dashboard steps |
| rate_limited | wait, then continue from the last confirmed item |
| network_ambiguous on a write | reconcile by reading before any retry |
| partial | report what arrived; never extrapolate |
| unavailable_feature | say the feature is not active/available through this connection |

## 6. Re-discovery

Rebuild the map when the merchant connects/reconnects something, or after
`auth_expired`. `capabilities diff old.json new.json` lists added, removed
and changed operations.
