from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Any

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt

from calco_erp.calco_production.material_reservation import (
    NOT_READY,
    READY_TO_RESERVE,
    plan_material_reservation,
)


ALLOWED_GENERATOR_ROLES = {"Production Manager", "Production Head"}
RESERVATION_SET_FIELD = "custom_calco_reservation_set_id"
EFFECTIVE_QTY_FIELD = "custom_calco_effective_production_qty"
ALLOCATION_HASH_FIELD = "custom_calco_allocation_basis_hash"

CREATED = "CREATED"
REUSED = "REUSED"
BLOCKED = "BLOCKED"
STALE = "STALE_REBUILD_REQUIRED"


def ensure_material_reservation_fields():
    create_custom_fields(
        {
            "Stock Reservation Entry": [
                {
                    "fieldname": RESERVATION_SET_FIELD,
                    "label": "Calco Reservation Set",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "insert_after": "status",
                },
                {
                    "fieldname": EFFECTIVE_QTY_FIELD,
                    "label": "Effective Production Quantity",
                    "fieldtype": "Float",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": RESERVATION_SET_FIELD,
                },
                {
                    "fieldname": ALLOCATION_HASH_FIELD,
                    "label": "Allocation Basis Hash",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                    "length": 64,
                    "insert_after": EFFECTIVE_QTY_FIELD,
                },
            ]
        },
        update=True,
    )


@frappe.whitelist()
def get_draft_material_reservation_state(work_order: str) -> dict[str, Any]:
    preview = plan_material_reservation(work_order)
    allocation_hash = (
        make_allocation_basis_hash(preview)
        if preview.get("overall_status") == READY_TO_RESERVE
        else ""
    )
    existing = _get_existing_reservations(cstr(work_order).strip())
    state = _resolve_existing_state(preview, allocation_hash, existing)
    state.update(
        {
            "work_order": preview.get("work_order") or work_order,
            "preview_status": preview.get("overall_status"),
            "effective_production_qty": flt(preview.get("effective_production_qty")),
            "rm_count": len(preview.get("raw_materials") or []),
            "can_create": (
                preview.get("overall_status") == READY_TO_RESERVE
                and state["status"] not in {REUSED, STALE, BLOCKED}
                and _has_generator_role()
            ),
            "authorized": _has_generator_role(),
            "blockers": _preview_blockers(preview),
        }
    )
    return state


@frappe.whitelist()
def create_draft_material_reservation(work_order: str) -> dict[str, Any]:
    """Create or reuse one atomic Draft SRE set from a fresh Phase 5.1 preview."""
    _require_generator_role()
    started_at = perf_counter()
    work_order = cstr(work_order).strip()
    preview = plan_material_reservation(work_order)

    if preview.get("overall_status") != READY_TO_RESERVE:
        return {
            "status": BLOCKED,
            "work_order": preview.get("work_order") or work_order,
            "preview_status": preview.get("overall_status") or NOT_READY,
            "blockers": _preview_blockers(preview),
            "sre_names": [],
            "created": False,
            "execution_time_ms": _elapsed_ms(started_at),
        }

    allocation_hash = make_allocation_basis_hash(preview)
    existing = _get_existing_reservations(work_order)
    state = _resolve_existing_state(preview, allocation_hash, existing)
    if state["status"] in {REUSED, STALE, BLOCKED}:
        state.update(
            {
                "work_order": work_order,
                "preview_status": READY_TO_RESERVE,
                "allocation_basis_hash": allocation_hash,
                "created": False,
                "execution_time_ms": _elapsed_ms(started_at),
            }
        )
        return state

    reservation_set_id = _make_reservation_set_id(work_order)
    company = frappe.db.get_value("Work Order", work_order, "company")
    savepoint = "calco_phase_5_2_draft_reservation"
    frappe.db.savepoint(savepoint)
    created_names = []
    try:
        for row in preview.get("raw_materials") or []:
            sre = _make_draft_sre(
                preview, row, reservation_set_id, allocation_hash, company
            )
            # The whitelisted method enforces the approved Calco roles itself.
            # Standard SRE permissions remain unchanged for all other entry paths.
            sre.insert(ignore_permissions=True)
            created_names.append(sre.name)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise

    return {
        "status": CREATED,
        "work_order": work_order,
        "preview_status": READY_TO_RESERVE,
        "reservation_set_id": reservation_set_id,
        "allocation_basis_hash": allocation_hash,
        "effective_production_qty": flt(preview.get("effective_production_qty")),
        "rm_count": len(preview.get("raw_materials") or []),
        "sre_names": created_names,
        "created": True,
        "docstatus": 0,
        "execution_time_ms": _elapsed_ms(started_at),
        "preview_time_ms": flt((preview.get("metrics") or {}).get("execution_time_ms")),
    }


def make_allocation_basis_hash(preview: dict[str, Any]) -> str:
    basis = {
        "version": 1,
        "work_order": preview.get("work_order"),
        "effective_production_qty": _quantity(preview.get("effective_production_qty")),
        "bom": preview.get("bom"),
        "source_warehouse": preview.get("source_warehouse"),
        "items": [],
    }
    for row in sorted(
        preview.get("raw_materials") or [], key=lambda value: cstr(value.get("item_code"))
    ):
        basis["items"].append(
            {
                "item_code": row.get("item_code"),
                "required_qty": _quantity(row.get("required_qty")),
                "source_warehouse": row.get("source_warehouse"),
                "work_order_item_rows": sorted(row.get("work_order_item_rows") or []),
                "allocations": [
                    {
                        "batch_no": allocation.get("batch_no"),
                        "allocated_qty": _quantity(allocation.get("allocated_qty")),
                    }
                    for allocation in sorted(
                        row.get("allocations") or [],
                        key=lambda value: (
                            cint(value.get("selection_rank")),
                            cstr(value.get("batch_no")),
                        ),
                    )
                ],
            }
        )
    encoded = json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _make_draft_sre(preview, row, reservation_set_id, allocation_hash, company):
    allocations = row.get("allocations") or []
    if not allocations or flt(row.get("shortage_qty")) > 1e-9:
        frappe.throw(
            _("RM {0} does not have a complete planner allocation.").format(
                row.get("item_code") or "-"
            )
        )

    item_rows = sorted(row.get("work_order_item_rows") or [])
    if not item_rows:
        frappe.throw(
            _("RM {0} has no Work Order Item lineage.").format(
                row.get("item_code") or "-"
            )
        )

    required_qty = flt(row.get("required_qty"))
    allocated_qty = sum(flt(value.get("allocated_qty")) for value in allocations)
    if abs(required_qty - allocated_qty) > 1e-9:
        frappe.throw(
            _("RM {0} allocation does not equal its required quantity.").format(
                row.get("item_code") or "-"
            )
        )

    sre = frappe.new_doc("Stock Reservation Entry")
    sre.update(
        {
            "company": company,
            "voucher_type": "Work Order",
            "voucher_no": preview["work_order"],
            "voucher_detail_no": item_rows[0],
            "item_code": row["item_code"],
            "warehouse": row["source_warehouse"],
            "stock_uom": row["stock_uom"],
            "available_qty": flt(row.get("available_to_this_work_order")),
            "voucher_qty": required_qty,
            "reserved_qty": required_qty,
            "has_batch_no": 1,
            "has_serial_no": 0,
            "reservation_based_on": "Serial and Batch",
            RESERVATION_SET_FIELD: reservation_set_id,
            EFFECTIVE_QTY_FIELD: flt(preview.get("effective_production_qty")),
            ALLOCATION_HASH_FIELD: allocation_hash,
        }
    )
    for allocation in allocations:
        sre.append(
            "sb_entries",
            {
                "batch_no": allocation["batch_no"],
                "item_code": row["item_code"],
                "warehouse": row["source_warehouse"],
                "qty": flt(allocation["allocated_qty"]),
            },
        )
    return sre


def _get_existing_reservations(work_order):
    rows = frappe.get_all(
        "Stock Reservation Entry",
        filters={"voucher_type": "Work Order", "voucher_no": work_order},
        fields=[
            "name",
            "docstatus",
            "item_code",
            "voucher_detail_no",
            RESERVATION_SET_FIELD,
            ALLOCATION_HASH_FIELD,
        ],
        limit_page_length=0,
        order_by="creation asc, name asc",
    )
    return [frappe._dict(row) for row in rows]


def _resolve_existing_state(preview, allocation_hash, existing):
    submitted = [row for row in existing if cint(row.docstatus) == 1]
    if submitted:
        return {
            "status": BLOCKED,
            "reason": _("A submitted Stock Reservation Entry already exists."),
            "sre_names": [row.name for row in submitted],
            "reservation_set_id": submitted[0].get(RESERVATION_SET_FIELD) or "",
        }

    drafts = [
        row
        for row in existing
        if cint(row.docstatus) == 0 and row.get(RESERVATION_SET_FIELD)
    ]
    if not drafts:
        return {"status": "AVAILABLE", "sre_names": [], "reservation_set_id": ""}

    set_ids = {row.get(RESERVATION_SET_FIELD) for row in drafts}
    hashes = {row.get(ALLOCATION_HASH_FIELD) for row in drafts}
    expected_items = sorted(
        row.get("item_code") for row in preview.get("raw_materials") or []
    )
    draft_items = sorted(row.item_code for row in drafts)
    if (
        len(set_ids) == 1
        and hashes == {allocation_hash}
        and draft_items == expected_items
    ):
        return {
            "status": REUSED,
            "reason": _("The unchanged Draft reservation set was reused."),
            "sre_names": [row.name for row in drafts],
            "reservation_set_id": next(iter(set_ids)),
        }

    return {
        "status": STALE,
        "reason": _(
            "The existing Draft reservation set is stale. Delete it and regenerate."
        ),
        "sre_names": [row.name for row in drafts],
        "reservation_set_id": ", ".join(sorted(set_ids)),
    }


def _preview_blockers(preview):
    blockers = list(preview.get("validation_errors") or [])
    for row in preview.get("raw_materials") or []:
        if flt(row.get("shortage_qty")) > 1e-9 or row.get("blocking_reason"):
            blockers.append(
                {
                    "item_code": row.get("item_code"),
                    "reason": row.get("blocking_reason")
                    or _("Shortage {0}").format(flt(row.get("shortage_qty"))),
                    "shortage_qty": flt(row.get("shortage_qty")),
                    "responsible_owner": row.get("responsible_owner"),
                }
            )
    return blockers


def _has_generator_role():
    if frappe.session.user == "Administrator":
        return True
    return bool(ALLOWED_GENERATOR_ROLES.intersection(frappe.get_roles()))


def _require_generator_role():
    if not _has_generator_role():
        frappe.throw(
            _("Only Production Manager or Production Head may generate Draft reservations."),
            frappe.PermissionError,
        )


def _make_reservation_set_id(work_order):
    return f"CALCO-MR-{work_order}-{frappe.generate_hash(length=12)}"


def _quantity(value):
    text = f"{flt(value):.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _elapsed_ms(started_at):
    return round((perf_counter() - started_at) * 1000, 3)
