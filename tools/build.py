#!/usr/bin/env python3
"""Validate the shared skill core and build host packages.

dist/claude-code/nitaaq-store/   core + Claude Code-only frontmatter fields
dist/codex/nitaaq-store/         core + agents/openai.yaml
dist/claude-ai/nitaaq-store.zip  core with spec-only frontmatter (upload)

Standard library only. Exit code 1 on any validation error.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skill" / "nitaaq-store"
DIST = ROOT / "dist"
ADAPTERS = ROOT / "adapters"

SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".nitaaq", ".DS_Store")
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*['\"][^'\"\s]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter")
    end = text.index("\n---\n", 4)
    return text[4:end], text[end + 5:]


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return inner.replace('\\"', '"') if v[0] == '"' else inner.replace("''", "'")
    return v


def parse_frontmatter(fm: str) -> dict:
    """Parse the small YAML subset used here: scalars and one-level maps."""
    data: dict = {}
    current = None
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if current is None:
                raise ValueError(f"unexpected indentation: {line!r}")
            k, _, v = line.strip().partition(":")
            data[current][k.strip()] = _scalar(v)
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise ValueError(f"bad frontmatter line: {line!r}")
        k = k.strip()
        if v.strip() == "":
            data[k] = {}
            current = k
        else:
            data[k] = _scalar(v)
            current = None
    return data


def dump_frontmatter(data: dict) -> str:
    out = []
    for k, v in data.items():
        if isinstance(v, dict):
            out.append(f"{k}:")
            out += [f"  {mk}: {json.dumps(mv, ensure_ascii=False)}" for mk, mv in v.items()]
        else:
            out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(out)


def validate(core: Path = CORE) -> list[str]:
    errors = []
    skill = core / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    try:
        fm_text, body = split_frontmatter(text)
        fm = parse_frontmatter(fm_text)
    except ValueError as e:
        return [str(e)]
    extra = set(fm) - SPEC_FIELDS
    if extra:
        errors.append(f"non-portable frontmatter fields in core: {sorted(extra)}")
    name = fm.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
        errors.append(f"invalid name: {name!r}")
    if name != core.name:
        errors.append(f"name {name!r} must match directory {core.name!r}")
    desc = fm.get("description", "")
    if not (1 <= len(desc) <= 1024):
        errors.append(f"description length {len(desc)} not in 1..1024")
    if "compatibility" in fm and not (1 <= len(fm["compatibility"]) <= 500):
        errors.append("compatibility must be 1..500 chars")
    if isinstance(fm.get("metadata"), dict):
        for k, v in fm["metadata"].items():
            if not isinstance(v, str):
                errors.append(f"metadata.{k} must be a string")
    elif "metadata" in fm:
        errors.append("metadata must be a map")
    lines = text.count("\n") + 1
    if lines > 500:
        errors.append(f"SKILL.md has {lines} lines (keep under 500)")
    for md in [skill, *sorted((core / "references").glob("*.md"))]:
        for link in re.findall(r"\]\(((?!https?:)[^)#]+)\)", md.read_text(encoding="utf-8")):
            target = (md.parent / link).resolve()
            if not target.exists():
                errors.append(f"broken link in {md.relative_to(core)}: {link}")
    for f in core.rglob("*"):
        if f.is_file() and f.suffix in {".md", ".py", ".json", ".yaml", ".yml", ".txt"}:
            content = f.read_text(encoding="utf-8", errors="replace")
            for pat in SECRET_PATTERNS:
                if pat.search(content):
                    errors.append(f"possible secret in {f.relative_to(core)}")
    if "نطاق للمتاجر | Nitaaq Store" not in text:
        errors.append("display name missing from SKILL.md")
    return errors


def _copy_core(dest: Path):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(CORE, dest, ignore=IGNORE)


def build_claude_code() -> Path:
    dest = DIST / "claude-code" / "nitaaq-store"
    _copy_core(dest)
    overlay = json.loads((ADAPTERS / "claude-code" / "frontmatter-overlay.json").read_text(encoding="utf-8"))
    overlay = {k: v for k, v in overlay.items() if not k.startswith("_")}
    skill = dest / "SKILL.md"
    fm_text, body = split_frontmatter(skill.read_text(encoding="utf-8"))
    fm = parse_frontmatter(fm_text)
    fm.update(overlay)
    skill.write_text("---\n" + dump_frontmatter(fm) + "\n---\n" + body, encoding="utf-8")
    return dest


def build_codex() -> Path:
    dest = DIST / "codex" / "nitaaq-store"
    _copy_core(dest)
    (dest / "agents").mkdir(exist_ok=True)
    shutil.copy2(ADAPTERS / "codex" / "agents" / "openai.yaml", dest / "agents" / "openai.yaml")
    return dest


def build_claude_ai() -> Path:
    out = DIST / "claude-ai" / "nitaaq-store.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(CORE.rglob("*")):
            rel = f.relative_to(CORE)
            if f.is_file() and "__pycache__" not in rel.parts and ".nitaaq" not in rel.parts and f.suffix != ".pyc":
                z.write(f, Path("nitaaq-store") / rel)
    return out


def main(argv=None) -> int:
    errors = validate()
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(" -", e)
        return 1
    print("core valid")
    for fn in (build_claude_code, build_codex, build_claude_ai):
        print("built", fn().relative_to(ROOT))
    # every built SKILL.md must still parse
    for p in (DIST / "claude-code" / "nitaaq-store", DIST / "codex" / "nitaaq-store"):
        parse_frontmatter(split_frontmatter((p / "SKILL.md").read_text(encoding="utf-8"))[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
