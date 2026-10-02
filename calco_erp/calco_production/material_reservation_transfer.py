from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from calco_erp.calco_production.material_reservation_draft import RESERVATION_SET_FIELD
from calco_erp.calco_production.stock_entry_warehouse_defaults import (
    SOURCE_WAREHOUSE,
    TARGET_WAREHOUSE,
    TRANSFER_PURPOSE,
)
from calco_erp.inventory.availability import validate_batch_selection


EPSILON = 1e-9


def has_submitted_calco_reservation(work_order: str | None) -> bool:
    work_order = cstr(work_order).strip()
    if not work_order:
        return False
    return bool(
        frappe.db.exists(
            "Stock Reservation Entry",
            {
                "voucher_type": "Work Order",
                "voucher_no": work_order,
                "docstatus": 1,
                RESERVATION_SET_FIELD: ("is", "set"),
            },
        )
    )


def has_any_calco_reservation(work_order: str | None) -> bool:
    work_order = cstr(work_order).strip()
    if not work_order:
        return False
    return bool(
        frappe.db.exists(
            "Stock Reservation Entry",
            {
                "voucher_type": "Work Order",
                "voucher_no": work_order,
                RESERVATION_SET_FIELD: ("is", "set"),
            },
        )
    )


def is_controlled_calco_work_order(work_order: str | None) -> bool:
    """Identify Planning Center Work Orders without requiring an SRE to exist first."""
    work_order = cstr(work_order).strip()
    if not work_order:
        return False
    from calco_erp.calco_production.fg_planning_authority import is_dashboard_name
    if is_dashboard_name(work_order) or has_any_calco_reservation(work_order):
        return True
    values = frappe.db.get_value(
        "Work Order",
        work_order,
        ["production_plan", "production_plan_item", "custom_planning_review_status"],
        as_dict=True,
    )
    return bool(
        values
        and cstr(values.get("production_plan")).strip()
        and cstr(values.get("production_plan_item")).strip()
        and cstr(values.get("custom_planning_review_status")).strip() == "Confirmed"
    )


def require_submitted_material_reservation(work_order: str | None):
    """Return the authoritative submitted SRE set or block a controlled transfer."""
    work_order = cstr(work_order).strip()
    if not is_controlled_calco_work_order(work_order):
        return []
    reservations = _get_submitted_reservations(work_order)
    if not reservations:
        frappe.throw(
            _(
                "Complete and submit Material Reservation for Work Order {0} before creating or saving Material Transfer."
            ).format(work_order)
        )
    _validate_complete_submitted_set(work_order, reservations)
    return reservations


def apply_submitted_reservation_to_stock_entry(stock_entry_data):
    """Hydrate ERPNext's standard draft with the submitted Calco SRE allocation."""
    stock_entry = frappe.get_doc(stock_entry_data)
    if not _is_controlled_transfer(stock_entry):
        return stock_entry_data
    reservations = require_submitted_material_reservation(stock_entry.work_order)
    if not reservations:
        return stock_entry_data
    remaining = _remaining_allocations(reservations)
    if not remaining:
        frappe.throw(
            _("The submitted material reservation for Work Order {0} is fully transferred.").format(
                stock_entry.work_order
            )
        )

    templates = list(stock_entry.get("items") or [])
    requested_by_line = defaultdict(float)
    template_by_line = {}
    for row in templates:
        normalized_stock_qty = _normalized_stock_qty(row)
        if not row.get("s_warehouse") or normalized_stock_qty <= 0:
            continue
        key = (
            cstr(row.get("item_code")).strip(),
            cstr(row.get("bom_secondary_item")).strip(),
        )
        requested_by_line[key] += normalized_stock_qty
        template_by_line.setdefault(key, row)

    reserved_keys = {(row["item_code"], row["voucher_detail_no"]) for row in remaining}
    uncovered = sorted(
        key for key, qty in requested_by_line.items()
        if qty > EPSILON and key not in reserved_keys
    )
    if uncovered:
        frappe.throw(
            _("Submitted reservation does not cover Work Order material rows: {0}.").format(
                ", ".join(f"{item} ({line or '-'})" for item, line in uncovered)
            )
        )

    stock_entry.set("items", [])
    remaining_request = dict(requested_by_line)
    for allocation in remaining:
        key = (allocation["item_code"], allocation["voucher_detail_no"])
        requested = remaining_request.get(key, 0)
        if requested <= EPSILON:
            continue
        transfer_qty = min(requested, allocation["remaining_qty"])
        if transfer_qty <= EPSILON:
            continue
        template = template_by_line.get(key)
        if not template:
            frappe.throw(
                _("ERPNext did not generate a Stock Entry row for reserved item {0}.").format(
                    allocation["item_code"]
                )
            )
        values = _copy_child_values(template)
        conversion_factor = flt(values.get("conversion_factor")) or 1
        values.update(
            {
                "qty": transfer_qty / conversion_factor,
                "transfer_qty": transfer_qty,
                "s_warehouse": allocation["warehouse"],
                "t_warehouse": TARGET_WAREHOUSE,
                "batch_no": allocation["batch_no"],
                "serial_no": "",
                "serial_and_batch_bundle": "",
                "use_serial_batch_fields": 1,
            }
        )
        stock_entry.append("items", values)
        remaining_request[key] = requested - transfer_qty

    shortages = [
        (item, qty)
        for (item, _line), qty in remaining_request.items()
        if qty > EPSILON
    ]
    if shortages:
        frappe.throw(
            _("Submitted reservation has insufficient remaining quantity: {0}.").format(
                ", ".join(f"{item} {round(qty, 3)}" for item, qty in shortages)
            )
        )
    stock_entry.from_warehouse = SOURCE_WAREHOUSE
    stock_entry.to_warehouse = TARGET_WAREHOUSE
    return stock_entry.as_dict()


def validate_reserved_material_transfer(doc, method=None):
    if not _is_controlled_transfer(doc):
        return
    reservations = require_submitted_material_reservation(doc.work_order)
    if not reservations:
        return

    _lock_reservations(reservations)
    reservations = _get_submitted_reservations(doc.work_order)
    allowed = _allocation_map(_remaining_allocations(reservations))
    actual = _stock_entry_allocation_map(doc)
    if not actual:
        frappe.throw(_("Material Transfer has no reserved batch allocation."))

    for key, quantity in actual.items():
        item_code, _voucher_detail_no, batch_no, warehouse = key
        if warehouse != SOURCE_WAREHOUSE:
            frappe.throw(
                _("Reserved material {0} must transfer from {1}, not {2}.").format(
                    item_code, SOURCE_WAREHOUSE, warehouse or _("blank")
                )
            )
        if quantity > flt(allowed.get(key)) + EPSILON:
            frappe.throw(
                _(
                    "Batch {0} for item {1} is not reserved for this Work Order or exceeds remaining reserved quantity {2}."
                ).format(batch_no, item_code, round(flt(allowed.get(key)), 3))
            )
        evidence = validate_batch_selection(
            item_code=item_code,
            batch_no=batch_no,
            warehouse=warehouse,
            work_order=doc.work_order,
            posting_datetime=_posting_datetime(doc),
            required_qty=quantity,
        )
        if not evidence.get("valid"):
            frappe.throw(
                _("Reserved batch {0} for item {1} is no longer eligible: {2}").format(
                    batch_no,
                    item_code,
                    "; ".join(evidence.get("exclusion_reasons") or [_("Unknown reason")]),
                )
            )
    _validate_work_order_state(doc.work_order)
    _consume_own_reservation_for_transfer(reservations, actual)


def validate_material_transfer_reservation_gate(doc, method=None):
    """Revalidate a controlled transfer on save without consuming its reservation."""
    if not _is_controlled_transfer(doc):
        return
    reservations = require_submitted_material_reservation(doc.work_order)
    if not reservations:
        return
    actual = _stock_entry_allocation_map(doc)
    allowed = _allocation_map(_remaining_allocations(reservations))
    if not actual:
        frappe.throw(_("Material Transfer has no reserved batch allocation."))
    for key, quantity in actual.items():
        item_code, _voucher_detail_no, batch_no, warehouse = key
        if quantity > flt(allowed.get(key)) + EPSILON:
            frappe.throw(
                _(
                    "Batch {0} for item {1} is not reserved for this Work Order or exceeds remaining reserved quantity {2}."
                ).format(batch_no, item_code, round(flt(allowed.get(key)), 3))
            )
        evidence = validate_batch_selection(
            item_code=item_code,
            batch_no=batch_no,
            warehouse=warehouse,
            work_order=doc.work_order,
            posting_datetime=_posting_datetime(doc),
            required_qty=quantity,
        )
        if not evidence.get("valid"):
            frappe.throw(
                _("Reserved batch {0} for item {1} is no longer eligible: {2}").format(
                    batch_no,
                    item_code,
                    "; ".join(evidence.get("exclusion_reasons") or [_("Unknown reason")]),
                )
            )
    _validate_work_order_state(doc.work_order)


def _posting_datetime(doc):
    posting_date = cstr(doc.get("posting_date")).strip()
    posting_time = cstr(doc.get("posting_time")).strip()
    if posting_date and posting_time:
        return f"{posting_date} {posting_time}"
    return posting_date or None


def _consume_own_reservation_for_transfer(reservations, actual):
    """Release the validated current transfer from its own SRE before stock posting."""
    remaining = {key: flt(quantity) for key, quantity in actual.items()}
    for reservation in reservations:
        transferred_qty = 0.0
        for entry in reservation.get("sb_entries") or []:
            key = (
                reservation.item_code,
                cstr(reservation.voucher_detail_no).strip(),
                entry.batch_no,
                reservation.warehouse,
            )
            available = max(flt(entry.qty) - flt(entry.get("delivered_qty")), 0)
            consumed = min(max(flt(remaining.get(key)), 0), available)
            delivered = flt(entry.get("delivered_qty")) + consumed
            if consumed > EPSILON:
                entry.db_set("delivered_qty", delivered, update_modified=False)
                remaining[key] = max(flt(remaining.get(key)) - consumed, 0)
            transferred_qty += delivered

        reservation.db_set("transferred_qty", transferred_qty, update_modified=False)
        reservation.update_status(update_modified=False)
        reservation.update_reserved_stock_in_bin()

    unmatched = {key: quantity for key, quantity in remaining.items() if quantity > EPSILON}
    if unmatched:
        frappe.throw(_("Validated transfer allocation no longer matches its submitted SRE."))


def sync_reservation_after_material_transfer(doc, method=None):
    """Recompute standard SRE transfer usage from submitted Stock Entry bundles."""
    if not _is_controlled_transfer(doc) or not has_submitted_calco_reservation(doc.work_order):
        return

    from erpnext.manufacturing.doctype.work_order.work_order import get_row_wise_serial_batch

    reservations = _get_submitted_reservations(doc.work_order)
    _lock_reservations(reservations)
    transferred = get_row_wise_serial_batch(doc.work_order)

    available = {}
    for key, details in transferred.items():
        for batch_no, quantity in (details.get("batch_nos") or {}).items():
            available[(key[0], key[1], batch_no)] = flt(quantity)

    for reservation in reservations:
        transferred_qty = 0.0
        for entry in reservation.get("sb_entries") or []:
            key = (reservation.item_code, reservation.warehouse, entry.batch_no)
            quantity = min(flt(entry.qty), flt(available.get(key)))
            entry.db_set("delivered_qty", quantity, update_modified=False)
            transferred_qty += quantity
            available[key] = max(flt(available.get(key)) - quantity, 0)
        reservation.db_set("transferred_qty", transferred_qty, update_modified=False)
        reservation.update_status(update_modified=False)
        reservation.update_reserved_stock_in_bin()


def _is_controlled_transfer(doc) -> bool:
    return bool(
        cstr(doc.get("work_order")).strip()
        and cstr(doc.get("purpose") or doc.get("stock_entry_type")).strip() == TRANSFER_PURPOSE
        and not cint(doc.get("is_return"))
    )


def _get_submitted_reservations(work_order):
    names = frappe.get_all(
        "Stock Reservation Entry",
        filters={
            "voucher_type": "Work Order",
            "voucher_no": work_order,
            "docstatus": 1,
            RESERVATION_SET_FIELD: ("is", "set"),
        },
        pluck="name",
        order_by="item_code asc, warehouse asc, name asc",
        limit_page_length=0,
    )
    docs = [frappe.get_doc("Stock Reservation Entry", name) for name in names]
    set_ids = {cstr(doc.get(RESERVATION_SET_FIELD)).strip() for doc in docs}
    if len(set_ids) > 1:
        frappe.throw(
            _("Work Order {0} has multiple submitted Calco reservation sets.").format(work_order)
        )
    return docs


def _validate_complete_submitted_set(work_order, reservations):
    set_id = cstr(reservations[0].get(RESERVATION_SET_FIELD)).strip()
    rows = frappe.get_all(
        "Stock Reservation Entry",
        filters={
            "voucher_type": "Work Order",
            "voucher_no": work_order,
            RESERVATION_SET_FIELD: set_id,
        },
        fields=["name", "docstatus"],
        limit_page_length=0,
    )
    if not rows or any(cint(row.docstatus) != 1 for row in rows):
        frappe.throw(
            _(
                "Material Reservation set {0} is incomplete. Submit the complete set before Material Transfer."
            ).format(set_id)
        )


def _remaining_allocations(reservations):
    result = []
    for reservation in reservations:
        if cint(reservation.docstatus) != 1:
            continue
        parent_remaining = max(
            flt(reservation.reserved_qty) - flt(reservation.transferred_qty), 0
        )
        children_remaining = 0.0
        rows = []
        for entry in reservation.get("sb_entries") or []:
            remaining = max(flt(entry.qty) - flt(entry.get("delivered_qty")), 0)
            children_remaining += remaining
            if remaining > EPSILON:
                rows.append(
                    {
                        "sre": reservation.name,
                        "reservation_set_id": reservation.get(RESERVATION_SET_FIELD),
                        "item_code": reservation.item_code,
                        "voucher_detail_no": cstr(reservation.voucher_detail_no).strip(),
                        "warehouse": reservation.warehouse,
                        "batch_no": entry.batch_no,
                        "remaining_qty": remaining,
                    }
                )
        if abs(parent_remaining - children_remaining) > EPSILON:
            frappe.throw(
                _("Reservation {0} parent and batch remaining quantities do not reconcile.").format(
                    reservation.name
                )
            )
        result.extend(rows)
    return result


def _allocation_map(allocations):
    result = defaultdict(float)
    for row in allocations:
        key = (
            row["item_code"],
            row["voucher_detail_no"],
            row["batch_no"],
            row["warehouse"],
        )
        result[key] += flt(row["remaining_qty"])
    return result


def _stock_entry_allocation_map(doc):
    result = defaultdict(float)
    for row in doc.get("items") or []:
        if not row.get("s_warehouse") or flt(row.get("transfer_qty") or row.get("qty")) <= 0:
            continue
        line = cstr(row.get("bom_secondary_item")).strip()
        batch_quantities = _row_batch_quantities(row)
        if not batch_quantities:
            frappe.throw(
                _("Reserved batch is mandatory for item {0}.").format(row.item_code)
            )
        for batch_no, quantity in batch_quantities.items():
            result[(row.item_code, line, batch_no, row.s_warehouse)] += quantity
    return result


def _row_batch_quantities(row):
    batch_no = cstr(row.get("batch_no")).strip()
    if batch_no:
        return {batch_no: flt(row.get("transfer_qty") or row.get("qty"))}

    bundle = cstr(row.get("serial_and_batch_bundle")).strip()
    if not bundle:
        return {}
    rows = frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle},
        fields=["batch_no", "qty"],
        limit_page_length=0,
    )
    result = defaultdict(float)
    for entry in rows:
        if entry.batch_no:
            result[entry.batch_no] += abs(flt(entry.qty))
    return result


def _copy_child_values(row):
    as_dict = getattr(row, "as_dict", None)
    values = dict(as_dict() if callable(as_dict) else row)
    for fieldname in (
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "parent",
        "parentfield",
        "parenttype",
    ):
        values.pop(fieldname, None)
    return values


def _normalized_stock_qty(row):
    conversion_factor = flt(row.get("conversion_factor")) or 1
    precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
    return flt(flt(row.get("qty")) * conversion_factor, precision)


def _lock_reservations(reservations):
    names = sorted(doc.name for doc in reservations)
    if names:
        frappe.db.sql(
            "SELECT name FROM `tabStock Reservation Entry` "
            "WHERE name IN %(names)s ORDER BY name FOR UPDATE",
            {"names": tuple(names)},
        )


def _validate_work_order_state(work_order):
    values = frappe.db.get_value(
        "Work Order", work_order, ["docstatus", "status"], as_dict=True
    )
    if not values or cint(values.docstatus) != 1:
        frappe.throw(_("Work Order {0} must remain submitted.").format(work_order))
    if cstr(values.status).strip() in {"Stopped", "Cancelled", "Completed"}:
        frappe.throw(
            _("Work Order {0} status {1} does not allow material transfer.").format(
                work_order, values.status
            )
        )
