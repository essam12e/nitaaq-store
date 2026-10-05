"""Approvals bound to the exact change the merchant reviewed.

An approval records: store/account, operation, entities, before/after values
per entity, a hash of the payload and of the reviewed snapshot fields,
validity, and status. `check()` passes only when the change about to be
executed is the same change that was approved, on the same store, and the
store still holds the values that were shown to the merchant.

Anything else (changed payload, new entity, stale values, other store,
expired, revoked, already used) needs a new approval. An unchanged, valid
approval is never asked for again.

This module records and checks approvals. It cannot stop a host from
calling a tool on its own; on Claude Code an optional PreToolUse hook can
enforce it (see references/salla-operator.md).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .locks import locked_json, read_json
from .writes import same_value, _flatten, _get

STATUSES = ("granted", "used", "revoked", "expired")

REASONS_AR = {
    "not_found": "لا توجد موافقة بهذا الرقم.",
    "wrong_store": "الموافقة على متجر آخر.",
    "wrong_account": "الموافقة على حساب آخر.",
    "wrong_operation": "الموافقة على عملية مختلفة.",
    "revoked": "الموافقة أُلغيت.",
    "used": "الموافقة استُخدمت من قبل (لمرة واحدة).",
    "expired": "انتهت صلاحية الموافقة.",
    "entity_not_approved": "فيه عنصر ما شمله الاعتماد.",
    "payload_changed": "القيم المطلوب تنفيذها تغيّرت عن اللي اعتمدتها.",
    "stale_snapshot": "القيم الحالية في المتجر تغيّرت بعد المراجعة.",
    "snapshot_missing": "ما قرأنا القيم الحالية قبل التنفيذ.",
    "too_many_items": "عدد العناصر أكبر من الحد المسموح للاعتماد الواحد.",
}

MAX_ITEMS_DEFAULT = 50


def canonical_hash(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _norm_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        out.append({"entity_id": str(it["entity_id"]), "before": it.get("before") or {}, "after": it.get("after") or {}})
    return sorted(out, key=lambda x: x["entity_id"])


def payload_hash(operation: str, items: list[dict]) -> str:
    """Hash of what will be written (operation + entity ids + after values)."""
    return canonical_hash({"op": operation, "items": [{"entity_id": i["entity_id"], "after": i["after"]}
                                                     for i in _norm_items(items)]})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class ApprovalStore:
    """Approvals for one store, in <root>/stores/<store_id>/approvals.json (locked, atomic)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @classmethod
    def for_store(cls, root: str | Path, store_id: str) -> "ApprovalStore":
        from .writes import _safe
        return cls(Path(root) / "stores" / _safe(store_id) / "approvals.json")

    def all(self) -> dict:
        return read_json(self.path)

    def get(self, approval_id: str) -> dict | None:
        return self.all().get(approval_id)

    def grant(self, store_id: str, operation: str, items: list[dict], *, proposal_id: str | None = None,
              account_id: str | None = None, merchant_words: str = "", ttl_hours: float = 24,
              single_use: bool = True, max_items: int = MAX_ITEMS_DEFAULT,
              now: datetime | None = None) -> dict:
        items = _norm_items(items)
        if not items:
            raise ValueError("approval needs at least one item")
        if len(items) > max_items:
            raise ValueError(f"{len(items)} items exceed max_items={max_items}")
        t = _now(now)
        rec = {
            "approval_id": "ap_" + secrets.token_hex(6),
            "store_id": str(store_id), "account_id": account_id, "operation": operation,
            "entities": [i["entity_id"] for i in items], "items": items,
            "payload_hash": payload_hash(operation, items),
            "snapshot_hashes": {i["entity_id"]: canonical_hash(i["before"]) for i in items},
            "proposal_id": proposal_id, "merchant_words": merchant_words,
            "single_use": single_use, "max_items": max_items,
            "granted_at": t.isoformat(timespec="seconds"),
            "expires_at": (t + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
            "status": "granted",
        }
        with locked_json(self.path) as data:
            data[rec["approval_id"]] = rec
        return rec

    def check(self, approval_id: str, store_id: str, operation: str, items: list[dict],
              fresh: dict | None = None, account_id: str | None = None,
              now: datetime | None = None) -> dict:
        """Is executing `items` now covered by this approval?

        items: [{entity_id, after}] about to be written (subset of the approval).
        fresh: {entity_id: current object read just now}. Required: values the
        merchant saw as "before" must still be what the store holds.
        """
        rec = self.get(approval_id)
        reasons: list[str] = []
        if not rec:
            return _result(False, ["not_found"])
        if rec["store_id"] != str(store_id):
            reasons.append("wrong_store")
        if (rec.get("account_id") or None) != (account_id or None):
            reasons.append("wrong_account")
        if rec["operation"] != operation:
            reasons.append("wrong_operation")
        status = rec["status"]
        if status in ("revoked", "used"):
            reasons.append(status)
        if status == "expired" or _now(now) >= _parse(rec["expires_at"]):
            reasons.append("expired")
        want = _norm_items(items)
        if len(want) > rec.get("max_items", MAX_ITEMS_DEFAULT):
            reasons.append("too_many_items")
        approved = {i["entity_id"]: i for i in rec["items"]}
        for it in want:
            ap = approved.get(it["entity_id"])
            if ap is None:
                reasons.append("entity_not_approved")
                continue
            if canonical_hash(it["after"]) != canonical_hash(ap["after"]):
                if not _same_values(it["after"], ap["after"]):
                    reasons.append("payload_changed")
            if fresh is None or it["entity_id"] not in fresh:
                reasons.append("snapshot_missing")
                continue
            for f, before in _flatten(ap["before"]).items():
                if not same_value(_get(fresh[it["entity_id"]], f, None), before):
                    reasons.append("stale_snapshot")
                    break
        reasons = list(dict.fromkeys(reasons))
        return _result(not reasons, reasons, rec)

    def mark_used(self, approval_id: str) -> None:
        with locked_json(self.path) as data:
            rec = data.get(approval_id)
            if rec and rec.get("single_use", True) and rec["status"] == "granted":
                rec["status"] = "used"
                rec["used_at"] = _now(None).isoformat(timespec="seconds")

    def revoke(self, approval_id: str) -> bool:
        with locked_json(self.path) as data:
            rec = data.get(approval_id)
            if not rec or rec["status"] != "granted":
                return False
            rec["status"] = "revoked"
            rec["revoked_at"] = _now(None).isoformat(timespec="seconds")
            return True


def _same_values(a: dict, b: dict) -> bool:
    fa, fb = _flatten(a), _flatten(b)
    return set(fa) == set(fb) and all(same_value(fa[k], fb[k]) for k in fa)


def _result(ok: bool, reasons: list[str], rec: dict | None = None) -> dict:
    return {"ok": ok, "reasons": reasons, "reasons_ar": [REASONS_AR[r] for r in reasons],
            "approval_id": rec["approval_id"] if rec else None}


def requires_approval(op: dict, patch_fields: list[str], item_count: int = 1,
                      customer_visible: bool = False) -> tuple[bool, str]:
    """Approval rule from the operation catalog entry plus the patch.

    Extends writes.needs_approval with operations that are sensitive by
    nature (prices via coupons/offers, inventory, theme publish, customer
    messages) even when the patch fields alone look harmless.
    """
    from .writes import needs_approval
    sensitive_ops = {"inventory.set", "inventory.adjust", "coupons.create", "products.delete",
                     "products.set_status", "orders.update_status", "reviews.reply", "theme.publish",
                     "pages.publish", "home.sections.delete", "pages.delete", "branding.update"}
    if op.get("id") in sensitive_ops:
        return True, "عملية حساسة بطبيعتها"
    return needs_approval(op.get("access", "write"), op.get("sensitivity", "medium"), patch_fields,
                          item_count, customer_visible)
