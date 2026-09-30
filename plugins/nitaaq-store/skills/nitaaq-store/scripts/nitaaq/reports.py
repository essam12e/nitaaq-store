"""Deterministic report math with explicit metadata.

Every number the agent shows comes from here (or from a trusted report
tool output), never from mental arithmetic. Missing data stays None and is
reported as missing; it is never turned into zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from .arabic import parse_amount

try:
    from zoneinfo import ZoneInfo
    RIYADH = ZoneInfo("Asia/Riyadh")
except Exception:  # tzdata missing: Riyadh is UTC+3 with no DST
    RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")

TWO = Decimal("0.01")


def money(v: Decimal | None) -> str:
    if v is None:
        return "غير متوفر"
    return f"{v.quantize(TWO, ROUND_HALF_UP):,}"


@dataclass
class ReportMeta:
    source: str                       # e.g. "أداة orders.list عبر MCP" / "ملف تصدير orders.csv"
    date_from: str | None = None      # ISO date, inclusive
    date_to: str | None = None        # ISO date, inclusive
    timezone: str = "Asia/Riyadh"
    currency: str = "SAR"
    included_statuses: list | None = None   # None = unknown / all returned
    sales_definition: str = "order_total"   # or "items_net"
    treatment: dict = field(default_factory=dict)  # discounts/tax/shipping/refunds/cancellations
    records_fetched: int | None = None
    records_total: int | None = None  # as reported by the source, if any
    fetched_at: str | None = None
    missing: list = field(default_factory=list)

    @property
    def complete(self) -> bool | None:
        if self.records_total is None or self.records_fetched is None:
            return None
        return self.records_fetched >= self.records_total

    def to_markdown_ar(self) -> str:
        defs = {
            "order_total": "إجمالي قيمة الطلب كما يعيده المصدر (يشمل الضريبة والشحن ويطرح الخصم عادةً — راجع المعالجة أدناه)",
            "items_net": "قيمة المنتجات بعد الخصم، بدون الشحن والضريبة",
        }
        comp = {True: "كاملة", False: f"جزئية ({self.records_fetched} من {self.records_total})", None: "غير معروفة — المصدر لم يذكر العدد الكلي"}[self.complete]
        rows = [
            ("المصدر", self.source),
            ("الفترة", f"{self.date_from or '؟'} إلى {self.date_to or '؟'}"),
            ("المنطقة الزمنية", self.timezone),
            ("العملة", self.currency),
            ("تعريف المبيعات", defs.get(self.sales_definition, self.sales_definition)),
            ("الحالات المشمولة", "، ".join(self.included_statuses) if self.included_statuses else "كل ما أعاده المصدر (غير محدد)"),
            ("اكتمال البيانات", comp),
            ("وقت الجلب", self.fetched_at or "غير مسجّل"),
        ]
        labels = {"discounts": "الخصومات", "tax": "الضريبة", "shipping": "الشحن", "refunds": "المرتجعات", "cancellations": "الإلغاءات"}
        for k, lab in labels.items():
            rows.append((lab, self.treatment.get(k, "غير معروف")))
        if self.missing:
            rows.append(("بيانات ناقصة", "، ".join(self.missing)))
        return "\n".join(["| البند | القيمة |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows])


def parse_dt(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    else:
        s = str(v).strip().replace("Z", "+00:00")
        dt = None
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=RIYADH)  # store-local time assumed; stated in meta
    return dt.astimezone(RIYADH)


def filter_orders(orders: list[dict], date_from: str | None = None, date_to: str | None = None,
                  statuses: list[str] | None = None) -> tuple[list[dict], dict]:
    kept, dropped = [], {"no_date": 0, "out_of_range": 0, "status": 0}
    d0 = date.fromisoformat(date_from) if date_from else None
    d1 = date.fromisoformat(date_to) if date_to else None
    wanted = {s.lower() for s in statuses} if statuses else None
    for o in orders:
        dt = parse_dt(o.get("date"))
        if dt is None:
            dropped["no_date"] += 1
            continue
        if (d0 and dt.date() < d0) or (d1 and dt.date() > d1):
            dropped["out_of_range"] += 1
            continue
        if wanted is not None and str(o.get("status", "")).lower() not in wanted:
            dropped["status"] += 1
            continue
        kept.append(o)
    return kept, dropped


def _order_value(o: dict, definition: str) -> Decimal | None:
    if definition == "items_net":
        sub = parse_amount(o.get("subtotal"))
        if sub is None and o.get("items"):
            vals = [parse_amount(i.get("total")) for i in o["items"]]
            sub = sum(vals, Decimal(0)) if all(v is not None for v in vals) else None
        if sub is None:
            return None
        disc = parse_amount(o.get("discount")) or Decimal(0)
        return sub - disc
    return parse_amount(o.get("total"))


def sales_summary(orders: list[dict], definition: str = "order_total") -> dict:
    values = [_order_value(o, definition) for o in orders]
    known = [v for v in values if v is not None]
    total = sum(known, Decimal(0)) if known else (Decimal(0) if not orders else None)
    n = len(orders)
    return {
        "orders": n,
        "orders_missing_value": len(values) - len(known),
        "sales": total,
        "aov": (total / len(known)).quantize(TWO, ROUND_HALF_UP) if known else None,
    }


def by_month(orders: list[dict], definition: str = "order_total") -> list[dict]:
    buckets: dict = defaultdict(list)
    for o in orders:
        dt = parse_dt(o.get("date"))
        if dt:
            buckets[dt.strftime("%Y-%m")].append(o)
    return [{"month": m, **sales_summary(buckets[m], definition)} for m in sorted(buckets)]


def by_dimension(orders: list[dict], key: str, definition: str = "order_total") -> list[dict]:
    """Break down by an order field (city, payment_method, branch, device...).

    Orders without the field go to an explicit "غير محدد" bucket, so the
    parts always add up to the whole.
    """
    buckets: dict = defaultdict(list)
    for o in orders:
        v = o.get(key)
        buckets[str(v) if v not in (None, "") else "غير محدد"].append(o)
    rows = [{key: k, **sales_summary(v, definition)} for k, v in buckets.items()]
    rows.sort(key=lambda r: -(r["sales"] or 0))
    return rows


def top_products(orders: list[dict], by: str = "quantity", limit: int = 10) -> list[dict]:
    """Rank products by sold quantity or item revenue. Metric stated by caller."""
    agg: dict = {}
    for o in orders:
        for it in o.get("items") or []:
            pid = str(it.get("product_id") or it.get("sku") or it.get("name"))
            row = agg.setdefault(pid, {"product": it.get("name") or pid, "quantity": 0, "revenue": Decimal(0), "revenue_missing": 0})
            q = parse_amount(it.get("quantity")) or Decimal(0)
            row["quantity"] += int(q)
            line = parse_amount(it.get("total"))
            if line is None:
                unit = parse_amount(it.get("price"))
                line = unit * q if unit is not None else None
            if line is None:
                row["revenue_missing"] += 1
            else:
                row["revenue"] += line
    rows = list(agg.values())
    rows.sort(key=lambda r: (-(r["quantity"] if by == "quantity" else r["revenue"]), r["product"]))
    return rows[:limit]


def product_margins(orders: list[dict]) -> dict:
    """Product margin = item revenue - item cost. NOT net profit.

    Items without a cost are excluded and counted, never assumed zero-cost.
    """
    revenue = cost = Decimal(0)
    covered = missing = 0
    for o in orders:
        for it in o.get("items") or []:
            q = parse_amount(it.get("quantity")) or Decimal(0)
            line = parse_amount(it.get("total"))
            if line is None and parse_amount(it.get("price")) is not None:
                line = parse_amount(it.get("price")) * q
            c = parse_amount(it.get("cost"))
            if line is None or c is None:
                missing += 1
                continue
            revenue += line
            cost += c * q
            covered += 1
    margin = revenue - cost if covered else None
    return {
        "label_ar": "هامش المنتجات (قبل المصاريف التشغيلية — ليس صافي الربح)",
        "items_with_cost": covered, "items_without_cost": missing,
        "revenue_covered": revenue if covered else None, "cost": cost if covered else None,
        "margin": margin,
        "margin_percent": (margin / revenue * 100).quantize(Decimal("0.1")) if covered and revenue else None,
    }


def customers_new_returning(orders: list[dict], first_order_dates: dict | None = None,
                            date_from: str | None = None) -> dict:
    """New vs returning needs each customer's all-time first order date.

    Without first_order_dates the split is only "within the period" and is
    labelled that way.
    """
    ids = {str(o.get("customer_id")) for o in orders if o.get("customer_id")}
    if first_order_dates and date_from:
        d0 = date.fromisoformat(date_from)
        new = {c for c in ids if (fd := parse_dt(first_order_dates.get(c))) and fd.date() >= d0}
        unknown = {c for c in ids if c not in first_order_dates}
        return {"basis": "all_time", "new": len(new), "returning": len(ids - new - unknown), "unknown": len(unknown)}
    counts: dict = defaultdict(int)
    for o in orders:
        if o.get("customer_id"):
            counts[str(o["customer_id"])] += 1
    return {"basis": "within_period_only", "single_order": sum(1 for v in counts.values() if v == 1),
            "repeat_in_period": sum(1 for v in counts.values() if v > 1),
            "note_ar": "التقسيم داخل الفترة فقط؛ لا يثبت أن العميل جديد على المتجر."}


def roas(attributed_sales, ad_spend) -> dict:
    s, sp = parse_amount(attributed_sales), parse_amount(ad_spend)
    if sp is None:
        return {"roas": None, "note_ar": "لا يمكن حساب العائد على الإنفاق الإعلاني بدون بيانات الإنفاق."}
    if sp == 0:
        return {"roas": None, "note_ar": "الإنفاق صفر؛ النسبة غير معرّفة."}
    return {"roas": (s / sp).quantize(TWO) if s is not None else None,
            "note_ar": "مبيعات منسوبة حسب نموذج الإسناد في المنصة؛ لا تثبت أن الحملة سببها."}


def table_ar(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(lab for _, lab in columns) + " |"
    sep = "|" + "---|" * len(columns)
    body = []
    for r in rows:
        cells = []
        for key, _ in columns:
            v = r.get(key)
            cells.append(money(v) if isinstance(v, Decimal) else ("غير متوفر" if v is None else str(v)))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *body])


def meta_dict(meta: ReportMeta) -> dict:
    d = asdict(meta)
    d["complete"] = meta.complete
    return d
