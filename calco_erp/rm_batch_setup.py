from __future__ import annotations

import re
from datetime import date

import frappe
from frappe.utils import cint, cstr, flt, getdate, nowdate


DEFAULT_PLANT_CODE = "B"
PLANT_CODE_K = "K"
PLANT_CODE_B = "B"


def ensure_purchase_receipt_batch_numbers(doc, method=None):
    if doc.doctype != "Purchase Receipt" or cint(doc.get("is_return")):
        return

    posting_date = getdate(doc.get("posting_date") or nowdate())
    serial_state: dict[str, int] = {}
    missing_batches: list[str] = []

    for row in doc.get("items", []):
        if not requires_purchase_receipt_batch(row):
            continue

        if row.get("batch_no"):
            row.batch_no = ensure_batch(batch_no=row.batch_no, item_code=row.item_code)
            continue

        batch_no = next_rm_batch_number(
            item_code=row.item_code,
            posting_date=posting_date,
            plant_code=infer_plant_code(doc, row),
            serial_state=serial_state,
        )
        row.batch_no = ensure_batch(batch_no=batch_no, item_code=row.item_code)

        if not row.get("batch_no"):
            missing_batches.append(cstr(row.get("item_code") or row.get("idx")))

    if missing_batches:
        frappe.throw(
            "Batch No is required on Purchase Receipt item rows for batch-controlled RM items: {0}.".format(
                ", ".join(missing_batches)
            )
        )


def requires_purchase_receipt_batch(row) -> bool:
    if not row.get("item_code") or get_effective_received_qty(row) <= 0:
        return False

    return bool(frappe.db.get_value("Item", row.item_code, "has_batch_no"))


def get_effective_received_qty(row) -> float:
    received_qty = flt(row.get("received_qty"))
    if received_qty:
        return received_qty

    return flt(row.get("qty"))


def next_rm_batch_number(
    item_code: str,
    posting_date: date,
    plant_code: str,
    serial_state: dict[str, int] | None = None,
) -> str:
    serial_state = serial_state or {}
    prefix = batch_prefix(item_code=item_code, posting_date=posting_date, plant_code=plant_code)

    next_serial = serial_state.get(prefix)
    if next_serial is None:
        next_serial = get_next_serial(prefix)

    batch_no = f"{prefix}{next_serial}"
    while frappe.db.exists("Batch", batch_no):
        next_serial += 1
        batch_no = f"{prefix}{next_serial}"

    serial_state[prefix] = next_serial + 1
    return batch_no


def batch_prefix(item_code: str, posting_date: date, plant_code: str) -> str:
    receipt_date = getdate(posting_date)
    return f"{receipt_date.strftime('%d%m')}{sanitize_item_code(item_code)}{receipt_date.strftime('%y')}{normalize_plant_code(plant_code)}"


def sanitize_item_code(item_code: str) -> str:
    cleaned = "".join(character for character in cstr(item_code).upper() if character.isalnum())
    return cleaned or "ITEM"


def normalize_plant_code(plant_code: str | None) -> str:
    code = cstr(plant_code).strip().upper()
    if code in {PLANT_CODE_B, PLANT_CODE_K}:
        return code
    return DEFAULT_PLANT_CODE


def infer_plant_code(doc, row) -> str:
    warehouse_name = cstr(row.get("warehouse") or doc.get("set_warehouse") or "").strip()
    warehouse_upper = warehouse_name.upper()

    if any(token in warehouse_upper for token in ("BADDI", " B ", "-B", "(B)", "/B", " B-")):
        return PLANT_CODE_B

    if any(token in warehouse_upper for token in ("KALA AMB", "KALAMB", " K ", "-K", "(K)", "/K", " K-")):
        return PLANT_CODE_K

    return DEFAULT_PLANT_CODE


def get_next_serial(prefix: str) -> int:
    existing_batches = frappe.get_all(
        "Batch",
        filters={"name": ("like", f"{prefix}%")},
        pluck="name",
        limit_page_length=0,
    )
    max_serial = 0
    for batch_name in existing_batches:
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", cstr(batch_name))
        if match:
            max_serial = max(max_serial, cint(match.group(1)))
    return max_serial + 1


def ensure_batch(batch_no: str, item_code: str) -> str:
    if frappe.db.exists("Batch", batch_no):
        existing_item = frappe.db.get_value("Batch", batch_no, "item")
        if existing_item and existing_item != item_code:
            frappe.throw(f"Batch {batch_no} already exists for item {existing_item}.")
        return batch_no

    batch = frappe.get_doc(
        {
            "doctype": "Batch",
            "batch_id": batch_no,
            "item": item_code,
        }
    )
    batch.insert(ignore_permissions=True)
    return batch.name


def rm_batch_number_smoke_test() -> dict[str, object]:
    savepoint = "rm_batch_number_smoke_test"
    frappe.db.sql(f"SAVEPOINT {savepoint}")

    try:
        supplier = frappe.db.exists("Supplier", "Test RM Supplier") or frappe.db.get_value("Supplier", {}, "name")
        warehouse = (
            frappe.db.get_value("Warehouse", {"name": ("like", "%Stores%")}, "name")
            or frappe.db.get_value("Warehouse", {}, "name")
        )
        item_code = (
            frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0, "item_group": "Raw Material"}, "name")
            or frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0}, "name")
        )
        if not supplier or not warehouse or not item_code:
            frappe.throw("Supplier, warehouse, or batch-tracked item is missing for RM batch smoke test.")

        receipt = frappe.get_doc(
            {
                "doctype": "Purchase Receipt",
                "supplier": supplier,
                "posting_date": nowdate(),
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "received_qty": 1,
                        "uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                        "stock_uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                        "conversion_factor": 1,
                        "rate": 1,
                        "warehouse": warehouse,
                    },
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "received_qty": 1,
                        "uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                        "stock_uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                        "conversion_factor": 1,
                        "rate": 1,
                        "warehouse": warehouse,
                    },
                ],
            }
        )

        ensure_purchase_receipt_batch_numbers(receipt)

        rows = [
            {
                "item_code": row.item_code,
                "warehouse": row.warehouse,
                "batch_no": row.batch_no,
            }
            for row in receipt.items
        ]

        return {
            "supplier": supplier,
            "warehouse": warehouse,
            "item_code": item_code,
            "posting_date": receipt.posting_date,
            "rows": rows,
            "serial_increment_ok": len(rows) == 2 and rows[0]["batch_no"] != rows[1]["batch_no"],
        }
    finally:
        frappe.db.sql(f"ROLLBACK TO SAVEPOINT {savepoint}")
