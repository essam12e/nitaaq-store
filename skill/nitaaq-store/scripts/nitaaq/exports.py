"""Analyze merchant export files (CSV/XLSX-as-CSV) locally.

Column names differ between export types and over time, so columns are
mapped through synonym lists and every unmapped column is reported rather
than guessed. Reports always state the file's date coverage and limits.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from pathlib import Path

from .arabic import normalize, parse_amount
from .reports import parse_dt

SYNONYMS = {
    "order_id": ["order id", "order_id", "order number", "reference", "reference id", "رقم الطلب", "الطلب", "معرف الطلب"],
    "date": ["date", "order date", "created at", "created_at", "تاريخ الطلب", "التاريخ", "تاريخ الانشاء"],
    "status": ["status", "order status", "حالة الطلب", "الحالة"],
    "total": ["total", "order total", "grand total", "amount", "الاجمالي", "اجمالي الطلب", "المبلغ الاجمالي", "الإجمالي"],
    "subtotal": ["subtotal", "sub total", "المجموع الفرعي", "مجموع المنتجات"],
    "discount": ["discount", "الخصم", "قيمة الخصم"],
    "tax": ["tax", "vat", "الضريبة", "ضريبة القيمة المضافة"],
    "shipping": ["shipping", "shipping cost", "الشحن", "تكلفة الشحن", "رسوم الشحن"],
    "currency": ["currency", "العملة"],
    "city": ["city", "shipping city", "المدينة", "مدينة الشحن"],
    "payment_method": ["payment method", "payment", "طريقة الدفع", "وسيلة الدفع"],
    "customer_id": ["customer id", "customer_id", "customer mobile", "رقم العميل", "معرف العميل", "جوال العميل"],
    "customer_name": ["customer", "customer name", "اسم العميل", "العميل"],
    "product_id": ["product id", "product_id", "رقم المنتج", "معرف المنتج"],
    "name": ["product", "product name", "name", "اسم المنتج", "المنتج"],
    "sku": ["sku", "رمز المنتج", "رمز التخزين"],
    "quantity": ["quantity", "qty", "الكمية"],
    "price": ["price", "unit price", "السعر", "سعر الوحدة"],
    "sale_price": ["sale price", "discounted price", "سعر التخفيض", "السعر المخفض"],
    "cost": ["cost", "cost price", "التكلفة", "سعر التكلفة"],
    "item_total": ["item total", "line total", "اجمالي المنتج", "إجمالي المنتج"],
    "category": ["category", "categories", "التصنيف", "التصنيفات"],
    "brand": ["brand", "الماركة", "العلامة التجارية"],
    "product_status": ["product status", "حالة المنتج"],
    "description": ["description", "الوصف", "وصف المنتج"],
    "image": ["image", "images", "image url", "الصورة", "الصور"],
}

_INDEX = {normalize(s): k for k, vals in SYNONYMS.items() for s in vals}


def read_csv(path: str | Path) -> tuple[list[str], list[dict]]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1256"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("تعذر قراءة ترميز الملف")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return list(reader.fieldnames or []), rows


def map_columns(headers: list[str]) -> tuple[dict, list[str]]:
    mapping, unmapped, used = {}, [], set()
    for h in headers:
        key = _INDEX.get(normalize(h))
        if key and key not in used:
            mapping[h] = key
            used.add(key)
        else:
            unmapped.append(h)
    return mapping, unmapped


def detect_kind(fields: set) -> str:
    if "order_id" in fields and ({"total", "date"} & fields):
        return "orders"
    if "name" in fields and ({"price", "quantity", "sku"} & fields):
        return "products"
    return "unknown"


def analyze(path: str | Path) -> dict:
    headers, rows = read_csv(path)
    mapping, unmapped = map_columns(headers)
    fields = set(mapping.values())
    kind = detect_kind(fields)
    norm_rows = [{mapping[h]: v for h, v in r.items() if h in mapping} for r in rows]
    missing = {f: sum(1 for r in norm_rows if not (r.get(f) or "").strip()) for f in sorted(fields)}
    result = {
        "file": str(path), "kind": kind, "rows": len(rows), "headers": headers,
        "mapped": mapping, "unmapped": unmapped, "empty_cells": {k: v for k, v in missing.items() if v},
        "limitations": [],
    }
    dates = [d for d in (parse_dt(r.get("date")) for r in norm_rows) if d]
    if dates:
        result["date_min"] = min(dates).date().isoformat()
        result["date_max"] = max(dates).date().isoformat()
    elif "date" in fields:
        result["limitations"].append("تعذر قراءة التواريخ")
    if kind == "orders":
        ids = [r.get("order_id") for r in norm_rows if r.get("order_id")]
        dup = [k for k, v in Counter(ids).items() if v > 1]
        result["orders"] = len(set(ids))
        result["multi_row_orders"] = len(dup)
        if dup and "name" not in fields:
            result["limitations"].append("أرقام طلبات مكررة بدون أعمدة منتجات؛ قد تكون صفوفاً مكررة")
        if "status" not in fields:
            result["limitations"].append("لا يوجد عمود حالة؛ لا يمكن استبعاد الملغي والمسترجع")
        if "cost" not in fields:
            result["limitations"].append("لا توجد تكلفة؛ لا يمكن حساب هامش المنتجات")
    if kind == "products":
        names = [normalize(r.get("name")) for r in norm_rows if r.get("name")]
        result["products"] = len(norm_rows)
        result["duplicate_names"] = [k for k, v in Counter(names).items() if v > 1]
        result["missing_description"] = missing.get("description")
        result["missing_image"] = missing.get("image")
        result["missing_category"] = missing.get("category")
    result["limitations"].append("الملف لقطة بتاريخ تصديره؛ قد لا يطابق المتجر الآن")
    return result


def to_orders(path: str | Path) -> list[dict]:
    """Group export rows into order dicts consumable by reports.py."""
    headers, rows = read_csv(path)
    mapping, _ = map_columns(headers)
    orders: dict = {}
    for r in rows:
        n = {mapping[h]: v for h, v in r.items() if h in mapping}
        oid = n.get("order_id") or f"row-{len(orders) + 1}"
        o = orders.setdefault(oid, {"id": oid, "items": []})
        for k in ("date", "status", "total", "subtotal", "discount", "tax", "shipping", "currency",
                  "city", "payment_method", "customer_id"):
            if n.get(k) not in (None, "") and k not in o:
                o[k] = n[k]
        if n.get("name") or n.get("product_id"):
            o["items"].append({
                "product_id": n.get("product_id") or n.get("sku"), "name": n.get("name"),
                "quantity": n.get("quantity") or "1", "price": n.get("price"),
                "total": n.get("item_total"), "cost": n.get("cost"),
            })
    for o in orders.values():
        if parse_amount(o.get("total")) is None:
            o.pop("total", None)
    return list(orders.values())
