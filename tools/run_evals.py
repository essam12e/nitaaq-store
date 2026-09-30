#!/usr/bin/env python3
"""Run Nitaaq Store evals.

  python3 tools/run_evals.py                 # offline: activation cases
  python3 tools/run_evals.py --live-claude   # also behavior cases in real Claude Code sessions
  python3 tools/run_evals.py --live-claude --only seo-title-update-verified

Live mode needs the `claude` CLI, signed in. Each behavior case gets a fresh
temporary project with the Claude Code package installed at
.claude/skills/nitaaq-store and, when the case asks for it, a .mcp.json that
starts tests/mock_merchant_mcp.py. Results go to evals/results/<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build  # noqa: E402

sys.path.insert(0, str(build.CORE / "scripts"))
from nitaaq.activation import detect  # noqa: E402

EVALS = ROOT / "evals" / "evals.json"
MOCK = ROOT / "tests" / "mock_merchant_mcp.py"


def run_activation(cases):
    results = []
    for c in cases:
        got = detect(c["text"]).activate
        results.append({"text": c["text"], "expect": c["expect"], "got": got, "pass": got == c["expect"]})
    return results


def _project(case, workdir: Path) -> tuple[Path, Path | None]:
    proj = workdir / case["id"]
    (proj / ".claude" / "skills").mkdir(parents=True)
    shutil.copytree(build.DIST / "claude-code" / "nitaaq-store", proj / ".claude" / "skills" / "nitaaq-store")
    state = None
    if case.get("mcp"):
        state = proj / "mock-state.json"
        cfg = {"mcpServers": {"store": {"command": sys.executable, "args": [str(MOCK)],
                                        "env": {"MOCK_PROFILE": case["mcp"]["profile"], "MOCK_STATE_FILE": str(state),
                                                "MOCK_FAULTS": ",".join(case["mcp"].get("faults", []))}}}}
        (proj / ".mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
    return proj, state


def run_behavior(case, workdir: Path, timeout: int) -> dict:
    proj, state_file = _project(case, workdir)
    allowed = ["Read", "Write", "Edit", "Glob", "Grep", "Skill", "Bash(python3:*)", "Bash(mkdir:*)", "Bash(cat:*)", "Bash(ls:*)",
               "mcp__store"]
    cmd = ["claude", "-p", case["prompt"], "--output-format", "json", "--allowedTools", *allowed]
    if case.get("mcp"):
        cmd += ["--mcp-config", str(proj / ".mcp.json"), "--strict-mcp-config"]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, timeout=timeout)
        raw = p.stdout
    except subprocess.TimeoutExpired:
        return {"id": case["id"], "pass": False, "error": "timeout"}
    try:
        out = json.loads(raw)
        text = out.get("result", "")
    except json.JSONDecodeError:
        text, out = raw, {}
    checks = []
    for pat in case.get("must_match", []):
        checks.append({"check": f"match {pat}", "pass": bool(re.search(pat, text))})
    for pat in case.get("must_not_match", []):
        checks.append({"check": f"not match {pat}", "pass": not re.search(pat, text)})
    st = case.get("state") or {}
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file and state_file.exists() else None
    if st.get("no_calls"):
        writes = [c for c in (state or {}).get("calls", []) if c and not c.startswith(("list", "get"))]
        checks.append({"check": "no write calls", "pass": not writes})
    if "product" in st:
        prod = (state or {}).get("products", {}).get(str(st["product"])) if state else None
        for k, v in st["equals"].items():
            checks.append({"check": f"product {st['product']}.{k} == {v!r}", "pass": bool(prod) and prod.get(k) == v,
                           "got": prod.get(k) if prod else None})
    return {"id": case["id"], "pass": all(c["pass"] for c in checks), "checks": checks,
            "seconds": round(time.time() - t0, 1), "response": text[:4000],
            "mcp_calls": (state or {}).get("calls") if state else None, "cost_usd": out.get("total_cost_usd")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-claude", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    spec = json.loads(EVALS.read_text(encoding="utf-8"))
    act = run_activation(spec["activation"])
    ok = all(r["pass"] for r in act)
    print(f"activation: {sum(r['pass'] for r in act)}/{len(act)}")
    for r in act:
        if not r["pass"]:
            print("  FAIL", r)
    result = {"activation": act}
    if a.live_claude:
        if shutil.which("claude") is None:
            print("claude CLI not found")
            return 2
        if build.main([]) != 0:
            return 1
        cases = [c for c in spec["behavior"] if not a.only or c["id"] == a.only]
        with tempfile.TemporaryDirectory() as d:
            beh = [run_behavior(c, Path(d), a.timeout) for c in cases]
        for r in beh:
            print(f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} ({r.get('seconds')}s)")
            for c in r.get("checks", []):
                if not c["pass"]:
                    print("   ✗", c)
        ok = ok and all(r["pass"] for r in beh)
        result["behavior"] = beh
        out = ROOT / "evals" / "results" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print("saved", out.relative_to(ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
