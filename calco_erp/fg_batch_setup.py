from __future__ import annotations

import re
from datetime import date

import frappe
from frappe.utils import cint, cstr, flt, getdate, now_datetime

from calco_erp.machine_setup import MACHINE_FIELD
from calco_erp.calco_quality.rm_warehouse_flow import create_outward_batch_bundle
from calco_erp.rm_batch_setup import ensure_batch
from calco_erp.utils.dependencies import validate_stock_entry_chain


def prepare_manufacture_stock_entry(doc, method=None):
    ensure_fg_batch_numbers(doc)
    validate_stock_entry_chain(doc, method)
    normalize_rm_batch_consumption_rows(doc)


def ensure_fg_batch_numbers(doc, method=None):
    if get_stock_entry_purpose(doc) != "Manufacture":
        return

    posting_date = getdate(doc.get("posting_date"))
    line_number = get_line_number(doc)
    serial_state: dict[str, int] = {}
    controlled = _is_controlled_final_manufacture(doc)
    if controlled:
        production_batch = _get_controlled_production_batch(doc.work_order)

    for row in doc.get("items", []):
        if not should_generate_fg_batch(row):
            continue

        batch_no = (
            production_batch
            if controlled and row.get("is_finished_item")
            else next_fg_batch_number(
                line_number=line_number,
                posting_date=posting_date,
                serial_state=serial_state,
            )
        )
        row.batch_no = ensure_batch(batch_no=batch_no, item_code=row.item_code)
        if controlled and row.get("is_finished_item"):
            _sync_controlled_fg_batch(doc.work_order, row.batch_no)


def allocate_production_batch_on_compounding_start(job_card, start_time=None) -> str:
    from calco_erp.calco_production.manufacture_entry import is_controlled_work_order
    from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION

    if not job_card or cstr(job_card.get("operation")).strip() != COMPOUNDING_OPERATION:
        return ""
    work_order = cstr(job_card.get("work_order")).strip()
    if not work_order or not is_controlled_work_order(work_order):
        return ""

    from calco_erp.calco_production.parallel_production import lock_identity
    lock_identity()
    frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name = %s FOR UPDATE", (work_order,))
    wo = frappe.get_doc("Work Order", work_order)
    existing = cstr(wo.get("custom_fg_batch_no")).strip()
    if existing:
        return existing

    posting_date = getdate(start_time or now_datetime())
    line_number = get_line_number(wo)
    prefix = fg_batch_prefix(line_number=line_number, posting_date=posting_date)
    _acquire_batch_prefix_lock(prefix, job_card)
    batch_no = next_fg_batch_number(line_number=line_number, posting_date=posting_date)
    batch_no = ensure_batch(batch_no=batch_no, item_code=wo.production_item)
    _sync_controlled_fg_batch(work_order, batch_no)
    return batch_no


def _get_controlled_production_batch(work_order: str) -> str:
    batch_no = cstr(
        frappe.db.get_value("Work Order", work_order, "custom_fg_batch_no")
    ).strip()
    if not batch_no:
        frappe.throw(
            "Production batch identity is missing. Start the Compounding / Extrusion Job Card first."
        )
    return batch_no


def get_stock_entry_purpose(doc) -> str:
    return (doc.get("stock_entry_type") or doc.get("purpose") or "").strip()


def should_generate_fg_batch(row) -> bool:
    if not row.get("is_finished_item"):
        return False
    if not row.get("item_code") or row.get("batch_no"):
        return False
    return bool(frappe.db.get_value("Item", row.item_code, "has_batch_no"))


def get_line_number(doc) -> str:
    machine = cstr(doc.get(MACHINE_FIELD)).strip()
    if not machine and doc.get("work_order"):
        machine = cstr(frappe.db.get_value("Work Order", doc.work_order, MACHINE_FIELD) or "").strip()

    line_number = extract_line_number(machine)
    if line_number:
        return line_number

    if machine and frappe.db.exists("Workstation", machine):
        description = cstr(frappe.db.get_value("Workstation", machine, "description") or "").strip()
        line_number = extract_line_number(description)
        if line_number:
            return line_number

    frappe.throw("Machine with a numeric production line is mandatory for FG batch generation.")


def extract_line_number(value: str | None) -> str:
    match = re.search(r"(\d+)", cstr(value))
    return match.group(1) if match else ""


def next_fg_batch_number(
    line_number: str,
    posting_date: date,
    serial_state: dict[str, int] | None = None,
) -> str:
    serial_state = serial_state or {}
    prefix = fg_batch_prefix(line_number=line_number, posting_date=posting_date)

    next_serial = serial_state.get(prefix)
    if next_serial is None:
        next_serial = get_next_fg_serial(prefix)

    batch_no = f"{prefix}{next_serial}"
    while frappe.db.exists("Batch", batch_no) or frappe.db.exists("Production Consumption Entry", {"fg_batch_no": batch_no}):
        next_serial += 1
        batch_no = f"{prefix}{next_serial}"

    serial_state[prefix] = next_serial + 1
    return batch_no


def fg_batch_prefix(line_number: str, posting_date: date) -> str:
    production_date = getdate(posting_date)
    return f"{cstr(line_number)}{production_date.strftime('%m%d')}{production_date.strftime('%Y')[-1]}"


def get_next_fg_serial(prefix: str) -> int:
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


def _is_controlled_final_manufacture(doc) -> bool:
    if get_stock_entry_purpose(doc) != "Manufacture" or not doc.get("work_order"):
        return False
    from calco_erp.calco_production.manufacture_entry import is_controlled_work_order

    return is_controlled_work_order(doc.work_order)


def _acquire_batch_prefix_lock(prefix: str, doc):
    if getattr(doc.flags, "calco_fg_batch_lock", None) == prefix:
        return
    lock_name = f"calco_fg_batch:{prefix}"
    acquired = frappe.db.sql("SELECT GET_LOCK(%s, 10)", (lock_name,))[0][0]
    if cint(acquired) != 1:
        frappe.throw(f"Could not lock FG batch sequence {prefix}. Please retry.")

    def release_lock():
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))

    frappe.db.after_commit.add(release_lock)
    frappe.db.after_rollback.add(release_lock)
    doc.flags.calco_fg_batch_lock = prefix


def _sync_controlled_fg_batch(work_order: str, batch_no: str):
    if frappe.get_meta("Work Order").has_field("custom_fg_batch_no"):
        frappe.db.set_value(
            "Work Order", work_order, "custom_fg_batch_no", batch_no, update_modified=False
        )
    if frappe.get_meta("Job Card").has_field("custom_fg_batch_no"):
        frappe.db.sql(
            """UPDATE `tabJob Card`
               SET custom_fg_batch_no = %s
             WHERE work_order = %s AND docstatus < 2""",
            (batch_no, work_order),
        )


def normalize_rm_batch_consumption_rows(doc, method=None):
    if get_stock_entry_purpose(doc) not in {"Manufacture", "Material Consumption for Manufacture"}:
        return

    posting_datetime = now_datetime()
    for row in doc.get("items", []):
        if row.get("is_finished_item"):
            continue
        if not row.get("item_code") or not row.get("s_warehouse") or not row.get("batch_no"):
            continue
        if row.get("serial_and_batch_bundle"):
            continue

        row.serial_and_batch_bundle = create_outward_batch_bundle(
            item_code=row.item_code,
            batch_no=row.batch_no,
            qty=flt(row.get("qty") or row.get("transfer_qty") or 0),
            warehouse=row.s_warehouse,
            company=doc.company,
            posting_datetime=posting_datetime,
        )
        row.use_serial_batch_fields = 0
        row.batch_no = ""


def fg_batch_number_smoke_test() -> dict[str, object]:
    savepoint = "fg_batch_number_smoke_test"
    frappe.db.sql(f"SAVEPOINT {savepoint}")

    try:
        posting_date = "2025-07-28"
        machine = "Line 6"
        item_code = (
            frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0, "item_group": "Finished Goods"}, "name")
            or frappe.db.get_value("Item", {"has_batch_no": 1, "disabled": 0}, "name")
        )
        if not item_code:
            frappe.throw("No batch-tracked finished good item is available for FG batch smoke test.")

        docs = []
        for _ in range(2):
            docs.append(
                frappe.get_doc(
                    {
                        "doctype": "Stock Entry",
                        "purpose": "Manufacture",
                        "posting_date": posting_date,
                        MACHINE_FIELD: machine,
                        "items": [
                            {
                                "item_code": item_code,
                                "qty": 1,
                                "transfer_qty": 1,
                                "basic_rate": 1,
                                "valuation_rate": 1,
                                "is_finished_item": 1,
                                "t_warehouse": frappe.db.get_value("Warehouse", {"name": ("like", "%Finished Goods%")}, "name")
                                or frappe.db.get_value("Warehouse", {}, "name"),
                            }
                        ],
                    }
                )
            )

        for doc in docs:
            ensure_fg_batch_numbers(doc)

        batches = [doc.items[0].batch_no for doc in docs]
        return {
            "posting_date": posting_date,
            "machine": machine,
            "item_code": item_code,
            "batches": batches,
            "serial_increment_ok": len(batches) == 2 and batches[0] != batches[1],
        }
    finally:
        frappe.db.sql(f"ROLLBACK TO SAVEPOINT {savepoint}")
