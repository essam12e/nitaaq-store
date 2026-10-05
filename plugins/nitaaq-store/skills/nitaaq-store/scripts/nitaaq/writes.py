"""Write safety: minimal patches, conflict detection, readback, idempotency.

Flow for every write:
  snapshot = read current values
  patch    = build_patch(snapshot, desired, fields)      # only changed fields
  (propose diff_table(...) and get approval when sensitive)
  fresh    = re-read right before writing
  conflicts= detect_conflicts(snapshot, fresh, fields)   # stop if not empty
  key      = ledger.begin(op, entity, patch)             # blocks duplicates
  ... call the tool ...
  ledger.finish(key, "succeeded" | "failed" | "unknown")
  verify_readback(patch, readback)                       # persistence proof
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .arabic import normalize, parse_amount
from .locks import locked_json, read_json

_MISSING = object()


def _get(d: dict, path: str, default=_MISSING):
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _set(d: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _code_like(v) -> bool:
    """"00123" is an identifier (SKU/barcode), not the number 123."""
    return isinstance(v, str) and len(v.strip()) > 1 and v.strip()[0] == "0" and v.strip()[1].isdigit()


def same_value(a, b) -> bool:
    """Compare values the way a merchant would ("100.00" == 100, spacing ignored)."""
    if a is _MISSING or b is _MISSING:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    na, nb = parse_amount(a), parse_amount(b)
    if na is not None and nb is not None and not (_code_like(a) or _code_like(b)):
        return na == nb
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.split()) == " ".join(b.split())
    if isinstance(a, list) and isinstance(b, list):
        try:
            return sorted(map(json.dumps, a)) == sorted(map(json.dumps, b))
        except TypeError:
            return a == b
    return a == b


def build_patch(current: dict, desired: dict, fields: list[str] | None = None) -> dict:
    """Return only the selected fields whose desired value differs.

    Fields the merchant did not ask to change are never included, so a
    partial-update tool keeps them. If the tool is full-replace, merge the
    patch into the current object with merge_for_full_replace().
    """
    fields = fields or list(_flatten(desired).keys())
    patch: dict = {}
    for f in fields:
        new = _get(desired, f)
        if new is _MISSING:
            continue
        old = _get(current, f)
        if not same_value(old, new):
            _set(patch, f, new)
    return patch


def merge_for_full_replace(current: dict, patch: dict) -> dict:
    """For tools that replace the whole object: current + patch, nothing dropped."""
    merged = json.loads(json.dumps(current, default=str))
    for f, v in _flatten(patch).items():
        _set(merged, f, v)
    return merged


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def diff_rows(current: dict, patch: dict, labels: dict | None = None) -> list[dict]:
    labels = labels or {}
    rows = []
    for f, new in _flatten(patch).items():
        old = _get(current, f, None)
        rows.append({"field": f, "label": labels.get(f, f), "before": old, "after": new})
    return rows


def diff_table_ar(current: dict, patch: dict, labels: dict | None = None) -> str:
    rows = diff_rows(current, patch, labels)
    if not rows:
        return "لا توجد فروقات: القيم الحالية مطابقة لما طلبته."
    out = ["| الحقل | قبل | بعد |", "|---|---|---|"]
    for r in rows:
        before = "—" if r["before"] in (None, "") else str(r["before"])
        out.append(f"| {r['label']} | {before} | {r['after']} |")
    return "\n".join(out)


def detect_conflicts(snapshot: dict, fresh: dict, fields: list[str]) -> list[dict]:
    """Fields that changed between the reviewed snapshot and a fresh read."""
    conflicts = []
    for f in fields:
        a, b = _get(snapshot, f, None), _get(fresh, f, None)
        if not same_value(a, b):
            conflicts.append({"field": f, "reviewed": a, "now": b})
    return conflicts


def verify_readback(patch: dict, readback: dict) -> dict:
    """Compare what we wrote with what the store returns afterwards."""
    results = []
    for f, expected in _flatten(patch).items():
        got = _get(readback, f, _MISSING)
        if got is _MISSING:
            results.append({"field": f, "status": "not_returned", "expected": expected})
        elif same_value(expected, got):
            results.append({"field": f, "status": "persisted", "expected": expected, "got": got})
        else:
            results.append({"field": f, "status": "mismatch", "expected": expected, "got": got})
    ok = all(r["status"] == "persisted" for r in results)
    unverified = any(r["status"] == "not_returned" for r in results)
    return {"verified": ok, "unverified_fields": unverified, "fields": results}


# ---------------------------------------------------------------- idempotency


def operation_key(op: str, entity: str | None, payload: dict, store_id: str | None = None,
                  approval_id: str | None = None) -> str:
    """Stable key for one intended side effect.

    store_id keeps the same operation on two stores apart; approval_id ties
    the attempt to the approval that allowed it. Both are optional so keys
    written by 1.1.0 (without them) keep matching.
    """
    body = {"op": op, "entity": entity, "payload": payload}
    if store_id is not None:
        body["store_id"] = str(store_id)
    if approval_id is not None:
        body["approval_id"] = approval_id
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def ledger_path(root: str | Path, store_id: str) -> Path:
    """Per-store ledger location: <root>/stores/<store_id>/ledger.json."""
    return Path(root) / "stores" / _safe(store_id) / "ledger.json"


def _safe(part) -> str:
    s = str(part)
    if not s or s in (".", "..") or any(c in s for c in "/\\\0"):
        raise ValueError(f"unsafe path component: {part!r}")
    return s


class Ledger:
    """Local JSON ledger of write attempts, to stop duplicate side effects.

    Safe across processes: every check-and-set runs under an exclusive file
    lock and the file is replaced atomically. A write whose outcome is
    "unknown" (timeout after sending) must be reconciled by reading the
    store before any retry.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = read_json(self.path)

    def _refresh(self) -> dict:
        self.data = read_json(self.path)
        return self.data

    def check(self, key: str) -> str:
        """"new" | "done" | "reconcile_first" | "retry_allowed"."""
        return _state_of(self._refresh().get(key))

    def begin(self, key: str, op: str, entity: str | None, summary: str = "",
              store_id: str | None = None, approval_id: str | None = None) -> str:
        """Atomically claim the key. Only one caller ever gets "go" for a pending key."""
        with locked_json(self.path) as data:
            state = _state_of(data.get(key))
            if state in ("done", "reconcile_first"):
                self.data = data
                return state
            data[key] = {"op": op, "entity": entity, "summary": summary, "status": "pending",
                         "store_id": store_id, "approval_id": approval_id,
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.data = data
        return "go"

    def finish(self, key: str, status: str, result_id: str | None = None, note: str = ""):
        if status not in ("succeeded", "failed", "unknown"):
            raise ValueError(status)
        with locked_json(self.path) as data:
            rec = data.setdefault(key, {})
            rec.update({"status": status, "result_id": result_id, "note": note,
                        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            self.data = data

    def reconcile(self, key: str, found_id: str | None):
        """After reading the store: found_id means the write did happen."""
        self.finish(key, "succeeded" if found_id else "failed", found_id, "reconciled by readback")


def _state_of(rec) -> str:
    if not rec:
        return "new"
    return {"succeeded": "done", "unknown": "reconcile_first", "pending": "reconcile_first",
            "failed": "retry_allowed"}.get(rec.get("status"), "reconcile_first")


def find_existing_by_name(items: list[dict], name: str, name_field: str = "name") -> list[dict]:
    """Reconciliation for creates without idempotency keys: same normalized name."""
    target = normalize(name)
    return [i for i in items if normalize(str(i.get(name_field, ""))) == target]


# ------------------------------------------------------------ authorization


@dataclass
class Authorization:
    """What the merchant approved in this conversation, so we don't re-ask.

    ops: operation ids covered; entities: ids covered ("*" for all listed in
    the reviewed table); fields: fields covered (empty = the reviewed patch).
    """
    ops: set
    entities: set
    fields: set = field(default_factory=set)
    note: str = ""
    max_items: int | None = None

    def covers(self, op: str, entity: str, patch_fields: list[str]) -> bool:
        if op not in self.ops:
            return False
        if "*" not in self.entities and str(entity) not in self.entities:
            return False
        if self.fields and not set(patch_fields) <= self.fields:
            return False
        return True


SENSITIVE_FIELDS = {"price", "sale_price", "cost_price", "status", "quantity", "categories", "name", "product_type"}


def needs_approval(op_access: str, sensitivity: str, patch_fields: list[str], item_count: int = 1,
                   customer_visible: bool = False) -> tuple[bool, str]:
    if op_access == "destructive":
        return True, "عملية حذف أو إجراء لا يمكن التراجع عنه"
    if customer_visible:
        return True, "قد يصل إشعار أو رسالة للعميل"
    if sensitivity == "high":
        return True, "عملية حساسة"
    if item_count > 1:
        return True, f"تعديل جماعي على {item_count} عنصر"
    if set(patch_fields) & SENSITIVE_FIELDS:
        return True, "يغيّر السعر أو الحالة أو الكمية أو الاسم"
    return False, ""


# -------------------------------------------------------------- batch report


@dataclass
class BatchReport:
    items: list = field(default_factory=list)

    def add(self, label: str, status: str, detail: str = "", entity_id: str | None = None):
        if status not in ("succeeded", "failed", "partial", "skipped", "unverified"):
            raise ValueError(status)
        self.items.append({"label": label, "status": status, "detail": detail, "id": entity_id})

    def counts(self) -> dict:
        c: dict = {}
        for i in self.items:
            c[i["status"]] = c.get(i["status"], 0) + 1
        return c

    def to_markdown_ar(self) -> str:
        names = {"succeeded": "✅ تم وتحقّقنا", "failed": "❌ فشل", "partial": "⚠️ جزئي",
                 "skipped": "⏭️ تُرك", "unverified": "❔ نُفّذ ولم يتأكد الحفظ"}
        c = self.counts()
        head = "، ".join(f"{names[k]}: {v}" for k, v in c.items())
        rows = ["| العنصر | النتيجة | التفاصيل |", "|---|---|---|"]
        rows += [f"| {i['label']} | {names[i['status']]} | {i['detail'] or '—'} |" for i in self.items]
        return head + "\n\n" + "\n".join(rows)


def as_decimal(v) -> Decimal | None:
    return parse_amount(v)
