"""Write gate for Claude Code: no store write without a matching approval.

The plugin ships a PreToolUse hook (hooks/write_gate.py) that calls
`decide()` before every MCP tool call. This module is the logic; the hook
script only reads stdin and prints the decision.

How a write gets through:

1. The merchant approves exact items (`approvals grant`).
2. Right before the call, the Salla operator arms the gate for that exact
   tool call: `gate arm --id ap_... --tool <tool> --input call.json ...`.
   Arming re-runs `approvals check` (same store, operation, values, fresh
   snapshot) and checks that the call's input carries each entity id and
   each approved value. It records a short-lived token bound to the tool
   name and a hash of the exact input.
3. The hook lets the call through only if an armed token matches the tool
   and the input exactly, and consumes it. Anything else is denied with an
   Arabic reason.

Known Salla merchant connector tools (names observed on a live merchant
connector on 2026-10-05) are classified from a fixed table first, whatever
the server is called (claude.ai names connector servers by id). Salla's own
two-step writes stage a change with `*_propose` (nothing reaches the store)
and commit it with `*_apply`; only the apply step is gated, and it is armed
with `--covers` pointing at the propose input that carries the values.

Other tools are classified with the capability map (.nitaaq/capabilities.json):
  outside  not a Salla merchant tool (other servers, Salla Partners) -> not gated
  read     mapped to read operations, or read-only by annotation/name -> passes
  write    mapped to a write or destructive operation -> needs an armed token
  unknown  a Salla tool the map cannot classify -> needs an armed token;
           the reason asks to rebuild the map

Calls from native subagents (the hook input carries agent_id) to Salla tools
are denied outright: specialists read saved evidence only.

The gate never auto-approves: a call it passes still goes through the
host's normal permission prompt. It can be switched off only from the
environment Claude Code starts with (NITAAQ_WRITE_GATE=off), not from a file
the agent could write.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .approvals import ApprovalStore, canonical_hash
from .capabilities import READ_VERBS, WRITE_VERBS, ACTION_PARAM_NAMES, PARTNERS_TOOL_NAMES, split_name
from .locks import locked_json, read_json
from .writes import same_value

ARM_TTL_MINUTES = 10
GATE_FILE = Path("gate") / "armed.json"
LOCK_TIMEOUT = 5.0

REASONS_AR = {
    "approval_failed": "الموافقة ما تغطي هذا التنفيذ.",
    "entity_not_in_input": "مدخلات الأداة ما فيها رقم العنصر المعتمد.",
    "value_not_in_input": "مدخلات الأداة ما فيها القيمة المعتمدة.",
    "already_armed": "فيه تنفيذ مجهّز لنفس العنصر بنفس الموافقة وما استُخدم بعد.",
    "subagent": "الوكلاء المتخصصون يقرؤون الأدلة المحفوظة فقط؛ أدوات المتجر للمحادثة الرئيسية.",
    "not_armed": "ما فيه موافقة مجهّزة لهذا الاستدعاء بالضبط.",
    "unknown_tool": "أداة سلة غير مصنفة في خريطة القدرات.",
}


# Salla merchant connector tools, by what they do to the store.
SALLA_WRITE_TOOLS = frozenset("""
homepage_component_delete homepage_component_edit inventory_update landing_page_component_delete
landing_page_component_edit landing_page_manage landing_page_remove menu_items_edit menu_manage menu_remove
orders_history_add orders_status_update product_image_add products_create products_options_apply
products_update_apply products_variants_update_apply store_branding_update theme_rating_submit
theme_settings_update theme_version_manage theme_version_remove
""".split())
SALLA_STAGE_TOOLS = frozenset("products_options_propose products_update_propose products_variants_update_propose".split())
SALLA_READ_TOOLS = frozenset("""
abandoned_carts_get abandoned_carts_list categories_list customers_get customers_list homepage_component_get
homepage_components_list inventory_list landing_page_component_get landing_page_components_list
landing_page_settings_get landing_pages_list languages_list menu_list order_history_get orders_get
orders_invoices_get orders_invoices_list orders_list orders_statuses_list product_image_get products_get
products_list products_list_with_images products_sku_get products_trashed_list products_variants_list
reviews_list shipments_list store_branding_get store_context_get store_dashboard_card theme_get
theme_settings_list theme_versions_list themes_list
""".split())


def _known_kind(short: str) -> str | None:
    if short in SALLA_WRITE_TOOLS:
        return "write"
    if short in SALLA_STAGE_TOOLS:
        return "stage"
    if short in SALLA_READ_TOOLS or short.startswith("reports_"):
        return "read"
    return None


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def gate_path(root: str | Path) -> Path:
    return Path(root) / GATE_FILE


def _server(tool_name: str) -> str:
    parts = tool_name.split("__")
    return parts[1] if tool_name.startswith("mcp__") and len(parts) >= 3 else ""


def _short(tool_name: str) -> str:
    return tool_name.split("__")[-1] if tool_name.startswith("mcp__") else tool_name


def _action_value(tool_input: dict) -> str | None:
    for k, v in (tool_input or {}).items():
        if str(k).lower() in ACTION_PARAM_NAMES and isinstance(v, str):
            return v
    return None


def salla_servers(cmap: dict | None) -> set:
    out = set()
    for op in ((cmap or {}).get("operations") or {}).values():
        for c in op.get("candidates") or []:
            if c.get("server"):
                out.add(c["server"])
    return out


def classify(tool_name: str, tool_input: dict | None, cmap: dict | None, tools: list[dict] | None = None) -> dict:
    """Where a tool call stands for the gate. See the module docstring."""
    tool_input = tool_input or {}
    server = _server(tool_name)
    if not tool_name.startswith("mcp__"):
        return {"scope": "outside", "kind": "outside", "ops": []}
    if _short(tool_name).lower() in PARTNERS_TOOL_NAMES:
        return {"scope": "outside", "kind": "outside", "ops": [], "note": "salla_partners"}
    known = _known_kind(_short(tool_name))
    if known:
        return {"scope": "salla", "kind": known, "ops": [], "note": "known_salla_tool"}
    action = _action_value(tool_input)
    reads, writes = [], []
    for op_id, op in ((cmap or {}).get("operations") or {}).items():
        for c in op.get("candidates") or []:
            if c.get("tool") != tool_name:
                continue
            if c.get("via_action") and (action or "").lower() != str(c["via_action"]).lower():
                continue
            (reads if op.get("access") == "read" else writes).append(op_id)
    in_scope = bool(reads or writes) or server in salla_servers(cmap) or "salla" in server.lower()
    if not in_scope:
        return {"scope": "outside", "kind": "outside", "ops": []}
    if writes and reads and action:
        words = set(split_name(action))
        if words & READ_VERBS and not words & WRITE_VERBS:
            writes = []  # one tool for several operations; this call's action only reads
    if writes:
        return {"scope": "salla", "kind": "write", "ops": sorted(set(writes))}
    if reads:
        return {"scope": "salla", "kind": "read", "ops": sorted(set(reads))}
    # Not in the map: trust an explicit read-only annotation, or a name (and
    # action value) that only carries read verbs. Anything else is unknown.
    for t in tools or []:
        if t.get("name") == tool_name and (t.get("annotations") or {}).get("readOnlyHint") is True:
            return {"scope": "salla", "kind": "read", "ops": [], "note": "readOnlyHint"}
    words = set(split_name(tool_name)) | (set(split_name(action)) if action else set())
    if words & READ_VERBS and not words & WRITE_VERBS:
        return {"scope": "salla", "kind": "read", "ops": [], "note": "read_verbs"}
    return {"scope": "salla", "kind": "unknown", "ops": []}


def _scalars(obj) -> list:
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _scalars(v)]
    if isinstance(obj, (list, tuple)):
        return [x for v in obj for x in _scalars(v)]
    return [obj]


def input_covers(tool_input: dict, items: list[dict]) -> list[dict]:
    """Problems if the call's input does not carry each entity id and approved value."""
    leaves = _scalars(tool_input or {})
    problems = []
    for it in items:
        eid = str(it["entity_id"])
        if not any(str(x) == eid for x in leaves if x is not None and not isinstance(x, bool)):
            problems.append({"entity_id": eid, "reason": "entity_not_in_input"})
        for v in _scalars(it.get("after") or {}):
            if not any(same_value(x, v) for x in leaves):
                problems.append({"entity_id": eid, "reason": "value_not_in_input", "value": v})
    return problems


def _expire(data: dict, t: datetime) -> None:
    for rec in data.values():
        if rec.get("status") == "armed" and t >= datetime.fromisoformat(rec["expires_at"]):
            rec["status"] = "expired"


def arm(root: str | Path, store_id: str, approval_id: str, operation: str, items: list[dict], fresh: dict | None,
        tool_name: str, tool_input: dict, *, account_id: str | None = None, ttl_minutes: float = ARM_TTL_MINUTES,
        covers: dict | None = None, now: datetime | None = None) -> dict:
    """Bind one exact tool call to a valid approval. Returns {ok, reasons, reasons_ar, token?}.

    covers: for a commit step whose input carries only ids and a token
    (products_update_apply), the staged input that carries the values
    (the products_update_propose input). Values are checked there; each
    entity id must still appear in the call itself.
    """
    t = _now(now)
    chk = ApprovalStore.for_store(root, store_id).check(approval_id, store_id, operation, items, fresh,
                                                        account_id=account_id, now=t)
    reasons = [] if chk["ok"] else ["approval_failed"]
    problems = input_covers(covers if covers is not None else tool_input, items)
    if covers is not None:
        problems += [p for p in input_covers(tool_input, [{"entity_id": i["entity_id"]} for i in items])]
    reasons += list(dict.fromkeys(p["reason"] for p in problems))
    entities = sorted(str(i["entity_id"]) for i in items)
    if reasons:
        return {"ok": False, "reasons": reasons, "reasons_ar": [REASONS_AR[r] for r in reasons],
                "approval": chk, "input_problems": problems}
    with locked_json(gate_path(root), timeout=LOCK_TIMEOUT) as data:
        _expire(data, t)
        for rec in data.values():
            if rec["status"] == "armed" and rec["approval_id"] == approval_id and set(rec["entities"]) & set(entities):
                return {"ok": False, "reasons": ["already_armed"], "reasons_ar": [REASONS_AR["already_armed"]],
                        "token": rec["token"]}
        rec = {"token": "gt_" + secrets.token_hex(6), "tool": tool_name, "input_hash": canonical_hash(tool_input),
               "store_id": str(store_id), "approval_id": approval_id, "operation": operation, "entities": entities,
               "armed_at": t.isoformat(timespec="seconds"),
               "expires_at": (t + timedelta(minutes=ttl_minutes)).isoformat(timespec="seconds"), "status": "armed"}
        data[rec["token"]] = rec
    return {"ok": True, "reasons": [], "reasons_ar": [], "token": rec["token"], "expires_at": rec["expires_at"]}


def disarm(root: str | Path, token: str) -> bool:
    with locked_json(gate_path(root), timeout=LOCK_TIMEOUT) as data:
        rec = data.get(token)
        if not rec or rec["status"] != "armed":
            return False
        rec["status"] = "disarmed"
        return True


def consume(root: str | Path, tool_name: str, tool_input: dict, now: datetime | None = None) -> dict | None:
    """Use the armed token matching this exact call, if any."""
    path = gate_path(root)
    if not path.exists():
        return None
    t = _now(now)
    h = canonical_hash(tool_input or {})
    with locked_json(path, timeout=LOCK_TIMEOUT) as data:
        _expire(data, t)
        for rec in sorted(data.values(), key=lambda r: r["armed_at"]):
            if rec["status"] == "armed" and rec["tool"] == tool_name and rec["input_hash"] == h:
                rec["status"] = "consumed"
                rec["consumed_at"] = t.isoformat(timespec="seconds")
                return dict(rec)
    return None


def candidate_roots(cwd: str | None, env: dict | None = None, plugin_root: str | None = None) -> list[Path]:
    """Where .nitaaq state may live: NITAAQ_ROOT, the session directory, the skill directory."""
    env = os.environ if env is None else env
    roots = []
    if env.get("NITAAQ_ROOT"):
        roots.append(Path(env["NITAAQ_ROOT"]))
    if cwd:
        roots.append(Path(cwd) / ".nitaaq")
    if plugin_root:
        roots.append(Path(plugin_root) / "skills" / "nitaaq-store" / ".nitaaq")
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _first(roots: list[Path], name: str):
    for r in roots:
        p = r / name
        if p.exists():
            data = read_json(p)
            if data:
                return data
    return None


def decide(event: dict, roots: list[Path], env: dict | None = None, now: datetime | None = None) -> dict:
    """{"decision": "pass"|"deny", "reason": str, "class": {...}} for one PreToolUse event."""
    env = os.environ if env is None else env
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    if str(env.get("NITAAQ_WRITE_GATE", "")).lower() in ("off", "0", "false"):
        return {"decision": "pass", "reason": "gate_off", "class": {}}
    cmap = _first(roots, "capabilities.json")
    tools = _first(roots, "tools.json")
    if isinstance(tools, dict):
        tools = tools.get("tools", [])
    cls = classify(tool_name, tool_input, cmap, tools)
    if cls["scope"] == "outside":
        return {"decision": "pass", "reason": "outside", "class": cls}
    if event.get("agent_id"):
        return {"decision": "deny", "reason": _deny_text("subagent", tool_name, cls), "class": cls}
    if cls["kind"] in ("read", "stage"):
        return {"decision": "pass", "reason": cls["kind"], "class": cls}
    for r in roots:
        rec = consume(r, tool_name, tool_input, now=now)
        if rec:
            return {"decision": "pass", "reason": "armed", "class": cls, "token": rec["token"],
                    "approval_id": rec["approval_id"]}
    return {"decision": "deny", "reason": _deny_text("unknown_tool" if cls["kind"] == "unknown" else "not_armed",
                                                     tool_name, cls), "class": cls}


def _deny_text(code: str, tool_name: str, cls: dict) -> str:
    head = f"بوابة نطاق منعت {tool_name}: {REASONS_AR[code]}"
    if code == "subagent":
        return head
    if tool_name.endswith("_apply"):
        how_extra = " For a *_apply step add --covers propose.json (the propose call's input)."
    else:
        how_extra = ""
    how = ("Record the merchant's approval (approvals grant), then arm this exact call right before it: "
           "python3 scripts/nitaaq_cli.py gate arm --store-id <id> --id <ap_...> --op <operation> "
           "--items items.json --fresh fresh.json --tool " + tool_name + " --input call.json." + how_extra)
    if code == "unknown_tool":
        how = ("Rebuild the capability map (capabilities build) and confirm what this tool does. "
               "If it writes, it needs an approval and an armed call like any write. " + how)
    return head + "\n" + how


def hook_main(stdin_text: str, env: dict | None = None, plugin_root: str | None = None) -> str:
    """Run one PreToolUse event. Returns what the hook prints (empty means no objection)."""
    env = os.environ if env is None else env
    try:
        event = json.loads(stdin_text or "{}")
    except ValueError:
        return ""
    tool_name = str(event.get("tool_name") or "")
    try:
        res = decide(event, candidate_roots(event.get("cwd"), env, plugin_root), env)
    except Exception as e:  # fail closed for Salla tools only
        if "salla" in _server(tool_name).lower() and _short(tool_name).lower() not in PARTNERS_TOOL_NAMES:
            res = {"decision": "deny", "reason": f"بوابة نطاق ما قدرت تتحقق ({type(e).__name__}); منعت الاستدعاء احتياطاً."}
        else:
            return ""
    if res["decision"] != "deny":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": res["reason"]}}, ensure_ascii=False)
