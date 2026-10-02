from __future__ import annotations

from datetime import date, datetime
from time import perf_counter
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate

from calco_erp.calco_production.production_readiness import (
    _effective_quantity,
    _evaluate_bom,
    _evaluate_machine,
    _evaluate_planning,
)
from calco_erp.calco_purchase.master_data_governance_dashboard import (
    get_operational_readiness_by_item,
)
from calco_erp.inventory.availability import (
    PRODUCTION_SOURCE_WAREHOUSE,
    get_item_availability,
    get_production_warehouses,
)


READY_TO_RESERVE = "READY_TO_RESERVE"
SHORTAGE = "SHORTAGE"
NOT_READY = "NOT_READY"
EPSILON = 1e-9


@frappe.whitelist()
def get_material_reservation_preview(work_order: str) -> dict[str, Any]:
    """Return a read-only, deterministic material reservation preview."""
    return plan_material_reservation(work_order)


def plan_material_reservation(work_order: str | Any) -> dict[str, Any]:
    started_at = perf_counter()
    document, error = _load_work_order(work_order)
    if error:
        return _error_result(cstr(work_order), error, started_at)

    if cint(document.docstatus) != 1:
        return _error_result(
            document.name,
            _("Work Order must be submitted before materials can be planned for reservation."),
            started_at,
            document,
        )

    work_order_qty = flt(document.qty)
    effective_qty, partial_used = _effective_quantity(document)
    if work_order_qty <= 0 or effective_qty <= 0:
        return _error_result(
            document.name,
            _("Work Order and effective production quantities must be greater than zero."),
            started_at,
            document,
        )

    warehouses = get_production_warehouses(document.company)
    source_warehouse = cstr(warehouses.get("source_warehouse")).strip()
    if not warehouses.get("eligible") or source_warehouse != PRODUCTION_SOURCE_WAREHOUSE:
        reasons = warehouses.get("exclusion_reasons") or [
            _("Production source warehouse must be {0}.").format(
                PRODUCTION_SOURCE_WAREHOUSE
            )
        ]
        return _error_result(
            document.name, "; ".join(reasons), started_at, document
        )

    prerequisite_checks = _evaluate_planner_prerequisites(document, effective_qty)
    blocking_checks = [check for check in prerequisite_checks if not check.get("ready")]
    if blocking_checks:
        reasons = [
            f"{check.get('label')}: {reason}"
            for check in blocking_checks
            for reason in check.get("blockers") or []
        ]
        return _error_result(
            document.name,
            "; ".join(reasons) or _("Production Readiness is not complete."),
            started_at,
            document,
            _readiness_result(document, effective_qty, prerequisite_checks),
        )

    requirements, requirement_errors = _get_required_item_snapshot(
        document, effective_qty
    )
    if requirement_errors:
        return _error_result(
            document.name,
            "; ".join(requirement_errors),
            started_at,
            document,
            _readiness_result(document, effective_qty, prerequisite_checks),
        )

    item_codes = sorted(requirements)
    items = _get_items(item_codes)
    operational_readiness = get_operational_readiness_by_item(item_codes)
    rows = []
    eligible_batch_count = 0

    for item_code in item_codes:
        requirement = requirements[item_code]
        item = items.get(item_code)
        readiness_result = operational_readiness.get(item_code) or {
            "ready": False,
            "percent": 0,
            "overall": "Missing",
            "blockers": [_("Operational Readiness evidence is missing.")],
        }
        row = _base_rm_row(requirement, item, readiness_result)

        if not item:
            row.update(_shortage(_("Item does not exist."), "Master Data Governance"))
            rows.append(row)
            continue
        if cint(item.disabled) or not cint(item.is_stock_item):
            row.update(
                _shortage(
                    _("Item must be an enabled stock Item."),
                    "Master Data Governance",
                )
            )
            rows.append(row)
            continue
        if not readiness_result.get("ready"):
            blockers = readiness_result.get("blockers") or [
                _("Operational Readiness is {0}%.").format(
                    cint(readiness_result.get("percent"))
                )
            ]
            row.update(
                _shortage(
                    "; ".join(blockers),
                    _readiness_owner(readiness_result),
                )
            )
            rows.append(row)
            continue

        availability = get_item_availability(
            item_code=item_code,
            warehouse=source_warehouse,
            work_order=document.name,
        )
        allocations = _allocate_batches(
            availability.get("batches") or [], row["required_qty"]
        )
        allocated_qty = sum(flt(entry["allocated_qty"]) for entry in allocations)
        shortage_qty = max(row["required_qty"] - allocated_qty, 0)
        eligible_batch_count += sum(
            1 for batch in availability.get("batches") or [] if batch.get("eligible")
        )
        row.update(
            {
                "physical_qty": flt(availability.get("physical_quantity")),
                "released_qty": flt(availability.get("released_quantity")),
                "own_work_order_claim": flt(
                    availability.get("own_work_order_allocation")
                ),
                "external_claim": flt(
                    availability.get("other_work_order_allocation")
                ),
                "eligible_qty": flt(availability.get("eligible_quantity")),
                "available_to_this_work_order": flt(
                    availability.get("available_to_current_work_order")
                ),
                "allocated_qty": allocated_qty,
                "shortage_qty": shortage_qty,
                "allocations": allocations,
                "blocking_reason": "",
                "responsible_owner": "",
                "next_action": "",
            }
        )
        if shortage_qty > EPSILON:
            reasons = availability.get("exclusion_reasons") or []
            row.update(
                _shortage(
                    "; ".join(reasons)
                    or _("Eligible released stock is insufficient."),
                    "Stores / Purchase / Quality",
                )
            )
            row["allocated_qty"] = allocated_qty
            row["shortage_qty"] = shortage_qty
            row["allocations"] = allocations
        rows.append(row)

    status = (
        SHORTAGE
        if any(flt(row["shortage_qty"]) > EPSILON for row in rows)
        else READY_TO_RESERVE
    )
    readiness = _readiness_result(
        document,
        effective_qty,
        prerequisite_checks,
        rows,
        status == READY_TO_RESERVE,
    )
    return {
        "work_order": document.name,
        "fg_item": document.production_item,
        "bom": document.bom_no,
        "work_order_qty": work_order_qty,
        "effective_production_qty": effective_qty,
        "partial_production_used": partial_used,
        "partial_approval_evidence": _partial_evidence(document, partial_used),
        "source_warehouse": source_warehouse,
        "overall_status": status,
        "validation_errors": [],
        "production_readiness": readiness,
        "raw_materials": rows,
        "metrics": {
            "execution_time_ms": round((perf_counter() - started_at) * 1000, 3),
            "query_count": None,
            "query_count_note": "Frappe v16 does not expose a stable request query counter.",
            "rm_count": len(rows),
            "eligible_batch_count": eligible_batch_count,
            "availability_service_calls": sum(
                1 for row in rows if row.get("operational_readiness", {}).get("ready")
            ),
        },
        "read_only": True,
    }


def _evaluate_planner_prerequisites(work_order, effective_qty):
    """Reuse Phase 4 rules without running its material availability pass twice."""
    bom_no = work_order.get("bom_no")
    return [
        _evaluate_machine(work_order, work_order.get("custom_machine")),
        _evaluate_bom(work_order, bom_no, effective_qty),
        _evaluate_planning(work_order, bom_no),
    ]


def _readiness_result(
    work_order,
    effective_qty,
    prerequisite_checks,
    material_rows=None,
    material_ready=False,
):
    material_rows = material_rows or []
    material_blockers = [
        f"{row['item_code']}: {row['blocking_reason']}"
        for row in material_rows
        if flt(row.get("shortage_qty")) > EPSILON
    ]
    material_check = {
        "key": "material",
        "label": "Material",
        "ready": bool(material_ready),
        "status": "Ready" if material_ready else "Not Ready",
        "owner": "Stores / Purchase / Quality",
        "blockers": material_blockers,
        "details": {"items": material_rows},
    }
    checks = [material_check, *prerequisite_checks]
    ready = material_ready and all(check.get("ready") for check in prerequisite_checks)
    return {
        "work_order": work_order.name,
        "overall_result": "Ready for Material Issue" if ready else "Not Ready",
        "ready": ready,
        "evaluation_qty": effective_qty,
        "planned_qty": flt(work_order.qty),
        "checks": checks,
        "blockers": [
            {
                "check": check.get("label"),
                "reason": reason,
                "owner": check.get("owner"),
            }
            for check in checks
            for reason in check.get("blockers") or []
        ],
    }


def _load_work_order(work_order: str | Any):
    if not isinstance(work_order, str):
        if getattr(work_order, "doctype", "Work Order") == "Work Order":
            return work_order, ""
        return None, _("A valid Work Order is required.")
    name = cstr(work_order).strip()
    if not name or not frappe.db.exists("Work Order", name):
        return None, _("Work Order {0} does not exist.").format(name or "-")
    return frappe.get_doc("Work Order", name), ""


def _get_required_item_snapshot(work_order, effective_qty):
    errors = []
    scale = flt(effective_qty) / flt(work_order.qty)
    requirements: dict[str, dict[str, Any]] = {}
    for row in work_order.get("required_items") or []:
        item_code = cstr(row.get("item_code")).strip()
        if not item_code or flt(row.get("required_qty")) <= 0:
            continue
        source_warehouse = cstr(row.get("source_warehouse")).strip()
        if source_warehouse != PRODUCTION_SOURCE_WAREHOUSE:
            errors.append(
                _("RM {0} source warehouse must be {1}, not {2}.").format(
                    item_code,
                    PRODUCTION_SOURCE_WAREHOUSE,
                    source_warehouse or "-",
                )
            )
            continue
        requirement = requirements.setdefault(
            item_code,
            {
                "item_code": item_code,
                "required_qty": 0.0,
                "source_warehouse": source_warehouse,
                "work_order_item_rows": [],
            },
        )
        requirement["required_qty"] += flt(row.get("required_qty")) * scale
        if row.get("name"):
            requirement["work_order_item_rows"].append(row.name)
    if not requirements and not errors:
        errors.append(_("Work Order has no authoritative required-item snapshot."))
    return requirements, errors


def _get_items(item_codes):
    return {
        row.name: row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", item_codes)},
            fields=[
                "name",
                "item_name",
                "stock_uom",
                "item_group",
                "disabled",
                "is_stock_item",
                "has_batch_no",
            ],
            limit_page_length=0,
        )
    }


def _base_rm_row(requirement, item, readiness):
    required_qty = flt(requirement["required_qty"])
    return {
        "item_code": requirement["item_code"],
        "item_name": item.item_name if item else "",
        "required_qty": required_qty,
        "stock_uom": item.stock_uom if item else "",
        "source_warehouse": requirement["source_warehouse"],
        "work_order_item_rows": requirement["work_order_item_rows"],
        "operational_readiness": {
            "ready": bool(readiness.get("ready")),
            "percentage": cint(readiness.get("percent")),
            "overall": readiness.get("overall") or "Missing",
            "blockers": readiness.get("blockers") or [],
            "source": readiness.get("source") or "Master Data Governance",
        },
        "physical_qty": 0.0,
        "released_qty": 0.0,
        "own_work_order_claim": 0.0,
        "external_claim": 0.0,
        "eligible_qty": 0.0,
        "available_to_this_work_order": 0.0,
        "allocated_qty": 0.0,
        "shortage_qty": required_qty,
        "allocations": [],
        "blocking_reason": "",
        "responsible_owner": "",
        "next_action": "",
    }


def _allocate_batches(batches, required_qty):
    remaining = max(flt(required_qty), 0)
    allocations = []
    for rank, batch in enumerate(sorted(batches, key=_batch_ordering_key), start=1):
        if not batch.get("eligible"):
            continue
        available = max(flt(batch.get("available_to_current_work_order")), 0)
        allocated = min(available, remaining)
        if allocated <= EPSILON:
            continue
        release_records = batch.get("evidence_source", {}).get("release") or []
        allocations.append(
            {
                "batch_no": batch.get("batch_no"),
                "allocated_qty": allocated,
                "physical_qty": flt(batch.get("physical_quantity")),
                "released_qty": flt(batch.get("released_quantity")),
                "available_qty": available,
                "expiry_date": batch.get("expiry_date"),
                "manufacturing_date": batch.get("manufacturing_date"),
                "physical_stock_date": batch.get("first_posting_date"),
                "batch_creation": batch.get("batch_creation"),
                "rm_release_note": release_records[0] if release_records else "",
                "rm_release_notes": release_records,
                "selection_rank": rank,
                "selection_reason": "FEFO, then manufacturing date, physical stock date, batch creation, batch number",
            }
        )
        remaining -= allocated
        if remaining <= EPSILON:
            break
    return allocations


def _batch_ordering_key(batch):
    return (
        _date_or_max(batch.get("expiry_date")),
        _date_or_max(batch.get("manufacturing_date")),
        _date_or_max(batch.get("first_posting_date")),
        _datetime_or_max(batch.get("batch_creation")),
        cstr(batch.get("batch_no")),
    )


def _date_or_max(value):
    return getdate(value) if value else date.max


def _datetime_or_max(value):
    return get_datetime(value) if value else datetime.max


def _shortage(reason, owner):
    return {
        "blocking_reason": reason,
        "responsible_owner": owner,
        "next_action": _("Resolve the blocker and refresh the reservation preview."),
    }


def _readiness_owner(readiness):
    blockers = " ".join(readiness.get("blockers") or []).lower()
    if "technical" in blockers:
        return "Technical / Production Head"
    if "quality" in blockers or "testing standard" in blockers:
        return "Quality"
    if "supplier" in blockers:
        return "Purchase"
    if "planning" in blockers:
        return "Planning"
    return "Master Data Governance"


def _partial_evidence(work_order, partial_used):
    if not partial_used:
        return {}
    return {
        "approved": True,
        "approved_quantity": flt(work_order.get("custom_partial_production_qty")),
        "reason": work_order.get("custom_partial_production_reason") or "",
        "approved_by": work_order.get("custom_partial_production_approved_by") or "",
        "approved_on": str(
            work_order.get("custom_partial_production_approved_on") or ""
        ),
    }


def _error_result(name, reason, started_at, work_order=None, readiness=None):
    return {
        "work_order": getattr(work_order, "name", None) or name,
        "fg_item": getattr(work_order, "production_item", "") if work_order else "",
        "bom": getattr(work_order, "bom_no", "") if work_order else "",
        "work_order_qty": flt(getattr(work_order, "qty", 0)) if work_order else 0.0,
        "effective_production_qty": 0.0,
        "partial_production_used": False,
        "partial_approval_evidence": {},
        "source_warehouse": PRODUCTION_SOURCE_WAREHOUSE,
        "overall_status": NOT_READY,
        "validation_errors": [reason],
        "production_readiness": readiness or {},
        "raw_materials": [],
        "metrics": {
            "execution_time_ms": round((perf_counter() - started_at) * 1000, 3),
            "query_count": None,
            "query_count_note": "Frappe v16 does not expose a stable request query counter.",
            "rm_count": 0,
            "eligible_batch_count": 0,
            "availability_service_calls": 0,
        },
        "read_only": True,
    }
