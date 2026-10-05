"""Run state, stage transitions, cancellation/resume and the audit log.

Layout (inside the working directory, never inside the skill folder):

  .nitaaq/stores/<store_id>/runs/<run_id>/run.json     run + stages
  .nitaaq/stores/<store_id>/runs/<run_id>/audit.jsonl  append-only events

Transitions run under a file lock and are written atomically, so parallel
stages (native subagents) cannot overwrite each other's status. This is
session-local storage on the machine running the host: nothing here runs in
the background after the session ends.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .locks import append_jsonl, locked_json, read_json
from .redact import redact_text
from .writes import _safe

STAGE_STATES = ("planned", "running", "done", "failed", "blocked", "cancelled", "skipped")
TERMINAL = {"done", "failed", "blocked", "cancelled", "skipped"}
ALLOWED = {
    "planned": {"running", "blocked", "skipped", "cancelled"},
    "running": {"done", "failed", "blocked", "cancelled"},
    "failed": {"running"},   # a bounded retry
    "blocked": {"running"},  # the blocker was resolved (e.g. merchant reconnected)
    "done": set(), "cancelled": set(), "skipped": set(),
}
MAX_STAGE_ATTEMPTS = 2

_SECRET_KEYS = re.compile(r"(?i)(token|secret|password|passwd|authorization|cookie|api[_-]?key|credential)")


_SECRET_VALUE = re.compile(r"(?i)\b(token|secret|password|passwd|authorization|cookie|api[_-]?key|bearer)\b(\s*[:=]\s*|\s+)[^\s,;]+")
_SECRET_SHAPES = re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(obj):
    """Remove credential-looking keys and redact personal data in strings (for audit logs)."""
    if isinstance(obj, dict):
        return {k: ("[removed]" if _SECRET_KEYS.search(str(k)) else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_SHAPES.sub("[removed]", _SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + "[removed]", redact_text(obj)))
    return obj


class InvalidTransition(ValueError):
    pass


class Run:
    def __init__(self, root: str | Path, store_id: str, run_id: str):
        self.root = Path(root)
        self.store_id = str(store_id)
        self.run_id = _safe(run_id)
        self.dir = self.root / "stores" / _safe(store_id) / "runs" / self.run_id
        self.path = self.dir / "run.json"
        self.audit_path = self.dir / "audit.jsonl"

    @classmethod
    def create(cls, root: str | Path, store_id: str, question: str = "", *, account_id: str | None = None,
               capability_map_version: str | None = None, mode: str = "sequential") -> "Run":
        if mode not in ("sequential", "native"):
            raise ValueError(mode)
        run_id = datetime.now(timezone.utc).strftime("r_%Y%m%d_%H%M%S_") + secrets.token_hex(3)
        run = cls(root, store_id, run_id)
        with locked_json(run.path) as data:
            data.update({"run_id": run_id, "store_id": str(store_id), "account_id": account_id,
                         "question": scrub(question), "created_at": _now(), "status": "running",
                         "mode": mode, "capability_map_version": capability_map_version,
                         "review_mode": None, "cancel_requested": False, "stages": []})
        run.audit("run_created", mode=mode)
        return run

    def data(self) -> dict:
        d = read_json(self.path)
        if not d:
            raise FileNotFoundError(f"run {self.run_id} not found for store {self.store_id}")
        if d.get("store_id") != self.store_id:
            raise ValueError("run belongs to another store")
        return d

    def add_stage(self, agent: str, *, depends_on: list[str] | None = None, task_id: str | None = None,
                  evidence_refs: list[str] | None = None) -> str:
        with locked_json(self.path) as d:
            if d.get("cancel_requested"):
                raise InvalidTransition("run is cancelled")
            tid = task_id or f"t{len(d['stages']) + 1}"
            if any(s["task_id"] == tid for s in d["stages"]):
                raise ValueError(f"duplicate task id {tid}")
            known = {s["task_id"] for s in d["stages"]}
            for dep in depends_on or []:
                if dep not in known:
                    raise ValueError(f"unknown dependency {dep}")
            d["stages"].append({"task_id": tid, "agent": agent, "status": "planned", "depends_on": depends_on or [],
                                "evidence_refs": evidence_refs or [], "attempts": 0, "outputs": [],
                                "reason_ar": None, "updated_at": _now()})
        self.audit("stage_added", task_id=tid, agent=agent)
        return tid

    def transition(self, task_id: str, status: str, *, reason_ar: str | None = None,
                   outputs: list[str] | None = None, evidence_refs: list[str] | None = None) -> dict:
        if status not in STAGE_STATES:
            raise ValueError(status)
        with locked_json(self.path) as d:
            st = next((s for s in d["stages"] if s["task_id"] == task_id), None)
            if st is None:
                raise KeyError(task_id)
            if status == "running" and d.get("cancel_requested"):
                raise InvalidTransition("run is cancelled")
            if status not in ALLOWED[st["status"]]:
                raise InvalidTransition(f"{task_id}: {st['status']} -> {status} not allowed")
            if status == "running":
                if st["attempts"] >= MAX_STAGE_ATTEMPTS:
                    raise InvalidTransition(f"{task_id}: attempt limit {MAX_STAGE_ATTEMPTS} reached")
                waiting = [x["task_id"] for x in d["stages"]
                           if x["task_id"] in st["depends_on"] and x["status"] != "done"]
                if waiting:
                    raise InvalidTransition(f"{task_id} waits for {waiting}")
                st["attempts"] += 1
            st["status"] = status
            st["updated_at"] = _now()
            if reason_ar is not None:
                st["reason_ar"] = scrub(reason_ar)
            if outputs:
                st["outputs"] += outputs
            if evidence_refs:
                st["evidence_refs"] = sorted(set(st["evidence_refs"]) | set(evidence_refs))
            if all(s["status"] in TERMINAL for s in d["stages"]):
                d["status"] = "cancelled" if d.get("cancel_requested") else "done"
            result = dict(st)
        self.audit("stage_" + status, task_id=task_id, reason_ar=reason_ar)
        return result

    def set_review_mode(self, mode: str) -> None:
        """"independent_context" only when the review really ran in a separate agent context."""
        if mode not in ("independent_context", "self_check"):
            raise ValueError(mode)
        with locked_json(self.path) as d:
            d["review_mode"] = mode
        self.audit("review_mode", mode=mode)

    def request_cancel(self) -> None:
        with locked_json(self.path) as d:
            d["cancel_requested"] = True
            for s in d["stages"]:
                if s["status"] == "planned":
                    s["status"] = "cancelled"
                    s["updated_at"] = _now()
            if all(s["status"] in TERMINAL for s in d["stages"]):
                d["status"] = "cancelled"
        self.audit("cancel_requested")

    def resume_plan(self) -> dict:
        """What is left: stages ready to run now, and stages still waiting."""
        d = self.data()
        done = {s["task_id"] for s in d["stages"] if s["status"] == "done"}
        ready, waiting, retry = [], [], []
        for s in d["stages"]:
            if s["status"] == "planned":
                (ready if set(s["depends_on"]) <= done else waiting).append(s["task_id"])
            elif s["status"] == "running":
                retry.append(s["task_id"])  # interrupted mid-stage; reads may repeat, writes reconcile first
            elif s["status"] == "failed" and s["attempts"] < MAX_STAGE_ATTEMPTS:
                retry.append(s["task_id"])
        return {"cancelled": d.get("cancel_requested", False), "ready": ready, "waiting": waiting,
                "interrupted_or_retryable": retry}

    def audit(self, event: str, **data) -> None:
        append_jsonl(self.audit_path, {"at": _now(), "run_id": self.run_id, "store_id": self.store_id,
                                       "event": event, **scrub({k: v for k, v in data.items() if v is not None})})

    def audit_events(self) -> list[dict]:
        import json
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
