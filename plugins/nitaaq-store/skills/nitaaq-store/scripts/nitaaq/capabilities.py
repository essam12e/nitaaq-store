"""Build a capability map from the tools a host actually exposes.

The agent dumps its visible tool list (name, description, input schema,
annotations) to JSON. This module maps those tools onto the stable
operation catalog without inventing any tool, and records how strong the
evidence is for each mapping.

Evidence levels (ordered; each level needs its own proof):
  discovered        a tool's name/description matches the operation
  schema_validated  the tool's input schema has the fields the operation needs
  tested            exercised in a controlled environment (recorded explicitly)
  live_verified     write+readback against a live authorized store (recorded
                    explicitly with a store id and readback note)

build_map() only ever produces the first two. The last two can only be
added by record_evidence() with a note, so mock tests can never make an
operation "live verified".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .arabic import tokens as ar_tokens

CATALOG_PATH = Path(__file__).resolve().parents[2] / "assets" / "operation-catalog.json"

EVIDENCE_LEVELS = ["unavailable", "discovered", "schema_validated", "tested", "live_verified"]

# Tools published by the Salla Partners MCP (Partners Agent Kit). They manage
# partner apps, not a merchant's products/orders. Source:
# https://docs.salla.dev/doc-2228622 (checked 2026-09-30).
PARTNERS_TOOL_NAMES = {
    "salla_apps", "salla_events", "salla_snippets", "salla_embedded_pages",
    "salla_onboarding_steps", "salla_settings", "salla_shipping",
    "salla_scopes", "salla_upload", "salla_reference",
}
PARTNERS_HINTS = ["partner", "partners", "portal", "app store listing", "embedded page", "onboarding step"]

PAGINATION_PARAMS = {"page", "per_page", "perpage", "limit", "offset", "cursor", "next", "page_size", "pagesize", "next_page"}
STORE_PARAMS = {"store_id", "storeid", "merchant_id", "merchantid", "store", "merchant", "shop", "shop_id", "domain", "store_domain"}
ACTION_PARAM_NAMES = {"action", "operation", "method", "command", "op"}

WRITE_VERBS = {"create", "add", "update", "edit", "set", "delete", "remove", "patch", "post", "upload",
               "publish", "hide", "reorder", "clone", "duplicate", "adjust", "increment", "decrement", "change"}
READ_VERBS = {"list", "get", "search", "find", "fetch", "show", "read", "view", "browse", "retrieve", "details"}


def load_catalog(path: Path | None = None) -> dict:
    return json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))


def split_name(name: str) -> list[str]:
    """Split a tool name into lowercase tokens, dropping an mcp__server__ prefix."""
    base = name.split("__")[-1] if name.startswith("mcp__") else name
    base = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)
    parts = [p.lower() for p in re.split(r"[\s_\-\.:/]+", base) if p]
    out = []
    for p in parts:
        out.append(p)
        if len(p) > 3 and p.endswith("ies"):
            out.append(p[:-3] + "y")
        elif len(p) > 3 and p.endswith("s") and not p.endswith("ss"):
            out.append(p[:-1])
    return out


def server_of(tool: dict) -> str:
    if tool.get("server"):
        return str(tool["server"])
    name = tool.get("name", "")
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return parts[1]
    return ""


def schema_of(tool: dict) -> dict:
    for key in ("inputSchema", "input_schema", "parameters", "schema"):
        if isinstance(tool.get(key), dict):
            return tool[key]
    return {}


def _props(schema: dict) -> dict:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _enum_actions(schema: dict) -> list[str]:
    vals: list[str] = []
    for pname, pdef in _props(schema).items():
        if pname.lower() in ACTION_PARAM_NAMES and isinstance(pdef, dict):
            for v in pdef.get("enum") or []:
                vals.append(str(v))
    return vals


@dataclass
class ToolView:
    name: str
    server: str
    description: str
    schema: dict
    annotations: dict
    name_tokens: set = field(default_factory=set)
    desc_tokens: set = field(default_factory=set)
    enum_actions: list = field(default_factory=list)
    enum_tokens: set = field(default_factory=set)

    @classmethod
    def from_raw(cls, raw: dict) -> "ToolView":
        schema = schema_of(raw)
        enum_actions = _enum_actions(schema)
        tv = cls(
            name=raw.get("name", ""),
            server=server_of(raw),
            description=raw.get("description", "") or "",
            schema=schema,
            annotations=raw.get("annotations") or {},
            enum_actions=enum_actions,
        )
        tv.name_tokens = set(split_name(tv.name))
        tv.desc_tokens = set(ar_tokens(tv.description)) | set(split_name(re.sub(r"[^\w\s]", " ", tv.description)))
        for a in enum_actions:
            tv.enum_tokens |= set(split_name(a))
        return tv

    @property
    def short_name(self) -> str:
        return self.name.split("__")[-1] if self.name.startswith("mcp__") else self.name

    def is_partners(self) -> bool:
        if self.short_name.lower() in PARTNERS_TOOL_NAMES:
            return True
        text = (self.description or "").lower()
        return "partner" in self.server.lower() or any(h in text for h in PARTNERS_HINTS[:3])

    @property
    def properties(self) -> dict:
        return _props(self.schema)

    @property
    def required(self) -> list:
        req = self.schema.get("required")
        return list(req) if isinstance(req, list) else []


def _any(keywords, pool) -> list:
    return [k for k in keywords if k in pool]


def match_operation(op: dict, tool: ToolView, catalog: dict) -> dict | None:
    """Score one tool against one operation. None means no match."""
    ents = [catalog["entity_keywords"][e] for e in op["entities"]]
    acts = [k for a in op["actions"] for k in catalog["action_keywords"][a]]
    name_pool = tool.name_tokens | tool.enum_tokens

    # Primary entity may appear in name or description; others must be in the name/actions.
    primary_in_name = _any(ents[0], tool.name_tokens | tool.enum_tokens)
    primary_in_desc = _any(ents[0], tool.desc_tokens)
    if not (primary_in_name or primary_in_desc):
        return None
    if not primary_in_name:
        # Description-only evidence is accepted for generically named tools
        # ("run_query") but not when the name is about another entity
        # ("get_order" mentioning customers is not a customers tool).
        for ename, kws in catalog["entity_keywords"].items():
            if _any(kws, tool.name_tokens):
                return None
    for extra in ents[1:]:
        if not _any(extra, name_pool):
            return None

    via_action = None
    act_in_name = _any(acts, tool.name_tokens)
    if tool.enum_actions:
        for a in tool.enum_actions:
            if _any(acts, set(split_name(a))):
                via_action = a
                break
    act_in_desc = _any(acts, tool.desc_tokens)

    # Reject when the name carries a verb of the opposite kind.
    name_verbs = tool.name_tokens & (WRITE_VERBS | READ_VERBS)
    op_is_read = op["access"] == "read"
    if not act_in_name and not via_action:
        if name_verbs:
            return None  # the name says it does something else
        if not act_in_desc:
            return None
    if op_is_read and (tool.name_tokens & WRITE_VERBS) and not via_action:
        if not (tool.name_tokens & READ_VERBS):
            return None
    if not op_is_read and not via_action and not (tool.name_tokens & WRITE_VERBS) and (tool.name_tokens & READ_VERBS):
        return None

    if op.get("require_any"):
        pool = tool.name_tokens | tool.desc_tokens | tool.enum_tokens
        if not _any(op["require_any"], pool):
            return None

    ann = tool.annotations
    if ann.get("readOnlyHint") is True and not op_is_read:
        return None

    score = 3.0 * bool(primary_in_name) + 1.0 * bool(primary_in_desc)
    score += 2.0 * bool(act_in_name or via_action) + 0.5 * bool(act_in_desc)
    score += 1.0 * (len(ents) - 1)
    props = {p.lower() for p in tool.properties}
    hints = [h for h in op.get("schema_hints", []) if h in props]
    score += 0.5 * len(hints)
    if op.get("require_any"):
        score += 1.0
    # Penalize names that also mention entities this operation is not about
    # (get_order_status_history is a weaker fit for orders.get than get_order).
    for ename, kws in catalog["entity_keywords"].items():
        if ename not in op["entities"] and _any(kws, tool.name_tokens):
            score -= 1.0

    pagination = sorted(props & PAGINATION_PARAMS)
    store_params = sorted(props & STORE_PARAMS)
    has_schema = bool(tool.properties) or tool.schema.get("type") == "object"
    needed = op.get("schema_hints", [])
    schema_ok = has_schema and (not needed or bool(hints))
    level = "schema_validated" if schema_ok else "discovered"

    return {
        "tool": tool.name,
        "server": tool.server,
        "score": round(score, 2),
        "via_action": via_action,
        "access": op["access"],
        "annotations": {k: ann[k] for k in ("readOnlyHint", "destructiveHint", "idempotentHint") if k in ann},
        "required_params": tool.required,
        "properties": sorted(tool.properties),
        "pagination_params": pagination,
        "store_params": store_params,
        "matched_schema_hints": hints,
        "evidence_level": level,
    }


def classify_connection(tools: list[ToolView], ops_found: dict) -> dict:
    partners = [t.name for t in tools if t.is_partners()]
    merchant_domains = {"products", "orders", "inventory", "reports", "catalog"}
    merchant_ops = [k for k, v in ops_found.items() if v["status"] != "unavailable" and v["domain"] in merchant_domains]
    if merchant_ops and partners:
        kind = "mixed"
    elif merchant_ops:
        kind = "merchant"
    elif partners:
        kind = "partners_only"
    elif tools:
        kind = "no_merchant_tools"
    else:
        kind = "none"
    notes = {
        "merchant": "اتصال تشغيل متجر: توجد أدوات تاجر مكتشفة.",
        "mixed": "أدوات تاجر وأدوات شركاء معاً؛ أدوات الشركاء لا تُستخدم لبيانات المتجر.",
        "partners_only": "اتصال شركاء سلة فقط: لا يمنح وصولاً لمنتجات المتجر أو طلباته أو مخزونه أو تقاريره.",
        "no_merchant_tools": "لا توجد أدوات تاجر لسلة بين الأدوات المتاحة.",
        "none": "لا توجد أدوات MCP مكتشفة.",
    }
    return {"kind": kind, "partners_tools": partners, "merchant_operations": len(merchant_ops), "note_ar": notes[kind]}


def build_map(raw_tools: list[dict], catalog: dict | None = None, ambiguity_margin: float = 0.5) -> dict:
    catalog = catalog or load_catalog()
    tools = [ToolView.from_raw(t) for t in raw_tools if t.get("name")]
    merchant_candidates = [t for t in tools if not t.is_partners()]
    ops: dict = {}
    for op in catalog["operations"]:
        cands = [m for t in merchant_candidates if (m := match_operation(op, t, catalog))]
        cands.sort(key=lambda c: (-c["score"], c["tool"]))
        if not cands:
            status, level = "unavailable", "unavailable"
        elif len(cands) > 1 and cands[0]["score"] - cands[1]["score"] < ambiguity_margin:
            status, level = "ambiguous", cands[0]["evidence_level"]
        else:
            status, level = "mapped", cands[0]["evidence_level"]
        ops[op["id"]] = {
            "label_ar": op["label_ar"],
            "domain": op["domain"],
            "access": op["access"],
            "sensitivity": op["sensitivity"],
            "idempotent": op["idempotent"],
            "verify_with": op["verify_with"],
            "status": status,
            "evidence_level": level,
            "candidates": cands[:3],
            "fallback_ar": op["fallback_ar"],
            "evidence_log": [],
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_count": len(tools),
        "connection": classify_connection(tools, ops),
        "operations": ops,
    }


def record_evidence(cmap: dict, op_id: str, level: str, note: str, tool: str | None = None,
                    store_id: str | None = None) -> dict:
    """Raise an operation's evidence level with an explicit, auditable note.

    - "tested" needs a note describing the controlled environment.
    - "live_verified" needs the live store id and a readback note.
    Levels never go down through this function.
    """
    if level not in ("tested", "live_verified"):
        raise ValueError("record_evidence يقبل tested أو live_verified فقط")
    entry = cmap["operations"].get(op_id)
    if entry is None:
        raise KeyError(op_id)
    if entry["status"] == "unavailable":
        raise ValueError(f"العملية {op_id} غير متاحة؛ لا يمكن توثيق اختبارها")
    if not note or len(note.strip()) < 10:
        raise ValueError("اكتب ملاحظة تصف الاختبار أو القراءة اللاحقة")
    if level == "live_verified":
        if not store_id:
            raise ValueError("التحقق الحي يتطلب معرّف المتجر المصرّح")
        if "mock" in note.lower() or "وهمي" in note:
            raise ValueError("اختبار وهمي لا يثبت التحقق الحي")
    entry["evidence_log"].append({
        "level": level, "note": note, "tool": tool, "store_id": store_id,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    if EVIDENCE_LEVELS.index(level) > EVIDENCE_LEVELS.index(entry["evidence_level"]):
        entry["evidence_level"] = level
    return entry


def diff_maps(old: dict, new: dict) -> dict:
    """Detect capability changes, e.g. the merchant connected MCP later."""
    added, removed, changed = [], [], []
    for op_id, n in new["operations"].items():
        o = old.get("operations", {}).get(op_id)
        was = o["status"] != "unavailable" if o else False
        now = n["status"] != "unavailable"
        if now and not was:
            added.append(op_id)
        elif was and not now:
            removed.append(op_id)
        elif o and now and o["candidates"] and n["candidates"] and o["candidates"][0]["tool"] != n["candidates"][0]["tool"]:
            changed.append(op_id)
    return {
        "connection_before": old.get("connection", {}).get("kind"),
        "connection_after": new.get("connection", {}).get("kind"),
        "added": added, "removed": removed, "tool_changed": changed,
    }


def summarize_ar(cmap: dict) -> str:
    """Arabic merchant-facing summary of what is available."""
    by_status: dict = {"mapped": [], "ambiguous": [], "unavailable": []}
    for op_id, v in cmap["operations"].items():
        by_status[v["status"]].append(v["label_ar"])
    lines = [f"نوع الاتصال: {cmap['connection']['note_ar']}"]
    lines.append(f"متاح ({len(by_status['mapped'])}): " + ("، ".join(by_status["mapped"]) or "لا شيء"))
    if by_status["ambiguous"]:
        lines.append(f"يحتاج تأكيد الأداة ({len(by_status['ambiguous'])}): " + "، ".join(by_status["ambiguous"]))
    lines.append(f"غير متاح عبر الأدوات الحالية ({len(by_status['unavailable'])}). هذا قيد في الاتصال الحالي وليس بالضرورة قيداً في سلة.")
    return "\n".join(lines)
