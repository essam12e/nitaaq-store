"""Agent registry: who exists, what each needs, and what each may never do."""

from __future__ import annotations

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
REGISTRY_PATH = SKILL_DIR / "assets" / "agent-registry.json"

FORBIDDEN_NATIVE_TOOLS = {"Write", "Edit", "NotebookEdit", "Agent", "Task"}


def load(path: Path | None = None) -> dict:
    return json.loads(Path(path or REGISTRY_PATH).read_text(encoding="utf-8"))


def agents(reg: dict | None = None) -> list[dict]:
    return (reg or load())["agents"]


def get(agent_id: str, reg: dict | None = None) -> dict:
    for a in agents(reg):
        if a["id"] == agent_id:
            return a
    raise KeyError(agent_id)


def limits(reg: dict | None = None) -> dict:
    return (reg or load())["limits"]


def is_active(agent: dict) -> bool:
    return agent.get("status") == "active"


def capability_ok(agent: dict, available: set[str]) -> tuple[bool, list[str]]:
    """True if any required group is fully available. Returns the missing items of the closest group."""
    groups = agent.get("capabilities", {}).get("required_any") or []
    if not groups:
        return True, []
    best: list[str] | None = None
    for g in groups:
        missing = [x for x in g if x not in available]
        if not missing:
            return True, []
        if best is None or len(missing) < len(best):
            best = missing
    return False, best or []


def validate(reg: dict | None = None, skill_dir: Path = SKILL_DIR) -> list[str]:
    reg = reg or load()
    errs: list[str] = []
    ids = [a.get("id") for a in reg.get("agents", [])]
    if len(ids) != len(set(ids)):
        errs.append("duplicate agent ids")
    for key in ("max_delegation_depth", "max_specialists_absolute", "max_review_rounds", "max_stage_attempts"):
        if key not in reg.get("limits", {}):
            errs.append(f"limits.{key} missing")
    if reg.get("limits", {}).get("max_delegation_depth") != 1:
        errs.append("limits.max_delegation_depth must be 1 (specialists never delegate)")
    names = []
    for a in reg.get("agents", []):
        aid = a.get("id")
        if a.get("status") not in ("active", "planned"):
            errs.append(f"{aid}: status must be active or planned")
        if a.get("kind") not in ("main", "specialist", "reviewer"):
            errs.append(f"{aid}: bad kind")
        if not a.get("label_ar"):
            errs.append(f"{aid}: label_ar missing")
        if a.get("kind") in ("specialist", "reviewer") and a.get("status") == "active" and a.get("direct_writes") != "prohibited":
            errs.append(f"{aid}: specialists and reviewers must have direct_writes=prohibited")
        if a.get("kind") == "specialist" and not a.get("upstream") and a.get("status") == "active":
            errs.append(f"{aid}: upstream adaptation reference missing")
        for g in a.get("capabilities", {}).get("required_any", []):
            if not isinstance(g, list) or not g:
                errs.append(f"{aid}: required_any must be a list of non-empty lists")
        if a.get("status") == "active":
            ref = a.get("reference")
            if not ref or not (skill_dir / ref).is_file():
                errs.append(f"{aid}: reference file missing: {ref}")
        nat = a.get("native")
        if nat:
            if a.get("status") != "active":
                errs.append(f"{aid}: only active agents may have native definitions")
            name = nat.get("name", "")
            names.append(name)
            if not name.startswith("nitaaq-"):
                errs.append(f"{aid}: native name must start with nitaaq-")
            cc = nat.get("claude_code", {})
            bad = set(cc.get("tools", [])) & FORBIDDEN_NATIVE_TOOLS
            if bad:
                errs.append(f"{aid}: native tools must not include {sorted(bad)}")
            if "mcp__*" not in cc.get("disallowedTools", []):
                errs.append(f"{aid}: native agents must disallow mcp__* (store tools stay with the operator)")
            if nat.get("codex", {}).get("sandbox_mode") != "read-only":
                errs.append(f"{aid}: codex sandbox_mode must be read-only")
    if len(names) != len(set(names)):
        errs.append("duplicate native names")
    return errs
