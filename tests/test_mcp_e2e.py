"""End-to-end protocol tests over a real MCP stdio transport (mock server).

These prove the skill's helpers compose correctly with an MCP connection
and injected faults. They are controlled-environment tests: they may justify
the "tested" evidence level for the mock's operations, never "live_verified".
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _path
from nitaaq import capabilities, errors, products, writes

SERVER = Path(__file__).resolve().parent / "mock_merchant_mcp.py"


class StdioMCP:
    def __init__(self, faults=()):
        env = dict(os.environ, MOCK_FAULTS=",".join(faults))
        self.p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, encoding="utf-8", env=env)
        self.n = 0
        init = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                           "clientInfo": {"name": "nitaaq-tests", "version": "1"}})
        assert init["serverInfo"]["name"] == "mock-merchant"
        self.notify("notifications/initialized")

    def notify(self, method):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        assert resp["id"] == self.n
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]

    def tools(self):
        return self.request("tools/list")["tools"]

    def call(self, tool_name, /, **args):
        """Returns (ok, data_or_error_text)."""
        r = self.request("tools/call", {"name": tool_name, "arguments": args})
        text = r["content"][0]["text"]
        if r.get("isError"):
            return False, text
        return True, json.loads(text)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
        self.p.stdout.close()


class E2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = writes.Ledger(Path(self.tmp.name) / "ledger.json")

    def tearDown(self):
        self.tmp.cleanup()

    def mcp(self, *faults):
        m = StdioMCP(faults)
        self.addCleanup(m.close)
        return m

    def tool_for(self, cmap, op):
        entry = cmap["operations"][op]
        self.assertEqual(entry["status"], "mapped", op)
        return entry["candidates"][0]["tool"]

    def test_discovery_over_mcp(self):
        m = self.mcp()
        cmap = capabilities.build_map(m.tools())
        self.assertEqual(cmap["connection"]["kind"], "merchant")
        self.assertEqual(self.tool_for(cmap, "products.update"), "update_product")
        self.assertEqual(self.tool_for(cmap, "inventory.set"), "update_product_quantity")
        self.assertEqual(self.tool_for(cmap, "orders.statuses"), "list_order_statuses")
        self.assertEqual(self.tool_for(cmap, "store.info"), "get_store_info")
        self.assertEqual(cmap["operations"]["products.delete"]["status"], "unavailable")

    def test_update_with_readback_and_evidence(self):
        m = self.mcp()
        cmap = capabilities.build_map(m.tools())
        ok, snap = m.call("get_product", id=101)
        patch = writes.build_patch(snap, {"metadata_title": "عطر عود ملكي 100 مل | متجر الريحان", "price": 249},
                                   ["metadata_title", "price"])
        self.assertEqual(list(patch), ["metadata_title"])  # unchanged price is not sent
        _, fresh = m.call("get_product", id=101)
        self.assertEqual(writes.detect_conflicts(snap, fresh, list(patch)), [])
        key = writes.operation_key("products.update", "101", patch)
        self.assertEqual(self.ledger.begin(key, "products.update", "101"), "go")
        ok, _ = m.call(self.tool_for(cmap, "products.update"), id=101, **patch)
        self.assertTrue(ok)
        self.ledger.finish(key, "succeeded", "101")
        _, readback = m.call("get_product", id=101)
        v = writes.verify_readback(patch, readback)
        self.assertTrue(v["verified"])
        self.assertEqual(readback["description"], "عطر شرقي.")  # omitted field preserved
        e = capabilities.record_evidence(cmap, "products.update", "tested",
                                         "mock MCP over stdio: patch metadata_title, readback matched")
        self.assertEqual(e["evidence_level"], "tested")
        with self.assertRaises(ValueError):
            capabilities.record_evidence(cmap, "products.update", "live_verified", "mock MCP readback", store_id="777")
        # retrying the same approved change is a no-op
        self.assertEqual(self.ledger.begin(key, "products.update", "101"), "done")

    def test_conflict_stops_write(self):
        m = self.mcp("price_race")
        _, snap = m.call("get_product", id=101)
        patch = writes.build_patch(snap, {"sale_price": 179}, ["sale_price"])
        _, fresh = m.call("get_product", id=101)
        conflicts = writes.detect_conflicts(snap, fresh, ["price", "sale_price"])
        self.assertEqual([c["field"] for c in conflicts], ["price"])
        # protocol: stop and ask; nothing written
        _, after = m.call("get_product", id=101)
        self.assertEqual(after["sale_price"], 199)
        self.assertTrue(patch)

    def test_ambiguous_create_is_reconciled_not_duplicated(self):
        m = self.mcp("create_ambiguous")
        cmap = capabilities.build_map(m.tools())
        payload = {"name": "بخور كمبودي فاخر", "price": 120, "product_type": "product", "quantity": 10}
        self.assertTrue(products.check_prices(payload["price"]).ok)
        key = writes.operation_key("products.create", None, payload)
        self.assertEqual(self.ledger.begin(key, "products.create", None), "go")
        ok, err = m.call(self.tool_for(cmap, "products.create"), **payload)
        self.assertFalse(ok)
        c = errors.classify(err, write=True, idempotent=cmap["operations"]["products.create"]["idempotent"])
        self.assertEqual((c.kind, c.retry), ("network_ambiguous", "reconcile_first"))
        self.ledger.finish(key, "unknown")
        self.assertEqual(self.ledger.begin(key, "products.create", None), "reconcile_first")
        _, listing = m.call("list_products", per_page=100)
        found = writes.find_existing_by_name(listing["data"], payload["name"])
        self.assertEqual(len(found), 1)
        self.ledger.reconcile(key, str(found[0]["id"]))
        self.assertEqual(self.ledger.begin(key, "products.create", None), "done")
        _, listing = m.call("list_products", per_page=100)
        self.assertEqual(len(writes.find_existing_by_name(listing["data"], payload["name"])), 1)

    def test_restock_with_concurrent_sale(self):
        m = self.mcp("sale_after_write")
        cmap = capabilities.build_map(m.tools())
        _, prod = m.call("get_product", id=102)
        plan = products.plan_stock_change("increment", prod["quantity"], 10)
        self.assertTrue(plan.ok)
        ok, _ = m.call(self.tool_for(cmap, "inventory.set"), product_id=102, quantity=plan.target)
        self.assertTrue(ok)
        _, after = m.call("get_product", id=102)
        r = products.reconcile_stock_readback(plan, after["quantity"], sold_since=1)
        self.assertEqual(r["status"], "persisted_with_sales")
        # status is still "out": restocking alone does not make it sellable
        s = products.sellability(after)
        self.assertFalse(s["sellable"])
        _, fixed = m.call("update_product", id=102, status="sale")
        self.assertTrue(products.sellability(fixed)["sellable"])

    def test_permission_and_expiry(self):
        m = self.mcp("status_forbidden")
        _, statuses = m.call("list_order_statuses")
        target = next(s["id"] for s in statuses if s["name"] == "تم الشحن")  # resolved, not guessed
        ok, err = m.call("update_order_status", order_id=1004, status_id=target)
        self.assertFalse(ok)
        self.assertEqual(errors.classify(err, write=True).kind, "permission_denied")
        m2 = self.mcp("expired")
        ok, err = m2.call("list_orders")
        self.assertFalse(ok)
        self.assertEqual(errors.classify(err).kind, "auth_expired")


if __name__ == "__main__":
    unittest.main()
