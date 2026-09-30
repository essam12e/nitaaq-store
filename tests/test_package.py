"""Packaging, installation safety and documentation checks."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import _path
import build
import install

CLI = [sys.executable, str(_path.SKILL / "scripts" / "nitaaq_cli.py")]


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert build.main([]) == 0

    def test_core_valid(self):
        self.assertEqual(build.validate(), [])

    def test_core_frontmatter_is_portable(self):
        fm = build.parse_frontmatter(build.split_frontmatter((_path.SKILL / "SKILL.md").read_text(encoding="utf-8"))[0])
        self.assertLessEqual(set(fm), build.SPEC_FIELDS)
        self.assertEqual(fm["name"], "nitaaq-store")
        for phrase in ("نطاق للمتاجر", "استخدم مهارة نطاق", "استخدم مهارة استور", "استور"):
            self.assertIn(phrase, fm["description"])

    def test_yaml_parses_with_real_parser_when_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for p in (_path.SKILL / "SKILL.md", build.DIST / "claude-code/nitaaq-store/SKILL.md"):
            fm = yaml.safe_load(build.split_frontmatter(p.read_text(encoding="utf-8"))[0])
            self.assertEqual(fm["name"], "nitaaq-store")
        y = yaml.safe_load((build.DIST / "codex/nitaaq-store/agents/openai.yaml").read_text(encoding="utf-8"))
        self.assertEqual(y["interface"]["display_name"], "نطاق للمتاجر | Nitaaq Store")

    def test_host_packages(self):
        cc = build.parse_frontmatter(build.split_frontmatter((build.DIST / "claude-code/nitaaq-store/SKILL.md").read_text(encoding="utf-8"))[0])
        self.assertIn("argument-hint", cc)
        self.assertTrue((build.DIST / "codex/nitaaq-store/agents/openai.yaml").exists())
        self.assertFalse((build.DIST / "claude-code/nitaaq-store/agents").exists())
        with zipfile.ZipFile(build.DIST / "claude-ai/nitaaq-store.zip") as z:
            names = z.namelist()
            self.assertIn("nitaaq-store/SKILL.md", names)
            self.assertFalse(any("__pycache__" in n for n in names))
            fm = build.parse_frontmatter(build.split_frontmatter(z.read("nitaaq-store/SKILL.md").decode())[0])
            self.assertLessEqual(set(fm), build.SPEC_FIELDS)

    def test_validation_catches_problems(self):
        with tempfile.TemporaryDirectory() as d:
            core = Path(d) / "nitaaq-store"
            (core / "references").mkdir(parents=True)
            (core / "SKILL.md").write_text("---\nname: Nitaaq\ndescription: x\nwhen_to_use: y\n---\n[a](references/missing.md)\napi_key = \"abcdefghijklmnop\"\n", encoding="utf-8")
            errs = " ".join(build.validate(core))
            for s in ("non-portable", "invalid name", "broken link", "possible secret", "display name"):
                self.assertIn(s, errs)


class PluginTests(unittest.TestCase):
    def test_marketplace_and_plugin_consistent(self):
        mk = json.loads((_path.ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        entry = mk["plugins"][0]
        plugin_dir = (_path.ROOT / entry["source"]).resolve()
        pj = json.loads((plugin_dir / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["name"], pj["name"])
        self.assertNotIn("..", entry["source"])
        self.assertEqual(plugin_dir / "skills" / "nitaaq-store", _path.SKILL.resolve())
        fm = build.parse_frontmatter(build.split_frontmatter((_path.SKILL / "SKILL.md").read_text(encoding="utf-8"))[0])
        self.assertEqual(pj["version"], fm["metadata"]["version"])
        import nitaaq
        self.assertEqual(nitaaq.__version__, pj["version"])

    def test_claude_cli_validate_if_available(self):
        import shutil
        if not shutil.which("claude"):
            self.skipTest("claude CLI not installed")
        for target in (_path.ROOT, _path.ROOT / "plugins/nitaaq-store"):
            r = subprocess.run(["claude", "plugin", "validate", str(target)], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class EvalFileTests(unittest.TestCase):
    def test_offline_evals_pass(self):
        r = subprocess.run([sys.executable, str(_path.ROOT / "tools/run_evals.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class InstallTests(unittest.TestCase):
    def run_install(self, home, *args):
        return install.main(["--home", str(home), "--no-build", *args])

    def test_fresh_install_both_hosts(self):
        build.main([])
        with tempfile.TemporaryDirectory() as h:
            home = Path(h)
            other = home / ".claude/skills/other-skill"
            other.mkdir(parents=True)
            (other / "SKILL.md").write_text("---\nname: other-skill\ndescription: x\n---\n")
            mcp = home / ".claude.json"
            mcp.write_text('{"mcpServers": {"x": {}}}')
            codex_cfg = home / ".codex/config.toml"
            codex_cfg.parent.mkdir()
            codex_cfg.write_text("[mcp_servers.x]\n")
            before = (mcp.read_text(), codex_cfg.read_text(), (other / "SKILL.md").read_text())
            self.assertEqual(self.run_install(home), 0)
            self.assertTrue((home / ".claude/skills/nitaaq-store/SKILL.md").exists())
            self.assertTrue((home / ".agents/skills/nitaaq-store/agents/openai.yaml").exists())
            self.assertEqual(before, (mcp.read_text(), codex_cfg.read_text(), (other / "SKILL.md").read_text()))

    def test_upgrade_backs_up_outside_skills(self):
        build.main([])
        with tempfile.TemporaryDirectory() as h:
            home = Path(h)
            self.run_install(home, "--host", "claude-code")
            (home / ".claude/skills/nitaaq-store/local-note.txt").write_text("x")
            self.assertEqual(self.run_install(home, "--host", "claude-code"), 0)
            backups = list((home / ".nitaaq-store/backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "local-note.txt").exists())
            self.assertEqual([p.name for p in (home / ".claude/skills").iterdir()], ["nitaaq-store"])

    def test_refuses_foreign_directory(self):
        with tempfile.TemporaryDirectory() as h:
            home = Path(h)
            foreign = home / ".claude/skills/nitaaq-store"
            foreign.mkdir(parents=True)
            (foreign / "SKILL.md").write_text("---\nname: something-else\ndescription: x\n---\n")
            self.assertEqual(self.run_install(home, "--host", "claude-code"), 2)
            self.assertIn("something-else", (foreign / "SKILL.md").read_text())
            self.assertEqual(self.run_install(home, "--host", "claude-code", "--uninstall"), 2)
            self.assertTrue(foreign.exists())

    def test_dry_run_and_project_scope(self):
        build.main([])
        with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as p:
            self.assertEqual(self.run_install(Path(h), "--scope", "project", "--project", p, "--dry-run"), 0)
            self.assertFalse((Path(p) / ".claude").exists())
            self.assertEqual(self.run_install(Path(h), "--scope", "project", "--project", p), 0)
            self.assertTrue((Path(p) / ".claude/skills/nitaaq-store/SKILL.md").exists())
            self.assertTrue((Path(p) / ".agents/skills/nitaaq-store/SKILL.md").exists())
            self.assertEqual(self.run_install(Path(h), "--scope", "project", "--project", p, "--uninstall"), 0)
            self.assertFalse((Path(p) / ".claude/skills/nitaaq-store").exists())


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        r = subprocess.run(CLI + list(args), capture_output=True, text=True, cwd=_path.SKILL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_capabilities_and_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "cap.json"
            self.run_cli("capabilities", "build", "--tools", str(_path.FIX / "tools_merchant.json"), "--out", str(out))
            self.run_cli("capabilities", "evidence", "--map", str(out), "--op", "products.update", "--level", "tested",
                         "--note", "patched name on a sandbox product")
            self.assertEqual(json.loads(out.read_text())["operations"]["products.update"]["evidence_level"], "tested")

    def test_export_report_md(self):
        md = self.run_cli("analyze-export", str(_path.FIX / "orders_export.csv"), "--report", "--statuses", "مكتمل", "--md")
        self.assertIn("ملخص المبيعات", md)
        self.assertIn("827.00", md)
        self.assertIn("ليس صافي الربح", md)

    def test_audit_html_and_tracking(self):
        out = json.loads(self.run_cli("audit-html", str(_path.FIX / "product_page.html"), "--url", "https://rayhan.example/p12345"))
        self.assertIn("price_mismatch", [f["id"] for f in out["findings"]])
        t = json.loads(self.run_cli("tracking", "--html", str(_path.FIX / "product_page.html"), "--har", str(_path.FIX / "network.har")))
        self.assertEqual(t["ga4"]["level"], 2)

    def test_small_commands(self):
        self.assertIn('"activate": true', self.run_cli("activation", "استخدم مهارة استور"))
        self.assertIn('"target": 8', self.run_cli("stock-plan", "--mode", "increment", "--current", "5", "--value", "3"))
        self.assertIn('"sale_price": "150"', self.run_cli("price-check", "--phrase", "كان 200 وصار 150"))
        self.assertIn("reconcile_first", self.run_cli("classify-error", "timeout", "--write"))

    def test_error_exit(self):
        r = subprocess.run(CLI + ["stock-plan", "--mode", "decrement", "--current", "x", "--value", "1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("error", r.stdout)


class DocsTests(unittest.TestCase):
    def test_arabic_readme_and_docs(self):
        readme = (_path.ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("نطاق للمتاجر | Nitaaq Store", readme)
        arabic = len(re.findall("[؀-ۿ]", readme))
        self.assertGreater(arabic, 1500)
        for doc in ("install.md", "modes.md", "capabilities.md", "limitations.md"):
            self.assertTrue((_path.ROOT / "docs/ar" / doc).exists(), doc)

    def test_scope_exclusions_documented(self):
        s = (_path.SKILL / "SKILL.md").read_text(encoding="utf-8") + (_path.SKILL / "references/scope.md").read_text(encoding="utf-8")
        for term in ("Salla apps", "partner apps", "marketplace", "shipping", "Twilight", "Partners Portal"):
            self.assertIn(term, s)

    def test_no_invented_live_claims(self):
        for f in _path.ROOT.rglob("*.md"):
            if "dist" in f.parts:
                continue
            text = f.read_text(encoding="utf-8").lower()
            self.assertNotIn("60 working reports", text)
            self.assertNotIn("live verified against", text)

    def test_every_catalog_op_has_arabic_label(self):
        cat = json.loads((_path.SKILL / "assets/operation-catalog.json").read_text(encoding="utf-8"))
        ids = [o["id"] for o in cat["operations"]]
        self.assertEqual(len(ids), len(set(ids)))
        for o in cat["operations"]:
            self.assertRegex(o["label_ar"], "[؀-ۿ]")
            self.assertIn(o["access"], ("read", "write", "destructive"))


if __name__ == "__main__":
    unittest.main()
