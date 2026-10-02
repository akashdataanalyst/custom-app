from __future__ import annotations

import hashlib
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


ITEM_BARCODE_TYPE = "CODE-39"
BATCH_BARCODE_FIELD = "custom_batch_barcode"
SCAN_BARCODE_FIELD = "custom_scan_barcode"
PRINT_FORMAT_NAME = "Batch Barcode Label"
PRINT_FORMAT_MODULE = "Calco Production"


def encode_for_barcode(value: str) -> str:
    parts: list[str] = []
    for character in (value or "").strip().upper():
        if character.isalnum() or character == "-":
            parts.append(character)
        else:
            parts.append(f"X{ord(character):02X}")
    encoded = "".join(parts).strip("-")
    return encoded or "UNKNOWN"


def item_barcode_value(item_code: str) -> str:
    digest = hashlib.sha1((item_code or "").encode("utf-8")).hexdigest()[:6].upper()
    return f"ITM-{encode_for_barcode(item_code)}-{digest}"


def batch_barcode_value(batch_id: str) -> str:
    digest = hashlib.sha1((batch_id or "").encode("utf-8")).hexdigest()[:6].upper()
    return f"BAT-{encode_for_barcode(batch_id)}-{digest}"


def template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "print_formats" / "batch_barcode_label.html"


def ensure_barcode_setup():
    ensure_custom_fields()
    ensure_item_barcodes()
    ensure_batch_barcodes()
    ensure_batch_barcode_label()
    frappe.clear_cache()
    frappe.db.commit()


def ensure_custom_fields():
    custom_fields = {
        "Batch": [
            {
                "fieldname": BATCH_BARCODE_FIELD,
                "label": "Batch Barcode",
                "fieldtype": "Data",
                "insert_after": "batch_id",
                "in_list_view": 1,
                "search_index": 1,
                "unique": 1,
                "read_only": 1,
                "no_copy": 1,
            }
        ],
        "Purchase Receipt": [
            {
                "fieldname": SCAN_BARCODE_FIELD,
                "label": "Scan Barcode",
                "fieldtype": "Data",
                "insert_after": "supplier",
                "no_copy": 1,
                "translatable": 0,
            }
        ],
        "Stock Entry": [
            {
                "fieldname": SCAN_BARCODE_FIELD,
                "label": "Scan Barcode",
                "fieldtype": "Data",
                "insert_after": "purpose",
                "no_copy": 1,
                "translatable": 0,
            }
        ],
        "Delivery Note": [
            {
                "fieldname": SCAN_BARCODE_FIELD,
                "label": "Scan Barcode",
                "fieldtype": "Data",
                "insert_after": "customer",
                "no_copy": 1,
                "translatable": 0,
            }
        ],
    }
    create_custom_fields(custom_fields, update=True)


def ensure_item_barcodes():
    items_with_barcodes = {
        row.parent
        for row in frappe.get_all("Item Barcode", fields=["parent"], limit_page_length=0)
    }

    for item in frappe.get_all("Item", fields=["name", "stock_uom"], filters={"disabled": 0}, limit_page_length=0):
        if item.name in items_with_barcodes:
            continue

        doc = frappe.get_doc("Item", item.name)
        doc.append(
            "barcodes",
            {
                "barcode": item_barcode_value(doc.name),
                "barcode_type": ITEM_BARCODE_TYPE,
                "uom": doc.stock_uom,
            },
        )
        doc.save(ignore_permissions=True)


def ensure_batch_barcodes():
    if not frappe.get_meta("Batch").has_field(BATCH_BARCODE_FIELD):
        return

    for batch in frappe.get_all("Batch", fields=["name", BATCH_BARCODE_FIELD], limit_page_length=0):
        if batch.get(BATCH_BARCODE_FIELD):
            continue
        frappe.db.set_value(
            "Batch",
            batch.name,
            BATCH_BARCODE_FIELD,
            batch_barcode_value(batch.name),
            update_modified=False,
        )


def ensure_batch_barcode_label():
    html = template_path().read_text(encoding="utf-8")
    if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
        doc = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
    else:
        doc = frappe.new_doc("Print Format")
        doc.name = PRINT_FORMAT_NAME

    doc.print_format_for = "DocType"
    doc.doc_type = "Batch"
    doc.module = PRINT_FORMAT_MODULE
    doc.standard = "No"
    doc.custom_format = 1
    doc.disabled = 0
    doc.print_format_type = "Jinja"
    doc.raw_printing = 0
    doc.html = html
    doc.css = """
.barcode-label { width: 320px; padding: 12px; border: 1px solid #111; font-size: 12px; }
.barcode-label__title { font-size: 14px; font-weight: 700; margin-bottom: 8px; }
.barcode-label__row { margin-bottom: 4px; }
.barcode-label__barcode { margin: 10px 0 4px; }
.barcode-label__text { font-size: 11px; letter-spacing: 1px; text-align: center; }
"""

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def ensure_item_barcode_on_validate(doc, method=None):
    if not hasattr(doc, "barcodes"):
        return
    if doc.barcodes:
        return

    doc.append(
        "barcodes",
        {
            "barcode": item_barcode_value(doc.name or doc.item_code),
            "barcode_type": ITEM_BARCODE_TYPE,
            "uom": doc.stock_uom,
        },
    )


def ensure_batch_barcode_on_validate(doc, method=None):
    if not doc.get(BATCH_BARCODE_FIELD):
        doc.set(BATCH_BARCODE_FIELD, batch_barcode_value(doc.name or doc.batch_id))


@frappe.whitelist()
def resolve_barcode(barcode: str) -> dict[str, object]:
    barcode = (barcode or "").strip()
    if not barcode:
        frappe.throw("Barcode is required.")

    item_barcode = frappe.db.get_value("Item Barcode", {"barcode": barcode}, ["parent", "uom"], as_dict=True)
    if item_barcode:
        item_code = item_barcode.parent
        return {
            "match_type": "item",
            "barcode": barcode,
            "item_code": item_code,
            "item_name": frappe.db.get_value("Item", item_code, "item_name"),
            "uom": item_barcode.uom or frappe.db.get_value("Item", item_code, "stock_uom"),
            "has_batch_no": int(bool(frappe.db.get_value("Item", item_code, "has_batch_no"))),
        }

    batch = frappe.db.get_value("Batch", {BATCH_BARCODE_FIELD: barcode}, ["name", "item"], as_dict=True)
    if not batch and frappe.db.exists("Batch", barcode):
        batch = frappe._dict({"name": barcode, "item": frappe.db.get_value("Batch", barcode, "item")})
    if batch:
        return {
            "match_type": "batch",
            "barcode": barcode,
            "batch_no": batch.name,
            "item_code": batch.item,
            "item_name": frappe.db.get_value("Item", batch.item, "item_name"),
            "uom": frappe.db.get_value("Item", batch.item, "stock_uom"),
            "has_batch_no": 1,
        }

    if frappe.db.exists("Item", barcode):
        return {
            "match_type": "item",
            "barcode": barcode,
            "item_code": barcode,
            "item_name": frappe.db.get_value("Item", barcode, "item_name"),
            "uom": frappe.db.get_value("Item", barcode, "stock_uom"),
            "has_batch_no": int(bool(frappe.db.get_value("Item", barcode, "has_batch_no"))),
        }

    frappe.throw(f"Barcode {barcode} was not found in Item or Batch master.")


def barcode_status() -> dict[str, object]:
    batch_field_exists = frappe.get_meta("Batch").has_field(BATCH_BARCODE_FIELD)
    return {
        "installed_app": "calco_erp" in frappe.get_installed_apps(),
        "batch_barcode_field_exists": batch_field_exists,
        "purchase_receipt_scan_field_exists": frappe.get_meta("Purchase Receipt").has_field(SCAN_BARCODE_FIELD),
        "stock_entry_scan_field_exists": frappe.get_meta("Stock Entry").has_field(SCAN_BARCODE_FIELD),
        "delivery_note_scan_field_exists": frappe.get_meta("Delivery Note").has_field(SCAN_BARCODE_FIELD),
        "item_barcode_rows": frappe.db.count("Item Barcode"),
        "batch_barcode_rows": frappe.db.count("Batch", {BATCH_BARCODE_FIELD: ("is", "set")}) if batch_field_exists else 0,
        "print_format_exists": bool(frappe.db.exists("Print Format", PRINT_FORMAT_NAME)),
        "sample_item_barcode": frappe.db.get_value("Item Barcode", {}, "barcode"),
        "sample_batch_barcode": frappe.db.get_value("Batch", {BATCH_BARCODE_FIELD: ("is", "set")}, BATCH_BARCODE_FIELD)
        if batch_field_exists
        else None,
    }


def barcode_smoke_test() -> dict[str, object]:
    status = barcode_status()
    sample_item_barcode = status["sample_item_barcode"]
    sample_batch_barcode = status["sample_batch_barcode"]
    return {
        "status": status,
        "sample_item_resolution": resolve_barcode(sample_item_barcode) if sample_item_barcode else None,
        "sample_batch_resolution": resolve_barcode(sample_batch_barcode) if sample_batch_barcode else None,
    }
