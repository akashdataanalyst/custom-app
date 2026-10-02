from __future__ import annotations

from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, nowdate
from frappe.utils.file_manager import save_file

from calco_erp.calco_quality.purchase_receipt_qc import handle_purchase_receipt_submit as handle_rm_qc_submit
from calco_erp.calco_production.doctype.batch_production_record.batch_production_record import create_from_stock_entry
from calco_erp.calco_purchase.doctype.rm_inward_validation.rm_inward_validation import create_from_purchase_receipt
from calco_erp.fg_batch_setup import ensure_fg_batch_numbers
from calco_erp.machine_setup import MACHINE_FIELD
from calco_erp.rm_batch_setup import ensure_purchase_receipt_batch_numbers


RM_LABEL_PRINT_FORMAT = "RM Batch Label"
FG_LABEL_PRINT_FORMAT = "FG Bag Label"
THERMAL_PRINT_MODULE = "Calco Production"
PRINT_SETTINGS_DOCTYPE = "Print Settings"
ITEM_BAG_SIZE_FIELD = "custom_fg_bag_size_kg"

ENABLE_FIELD = "custom_enable_thermal_label_printing"
LANGUAGE_FIELD = "custom_thermal_printer_language"
PRINTER_NAME_FIELD = "custom_thermal_printer_name"
DEFAULT_BAG_SIZE_FIELD = "custom_default_fg_bag_size_kg"
RM_FORMAT_FIELD = "custom_rm_batch_label_format"
FG_FORMAT_FIELD = "custom_fg_bag_label_format"


def raw_template_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / "templates" / "raw_print" / filename


def ensure_thermal_label_setup():
    ensure_custom_fields()
    ensure_print_settings()
    ensure_print_formats()
    frappe.clear_cache()


def ensure_custom_fields():
    custom_fields = {
        PRINT_SETTINGS_DOCTYPE: [
            {
                "fieldname": ENABLE_FIELD,
                "label": "Enable Thermal Label Printing",
                "fieldtype": "Check",
                "insert_after": "enable_raw_printing",
                "default": "1",
            },
            {
                "fieldname": LANGUAGE_FIELD,
                "label": "Thermal Printer Language",
                "fieldtype": "Select",
                "options": "TSPL",
                "insert_after": ENABLE_FIELD,
                "default": "TSPL",
            },
            {
                "fieldname": PRINTER_NAME_FIELD,
                "label": "Thermal Printer Name",
                "fieldtype": "Data",
                "insert_after": LANGUAGE_FIELD,
            },
            {
                "fieldname": DEFAULT_BAG_SIZE_FIELD,
                "label": "Default FG Bag Size (Kg)",
                "fieldtype": "Float",
                "insert_after": PRINTER_NAME_FIELD,
                "default": "25",
            },
            {
                "fieldname": RM_FORMAT_FIELD,
                "label": "RM Label Format",
                "fieldtype": "Link",
                "options": "Print Format",
                "insert_after": DEFAULT_BAG_SIZE_FIELD,
                "default": RM_LABEL_PRINT_FORMAT,
            },
            {
                "fieldname": FG_FORMAT_FIELD,
                "label": "FG Label Format",
                "fieldtype": "Link",
                "options": "Print Format",
                "insert_after": RM_FORMAT_FIELD,
                "default": FG_LABEL_PRINT_FORMAT,
            },
        ],
        "Item": [
            {
                "fieldname": ITEM_BAG_SIZE_FIELD,
                "label": "FG Bag Size (Kg)",
                "fieldtype": "Float",
                "insert_after": "weight_per_unit",
            }
        ],
    }
    create_custom_fields(custom_fields, update=True)


def ensure_print_settings():
    if not cint(frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, "enable_raw_printing") or 0):
        frappe.db.set_single_value(PRINT_SETTINGS_DOCTYPE, "enable_raw_printing", 1, update_modified=False)

    default_values = {
        ENABLE_FIELD: 1,
        LANGUAGE_FIELD: "TSPL",
        DEFAULT_BAG_SIZE_FIELD: 25,
        RM_FORMAT_FIELD: RM_LABEL_PRINT_FORMAT,
        FG_FORMAT_FIELD: FG_LABEL_PRINT_FORMAT,
    }
    for fieldname, value in default_values.items():
        current_value = frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, fieldname)
        if current_value in (None, "", 0, 0.0):
            frappe.db.set_single_value(PRINT_SETTINGS_DOCTYPE, fieldname, value, update_modified=False)


def ensure_print_formats():
    ensure_raw_print_format(
        format_name=RM_LABEL_PRINT_FORMAT,
        doc_type="Purchase Receipt",
        raw_commands=raw_template_path("rm_batch_label.tspl").read_text(encoding="utf-8"),
    )
    ensure_raw_print_format(
        format_name=FG_LABEL_PRINT_FORMAT,
        doc_type="Stock Entry",
        raw_commands=raw_template_path("fg_bag_label.tspl").read_text(encoding="utf-8"),
    )


def ensure_raw_print_format(format_name: str, doc_type: str, raw_commands: str):
    if frappe.db.exists("Print Format", format_name):
        doc = frappe.get_doc("Print Format", format_name)
    else:
        doc = frappe.new_doc("Print Format")
        doc.name = format_name

    doc.print_format_for = "DocType"
    doc.doc_type = doc_type
    doc.module = THERMAL_PRINT_MODULE
    doc.standard = "No"
    doc.custom_format = 1
    doc.disabled = 0
    doc.print_format_type = "Jinja"
    doc.raw_printing = 1
    doc.raw_commands = raw_commands
    doc.html = ""

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def handle_purchase_receipt_submit(doc, method=None):
    create_from_purchase_receipt(doc, method)
    handle_rm_qc_submit(doc, method)
    auto_print_rm_batch_labels(doc, method)


def handle_stock_entry_submit(doc, method=None):
    create_from_stock_entry(doc, method)
    auto_print_fg_bag_labels(doc, method)


def auto_print_rm_batch_labels(doc, method=None):
    if not thermal_printing_enabled() or doc.doctype != "Purchase Receipt":
        return None

    rows = [row for row in doc.get("items", []) if row.get("batch_no")]
    if not rows:
        return None

    raw_commands = render_print_format(RM_LABEL_PRINT_FORMAT, doc)
    filename = f"{doc.name}-rm-batch-labels.tspl"
    return attach_raw_label_file(
        attached_to_doctype="Purchase Receipt",
        attached_to_name=doc.name,
        filename=filename,
        raw_commands=raw_commands,
    )


def auto_print_fg_bag_labels(doc, method=None):
    if not thermal_printing_enabled() or (doc.get("purpose") or doc.get("stock_entry_type")) != "Manufacture":
        return None

    finished_rows = [row for row in doc.get("items", []) if row.get("is_finished_item") and row.get("batch_no")]
    if not finished_rows:
        return None

    raw_commands = render_print_format(FG_LABEL_PRINT_FORMAT, doc)
    filename = f"{doc.name}-fg-bag-labels.tspl"
    return attach_raw_label_file(
        attached_to_doctype="Stock Entry",
        attached_to_name=doc.name,
        filename=filename,
        raw_commands=raw_commands,
    )


def thermal_printing_enabled() -> bool:
    return bool(cint(frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, ENABLE_FIELD) or 0))


def render_print_format(print_format_name: str, doc) -> str:
    raw_commands = frappe.db.get_value("Print Format", print_format_name, "raw_commands")
    if not raw_commands:
        frappe.throw(f"Print Format {print_format_name} is missing raw commands.")

    context = {
        "doc": doc,
        "frappe": frappe,
        "get_fg_bag_size": get_fg_bag_size,
        "build_fg_bag_rows": build_fg_bag_rows,
    }
    return frappe.render_template(raw_commands, context)


def get_fg_bag_size(item_code: str) -> float:
    item_bag_size = frappe.db.get_value("Item", item_code, ITEM_BAG_SIZE_FIELD)
    if item_bag_size:
        return flt(item_bag_size)
    return flt(frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, DEFAULT_BAG_SIZE_FIELD) or 25)


def build_fg_bag_rows(doc) -> list[dict[str, object]]:
    bag_rows: list[dict[str, object]] = []
    for row in doc.get("items", []):
        if not row.get("is_finished_item") or not row.get("batch_no"):
            continue

        bag_size = get_fg_bag_size(row.item_code)
        total_qty = flt(row.get("qty") or row.get("transfer_qty"))
        if bag_size <= 0 or total_qty <= 0:
            continue

        full_bags = int(total_qty // bag_size)
        remainder = round(total_qty - (full_bags * bag_size), 3)
        total_bags = full_bags + (1 if remainder > 0 else 0)
        item_name = row.get("item_name") or frappe.db.get_value("Item", row.item_code, "item_name") or row.item_code

        for bag_index in range(1, total_bags + 1):
            current_weight = bag_size if bag_index <= full_bags else remainder
            if current_weight <= 0:
                continue
            bag_rows.append(
                {
                    "item_name": item_name,
                    "item_code": row.item_code,
                    "batch_no": row.batch_no,
                    "net_weight": round(current_weight, 3),
                    "bag_no": bag_index,
                    "total_bags": total_bags,
                    "mfg_date": doc.get("posting_date"),
                    "barcode": row.batch_no,
                }
            )
    return bag_rows


def attach_raw_label_file(attached_to_doctype: str, attached_to_name: str, filename: str, raw_commands: str):
    remove_existing_attachment(attached_to_doctype, attached_to_name, filename)
    return save_file(
        filename,
        raw_commands.encode("utf-8"),
        attached_to_doctype,
        attached_to_name,
        is_private=0,
    )


def remove_existing_attachment(attached_to_doctype: str, attached_to_name: str, filename: str):
    existing_files = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": attached_to_doctype,
            "attached_to_name": attached_to_name,
            "file_name": filename,
        },
        pluck="name",
        limit_page_length=100,
    )
    for file_name in existing_files:
        frappe.delete_doc("File", file_name, ignore_permissions=True)


def thermal_label_status() -> dict[str, object]:
    return {
        "enabled": thermal_printing_enabled(),
        "language": frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, LANGUAGE_FIELD),
        "printer_name": frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, PRINTER_NAME_FIELD),
        "default_fg_bag_size_kg": frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, DEFAULT_BAG_SIZE_FIELD),
        "rm_label_format": frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, RM_FORMAT_FIELD),
        "fg_label_format": frappe.db.get_single_value(PRINT_SETTINGS_DOCTYPE, FG_FORMAT_FIELD),
        "rm_print_format_exists": bool(frappe.db.exists("Print Format", RM_LABEL_PRINT_FORMAT)),
        "fg_print_format_exists": bool(frappe.db.exists("Print Format", FG_LABEL_PRINT_FORMAT)),
    }


def thermal_label_smoke_test() -> dict[str, object]:
    savepoint = "thermal_label_smoke_test"
    frappe.db.sql(f"SAVEPOINT {savepoint}")

    try:
        ensure_thermal_label_setup()
        supplier = frappe.db.exists("Supplier", "Test RM Supplier") or frappe.db.get_value("Supplier", {}, "name")
        rm_item = (
            frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0, "item_group": "Raw Material"}, "name")
            or frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0}, "name")
        )
        fg_item = (
            frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0, "item_group": "Finished Goods"}, "name")
            or frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0}, "name")
        )
        rm_warehouse = (
            frappe.db.get_value("Warehouse", {"name": ("like", "%Stores%")}, "name")
            or frappe.db.get_value("Warehouse", {}, "name")
        )
        fg_warehouse = (
            frappe.db.get_value("Warehouse", {"name": ("like", "%Finished Goods%")}, "name")
            or frappe.db.get_value("Warehouse", {}, "name")
        )

        purchase_receipt = frappe.get_doc(
            {
                "doctype": "Purchase Receipt",
                "supplier": supplier,
                "posting_date": nowdate(),
                "items": [
                    {
                        "item_code": rm_item,
                        "qty": 1000,
                        "received_qty": 1000,
                        "uom": frappe.db.get_value("Item", rm_item, "stock_uom"),
                        "stock_uom": frappe.db.get_value("Item", rm_item, "stock_uom"),
                        "conversion_factor": 1,
                        "rate": 1,
                        "warehouse": rm_warehouse,
                    }
                ],
            }
        )
        ensure_purchase_receipt_batch_numbers(purchase_receipt)
        rm_commands = render_print_format(RM_LABEL_PRINT_FORMAT, purchase_receipt)

        stock_entry = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "purpose": "Manufacture",
                "posting_date": nowdate(),
                MACHINE_FIELD: "Line 6",
                "items": [
                    {
                        "item_code": fg_item,
                        "qty": 1000,
                        "transfer_qty": 1000,
                        "basic_rate": 1,
                        "valuation_rate": 1,
                        "is_finished_item": 1,
                        "t_warehouse": fg_warehouse,
                    }
                ],
            }
        )
        ensure_fg_batch_numbers(stock_entry)
        fg_commands = render_print_format(FG_LABEL_PRINT_FORMAT, stock_entry)

        return {
            "settings": thermal_label_status(),
            "rm_batch_no": purchase_receipt.items[0].batch_no,
            "rm_tspl_preview": rm_commands.splitlines()[:8],
            "fg_batch_no": stock_entry.items[0].batch_no,
            "fg_label_count": len(build_fg_bag_rows(stock_entry)),
            "fg_tspl_preview": fg_commands.splitlines()[:8],
        }
    finally:
        frappe.db.sql(f"ROLLBACK TO SAVEPOINT {savepoint}")
