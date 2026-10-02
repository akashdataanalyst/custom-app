from __future__ import annotations

import json
from contextlib import contextmanager
from time import perf_counter

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime

from calco_erp.calco_production.material_reservation import READY_TO_RESERVE, plan_material_reservation
from calco_erp.calco_production.material_reservation_draft import (
    ALLOCATION_HASH_FIELD,
    EFFECTIVE_QTY_FIELD,
    RESERVATION_SET_FIELD,
    make_allocation_basis_hash,
)


ALLOWED_SUBMITTER_ROLES = {"Stock Manager"}
EPSILON = 1e-9
DRAFT = "Draft"
STALE = "Stale"
RESERVED = "Reserved"
PARTIALLY_USED = "Partially Used"
CLOSED = "Closed"
CANCELLED = "Cancelled"
FAILED = "Failed"
STALE_RESERVATION = "STALE_RESERVATION"
_CONTEXT_KEY = "calco_material_reservation_set_operation"


@frappe.whitelist()
def get_material_reservation_set_state(work_order=None, reservation_set_id=None):
    frappe.has_permission("Work Order", ptype="read", throw=True)
    rows = _resolve_set_rows(work_order, reservation_set_id, include_cancelled=True)
    if not rows:
        return {
            "state": DRAFT,
            "work_order": cstr(work_order).strip(),
            "reservation_set_id": cstr(reservation_set_id).strip(),
            "sre_names": [],
            "authorized": _has_submitter_role(),
            "can_submit": False,
            "can_cancel": False,
        }

    state = _derive_set_state(rows)
    quantities = _summarize_set_quantities(rows)
    work_order_name = _single_value(rows, "voucher_no", _("Work Order"))
    set_id = _single_value(rows, RESERVATION_SET_FIELD, _("Reservation Set"))
    result = {
        "state": state,
        "work_order": work_order_name,
        "reservation_set_id": set_id,
        "sre_names": [row.name for row in rows],
        "authorized": _has_submitter_role(),
        "can_submit": state == DRAFT and _has_submitter_role(),
        "can_cancel": state == RESERVED and _has_submitter_role(),
        **quantities,
    }
    if state == DRAFT:
        differences = _compare_preview_to_set(plan_material_reservation(work_order_name), rows)
        if differences:
            result.update({"state": STALE, "can_submit": False, "differences": differences})
    return result


@frappe.whitelist()
def get_material_reservation_review(work_order=None, reservation_set_id=None):
    """Return the persisted SRE allocation without recalculating the planner."""
    frappe.has_permission("Work Order", ptype="read", throw=True)
    rows = _resolve_set_rows(work_order, reservation_set_id, include_cancelled=True)
    if not rows:
        frappe.throw(_("No Calco material reservation set was found."))

    state = _derive_set_state(rows)
    work_order_name = _single_value(rows, "voucher_no", _("Work Order"))
    set_id = _single_value(rows, RESERVATION_SET_FIELD, _("Reservation Set"))
    sre_names = [row.name for row in rows]
    headers = {row.name: row for row in rows}
    child_rows = frappe.get_all(
        "Serial and Batch Entry",
        filters={
            "parent": ("in", sre_names),
            "parenttype": "Stock Reservation Entry",
            "parentfield": "sb_entries",
        },
        fields=["parent", "idx", "item_code", "batch_no", "qty", "warehouse"],
        order_by="parent asc, idx asc",
        limit_page_length=0,
    )
    allocations = []
    for child in child_rows:
        child = frappe._dict(child)
        header = headers.get(child.parent)
        allocations.append(
            {
                "sre": child.parent,
                "item_code": child.item_code or header.item_code,
                "batch_no": child.batch_no,
                "reserved_qty": flt(child.qty),
                "warehouse": child.warehouse or header.warehouse,
            }
        )

    quantities = _summarize_set_quantities(rows)
    return {
        "state": state,
        "status_label": _("Draft Reservation") if state == DRAFT else state,
        "work_order": work_order_name,
        "reservation_set_id": set_id,
        "sre_names": sre_names,
        "total_rm_items": len(rows),
        "total_reserved_qty": quantities["reserved_qty"],
        "allocations": allocations,
        "authorized": _has_submitter_role(),
        "can_submit": state == DRAFT and _has_submitter_role(),
    }


@frappe.whitelist()
def submit_material_reservation(work_order=None, reservation_set_id=None):
    """Atomically submit a complete Calco Work Order reservation set."""
    _require_submitter_role()
    started_at = perf_counter()
    savepoint = "calco_phase_5_3_reservation_submit"
    frappe.db.savepoint(savepoint)
    try:
        initial_rows = _resolve_set_rows(work_order, reservation_set_id, include_cancelled=True)
        if not initial_rows:
            frappe.throw(_("No Calco material reservation set was found."))
        state = _derive_set_state(initial_rows)
        if state == RESERVED:
            return _result(RESERVED, initial_rows, started_at, idempotent=True)
        if state != DRAFT:
            frappe.throw(_("Reservation set is {0}; only a complete Draft set can be submitted.").format(state))

        work_order_name = _single_value(initial_rows, "voucher_no", _("Work Order"))
        set_id = _single_value(initial_rows, RESERVATION_SET_FIELD, _("Reservation Set"))
        _lock_work_order(work_order_name)
        _lock_set_rows(set_id)
        locked_rows = _resolve_set_rows(work_order_name, set_id, include_cancelled=True)
        if _derive_set_state(locked_rows) != DRAFT:
            frappe.throw(_("Reservation set changed while submission was starting."))
        work_order_doc = _validate_work_order(work_order_name)
        lock_started = perf_counter()
        _lock_inventory_keys(_inventory_keys(locked_rows))
        lock_time_ms = _elapsed_ms(lock_started)
        preview_started = perf_counter()
        preview = plan_material_reservation(work_order_doc)
        preview_time_ms = _elapsed_ms(preview_started)
        differences = _compare_preview_to_set(preview, locked_rows)
        if differences:
            return _stale_result(locked_rows, differences, started_at, lock_time_ms, preview_time_ms)
        _validate_no_conflicting_submitted_set(work_order_name, set_id)
        docs = [frappe.get_doc("Stock Reservation Entry", row.name) for row in locked_rows]
        with _approved_operation("submit", set_id, [doc.name for doc in docs]):
            for doc in docs:
                doc.submit()
        _add_audit_comment(work_order_doc, _("Material Reservation Submitted"), preview, set_id, docs)
        result = _result(RESERVED, docs, started_at)
        result.update({
            "lock_time_ms": lock_time_ms,
            "preview_time_ms": preview_time_ms,
            "submission_time_ms": max(round(result["execution_time_ms"] - lock_time_ms - preview_time_ms, 3), 0),
        })
        return result
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise


@frappe.whitelist()
def cancel_material_reservation(work_order=None, reservation_set_id=None):
    """Atomically cancel an active submitted Calco reservation set."""
    _require_submitter_role()
    started_at = perf_counter()
    savepoint = "calco_phase_5_3_reservation_cancel"
    frappe.db.savepoint(savepoint)
    try:
        initial_rows = _resolve_set_rows(work_order, reservation_set_id, include_cancelled=True)
        if not initial_rows:
            frappe.throw(_("No Calco material reservation set was found."))
        state = _derive_set_state(initial_rows)
        if state == CANCELLED:
            return _result(CANCELLED, initial_rows, started_at, idempotent=True)
        if state != RESERVED:
            frappe.throw(_("Reservation set is {0}; only a Reserved set can be cancelled.").format(state))
        work_order_name = _single_value(initial_rows, "voucher_no", _("Work Order"))
        set_id = _single_value(initial_rows, RESERVATION_SET_FIELD, _("Reservation Set"))
        _lock_work_order(work_order_name)
        _lock_set_rows(set_id)
        locked_rows = _resolve_set_rows(work_order_name, set_id, include_cancelled=True)
        if _derive_set_state(locked_rows) != RESERVED:
            frappe.throw(_("Reservation set changed while cancellation was starting."))
        _lock_inventory_keys(_inventory_keys(locked_rows))
        docs = [frappe.get_doc("Stock Reservation Entry", row.name) for row in locked_rows]
        with _approved_operation("cancel", set_id, [doc.name for doc in docs]):
            for doc in docs:
                doc.cancel()
        work_order_doc = frappe.get_doc("Work Order", work_order_name)
        _add_audit_comment(work_order_doc, _("Material Reservation Cancelled"), None, set_id, docs)
        return _result(CANCELLED, docs, started_at)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise


def protect_calco_sre_set_operation(doc, method=None):
    """Reject individual submit/cancel operations for Calco-controlled SREs."""
    set_id = cstr(doc.get(RESERVATION_SET_FIELD)).strip()
    if not set_id:
        return
    operation = "submit" if method == "before_submit" else "cancel"
    context = getattr(frappe.local, _CONTEXT_KEY, None) or {}
    if (
        context.get("operation") == operation
        and context.get("reservation_set_id") == set_id
        and doc.name in set(context.get("sre_names") or [])
    ):
        return
    frappe.throw(
        _(
            "Calco reservation {0} belongs to reservation set {1}. Use the controlled {2} Material Reservation action from the Work Order."
        ).format(doc.name, set_id, _("Submit") if operation == "submit" else _("Cancel"))
    )


def prevent_work_order_with_active_reservation_cancel(doc, method=None):
    active = _active_calco_reservations(doc.name)
    if active:
        frappe.throw(
            _(
                "Cancel the active Material Reservation set before cancelling Work Order {0}. Active reservations: {1}."
            ).format(doc.name, ", ".join(active))
        )


@frappe.whitelist()
def stop_unstop_with_reservation_guard(work_order, status):
    if cstr(status).strip() == "Stopped":
        from calco_erp.calco_production.stopped_execution import assert_stop_allowed
        assert_stop_allowed(work_order)
        active = _active_calco_reservations(work_order)
        if active:
            frappe.throw(
                _(
                    "Cancel the active Material Reservation set before stopping Work Order {0}. Active reservations: {1}."
                ).format(work_order, ", ".join(active))
            )
    from erpnext.manufacturing.doctype.work_order.work_order import stop_unstop

    return stop_unstop(work_order, status)


def _active_calco_reservations(work_order):
    return frappe.get_all(
        "Stock Reservation Entry",
        filters={
            "voucher_type": "Work Order",
            "voucher_no": work_order,
            "docstatus": 1,
            RESERVATION_SET_FIELD: ("is", "set"),
            "status": ("not in", ["Closed", "Delivered", "Cancelled"]),
        },
        pluck="name",
        limit_page_length=0,
    )


def _resolve_set_rows(work_order, reservation_set_id, include_cancelled=False):
    work_order = cstr(work_order).strip()
    reservation_set_id = cstr(reservation_set_id).strip()
    if not work_order and not reservation_set_id:
        frappe.throw(_("Work Order or Reservation Set is required."))
    filters = {
        "voucher_type": "Work Order",
        RESERVATION_SET_FIELD: ("is", "set"),
    }
    if work_order:
        filters["voucher_no"] = work_order
    if reservation_set_id:
        filters[RESERVATION_SET_FIELD] = reservation_set_id
    if not include_cancelled:
        filters["docstatus"] = ("<", 2)
    rows = frappe.get_all(
        "Stock Reservation Entry",
        filters=filters,
        fields=[
            "name", "docstatus", "status", "voucher_type", "voucher_no",
            "voucher_detail_no", "item_code", "warehouse", "voucher_qty",
            "reserved_qty", EFFECTIVE_QTY_FIELD, ALLOCATION_HASH_FIELD,
            "transferred_qty", "delivered_qty", "consumed_qty",
            RESERVATION_SET_FIELD,
        ],
        order_by="item_code asc, warehouse asc, name asc",
        limit_page_length=0,
    )
    rows = [frappe._dict(row) for row in rows]
    set_ids = {row.get(RESERVATION_SET_FIELD) for row in rows}
    if not reservation_set_id and len(set_ids) > 1:
        frappe.throw(
            _("Work Order {0} has multiple Calco reservation sets: {1}.").format(
                work_order, ", ".join(sorted(set_ids))
            )
        )
    return rows


def _derive_set_state(rows):
    statuses = {cint(row.docstatus) for row in rows}
    if statuses == {0}:
        return DRAFT
    if statuses == {1}:
        quantities = _summarize_set_quantities(rows)
        sre_statuses = {cstr(row.status).strip() for row in rows}
        if quantities["transferred_qty"] > EPSILON or sre_statuses & {PARTIALLY_USED, CLOSED}:
            if quantities["remaining_qty"] <= EPSILON or sre_statuses == {CLOSED}:
                return CLOSED
            return PARTIALLY_USED
        return RESERVED
    if statuses == {2}:
        return CANCELLED
    return FAILED


def _summarize_set_quantities(rows):
    reserved_qty = sum(max(flt(row.get("reserved_qty")), 0) for row in rows)
    transferred_qty = sum(max(flt(row.get("transferred_qty")), 0) for row in rows)
    return {
        "reserved_qty": reserved_qty,
        "transferred_qty": transferred_qty,
        "remaining_qty": max(reserved_qty - transferred_qty, 0),
    }


def _validate_work_order(work_order_name):
    doc = frappe.get_doc("Work Order", work_order_name)
    if cint(doc.docstatus) != 1:
        frappe.throw(_("Work Order {0} must be submitted.").format(work_order_name))
    if cstr(doc.status).strip() in {"Stopped", "Cancelled"}:
        frappe.throw(
            _("Work Order {0} is {1} and cannot reserve material.").format(
                work_order_name, doc.status
            )
        )
    return doc


def _compare_preview_to_set(preview, rows):
    differences = []
    if preview.get("overall_status") != READY_TO_RESERVE:
        differences.append({
            "field": "overall_status",
            "draft": DRAFT,
            "current": preview.get("overall_status"),
            "reason": "; ".join(preview.get("validation_errors") or [])
            or _("Current planner is not ready to reserve."),
        })
        return differences

    expected_hash = make_allocation_basis_hash(preview)
    for row in rows:
        if cstr(row.get(ALLOCATION_HASH_FIELD)) != expected_hash:
            differences.append({
                "sre": row.name,
                "field": ALLOCATION_HASH_FIELD,
                "draft": row.get(ALLOCATION_HASH_FIELD),
                "current": expected_hash,
            })
        if abs(flt(row.get(EFFECTIVE_QTY_FIELD)) - flt(preview.get("effective_production_qty"))) > EPSILON:
            differences.append({
                "sre": row.name,
                "field": EFFECTIVE_QTY_FIELD,
                "draft": flt(row.get(EFFECTIVE_QTY_FIELD)),
                "current": flt(preview.get("effective_production_qty")),
            })

    expected = _expected_set(preview)
    actual = _actual_set(rows)
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            differences.append({
                "item_code": key,
                "field": "reservation_basis",
                "draft": actual.get(key),
                "current": expected.get(key),
            })
    return differences


def _expected_set(preview):
    result = {}
    for row in preview.get("raw_materials") or []:
        item_code = cstr(row.get("item_code")).strip()
        if item_code in result:
            frappe.throw(_("Planner returned duplicate RM {0}.").format(item_code))
        item_rows = sorted(row.get("work_order_item_rows") or [])
        result[item_code] = {
            "required_qty": _qty(row.get("required_qty")),
            "warehouse": cstr(row.get("source_warehouse")).strip(),
            "work_order_item_rows": item_rows[:1],
            "batches": sorted([
                (cstr(value.get("batch_no")).strip(), _qty(value.get("allocated_qty")))
                for value in row.get("allocations") or []
            ]),
        }
    return result


def _actual_set(rows):
    result = {}
    for row in rows:
        item_code = cstr(row.item_code).strip()
        if item_code in result:
            result[item_code] = {"error": _("Duplicate Draft SRE for RM.")}
            continue
        children = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parenttype": "Stock Reservation Entry", "parent": row.name},
            fields=["batch_no", "qty", "warehouse"],
            order_by="batch_no asc, name asc",
            limit_page_length=0,
        )
        result[item_code] = {
            "required_qty": _qty(row.reserved_qty),
            "warehouse": cstr(row.warehouse).strip(),
            "work_order_item_rows": [cstr(row.voucher_detail_no).strip()],
            "batches": sorted([
                (cstr(child.batch_no).strip(), _qty(child.qty)) for child in children
            ]),
        }
        wrong_warehouses = sorted({
            cstr(child.warehouse).strip()
            for child in children
            if cstr(child.warehouse).strip() != cstr(row.warehouse).strip()
        })
        if wrong_warehouses:
            result[item_code]["wrong_child_warehouses"] = wrong_warehouses
    return result


def _validate_no_conflicting_submitted_set(work_order, set_id):
    rows = frappe.get_all(
        "Stock Reservation Entry",
        filters={
            "voucher_type": "Work Order", "voucher_no": work_order,
            "docstatus": 1,
        },
        fields=["name", RESERVATION_SET_FIELD],
        limit_page_length=0,
    )
    conflicts = [row.name for row in rows if cstr(row.get(RESERVATION_SET_FIELD)) != set_id]
    if conflicts:
        frappe.throw(_("Conflicting submitted reservations exist: {0}.").format(", ".join(conflicts)))


def _inventory_keys(rows):
    return sorted({(cstr(row.item_code).strip(), cstr(row.warehouse).strip()) for row in rows})


def _lock_work_order(work_order):
    found = frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name=%s FOR UPDATE", work_order)
    if not found:
        frappe.throw(_("Work Order {0} does not exist.").format(work_order))


def _lock_set_rows(set_id):
    frappe.db.sql(
        f"""SELECT name FROM `tabStock Reservation Entry`
            WHERE `{RESERVATION_SET_FIELD}`=%s ORDER BY name FOR UPDATE""",
        set_id,
    )


def _lock_inventory_keys(keys):
    """Serialize Calco reservations per Item/Warehouse without changing stock."""
    if not keys:
        frappe.throw(_("Reservation set has no Item/Warehouse keys."))
    for item_code, warehouse in keys:
        bins = frappe.db.sql(
            """SELECT name FROM `tabBin` WHERE item_code=%s AND warehouse=%s
               ORDER BY name FOR UPDATE""",
            (item_code, warehouse),
        )
        if not bins:
            frappe.throw(_("Bin is missing for Item {0} in Warehouse {1}.").format(item_code, warehouse))
        frappe.db.sql(
            """SELECT name FROM `tabStock Reservation Entry`
               WHERE item_code=%s AND warehouse=%s AND docstatus=1
               ORDER BY name FOR UPDATE""",
            (item_code, warehouse),
        )


@contextmanager
def _approved_operation(operation, reservation_set_id, sre_names):
    previous = getattr(frappe.local, _CONTEXT_KEY, None)
    setattr(frappe.local, _CONTEXT_KEY, {
        "operation": operation,
        "reservation_set_id": reservation_set_id,
        "sre_names": list(sre_names),
    })
    try:
        yield
    finally:
        setattr(frappe.local, _CONTEXT_KEY, previous)


def _add_audit_comment(work_order, action, preview, set_id, docs):
    details = {
        "reservation_set_id": set_id,
        "work_order": work_order.name,
        "actor": frappe.session.user,
        "timestamp": str(now_datetime()),
        "effective_production_qty": flt(
            (preview or {}).get("effective_production_qty") or docs[0].get(EFFECTIVE_QTY_FIELD)
        ),
        "allocation_hash": cstr(
            (make_allocation_basis_hash(preview) if preview else "")
            or docs[0].get(ALLOCATION_HASH_FIELD)
        ),
        "sre_names": [doc.name for doc in docs],
        "allocations": [{
            "sre": doc.name,
            "item_code": doc.item_code,
            "warehouse": doc.warehouse,
            "reserved_qty": flt(doc.reserved_qty),
            "batches": [
                {"batch_no": row.batch_no, "qty": flt(row.qty)}
                for row in doc.get("sb_entries") or []
            ],
        } for doc in docs],
    }
    work_order.add_comment(
        "Info",
        f"{action}: <pre>{frappe.utils.escape_html(json.dumps(details, sort_keys=True, indent=2))}</pre>",
    )


def _result(state, rows, started_at, idempotent=False):
    return {
        "state": state,
        "work_order": _single_value(rows, "voucher_no", _("Work Order")),
        "reservation_set_id": _single_value(rows, RESERVATION_SET_FIELD, _("Reservation Set")),
        "sre_names": [row.name for row in rows],
        "idempotent": idempotent,
        "execution_time_ms": _elapsed_ms(started_at),
    }


def _stale_result(rows, differences, started_at, lock_time_ms, preview_time_ms):
    return {
        **_result(STALE, rows, started_at),
        "status": STALE_RESERVATION,
        "differences": differences,
        "lock_time_ms": lock_time_ms,
        "preview_time_ms": preview_time_ms,
    }


def _single_value(rows, fieldname, label):
    values = {cstr(row.get(fieldname)).strip() for row in rows}
    if len(values) != 1 or not next(iter(values), ""):
        frappe.throw(_("Reservation set has inconsistent {0} values.").format(label))
    return next(iter(values))


def _has_submitter_role():
    return frappe.session.user == "Administrator" or bool(
        ALLOWED_SUBMITTER_ROLES.intersection(frappe.get_roles())
    )


def _require_submitter_role():
    if not _has_submitter_role():
        frappe.throw(
            _("Only Stock Manager may submit or cancel a Material Reservation set."),
            frappe.PermissionError,
        )


def _qty(value):
    return round(flt(value), 9)


def _elapsed_ms(started_at):
    return round((perf_counter() - started_at) * 1000, 3)
