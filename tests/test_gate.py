"""Phase 2b: the Claude Code write gate (PreToolUse hook)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _path import ROOT, SKILL

import mock_merchant_mcp as mock
from nitaaq import approvals, capabilities, gate

CLI = SKILL / "scripts" / "nitaaq_cli.py"
HOOK = ROOT / "plugins" / "nitaaq-store" / "hooks" / "write_gate.py"
HOOKS_JSON = ROOT / "plugins" / "nitaaq-store" / "hooks" / "hooks.json"
STORE = "777"
READS = ["get_store_info", "list_products", "get_product", "list_orders", "get_order", "list_order_statuses"]
WRITES = ["create_product", "update_product", "update_product_quantity", "update_order_status"]


def salla(name):
    return "mcp__salla__" + name


def tools():
    return [dict(t, name=salla(t["name"])) for t in mock.TOOLS]


class GateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)
        self.root = self.cwd / ".nitaaq"
        self.root.mkdir()
        self.cmap = capabilities.build_map(tools())
        (self.root / "capabilities.json").write_text(json.dumps(self.cmap), encoding="utf-8")
        (self.root / "tools.json").write_text(json.dumps(tools()), encoding="utf-8")
        self.env = {}

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, name, inp=None, **extra):
        ev = {"tool_name": name, "tool_input": inp or {}, "cwd": str(self.cwd)}
        ev.update(extra)
        return gate.decide(ev, gate.candidate_roots(str(self.cwd), self.env), self.env)

    def approve(self, after=None, before=None):
        items = [{"entity_id": "101", "before": before or {"price": 249}, "after": after or {"price": 219}}]
        rec = approvals.ApprovalStore.for_store(self.root, STORE).grant(STORE, "products.update", items,
                                                                        merchant_words="موافق على 219")
        return rec, items

    def arm(self, rec, items, inp, fresh=None, **kw):
        return gate.arm(self.root, STORE, rec["approval_id"], "products.update", items,
                        fresh if fresh is not None else {"101": {"id": 101, "price": 249}},
                        salla("update_product"), inp, **kw)


class Classification(GateBase):
    def test_no_read_tool_is_denied(self):
        for n in READS:
            r = self.decide(salla(n), {"id": 101})
            self.assertEqual(r["decision"], "pass", (n, r))
            self.assertEqual(r["class"]["kind"], "read", n)

    def test_every_write_tool_is_denied_without_approval(self):
        for n in WRITES:
            r = self.decide(salla(n), {"id": 101, "price": 1})
            self.assertEqual(r["decision"], "deny", n)
            self.assertIn("بوابة نطاق", r["reason"])
            self.assertIn("gate arm", r["reason"])

    def test_other_servers_and_partners_are_not_gated(self):
        self.assertEqual(self.decide("mcp__github__create_issue", {"title": "x"})["decision"], "pass")
        self.assertEqual(self.decide("mcp__salla__salla_apps", {"action": "create"})["decision"], "pass")
        self.assertEqual(self.decide("Bash", {"command": "ls"})["decision"], "pass")

    def test_unknown_salla_tool_is_denied_and_asks_for_map(self):
        r = self.decide(salla("sync_catalog"), {"x": 1})
        self.assertEqual(r["decision"], "deny")
        self.assertEqual(r["class"]["kind"], "unknown")
        self.assertIn("capabilities build", r["reason"])
        # an unmapped tool whose name only reads still passes
        self.assertEqual(self.decide(salla("list_coupons"))["decision"], "pass")

    def test_read_only_annotation_passes_unmapped_tool(self):
        extra = tools() + [{"name": salla("store_health"), "description": "x", "annotations": {"readOnlyHint": True}}]
        (self.root / "tools.json").write_text(json.dumps(extra), encoding="utf-8")
        self.assertEqual(self.decide(salla("store_health"))["decision"], "pass")

    def test_action_tool_classified_by_action(self):
        raw = [{"name": salla("products"), "description": "Manage store products.",
                "inputSchema": {"type": "object", "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "update"]}, "id": {"type": "integer"}}}}]
        cmap = capabilities.build_map(raw)
        self.assertEqual(gate.classify(salla("products"), {"action": "list"}, cmap)["kind"], "read")
        self.assertEqual(gate.classify(salla("products"), {"action": "update", "id": 1}, cmap)["kind"], "write")

    def test_without_map_salla_server_is_still_gated(self):
        (self.root / "capabilities.json").unlink()
        (self.root / "tools.json").unlink()
        self.assertEqual(self.decide(salla("update_product"), {"id": 1})["decision"], "deny")
        self.assertEqual(self.decide(salla("list_products"))["decision"], "pass")

    def test_subagents_cannot_call_store_tools(self):
        r = self.decide(salla("list_orders"), agent_id="a1", agent_type="nitaaq-store:nitaaq-store-analytics")
        self.assertEqual(r["decision"], "deny")
        self.assertIn("الأدلة المحفوظة", r["reason"])
        self.assertEqual(self.decide("mcp__github__get_me", agent_id="a1")["decision"], "pass")

    def test_off_switch_only_from_environment(self):
        self.env = {"NITAAQ_WRITE_GATE": "off"}
        self.assertEqual(self.decide(salla("update_product"), {"id": 1})["decision"], "pass")


class Arming(GateBase):
    def test_armed_exact_call_passes_once(self):
        rec, items = self.approve()
        inp = {"id": 101, "price": 219}
        a = self.arm(rec, items, inp)
        self.assertTrue(a["ok"], a)
        r = self.decide(salla("update_product"), inp)
        self.assertEqual(r["decision"], "pass")
        self.assertEqual(r["approval_id"], rec["approval_id"])
        self.assertEqual(self.decide(salla("update_product"), inp)["decision"], "deny")  # consumed

    def test_changed_input_or_other_tool_is_denied(self):
        rec, items = self.approve()
        self.assertTrue(self.arm(rec, items, {"id": 101, "price": 219})["ok"])
        self.assertEqual(self.decide(salla("update_product"), {"id": 101, "price": 199})["decision"], "deny")
        self.assertEqual(self.decide(salla("update_product"), {"id": 101, "price": 219, "name": "x"})["decision"], "deny")
        self.assertEqual(self.decide(salla("update_product_quantity"), {"id": 101, "price": 219})["decision"], "deny")

    def test_arm_refuses_input_that_does_not_carry_the_approved_change(self):
        rec, items = self.approve()
        a = self.arm(rec, items, {"id": 101, "price": 199})
        self.assertFalse(a["ok"])
        self.assertIn("value_not_in_input", a["reasons"])
        a = self.arm(rec, items, {"id": 102, "price": 219})
        self.assertIn("entity_not_in_input", a["reasons"])

    def test_arm_refuses_stale_snapshot_and_bad_approvals(self):
        rec, items = self.approve()
        a = self.arm(rec, items, {"id": 101, "price": 219}, fresh={"101": {"id": 101, "price": 230}})
        self.assertFalse(a["ok"])
        self.assertIn("stale_snapshot", a["approval"]["reasons"])
        st = approvals.ApprovalStore.for_store(self.root, STORE)
        st.revoke(rec["approval_id"])
        self.assertFalse(self.arm(rec, items, {"id": 101, "price": 219})["ok"])
        other = gate.arm(self.root, STORE, "ap_missing", "products.update", items, {"101": {"price": 249}},
                         salla("update_product"), {"id": 101, "price": 219})
        self.assertFalse(other["ok"])

    def test_arm_on_other_store_fails(self):
        rec, items = self.approve()
        a = gate.arm(self.root, "888", rec["approval_id"], "products.update", items, {"101": {"price": 249}},
                     salla("update_product"), {"id": 101, "price": 219})
        self.assertFalse(a["ok"])

    def test_double_arm_for_same_entity_refused_until_used(self):
        rec, items = self.approve()
        inp = {"id": 101, "price": 219}
        self.assertTrue(self.arm(rec, items, inp)["ok"])
        self.assertEqual(self.arm(rec, items, inp)["reasons"], ["already_armed"])
        self.decide(salla("update_product"), inp)
        self.assertTrue(self.arm(rec, items, inp)["ok"])  # approval itself still decides (not marked used yet)

    def test_armed_token_expires(self):
        rec, items = self.approve()
        inp = {"id": 101, "price": 219}
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.assertTrue(self.arm(rec, items, inp, now=past)["ok"])
        self.assertEqual(self.decide(salla("update_product"), inp)["decision"], "deny")

    def test_disarm(self):
        rec, items = self.approve()
        inp = {"id": 101, "price": 219}
        tok = self.arm(rec, items, inp)["token"]
        self.assertTrue(gate.disarm(self.root, tok))
        self.assertEqual(self.decide(salla("update_product"), inp)["decision"], "deny")

    def test_eval_no_write_passes_without_matching_approval(self):
        """Acceptance: 0 writes without a matching approval, 0 wrongly denied reads."""
        rec, items = self.approve()
        armed = {"id": 101, "price": 219}
        self.assertTrue(self.arm(rec, items, armed)["ok"])
        attempts = [(salla(n), {"id": i, "price": p}) for n in WRITES for i in (101, 102) for p in (199, 219, 300)]
        passed = [(n, x) for n, x in attempts if self.decide(n, x)["decision"] == "pass"]
        self.assertEqual(passed, [(salla("update_product"), armed)])
        denied_reads = [n for n in READS if self.decide(salla(n), {"id": 101})["decision"] == "deny"]
        self.assertEqual(denied_reads, [])


class HookScript(GateBase):
    def run_hook(self, event, env_extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NITAAQ_")}
        env.update(env_extra or {})
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event, ensure_ascii=False),
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_hooks_json_points_at_script(self):
        cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        entry = cfg["hooks"]["PreToolUse"][0]
        self.assertEqual(entry["matcher"], "mcp__.*")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/write_gate.py", entry["hooks"][0]["command"])
        self.assertTrue(HOOK.exists())

    def test_hook_denies_write_and_stays_silent_on_read(self):
        out = self.run_hook({"tool_name": salla("update_product"), "tool_input": {"id": 101, "price": 1},
                             "cwd": str(self.cwd), "hook_event_name": "PreToolUse"})
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["permissionDecision"], "deny")
        self.assertIn("بوابة نطاق", d["permissionDecisionReason"])
        self.assertEqual(self.run_hook({"tool_name": salla("list_orders"), "tool_input": {}, "cwd": str(self.cwd)}), "")

    def test_hook_never_auto_allows(self):
        rec, items = self.approve()
        inp = {"id": 101, "price": 219}
        self.assertTrue(self.arm(rec, items, inp)["ok"])
        out = self.run_hook({"tool_name": salla("update_product"), "tool_input": inp, "cwd": str(self.cwd)})
        self.assertEqual(out, "")  # passes to the normal permission prompt, no "allow"

    def test_hook_fails_closed_for_salla_only(self):
        (self.root / "capabilities.json").write_text("{not json", encoding="utf-8")
        out = self.run_hook({"tool_name": salla("list_orders"), "tool_input": {}, "cwd": str(self.cwd)})
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.run_hook({"tool_name": "mcp__github__get_me", "tool_input": {}, "cwd": str(self.cwd)}), "")
        self.assertEqual(self.run_hook({"garbage": True}), "")

    def test_cli_arm(self):
        rec, items = self.approve()
        f = self.cwd / "items.json"; f.write_text(json.dumps(items), encoding="utf-8")
        fr = self.cwd / "fresh.json"; fr.write_text(json.dumps({"101": {"price": 249}}), encoding="utf-8")
        inp = self.cwd / "call.json"; inp.write_text(json.dumps({"id": 101, "price": 219}), encoding="utf-8")
        p = subprocess.run([sys.executable, str(CLI), "gate", "arm", "--store-id", STORE, "--id", rec["approval_id"],
                            "--op", "products.update", "--items", str(f), "--fresh", str(fr), "--tool", salla("update_product"),
                            "--input", str(inp), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["ok"])
        self.assertEqual(self.run_hook({"tool_name": salla("update_product"), "tool_input": {"id": 101, "price": 219},
                                        "cwd": str(self.cwd)}), "")


if __name__ == "__main__":
    unittest.main()
