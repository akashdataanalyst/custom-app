from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from calco_erp.calco_production.material_reservation_transfer import has_any_calco_reservation
from calco_erp.calco_production.stock_entry_warehouse_defaults import (
    SOURCE_WAREHOUSE,
    TARGET_WAREHOUSE,
    TRANSFER_PURPOSE,
)
from calco_erp.inventory.availability import (
    get_batch_availability,
    get_item_availability,
    get_work_order_wip_lineage,
)


EPSILON = 1e-9
CONSUMPTION_PURPOSES = {"Material Consumption for Manufacture", "Manufacture"}


@frappe.whitelist()
def get_wip_return_preview(work_order=None, *args, **kwargs) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
    wo = _validate_work_order(work_order)
    controlled = has_any_calco_reservation(work_order)
    rows = _get_reconciliation_rows(work_order) if controlled else []
    returnable = [row for row in rows if flt(row["returnable_qty"]) > EPSILON]
    return {
        "work_order": work_order,
        "controlled": controlled,
        "can_create": bool(returnable),
        "source_warehouse": TARGET_WAREHOUSE,
        "target_warehouse": SOURCE_WAREHOUSE,
        "rows": returnable,
        "reconciliation": rows,
        "reason": "" if returnable else _("No unused transferred WIP material is returnable."),
        "work_order_status": wo.status,
    }


@frappe.whitelist()
def get_work_order_rm_reconciliation(work_order=None, *args, **kwargs) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
    _validate_work_order(work_order)
    rows = _get_reconciliation_rows(work_order)
    return {
        "work_order": work_order,
        "rows": rows,
        "reconciled": all(row["reconciled"] for row in rows),
        "unexplained_wip": sum(
            abs(flt(row["discrepancy_qty"])) for row in rows if not row["reconciled"]
        ),
        "evidence_source": [
            "ERPNext submitted Stock Entry",
            "ERPNext Serial and Batch Bundle API",
            "ERPNext physical stock balance",
        ],
    }


@frappe.whitelist()
def make_unused_wip_return_stock_entry(
    work_order=None, rows=None, *args, **kwargs
) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    rows = rows if rows is not None else kwargs.get("rows")
    frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
    frappe.has_permission("Stock Entry", "create", throw=True)
    wo = _validate_work_order(work_order)
    if not has_any_calco_reservation(work_order):
        frappe.throw(_("Work Order {0} does not use the controlled Calco flow.").format(work_order))
    selected = _validate_requested_rows(_parse_rows(rows), _get_reconciliation_rows(work_order))
    return _build_standard_return_draft(wo, selected).as_dict()


def validate_unused_wip_return(doc, method=None):
    if _is_controlled_return(doc):
        _validate_return_document(doc)


def validate_unused_wip_return_on_submit(doc, method=None):
    if not _is_controlled_return(doc):
        return
    frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name = %s FOR UPDATE", (doc.work_order,))
    _validate_return_document(doc)


def refresh_work_order_return_reconciliation(doc, method=None):
    """Refresh ERPNext's own required-item counters after a return submit/cancel."""
    if not _is_controlled_return(doc):
        return
    frappe.get_doc("Work Order", doc.work_order).update_required_items()


def _is_controlled_return(doc) -> bool:
    return bool(
        cstr(doc.get("work_order")).strip()
        and _purpose(doc) == TRANSFER_PURPOSE
        and cint(doc.get("is_return"))
        and has_any_calco_reservation(doc.work_order)
    )


def _validate_work_order(work_order: str):
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))
    wo = frappe.get_doc("Work Order", work_order)
    if cint(wo.docstatus) != 1:
        frappe.throw(_("Work Order {0} must be submitted.").format(work_order))
    if cstr(wo.status).strip() in {"Stopped", "Cancelled"}:
        frappe.throw(
            _("Work Order {0} status {1} does not allow unused material return.").format(
                work_order, wo.status
            )
        )
    if cstr(wo.wip_warehouse).strip() != TARGET_WAREHOUSE:
        frappe.throw(_("Work Order {0} must use WIP warehouse {1}.").format(work_order, TARGET_WAREHOUSE))
    return wo


def _get_reconciliation_rows(work_order: str) -> list[dict[str, Any]]:
    totals = get_work_order_wip_lineage(work_order)
    result = []
    for sequence, ((item_code, batch_no), values) in enumerate(sorted(totals.items()), start=1):
        expected_wip = flt(values["net_wip_qty"])
        physical = _physical_wip(item_code, batch_no, work_order)
        remaining_wip = min(expected_wip, physical)
        evidence = sorted(
            set(
                values["transfer_entries"]
                + values["consumption_entries"]
                + values["return_entries"]
            )
        )
        result.append(
            {
                "item_code": item_code,
                "batch_no": batch_no,
                "stock_uom": values["stock_uom"],
                "transferred_qty": flt(values["transferred_qty"]),
                "actual_consumed_qty": flt(values["consumed_qty"]),
                "returned_qty": flt(values["returned_qty"]),
                "expected_wip_qty": expected_wip,
                "physical_wip_qty": physical,
                "remaining_wip_qty": remaining_wip,
                "returnable_qty": remaining_wip,
                "reconciled": abs(expected_wip - physical) <= EPSILON,
                "discrepancy_qty": physical - expected_wip,
                "evidence": evidence,
                "ordering_sequence": sequence,
            }
        )
    return result


def _evidence_quantities(row) -> dict[str, float]:
    batch_nos = row.get("batch_nos") or {}
    if batch_nos:
        return {cstr(batch).strip(): flt(qty) for batch, qty in batch_nos.items()}
    return {cstr(row.get("batch_no")).strip(): flt(row.get("qty"))}


def _physical_wip(item_code: str, batch_no: str, work_order: str) -> float:
    if batch_no:
        availability = get_batch_availability(
            item_code=item_code,
            batch_no=batch_no,
            warehouse=TARGET_WAREHOUSE,
            work_order=work_order,
        )
    else:
        availability = get_item_availability(
            item_code=item_code,
            warehouse=TARGET_WAREHOUSE,
            work_order=work_order,
        )
    return max(flt(availability.get("physical_quantity")), 0)


def _parse_rows(rows) -> list[dict[str, Any]]:
    if isinstance(rows, str):
        rows = frappe.parse_json(rows)
    return list(rows or [])


def _validate_requested_rows(requested, reconciliation):
    allowed = {
        (row["item_code"], row["batch_no"]): flt(row["returnable_qty"])
        for row in reconciliation
    }
    selected = defaultdict(float)
    for index, row in enumerate(requested, start=1):
        item_code = cstr(row.get("item_code")).strip()
        batch_no = cstr(row.get("batch_no")).strip()
        quantity = flt(row.get("qty") or row.get("return_qty"))
        if not item_code or quantity <= 0:
            frappe.throw(_("Row #{0}: Item and positive return quantity are required.").format(index))
        key = (item_code, batch_no)
        if key not in allowed:
            frappe.throw(
                _("Row #{0}: Item {1}, batch {2} was not transferred for this Work Order.").format(
                    index, item_code, batch_no or _("blank")
                )
            )
        selected[key] += quantity
        if selected[key] > allowed[key] + EPSILON:
            frappe.throw(
                _("Row #{0}: Return {1} exceeds returnable WIP quantity {2} for {3} / {4}.").format(
                    index,
                    round(selected[key], 3),
                    round(allowed[key], 3),
                    item_code,
                    batch_no or _("non-batch"),
                )
            )
    if not selected:
        frappe.throw(_("Enter a positive return quantity for at least one row."))
    return [
        {"item_code": item, "batch_no": batch, "qty": qty}
        for (item, batch), qty in selected.items()
    ]


def _build_standard_return_draft(wo, selected):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_return_entry

    stock_entry = make_stock_return_entry(wo.name)
    if not stock_entry:
        frappe.throw(_("ERPNext found no unused transferred material for Work Order {0}.").format(wo.name))
    templates = {}
    for row in stock_entry.get("items") or []:
        if row.get("s_warehouse") and not row.get("is_finished_item"):
            templates.setdefault(cstr(row.item_code).strip(), row)
    stock_entry.set("items", [])
    for selection in selected:
        template = templates.get(selection["item_code"])
        if not template:
            frappe.throw(_("ERPNext did not generate a return template for item {0}.").format(selection["item_code"]))
        values = _copy_child_values(template)
        conversion_factor = flt(values.get("conversion_factor")) or 1
        values.update(
            {
                "qty": flt(selection["qty"]) / conversion_factor,
                "transfer_qty": flt(selection["qty"]),
                "s_warehouse": TARGET_WAREHOUSE,
                "t_warehouse": SOURCE_WAREHOUSE,
                "batch_no": selection["batch_no"],
                "serial_no": "",
                "serial_and_batch_bundle": "",
                "use_serial_batch_fields": 1 if selection["batch_no"] else 0,
                "is_finished_item": 0,
            }
        )
        stock_entry.append("items", values)
    stock_entry.from_warehouse = TARGET_WAREHOUSE
    stock_entry.to_warehouse = SOURCE_WAREHOUSE
    stock_entry.is_return = 1
    stock_entry.purpose = TRANSFER_PURPOSE
    stock_entry.stock_entry_type = TRANSFER_PURPOSE
    return stock_entry


def _validate_return_document(doc):
    _validate_work_order(doc.work_order)
    if cstr(doc.get("from_warehouse")).strip() != TARGET_WAREHOUSE:
        frappe.throw(_("Unused material must return from {0}.").format(TARGET_WAREHOUSE))
    if cstr(doc.get("to_warehouse")).strip() != SOURCE_WAREHOUSE:
        frappe.throw(_("Unused material must return to {0}.").format(SOURCE_WAREHOUSE))
    requested = []
    for row in doc.get("items") or []:
        if cstr(row.get("s_warehouse")).strip() != TARGET_WAREHOUSE:
            frappe.throw(_("Return row {0} must source from {1}.").format(row.idx, TARGET_WAREHOUSE))
        if cstr(row.get("t_warehouse")).strip() != SOURCE_WAREHOUSE:
            frappe.throw(_("Return row {0} must target {1}.").format(row.idx, SOURCE_WAREHOUSE))
        quantities = _document_batch_quantities(row) or {
            "": flt(row.get("transfer_qty") or row.get("qty"))
        }
        for batch_no, quantity in quantities.items():
            requested.append({"item_code": row.item_code, "batch_no": batch_no, "qty": quantity})
    _validate_requested_rows(requested, _get_reconciliation_rows(doc.work_order))


def _document_batch_quantities(row):
    batch_no = cstr(row.get("batch_no")).strip()
    if batch_no:
        return {batch_no: flt(row.get("transfer_qty") or row.get("qty"))}
    bundle = cstr(row.get("serial_and_batch_bundle")).strip()
    if not bundle:
        return {}
    entries = frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle},
        fields=["batch_no", "qty"],
        limit_page_length=0,
    )
    result = defaultdict(float)
    for entry in entries:
        if entry.batch_no:
            result[entry.batch_no] += abs(flt(entry.qty))
    return dict(result)


def _copy_child_values(row):
    as_dict = getattr(row, "as_dict", None)
    values = dict(as_dict() if callable(as_dict) else row)
    for fieldname in (
        "name", "owner", "creation", "modified", "modified_by", "docstatus",
        "idx", "parent", "parentfield", "parenttype",
    ):
        values.pop(fieldname, None)
    return values


def _purpose(doc) -> str:
    return cstr(doc.get("purpose") or doc.get("stock_entry_type")).strip()
