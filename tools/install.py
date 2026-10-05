#!/usr/bin/env python3
"""Install Nitaaq Store into Claude Code and/or Codex skill directories.

Locations (from the hosts' documentation, checked 2026-09-30):
  Claude Code  user:    ~/.claude/skills/nitaaq-store
               project: <project>/.claude/skills/nitaaq-store
  Codex        user:    ~/.agents/skills/nitaaq-store
               project: <project>/.agents/skills/nitaaq-store

Safety rules:
  - Only the nitaaq-store directory is written. Other skills, settings.json,
    .mcp.json and Codex config.toml are never read or modified.
  - An existing nitaaq-store is backed up to ~/.nitaaq-store/backups/ (outside
    any skills directory, so the backup is not loaded as a second skill).
  - A directory named nitaaq-store that is NOT this skill is left untouched
    and the install stops.

Optional native agents (--agents): copies the generated read-only
specialist/reviewer definitions to
  Claude Code  ~/.claude/agents/nitaaq-*.md   (or <project>/.claude/agents)
  Codex        ~/.codex/agents/nitaaq-*.toml  (or <project>/.codex/agents)
Only files that carry the gen_agents header are overwritten or removed. The
plugin install of Claude Code already ships them; this is for skill installs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build  # noqa: E402
import gen_agents  # noqa: E402

SKILL = "nitaaq-store"
MARKER = ".nitaaq-install.json"


def target_dir(host: str, scope: str, project: Path | None, home: Path) -> Path:
    base = {"claude-code": ".claude/skills", "codex": ".agents/skills"}[host]
    root = home if scope == "user" else project
    if root is None:
        raise SystemExit("--project مطلوب مع --scope project")
    return root / base / SKILL


def is_ours(path: Path) -> bool:
    skill = path / "SKILL.md"
    if not skill.is_file():
        return False
    try:
        fm = build.parse_frontmatter(build.split_frontmatter(skill.read_text(encoding="utf-8"))[0])
    except ValueError:
        return False
    return fm.get("name") == SKILL


def version_of(path: Path) -> str:
    try:
        fm = build.parse_frontmatter(build.split_frontmatter((path / "SKILL.md").read_text(encoding="utf-8"))[0])
        return fm.get("metadata", {}).get("version", "?")
    except (OSError, ValueError):
        return "?"


def plan(host: str, scope: str, project: Path | None, home: Path) -> dict:
    dest = target_dir(host, scope, project, home)
    src = build.DIST / host / SKILL
    if dest.is_symlink():
        action = "refuse"
        reason = "المسار رابط رمزي؛ لن نكتب عبره. احذفه يدوياً إن أردت."
    elif not dest.exists():
        action, reason = "install", "تثبيت جديد"
    elif is_ours(dest):
        action, reason = "upgrade", f"ترقية من الإصدار {version_of(dest)}"
    else:
        action, reason = "refuse", "يوجد مجلد بنفس الاسم ليس مهارة نطاق للمتاجر؛ لن نلمسه."
    return {"host": host, "scope": scope, "src": str(src), "dest": str(dest), "action": action, "reason": reason}


def apply(p: dict, home: Path) -> dict:
    dest, src = Path(p["dest"]), Path(p["src"])
    if p["action"] == "refuse":
        return {**p, "result": "refused"}
    backup = None
    if p["action"] == "upgrade":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = home / ".nitaaq-store" / "backups" / f"{p['host']}-{p['scope']}-{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(backup))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    (dest / MARKER).write_text(json.dumps({
        "skill": SKILL, "version": version_of(dest), "host": p["host"], "scope": p["scope"],
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "backup": str(backup) if backup else None,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**p, "result": "installed", "backup": str(backup) if backup else None}


def agent_dir(host: str, scope: str, project: Path | None, home: Path) -> Path:
    base = {"claude-code": ".claude/agents", "codex": ".codex/agents"}[host]
    root = home if scope == "user" else project
    if root is None:
        raise SystemExit("--project مطلوب مع --scope project")
    return root / base


def agent_sources(host: str) -> list[Path]:
    src, suffix = {"claude-code": (gen_agents.CLAUDE_DIR, ".md"), "codex": (gen_agents.CODEX_DIR, ".toml")}[host]
    return sorted(src.glob(f"nitaaq-*{suffix}"))


def agents_action(host: str, scope: str, project: Path | None, home: Path, remove: bool, dry: bool) -> list[dict]:
    """Install or remove our generated agent files; never touch a file we did not generate."""
    out = []
    d = agent_dir(host, scope, project, home)
    for src in agent_sources(host):
        dest = d / src.name
        ours = dest.is_file() and not dest.is_symlink() and gen_agents.HEADER in dest.read_text(encoding="utf-8", errors="replace")
        if dest.exists() and not ours:
            out.append({"dest": str(dest), "result": "refused", "reason": "ملف بنفس الاسم ليس من نطاق؛ لن نلمسه."})
        elif remove:
            if ours and not dry:
                dest.unlink()
            out.append({"dest": str(dest), "result": ("dry_run_uninstall" if dry else "removed") if ours else "not_installed"})
        elif dry:
            out.append({"dest": str(dest), "result": "dry_run"})
        else:
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            out.append({"dest": str(dest), "result": "installed"})
    return out


def uninstall(p: dict) -> dict:
    dest = Path(p["dest"])
    if not dest.exists():
        return {**p, "result": "not_installed"}
    if dest.is_symlink() or not is_ours(dest):
        return {**p, "result": "refused", "reason": "ليس مجلد نطاق للمتاجر"}
    shutil.rmtree(dest)
    return {**p, "result": "removed"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="تثبيت مهارة نطاق للمتاجر")
    ap.add_argument("--host", choices=["claude-code", "codex", "both"], default="both")
    ap.add_argument("--scope", choices=["user", "project"], default="user")
    ap.add_argument("--project", type=Path, help="مجلد المشروع عند --scope project")
    ap.add_argument("--home", type=Path, default=Path(os.path.expanduser("~")), help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="اعرض الخطة بدون تنفيذ")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--agents", action="store_true", help="ثبّت أيضاً الوكلاء المتخصصين الاختياريين (للقراءة فقط)")
    a = ap.parse_args(argv)

    if not a.no_build and not a.uninstall:
        if build.main([]) != 0:
            print("فشل التحقق من الحزمة؛ لم يتم التثبيت.")
            return 1

    hosts = ["claude-code", "codex"] if a.host == "both" else [a.host]
    project = a.project.resolve() if a.project else None
    results, code = [], 0
    for h in hosts:
        p = plan(h, a.scope, project, a.home)
        if a.uninstall:
            r = uninstall(p) if not a.dry_run else {**p, "result": "dry_run_uninstall"}
        elif a.dry_run:
            r = {**p, "result": "dry_run"}
        else:
            r = apply(p, a.home)
        if r["result"] == "refused":
            code = 2
        results.append(r)
        if a.agents:
            for ar in agents_action(h, a.scope, project, a.home, a.uninstall, a.dry_run):
                print(f"[agent] {ar['dest']}: {ar['result']} {ar.get('reason', '')}".rstrip())
                if ar["result"] == "refused":
                    code = 2

    names = {"claude-code": "Claude Code", "codex": "Codex"}
    for r in results:
        print(f"[{names[r['host']]}] {r['dest']}: {r['result']} — {r.get('reason', '')}")
        if r.get("backup"):
            print(f"    نسخة احتياطية: {r['backup']}")
    if any(r["result"] == "installed" for r in results):
        print("\nتم التثبيت. أعد تشغيل الجلسة إن لم تظهر المهارة.")
        print("Claude Code: اكتب /nitaaq-store أو «نطاق للمتاجر».  Codex: اكتب $nitaaq-store أو اخترها من /skills.")
        print("المهارة لا تضيف اتصال سلة؛ اربط أداة التاجر (MCP) من إعدادات المضيف إن كانت متاحة لك.")
    return code


if __name__ == "__main__":
    sys.exit(main())
