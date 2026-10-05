"""Validate task, evidence, finding, proposal, approval and review documents.

A small JSON-Schema subset (type, required, properties, enum, items,
minItems, maxItems, minLength) so the helpers stay standard-library only.
Schemas live in assets/schemas/.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parents[2] / "assets" / "schemas"

_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
          "boolean": bool, "null": type(None)}


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))


def _type_ok(v, t) -> bool:
    types = t if isinstance(t, list) else [t]
    for x in types:
        py = _TYPES[x]
        if x in ("integer", "number") and isinstance(v, bool):
            continue
        if isinstance(v, py):
            return True
    return False


def _check(v, s: dict, path: str, errs: list[str]):
    if "type" in s and not _type_ok(v, s["type"]):
        errs.append(f"{path}: expected {s['type']}")
        return
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} not in {s['enum']}")
    if isinstance(v, str) and len(v) < s.get("minLength", 0):
        errs.append(f"{path}: too short")
    if isinstance(v, dict):
        for r in s.get("required", []):
            if r not in v:
                errs.append(f"{path}.{r}: required")
        for k, sub in s.get("properties", {}).items():
            if k in v:
                _check(v[k], sub, f"{path}.{k}", errs)
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            errs.append(f"{path}: needs at least {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{path}: at most {s['maxItems']} items")
        if "items" in s:
            for i, x in enumerate(v):
                _check(x, s["items"], f"{path}[{i}]", errs)


def validate(doc, schema_name: str) -> list[str]:
    errs: list[str] = []
    _check(doc, load_schema(schema_name), schema_name, errs)
    return errs
