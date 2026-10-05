#!/usr/bin/env python3
"""A small mock merchant MCP server (stdio, JSON-RPC 2.0, newline-delimited).

Test infrastructure only. It is NOT Salla's API, and its tool names are
invented for the tests. It lets the tests drive the skill's protocol
(discover -> read -> patch -> write -> verify) through a real MCP transport
and inject faults.

Faults (comma-separated in MOCK_FAULTS):
  create_ambiguous    create_product applies the write, then reports a timeout
  price_race          after the first get_product, someone else changes the price
  sale_after_write    one unit sells right after update_product_quantity
  status_forbidden    update_order_status returns 403
  expired             every call returns 401 token expired
  orders_truncated    list_orders reports the full total but returns no rows after page 1

Optional order history (default behavior is unchanged without it):
  MOCK_ORDERS_FILE    JSON list of orders served by list_orders instead of the built-in one
  MOCK_PAGE_SIZE      page size for list_orders (default: everything on one page)
"""

import json
import os
import sys

FAULTS = set(filter(None, os.environ.get("MOCK_FAULTS", "").split(",")))
PROFILE = os.environ.get("MOCK_PROFILE", "merchant")  # "merchant" or "partners"
STATE_FILE = os.environ.get("MOCK_STATE_FILE")  # dump state after every call (for evals)
ORDERS_FILE = os.environ.get("MOCK_ORDERS_FILE")
PAGE_SIZE = int(os.environ.get("MOCK_PAGE_SIZE", "0"))

STATE = {
    "store": {"id": 777, "name": "متجر الريحان (تجريبي)"},
    "products": {
        101: {"id": 101, "name": "عطر عود ملكي 100 مل", "price": 249, "sale_price": 199, "quantity": 5,
              "status": "sale", "metadata_title": "", "description": "عطر شرقي."},
        102: {"id": 102, "name": "مسك أبيض", "price": 150, "sale_price": None, "quantity": 0,
              "status": "out", "metadata_title": "مسك أبيض", "description": ""},
    },
    "orders": {1004: {"id": 1004, "status_id": 1, "total": 250, "items": [{"product_id": 102, "quantity": 1}]}},
    "statuses": [{"id": 1, "name": "بإنتظار المراجعة"}, {"id": 2, "name": "قيد التنفيذ"}, {"id": 3, "name": "تم الشحن"}],
    "next_id": 103,
    "reads": 0,
}


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


TOOLS = [
    {"name": "get_store_info", "description": "Get the connected store's info.", "inputSchema": obj({}),
     "annotations": {"readOnlyHint": True}},
    {"name": "list_products", "description": "List store products with pagination.",
     "inputSchema": obj({"page": {"type": "integer"}, "per_page": {"type": "integer"}, "keyword": {"type": "string"}}),
     "annotations": {"readOnlyHint": True}},
    {"name": "get_product", "description": "Get one product by id.", "inputSchema": obj({"id": {"type": "integer"}}, ["id"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "create_product", "description": "Create a product.",
     "inputSchema": obj({"name": {"type": "string"}, "price": {"type": "number"}, "sale_price": {"type": "number"},
                         "product_type": {"type": "string", "enum": ["product", "service"]}, "quantity": {"type": "integer"}},
                        ["name", "price", "product_type", "quantity"])},
    {"name": "update_product", "description": "Update product fields; omitted fields are kept.",
     "inputSchema": obj({"id": {"type": "integer"}, "name": {"type": "string"}, "price": {"type": "number"},
                         "sale_price": {"type": "number"}, "metadata_title": {"type": "string"},
                         "description": {"type": "string"}, "status": {"type": "string", "enum": ["sale", "out", "hidden"]}}, ["id"]),
     "annotations": {"idempotentHint": True}},
    {"name": "update_product_quantity", "description": "Set the stock quantity of a product.",
     "inputSchema": obj({"product_id": {"type": "integer"}, "quantity": {"type": "integer"}}, ["product_id", "quantity"]),
     "annotations": {"idempotentHint": True}},
    {"name": "list_orders", "description": "List orders.", "inputSchema": obj({"page": {"type": "integer"}}),
     "annotations": {"readOnlyHint": True}},
    {"name": "get_order", "description": "Get order details.", "inputSchema": obj({"id": {"type": "integer"}}, ["id"]),
     "annotations": {"readOnlyHint": True}},
    {"name": "list_order_statuses", "description": "Store-specific order statuses.", "inputSchema": obj({}),
     "annotations": {"readOnlyHint": True}},
    {"name": "update_order_status", "description": "Change an order's status. May notify the customer.",
     "inputSchema": obj({"order_id": {"type": "integer"}, "status_id": {"type": "integer"}}, ["order_id", "status_id"])},
]


PARTNERS_TOOLS = [
    {"name": n, "description": d, "inputSchema": obj({"action": {"type": "string", "enum": ["list", "get", "create", "update"]}})}
    for n, d in [("salla_apps", "Manage your partner apps in the Salla Partners Portal."),
                 ("salla_events", "Subscribe partner apps to store events (webhooks)."),
                 ("salla_scopes", "Get or set the OAuth scopes a partner app requests.")]
]


class ToolError(Exception):
    pass


def call(name, a):
    if "expired" in FAULTS:
        raise ToolError("401 Unauthorized: token expired")
    p = STATE["products"]
    if name == "get_store_info":
        return STATE["store"]
    if name == "list_products":
        items = sorted(p.values(), key=lambda x: x["id"])
        if a.get("keyword"):
            items = [x for x in items if a["keyword"] in x["name"]]
        per, page = a.get("per_page", 50), a.get("page", 1)
        return {"data": items[(page - 1) * per: page * per], "pagination": {"total": len(items), "page": page, "per_page": per}}
    if name == "get_product":
        prod = p.get(a["id"])
        if not prod:
            raise ToolError("404 Product not found")
        out = dict(prod)
        STATE["reads"] += 1
        if "price_race" in FAULTS and STATE["reads"] == 1:
            prod["price"] = 259  # another admin edits it after our first read
        return out
    if name == "create_product":
        for k in ("name", "price", "product_type", "quantity"):
            if k not in a:
                raise ToolError(f"422 The {k} field is required")
        pid = STATE["next_id"]
        STATE["next_id"] += 1
        p[pid] = {"id": pid, "status": "sale", "metadata_title": "", "description": "", "sale_price": None, **a}
        if "create_ambiguous" in FAULTS:
            raise ToolError("504 Gateway Timeout: upstream timed out")
        return p[pid]
    if name == "update_product":
        prod = p.get(a["id"])
        if not prod:
            raise ToolError("404 Product not found")
        prod.update({k: v for k, v in a.items() if k != "id"})
        return prod
    if name == "update_product_quantity":
        prod = p[a["product_id"]]
        prod["quantity"] = a["quantity"]
        if "sale_after_write" in FAULTS:
            prod["quantity"] -= 1
        return {"product_id": prod["id"], "quantity": a["quantity"]}
    if name == "list_orders":
        rows = list(STATE["orders"].values())
        if ORDERS_FILE:
            with open(ORDERS_FILE, encoding="utf-8") as fh:
                rows = json.load(fh)
        if not PAGE_SIZE:
            return {"data": rows, "pagination": {"total": len(rows)}}
        page = int(a.get("page") or 1)
        chunk = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        if "orders_truncated" in FAULTS and page > 1:
            chunk = []
        pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
        return {"data": chunk, "pagination": {"total": len(rows), "page": page, "total_pages": pages}}
    if name == "get_order":
        return STATE["orders"][a["id"]]
    if name == "list_order_statuses":
        return STATE["statuses"]
    if name == "update_order_status":
        if "status_forbidden" in FAULTS:
            raise ToolError("403 Forbidden: insufficient scope orders.write")
        STATE["orders"][a["order_id"]]["status_id"] = a["status_id"]
        return STATE["orders"][a["order_id"]]
    raise ToolError(f"Unknown tool {name}")


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}}, "serverInfo": {"name": "mock-merchant", "version": "0.0.1"}}}
    if method and method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": PARTNERS_TOOLS if PROFILE == "partners" else TOOLS}}
    if method == "tools/call":
        params = msg.get("params", {})
        try:
            if PROFILE == "partners":
                data = {"apps": [], "note": "partner portal data only"}
            else:
                data = call(params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}}
        finally:
            if STATE_FILE:
                with open(STATE_FILE, "w", encoding="utf-8") as fh:
                    json.dump({"calls": STATE.setdefault("calls", []) + [params.get("name")],
                               "products": STATE["products"], "orders": STATE["orders"]}, fh, ensure_ascii=False, default=str)
                STATE["calls"].append(params.get("name"))
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
