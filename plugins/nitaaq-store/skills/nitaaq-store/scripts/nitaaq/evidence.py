"""Shared evidence: fetched once, labelled with scope and coverage, reused.

An evidence record says exactly what was fetched (store, source operation,
period, timezone, fetch time), how complete it is (fetched vs total, pages,
duplicates removed, missing values) and a content hash. Specialists and the
reviewer receive evidence ids, not raw conversations, and every load checks
that the evidence belongs to the store of the task (no cross-store leakage).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .locks import atomic_write_json, read_json
from .writes import _safe

# Default freshness for cached evidence, in minutes. Writes always re-read.
FRESHNESS_MINUTES = {
    "orders": 15, "inventory": 15, "carts": 15,
    "products": 60, "customers": 60, "coupons": 60, "reviews": 60,
    "public_page": 60, "reports": 60, "export": None,  # an export is a dated snapshot; never "refreshes"
}


class CrossStoreError(ValueError):
    pass


def content_hash(records) -> str:
    raw = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_key(store_id: str, operation: str, params: dict | None = None, period: tuple | list | None = None,
              timezone_name: str = "Asia/Riyadh", account_id: str | None = None) -> str:
    """store | account | operation | params | period | timezone."""
    body = {"store_id": str(store_id), "account_id": account_id, "op": operation, "params": params or {},
            "period": list(period) if period else None, "tz": timezone_name}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def dedupe(records: list[dict], key: str = "id") -> tuple[list[dict], dict]:
    """Drop repeated records (same key), keeping the first.

    Pages that overlap (an order arrives while paginating) repeat records.
    Same key with different content is kept once and reported as a conflict.
    Records without the key are kept and counted.
    """
    seen: dict = {}
    out, dups, conflicts, no_key = [], 0, [], 0
    for r in records:
        k = r.get(key)
        if k in (None, ""):
            no_key += 1
            out.append(r)
            continue
        k = str(k)
        if k in seen:
            dups += 1
            if content_hash(seen[k]) != content_hash(r):
                conflicts.append(k)
            continue
        seen[k] = r
        out.append(r)
    return out, {"duplicates_removed": dups, "conflicting_duplicates": conflicts, "records_without_key": no_key}


def missing_values(records: list[dict], fields: list[str]) -> dict:
    return {f: sum(1 for r in records if r.get(f) in (None, "")) for f in fields}


def make_evidence(store_id: str, source: dict, records: list[dict], *, period: tuple | list | None = None,
                  timezone_name: str = "Asia/Riyadh", records_total: int | None = None, pages: int | None = None,
                  fetched_at: str | None = None, account_id: str | None = None, key: str = "id",
                  check_fields: list[str] | None = None, kind: str | None = None) -> tuple[dict, list[dict]]:
    """Build an evidence record. Returns (record, deduplicated records).

    source: {"kind": "mcp"|"export"|"public"|"report_tool", "operation": "...", "tool": "<exact name or file>"}
    records_total: total the source reported (None if it did not say).
    """
    _safe(store_id)
    unique, dd = dedupe(records, key)
    fetched = len(records)
    complete = None if records_total is None else len(unique) >= records_total
    kind = kind or source.get("operation", "").split(".")[0] or source.get("kind")
    rec = {
        "store_id": str(store_id), "account_id": account_id, "source": source, "kind": kind,
        "period": list(period) if period else None, "timezone": timezone_name,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": {"records_fetched": fetched, "records_unique": len(unique), "records_total": records_total,
                     "pages": pages, "complete": complete, **dd,
                     "missing_values": missing_values(unique, check_fields or [])},
        "content_hash": content_hash(unique),
        "pii": "unknown",
    }
    period_tag = "_".join(rec["period"]) if rec["period"] else "na"
    op = (source.get("operation") or source.get("kind") or "data").replace(".", "-")
    rec["evidence_id"] = f"ev_{op}_{_safe(store_id)}_{period_tag}_{rec['content_hash'][7:15]}"
    rec["cache_key"] = cache_key(store_id, source.get("operation") or "", source.get("params"), period,
                                 timezone_name, account_id)
    return rec, unique


def coverage_note_ar(ev: dict) -> str:
    c = ev["coverage"]
    if c["complete"] is True:
        s = f"كاملة ({c['records_unique']} من {c['records_total']})"
    elif c["complete"] is False:
        s = f"جزئية ({c['records_unique']} من {c['records_total']})"
    else:
        s = f"غير معروفة الاكتمال ({c['records_unique']} سجل؛ المصدر لم يذكر العدد الكلي)"
    if c.get("duplicates_removed"):
        s += f"، حُذف {c['duplicates_removed']} مكرر"
    return s


def is_fresh(ev: dict, now: datetime | None = None, max_age_minutes: int | None = None) -> bool:
    if max_age_minutes is None:
        max_age_minutes = FRESHNESS_MINUTES.get(ev.get("kind"), 15)
    if max_age_minutes is None:
        return True  # dated snapshot (export): freshness is its export date, stated in the report
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(ev["fetched_at"]) <= timedelta(minutes=max_age_minutes)


class EvidenceStore:
    """<root>/stores/<store_id>/evidence/<evidence_id>.json — record + data."""

    def __init__(self, root: str | Path, store_id: str):
        self.store_id = str(store_id)
        self.dir = Path(root) / "stores" / _safe(store_id) / "evidence"

    def save(self, ev: dict, records: list[dict]) -> Path:
        if ev["store_id"] != self.store_id:
            raise CrossStoreError(f"evidence for store {ev['store_id']} cannot be saved under {self.store_id}")
        p = self.dir / f"{_safe(ev['evidence_id'])}.json"
        atomic_write_json(p, {"evidence": ev, "records": records})
        return p

    def load(self, evidence_id: str) -> tuple[dict, list[dict]]:
        p = self.dir / f"{_safe(evidence_id)}.json"
        if not p.exists():
            raise FileNotFoundError(evidence_id)
        doc = read_json(p)
        ev = doc["evidence"]
        if ev.get("store_id") != self.store_id:
            raise CrossStoreError(f"evidence {evidence_id} belongs to store {ev.get('store_id')}, not {self.store_id}")
        if content_hash(doc["records"]) != ev["content_hash"]:
            raise ValueError(f"evidence {evidence_id} content does not match its hash")
        return ev, doc["records"]

    def find_cached(self, key: str, now: datetime | None = None) -> str | None:
        if not self.dir.exists():
            return None
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            ev = read_json(p).get("evidence", {})
            if ev.get("cache_key") == key and is_fresh(ev, now):
                return ev["evidence_id"]
        return None
