"""Generate skill/nitaaq-store/assets/operation-catalog.json.

The catalog is the stable internal vocabulary of operations. It never names
connector tools; discovered tools are mapped onto it at runtime.
"""
import json
from pathlib import Path

R, W, D = "read", "write", "destructive"

E = {  # entity keyword sets (English + Arabic, normalized forms)
 "product": ["product", "products", "item", "catalog", "منتج", "منتجات"],
 "variant": ["variant", "variants", "option", "options", "sku", "skus", "متغير", "خيارات"],
 "stock": ["stock", "inventory", "quantity", "quantities", "مخزون", "كميه", "كميات"],
 "branch": ["branch", "branches", "warehouse", "location", "فرع", "فروع", "مستودع"],
 "category": ["category", "categories", "تصنيف", "تصنيفات"],
 "brand": ["brand", "brands", "ماركه", "علامه"],
 "tag": ["tag", "tags", "وسم"],
 "image": ["image", "images", "media", "photo", "file", "upload", "صوره", "صور"],
 "order": ["order", "orders", "طلب", "طلبات"],
 "status": ["status", "statuses", "state", "حاله"],
 "history": ["history", "histories", "timeline", "log", "سجل"],
 "note": ["note", "notes", "comment", "ملاحظه"],
 "invoice": ["invoice", "invoices", "tax", "فاتوره", "فواتير"],
 "customer": ["customer", "customers", "client", "عميل", "عملاء"],
 "cart": ["cart", "carts", "abandoned", "سله", "سلات", "متروكه"],
 "review": ["review", "reviews", "rating", "ratings", "feedback", "تقييم", "تقييمات"],
 "report": ["report", "reports", "analytics", "statistics", "stats", "تقرير", "تقارير", "احصائيات"],
 "theme": ["theme", "themes", "template", "ثيم", "قالب"],
 "section": ["section", "sections", "component", "components", "block", "قسم", "عنصر"],
 "homepage": ["home", "homepage", "رئيسيه"],
 "page": ["page", "pages", "landing", "صفحه", "هبوط"],
 "menu": ["menu", "menus", "navigation", "link", "links", "قائمه"],
 "settings": ["setting", "settings", "config", "configuration", "اعدادات"],
 "store": ["store", "merchant", "shop", "account", "متجر"],
 "coupon": ["coupon", "coupons", "كوبون"],
 "offer": ["offer", "offers", "promotion", "discount", "عرض", "عروض"],
 "shipping": ["shipping", "shipment", "shipments", "courier", "شحن", "شحنه"],
 "trash": ["trash", "trashed", "deleted", "محذوف"],
}
A = {
 "list": ["list", "search", "find", "browse", "all", "query", "filter", "عرض", "بحث"],
 "get": ["get", "details", "detail", "show", "fetch", "retrieve", "view", "info", "تفاصيل"],
 "create": ["create", "add", "new", "insert", "post", "انشاء", "اضافه"],
 "update": ["update", "edit", "modify", "patch", "change", "set", "تعديل", "تحديث"],
 "delete": ["delete", "remove", "destroy", "حذف"],
 "status": ["status", "publish", "hide", "unhide", "visibility", "activate", "deactivate", "نشر", "اخفاء"],
 "adjust": ["adjust", "increment", "decrement", "restock", "increase", "decrease"],
 "upload": ["upload", "attach", "add", "رفع"],
 "reorder": ["reorder", "sort", "position", "move", "ترتيب"],
 "clone": ["clone", "duplicate", "copy", "نسخ"],
 "preview": ["preview", "معاينه"],
 "schedule": ["schedule", "scheduled", "جدوله"],
}

def op(id, label, domain, access, ent, act, sensitivity="low", idempotent=None,
       verify=None, schema_hints=(), fallback=None, notes=None, require_any=()):
    return {"id": id, "label_ar": label, "domain": domain, "access": access,
            "entities": ent, "actions": act, "sensitivity": sensitivity,
            "idempotent": idempotent, "verify_with": verify,
            "schema_hints": list(schema_hints),
            "require_any": list(require_any),
            "fallback_ar": fallback or "غير متاح عبر الأدوات المتصلة؛ جهّز المقترح وقدّم خطوات لوحة تحكم سلة.",
            "notes": notes}

ops = [
 # Store identity
 op("store.info", "بيانات المتجر", "store", R, ["store"], ["get"], schema_hints=[]),
 # Products
 op("products.list", "قائمة المنتجات", "products", R, ["product"], ["list"], schema_hints=["page", "per_page", "keyword", "category", "status"]),
 op("products.get", "تفاصيل منتج", "products", R, ["product"], ["get"], schema_hints=["id", "product_id"]),
 op("products.create", "إنشاء منتج", "products", W, ["product"], ["create"], "medium", False, "products.get", ["name", "price", "product_type", "quantity"]),
 op("products.update", "تعديل منتج", "products", W, ["product"], ["update"], "medium", True, "products.get", ["id", "product_id"]),
 op("products.set_status", "نشر/إخفاء منتج", "products", W, ["product"], ["status"], "medium", True, "products.get", ["status"]),
 op("products.delete", "حذف منتج", "products", D, ["product"], ["delete"], "high", True, "products.get"),
 op("products.trash_list", "المنتجات المحذوفة", "products", R, ["product", "trash"], ["list"]),
 op("products.variants", "المتغيرات والخيارات", "products", R, ["variant"], ["list", "get"]),
 op("products.best_sellers", "الأكثر مبيعاً", "products", R, ["product"], ["list", "get"], notes="حدّد المقياس والفترة", require_any=["best", "top", "selling", "sellers", "bestseller", "bestsellers", "الاكثر"]),
 op("products.images.attach", "إرفاق صورة بمنتج", "products", W, ["image", "product"], ["upload"], "medium", False, "products.get", ["image", "url", "file"]),
 op("categories.list", "التصنيفات", "catalog", R, ["category"], ["list"]),
 op("categories.create", "إنشاء تصنيف", "catalog", W, ["category"], ["create"], "medium", False, "categories.list"),
 op("brands.list", "الماركات", "catalog", R, ["brand"], ["list"]),
 op("tags.list", "الوسوم", "catalog", R, ["tag"], ["list"]),
 # Inventory
 op("inventory.read", "قراءة المخزون", "inventory", R, ["stock"], ["get", "list"]),
 op("inventory.set", "تعيين الكمية", "inventory", W, ["stock"], ["update"], "medium", True, "inventory.read", ["quantity"]),
 op("inventory.adjust", "زيادة/إنقاص الكمية", "inventory", W, ["stock"], ["adjust"], "medium", False, "inventory.read", ["quantity", "mode"], notes="غير متكرر الأمان: لا تُعِد المحاولة قبل المطابقة"),
 op("inventory.branches", "كميات الفروع", "inventory", R, ["branch", "stock"], ["list", "get"]),
 # Appearance
 op("theme.list", "القوالب المثبتة", "appearance", R, ["theme"], ["list"]),
 op("theme.settings.get", "إعدادات القالب", "appearance", R, ["theme", "settings"], ["get"]),
 op("theme.settings.update", "تعديل إعدادات القالب", "appearance", W, ["theme", "settings"], ["update"], "medium", True, "theme.settings.get"),
 op("theme.clone", "نسخ إعدادات قالب", "appearance", W, ["theme"], ["clone"], "medium", False, "theme.list"),
 op("theme.preview", "معاينة القالب", "appearance", R, ["theme"], ["preview"]),
 op("theme.publish", "نشر القالب", "appearance", W, ["theme"], ["status"], "high", True, "theme.list"),
 op("home.sections.list", "أقسام الرئيسية", "appearance", R, ["section", "homepage"], ["list", "get"]),
 op("home.sections.types", "أنواع الأقسام المتاحة", "appearance", R, ["section"], ["list", "get"], notes="كتالوج أنواع الأقسام التي يدعمها القالب", require_any=["types", "type", "available", "catalog", "schema", "انواع"]),
 op("home.sections.create", "إضافة قسم", "appearance", W, ["section"], ["create"], "medium", False, "home.sections.list"),
 op("home.sections.update", "تعديل قسم", "appearance", W, ["section"], ["update"], "medium", True, "home.sections.list"),
 op("home.sections.reorder", "ترتيب الأقسام", "appearance", W, ["section"], ["reorder"], "low", True, "home.sections.list"),
 op("home.sections.delete", "حذف قسم", "appearance", D, ["section"], ["delete"], "high", True, "home.sections.list"),
 op("pages.list", "الصفحات", "appearance", R, ["page"], ["list"]),
 op("pages.create", "إنشاء صفحة هبوط", "appearance", W, ["page"], ["create"], "medium", False, "pages.list"),
 op("pages.update", "تعديل صفحة", "appearance", W, ["page"], ["update"], "medium", True, "pages.list"),
 op("pages.publish", "نشر/جدولة صفحة", "appearance", W, ["page"], ["status", "schedule"], "medium", True, "pages.list"),
 op("pages.delete", "حذف صفحة", "appearance", D, ["page"], ["delete"], "high", True, "pages.list"),
 op("menus.list", "القوائم", "appearance", R, ["menu"], ["list", "get"]),
 op("menus.update", "تعديل القوائم", "appearance", W, ["menu"], ["update", "create"], "medium", True, "menus.list"),
 op("assets.upload", "رفع ملف/صورة", "appearance", W, ["image"], ["upload"], "low", False),
 op("branding.update", "الشعار وألوان الهوية", "appearance", W, ["store", "settings"], ["update"], "high", True, "store.info", notes="قد يتطلب تحقق لوحة التحكم أو OTP"),
 # Orders
 op("orders.list", "قائمة الطلبات", "orders", R, ["order"], ["list"], schema_hints=["page", "from_date", "to_date", "status"]),
 op("orders.get", "تفاصيل طلب", "orders", R, ["order"], ["get"]),
 op("orders.statuses", "حالات الطلب الخاصة بالمتجر", "orders", R, ["order", "status"], ["list"]),
 op("orders.history", "سجل حالة الطلب", "orders", R, ["order", "history"], ["list", "get"]),
 op("orders.update_status", "تغيير حالة طلب", "orders", W, ["order", "status"], ["update"], "high", True, "orders.get", ["status_id", "status"], notes="قد يرسل إشعاراً للعميل"),
 op("orders.add_note", "ملاحظة داخلية على طلب", "orders", W, ["order", "note"], ["create", "update"], "low", False, "orders.get"),
 op("invoices.list", "الفواتير الضريبية", "orders", R, ["invoice"], ["list", "get"]),
 op("customers.list", "العملاء", "orders", R, ["customer"], ["list", "get"]),
 op("carts.abandoned", "السلات المتروكة", "orders", R, ["cart"], ["list", "get"]),
 op("reviews.list", "التقييمات", "orders", R, ["review"], ["list"]),
 op("reviews.reply", "الرد على تقييم", "orders", W, ["review"], ["create", "update"], "high", False, notes="رسالة ظاهرة للعملاء"),
 op("coupons.list", "الكوبونات", "orders", R, ["coupon"], ["list"]),
 op("coupons.create", "إنشاء كوبون", "orders", W, ["coupon"], ["create"], "high", False),
 op("offers.list", "العروض", "orders", R, ["offer"], ["list"]),
 op("shipping.list", "الشحن", "orders", R, ["shipping"], ["list", "get"]),
 # Reports
 op("reports.summary", "ملخص تشغيلي", "reports", R, ["report"], ["get", "list"], require_any=["summary", "overview", "dashboard", "ملخص"]),
 op("reports.sales", "تقارير المبيعات", "reports", R, ["report"], ["get", "list"], schema_hints=["from", "to", "date"], require_any=["sales", "revenue", "مبيعات"]),
]

out = {"version": 1, "entity_keywords": E, "action_keywords": A, "operations": ops}
p = Path(__file__).resolve().parents[1] / "skill/nitaaq-store/assets/operation-catalog.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(p, len(ops))
