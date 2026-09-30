# Activation / التفعيل

How a skill is picked up is decided by each host, not by this file:

| Host | Explicit | Implicit |
|---|---|---|
| Claude Code (skill folder) | `/nitaaq-store` (skill directory name) | Claude reads the `description` and loads the skill when relevant |
| Claude Code (plugin from the `nitaaq` marketplace) | `/nitaaq-store:nitaaq-store` (plugin skills are namespaced) | same |
| Codex | `$nitaaq-store`, or pick it from `/skills` | Codex matches the description unless `allow_implicit_invocation: false` in `agents/openai.yaml` (this package leaves it `true`) |
| claude.ai (uploaded skill) | No slash command guaranteed; mention the skill by name | Claude reads the description when Skills are enabled |

Never tell the merchant that `/nitaaq-store` works everywhere.

## Phrases

Explicit (always activate): «نطاق للمتاجر», «نطاق المتاجر», «استخدم مهارة نطاق»,
«استخدم مهارة استور», "Nitaaq Store", "nitaaq-store".

Contextual: «استور» / «ستور» / «إستور» activates when
- the message addresses it («استور، كم طلب جاني اليوم؟»), or
- it appears with store-operations words (سلة، متجري، منتج، طلبات، مخزون، تقرير، بنر، ثيم، سيو …), or
- the conversation is already using Nitaaq Store.

Do not activate for: App Store / «آب ستور», Play Store, «ستور روم», storage,
restore, stored procedures, or English "store" in general text.

When the helper returns `ambiguous`, look at the conversation. If still
unclear and the request would change the store, ask one short question:
«تقصد متجرك في سلة؟».

Helper: `python3 scripts/nitaaq_cli.py activation "<message>" [--context-active]`.
