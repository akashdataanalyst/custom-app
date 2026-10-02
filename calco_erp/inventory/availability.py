from __future__ import annotations

from collections import defaultdict
from calco_erp.calco_production.stock_quantity_precision import decimal as stock_decimal
from datetime import date, datetime
from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, flt, get_datetime, getdate, today

from erpnext.manufacturing.doctype.work_order.work_order import (
    get_reserved_qty_for_production,
)
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
    get_auto_batch_nos,
)
from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
    get_serial_batch_entries_for_voucher,
    get_sre_details_for_voucher,
    get_sre_reserved_qty_for_item_and_warehouse,
)
from erpnext.stock.serial_batch_bundle import get_batches_from_bundle
from erpnext.stock.utils import get_stock_balance


PRODUCTION_SOURCE_WAREHOUSE = "Stores - CPPL"
PRODUCTION_WIP_WAREHOUSE = "Work In Progress - CPPL"
TRANSFER_PURPOSE = "Material Transfer for Manufacture"
WIP_CONSUMPTION_PURPOSES = {
    "Material Consumption for Manufacture",
    "Manufacture",
}
WIP_LINEAGE_SOURCE = "Submitted Work Order WIP transaction lineage"
ACTIVE_WORK_ORDER_STATUSES = {"Not Started", "In Process"}
RECOVERY_UAT_SITE = "recovery120120.localhost"
UAT_RELEASE_OVERRIDE_FLAG = "calco_uat_relax_rm_release_gate"
UAT_RELEASE_OVERRIDE_SOURCE = "UAT Physical Stock Override"


def _get_uat_release_override_flag():
    try:
        return frappe.conf.get(UAT_RELEASE_OVERRIDE_FLAG)
    except RuntimeError:
        return None


def _get_current_site() -> str:
    return cstr(getattr(frappe.local, "site", "")).strip()


def is_uat_rm_release_override_active() -> bool:
    from calco_erp.release_profile import PRODUCTION_DISTRIBUTION
    if PRODUCTION_DISTRIBUTION:return False
    flag = _get_uat_release_override_flag()
    if type(flag) is not int or flag != 1:
        return False

    site = _get_current_site()
    if site != RECOVERY_UAT_SITE:
        frappe.logger("calco_uat_rm_release_override").error(
            "Refused RM release override on unapproved site %s; approved site is %s.",
            site or "<unknown>",
            RECOVERY_UAT_SITE,
        )
        return False
    return True


def get_production_warehouses(company: str | None = None) -> dict[str, Any]:
    source = _get_warehouse(PRODUCTION_SOURCE_WAREHOUSE, company)
    target = _get_warehouse(PRODUCTION_WIP_WAREHOUSE, company)
    reasons = []
    for role, warehouse in (("Source", source), ("WIP", target)):
        if not warehouse:
            reasons.append(
                _("{0} production warehouse is not configured or valid.").format(role)
            )

    return {
        "source_warehouse": source.get("name") if source else "",
        "wip_warehouse": target.get("name") if target else "",
        "eligible": not reasons,
        "eligibility_status": "Eligible" if not reasons else "Excluded",
        "exclusion_reasons": reasons,
        "evidence_source": {
            "doctype": "Warehouse",
            "records": [row["name"] for row in (source, target) if row],
        },
        "ordering_sequence": 0,
    }


def get_batch_availability(
    item_code: str,
    batch_no: str,
    warehouse: str | None = None,
    work_order: str | None = None,
    posting_datetime: str | datetime | None = None,
) -> dict[str, Any]:
    item_code = cstr(item_code).strip()
    batch_no = cstr(batch_no).strip()
    warehouse = _resolve_source_warehouse(warehouse)
    rows = _build_batch_availability(
        item_code=item_code,
        warehouse=warehouse,
        work_order=work_order,
        posting_datetime=posting_datetime,
        include_batch=batch_no,
    )
    for row in rows:
        if row["batch_no"] == batch_no:
            return row

    return _empty_batch_result(
        item_code,
        batch_no,
        warehouse,
        [_("Batch has no physical stock in the selected warehouse.")],
    )


def get_eligible_batches(
    item_code: str,
    warehouse: str | None = None,
    work_order: str | None = None,
    posting_datetime: str | datetime | None = None,
    ordering: str = "FEFO_FIFO",
) -> list[dict[str, Any]]:
    if ordering != "FEFO_FIFO":
        frappe.throw(_("Unsupported batch ordering policy {0}.").format(ordering))

    rows = _build_batch_availability(
        item_code=cstr(item_code).strip(),
        warehouse=_resolve_source_warehouse(warehouse),
        work_order=work_order,
        posting_datetime=posting_datetime,
    )
    eligible = [row for row in rows if row["eligible"]]
    for sequence, row in enumerate(eligible, start=1):
        row["ordering_sequence"] = sequence
    return eligible


def get_item_availability(
    item_code: str,
    warehouse: str | None = None,
    work_order: str | None = None,
    posting_datetime: str | datetime | None = None,
) -> dict[str, Any]:
    item_code = cstr(item_code).strip()
    warehouse = _resolve_source_warehouse(warehouse)
    item = _get_item(item_code)
    if not item:
        return _empty_item_result(
            item_code,
            warehouse,
            [_("Item does not exist.")],
        )
    if item.disabled:
        return _empty_item_result(
            item_code,
            warehouse,
            [_("Item is disabled.")],
        )
    if not item.is_stock_item:
        return _empty_item_result(
            item_code,
            warehouse,
            [_("Item is not a stock item.")],
        )

    use_wip_lineage = _uses_controlled_wip_authority(warehouse, work_order)
    wip_lineage = (
        get_work_order_wip_lineage(work_order, item_code=item_code)
        if use_wip_lineage
        else {}
    )

    if not item.has_batch_no:
        physical_qty = flt(get_stock_balance(item_code, warehouse))
        reservations = get_reservation_snapshot(
            item_code=item_code,
            warehouse=warehouse,
            work_order=work_order,
        )
        lineage_qty = flt(wip_lineage.get((item_code, ""), {}).get("net_wip_qty"))
        eligible_qty = min(physical_qty, lineage_qty) if use_wip_lineage else physical_qty
        available_qty = max(
            eligible_qty - flt(reservations["external_claim_quantity"]), 0
        )
        reasons = []
        if physical_qty <= 0:
            reasons.append(_("No physical stock is available."))
        return {
            "item_code": item_code,
            "warehouse": warehouse,
            "has_batch_no": False,
            "physical_quantity": physical_qty,
            "released_quantity": 0.0,
            "eligible_quantity": eligible_qty,
            "normal_released_eligible_quantity": (
                0.0 if use_wip_lineage else physical_qty
            ),
            "uat_override_eligible_quantity": 0.0,
            "eligibility_source": (
                WIP_LINEAGE_SOURCE
                if use_wip_lineage
                else "Not applicable for a non-batch item"
            ),
            "uat_rm_release_override_active": False,
            "uat_rm_release_override_used": False,
            "own_work_order_allocation": flt(
                reservations["own_work_order_reservation"]
            ),
            "other_work_order_allocation": flt(
                reservations["other_work_order_reservation"]
            ),
            "available_to_current_work_order": available_qty,
            "eligible": eligible_qty > 0,
            "eligibility_status": "Eligible" if eligible_qty > 0 else "Excluded",
            "exclusion_reasons": reasons,
            "evidence_source": {
                "physical": "ERPNext get_stock_balance",
                "release": (
                    "Not reapplied in WIP"
                    if use_wip_lineage
                    else "Not applicable for a non-batch item"
                ),
                "wip_lineage": wip_lineage.get((item_code, ""), {}),
                "reservation": reservations["evidence_source"],
            },
            "ordering_sequence": 0,
            "batches": [],
            "reservation_snapshot": reservations,
        }

    batches = _build_batch_availability(
        item_code=item_code,
        warehouse=warehouse,
        work_order=work_order,
        posting_datetime=posting_datetime,
    )
    eligible_batches = [row for row in batches if row["eligible"]]
    reservations = get_reservation_snapshot(
        item_code=item_code,
        warehouse=warehouse,
        work_order=work_order,
    )
    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "has_batch_no": True,
        "physical_quantity": sum(row["physical_quantity"] for row in batches),
        "released_quantity": sum(row["released_quantity"] for row in batches),
        "eligible_quantity": sum(row["eligible_quantity"] for row in batches),
        "normal_released_eligible_quantity": sum(
            row["normal_released_eligible_quantity"] for row in batches
        ),
        "uat_override_eligible_quantity": sum(
            row["uat_override_eligible_quantity"] for row in batches
        ),
        "eligibility_source": (
            WIP_LINEAGE_SOURCE
            if use_wip_lineage
            else UAT_RELEASE_OVERRIDE_SOURCE
            if any(row["uat_rm_release_override_used"] for row in batches)
            else "RM Release Note"
        ),
        "uat_rm_release_override_active": any(
            row["uat_rm_release_override_active"] for row in batches
        ),
        "uat_rm_release_override_used": any(
            row["uat_rm_release_override_used"] for row in batches
        ),
        "own_work_order_allocation": sum(
            row["own_work_order_allocation"] for row in batches
        ),
        "other_work_order_allocation": sum(
            row["other_work_order_allocation"] for row in batches
        ),
        "available_to_current_work_order": sum(
            row["available_to_current_work_order"] for row in batches
        ),
        "eligible": bool(eligible_batches),
        "eligibility_status": "Eligible" if eligible_batches else "Excluded",
        "exclusion_reasons": _unique(
            reason
            for row in batches
            for reason in row.get("exclusion_reasons", [])
        ),
        "evidence_source": {
            "physical": "ERPNext get_auto_batch_nos",
            "release": "Not reapplied in WIP" if use_wip_lineage else "RM Release Note",
            "wip_lineage": WIP_LINEAGE_SOURCE if use_wip_lineage else "",
            "reservation": reservations["evidence_source"],
        },
        "ordering_sequence": 0,
        "batches": batches,
        "reservation_snapshot": reservations,
    }


def get_reservation_snapshot(
    item_code: str,
    warehouse: str | None = None,
    work_order: str | None = None,
) -> dict[str, Any]:
    item_code = cstr(item_code).strip()
    warehouse = _resolve_source_warehouse(warehouse)
    work_order = cstr(work_order).strip()

    work_order_rows = _get_work_order_reservations(item_code, warehouse)
    total_work_order = sum(flt(row["quantity"]) for row in work_order_rows)
    own_work_order = sum(
        flt(row["quantity"])
        for row in work_order_rows
        if work_order and row["work_order"] == work_order
    )
    other_work_order = max(total_work_order - own_work_order, 0)

    pick_allocations = get_pick_list_allocations(
        item_code=item_code,
        warehouse=warehouse,
        work_order=work_order,
    )
    own_pick = sum(
        flt(row["quantity"])
        for row in pick_allocations
        if row["allocation_owner"] == "Current Work Order"
    )
    other_pick = sum(
        flt(row["quantity"])
        for row in pick_allocations
        if row["allocation_owner"] == "External"
    )

    sre_rows = _get_stock_reservation_allocations(
        item_code=item_code,
        warehouse=warehouse,
        work_order=work_order,
    )
    total_sre = flt(get_sre_reserved_qty_for_item_and_warehouse(item_code, warehouse))
    own_sre = sum(
        flt(row["quantity"])
        for row in sre_rows
        if row["allocation_owner"] == "Current Work Order"
    )
    external_sre = sum(
        flt(row["quantity"])
        for row in sre_rows
        if row["allocation_owner"] == "External"
        and row["claim_class"] == "Independent SRE"
    )

    allocation_claims = _deduplicated_allocation_claims(
        pick_allocations, sre_rows
    )
    represented_other_work_order = min(
        sum(
            flt(row["quantity"])
            for row in allocation_claims
            if row["allocation_owner"] == "External" and row.get("work_order")
        ),
        other_work_order,
    )
    unallocated_other_work_order = max(
        other_work_order - represented_other_work_order, 0
    )

    independent_external_claim = sum(
        flt(row["quantity"])
        for row in allocation_claims
        if row["allocation_owner"] == "External" and not row.get("work_order")
    )
    external_claim = other_work_order + independent_external_claim

    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "work_order": work_order,
        "total_work_order_reservation": total_work_order,
        "own_work_order_reservation": own_work_order,
        "other_work_order_reservation": other_work_order,
        "own_pick_list_allocation": own_pick,
        "other_pick_list_allocation": other_pick,
        "total_stock_reservation_entry_quantity": total_sre,
        "own_stock_reservation_entry_quantity": own_sre,
        "external_stock_reservation_entry_quantity": external_sre,
        "unallocated_other_work_order_reservation": unallocated_other_work_order,
        "external_claim_quantity": external_claim,
        "eligible": True,
        "eligibility_status": "Attributed",
        "exclusion_reasons": [],
        "evidence_source": {
            "work_orders": [row["work_order"] for row in work_order_rows],
            "pick_lists": _unique(row["pick_list"] for row in pick_allocations),
            "stock_reservation_entries": _unique(
                row["stock_reservation_entry"] for row in sre_rows
            ),
            "erpnext_api": [
                "get_reserved_qty_for_production",
                "get_sre_reserved_qty_for_item_and_warehouse",
            ],
        },
        "ordering_sequence": 0,
        "pick_list_allocations": pick_allocations,
        "stock_reservation_allocations": sre_rows,
    }


def get_pick_list_allocations(
    item_code: str,
    warehouse: str | None = None,
    work_order: str | None = None,
) -> list[dict[str, Any]]:
    item_code = cstr(item_code).strip()
    warehouse = _resolve_source_warehouse(warehouse)
    work_order = cstr(work_order).strip()
    allocations = []

    for row in _get_open_pick_list_rows(item_code, warehouse):
        pending_qty = max(flt(row.picked_qty) - flt(row.transferred_qty), 0)
        if pending_qty <= 0:
            continue

        batch_quantities = _get_pick_list_row_batches(row)
        remaining = pending_qty
        for batch_no, selected_qty in batch_quantities.items():
            quantity = min(abs(flt(selected_qty)), remaining)
            if quantity <= 0:
                continue
            owner = (
                "Current Work Order"
                if work_order and row.work_order == work_order
                else "External"
            )
            allocations.append(
                {
                    "pick_list": row.parent,
                    "pick_list_item": row.name,
                    "work_order": row.work_order or "",
                    "item_code": item_code,
                    "batch_no": batch_no,
                    "warehouse": warehouse,
                    "quantity": quantity,
                    "allocation_owner": owner,
                    "eligible": True,
                    "eligibility_status": "Allocated",
                    "exclusion_reasons": [],
                    "evidence_source": {
                        "doctype": "Pick List",
                        "record": row.parent,
                        "bundle": row.serial_and_batch_bundle or "",
                    },
                    "ordering_sequence": len(allocations) + 1,
                }
            )
            remaining -= quantity
            if remaining <= 0:
                break

    return allocations


def validate_batch_selection(
    item_code: str,
    batch_no: str,
    warehouse: str | None = None,
    work_order: str | None = None,
    posting_datetime: str | datetime | None = None,
    required_qty: float | None = None,
) -> dict[str, Any]:
    result = get_batch_availability(
        item_code=item_code,
        batch_no=batch_no,
        warehouse=warehouse,
        work_order=work_order,
        posting_datetime=posting_datetime,
    )
    reasons = list(result["exclusion_reasons"])
    if required_qty is not None and result["available_to_current_work_order"] + 1e-9 < flt(
        required_qty
    ):
        reasons.append(
            _("Available quantity {0} is less than required quantity {1}.").format(
                result["available_to_current_work_order"], flt(required_qty)
            )
        )

    valid = result["eligible"] and not reasons
    return {
        **result,
        "valid": valid,
        "eligibility_status": "Eligible" if valid else "Excluded",
        "exclusion_reasons": _unique(reasons),
    }


def _build_batch_availability(
    item_code: str,
    warehouse: str,
    work_order: str | None,
    posting_datetime: str | datetime | None,
    include_batch: str | None = None,
) -> list[dict[str, Any]]:
    item = _get_item(item_code)
    if not item or not item.has_batch_no:
        return []

    physical_rows = _get_physical_batch_rows(
        item_code=item_code,
        warehouse=warehouse,
        posting_datetime=posting_datetime,
        batch_no=include_batch,
    )
    physical_by_batch = defaultdict(float)
    for row in physical_rows:
        physical_by_batch[row.batch_no] += flt(row.qty)

    use_wip_lineage = _uses_controlled_wip_authority(warehouse, work_order)
    wip_lineage = (
        get_work_order_wip_lineage(work_order, item_code=item_code)
        if use_wip_lineage
        else {}
    )
    batch_names = list(physical_by_batch)
    for lineage_item, lineage_batch in wip_lineage:
        if lineage_item == item_code and lineage_batch and lineage_batch not in batch_names:
            batch_names.append(lineage_batch)
    if include_batch and include_batch not in batch_names:
        batch_names.append(include_batch)
    metadata = _get_batch_metadata(batch_names)
    releases = _get_release_evidence(item_code, batch_names)
    posting_evidence = _get_batch_posting_evidence(
        item_code, warehouse, batch_names
    )
    reservations = get_reservation_snapshot(item_code, warehouse, work_order)
    uat_override_active = is_uat_rm_release_override_active()
    uat_override_allowed = (
        uat_override_active and warehouse == PRODUCTION_SOURCE_WAREHOUSE
    )
    pick_allocations = reservations["pick_list_allocations"]
    sre_allocations = reservations["stock_reservation_allocations"]
    external_wip_claims = (
        get_external_wip_lineage_claims(item_code, work_order)
        if use_wip_lineage
        else {}
    )

    own_allocations = defaultdict(float)
    other_allocations = defaultdict(float)
    for row in _deduplicated_allocation_claims(
        pick_allocations, sre_allocations
    ):
        target = (
            own_allocations
            if row["allocation_owner"] == "Current Work Order"
            else other_allocations
        )
        target[row["batch_no"]] += flt(row["quantity"])
    for batch_no, claim in external_wip_claims.items():
        other_allocations[batch_no] = max(
            flt(other_allocations[batch_no]), flt(claim["quantity"])
        )

    effective_date = _as_date(posting_datetime) or getdate(today())
    rows = []
    for batch_no in batch_names:
        batch = metadata.get(batch_no)
        physical_qty = max(flt(physical_by_batch[batch_no]), 0)
        release = releases.get(batch_no, {"quantity": 0.0, "records": []})
        released_qty = max(flt(release["quantity"]), 0)
        reasons = []

        if not batch:
            reasons.append(_("Batch does not exist."))
        elif batch.item != item_code:
            reasons.append(
                _("Batch belongs to item {0}, not {1}.").format(
                    batch.item or "-", item_code
                )
            )
        elif batch.disabled:
            reasons.append(_("Batch is disabled."))

        if batch and batch.expiry_date and getdate(batch.expiry_date) < effective_date:
            reasons.append(_("Batch is expired."))
        if physical_qty <= 0:
            reasons.append(_("Batch has no physical stock in the selected warehouse."))

        batch_valid = bool(
            batch
            and batch.item == item_code
            and not batch.disabled
            and not (
                batch.expiry_date
                and getdate(batch.expiry_date) < effective_date
            )
            and physical_qty > 0
        )
        normal_eligible_qty = (
            min(physical_qty, released_qty)
            if batch_valid and released_qty > 0
            else 0.0
        )
        lineage = wip_lineage.get((item_code, batch_no), {})
        lineage_qty = max(flt(lineage.get("net_wip_qty")), 0)
        uat_override_used = bool(
            not use_wip_lineage
            and uat_override_allowed
            and batch_valid
            and released_qty <= 0
        )
        if use_wip_lineage and lineage_qty <= 0:
            reasons.append(
                _("No submitted same-Work-Order transfer lineage establishes this WIP batch.")
            )
        elif released_qty <= 0 and not uat_override_used and not use_wip_lineage:
            reasons.append(
                _("No submitted Released RM Release Note matches this Item + Batch.")
            )
        if use_wip_lineage:
            unclaimed_physical_qty = max(
                physical_qty - flt(other_allocations[batch_no]), 0
            )
            eligible_qty = (
                min(unclaimed_physical_qty, lineage_qty) if batch_valid else 0.0
            )
        else:
            eligible_qty = physical_qty if uat_override_used else normal_eligible_qty

        if uat_override_used:
            _log_uat_release_override(
                work_order=work_order,
                item_code=item_code,
                batch_no=batch_no,
                physical_qty=physical_qty,
                eligible_qty=eligible_qty,
                released_qty=released_qty,
            )

        row = {
            "item_code": item_code,
            "batch_no": batch_no,
            "warehouse": warehouse,
            "physical_quantity": physical_qty,
            "released_quantity": released_qty,
            "eligible_quantity": eligible_qty,
            "normal_released_eligible_quantity": normal_eligible_qty,
            "uat_override_eligible_quantity": (
                eligible_qty if uat_override_used else 0.0
            ),
            "own_work_order_allocation": own_allocations[batch_no],
            "other_work_order_allocation": other_allocations[batch_no],
            "available_to_current_work_order": (
                eligible_qty
                if use_wip_lineage
                else max(eligible_qty - other_allocations[batch_no], 0)
            ),
            "eligible": eligible_qty > 0,
            "eligibility_status": "Eligible" if eligible_qty > 0 else "Excluded",
            "eligibility_source": (
                WIP_LINEAGE_SOURCE
                if use_wip_lineage
                else UAT_RELEASE_OVERRIDE_SOURCE
                if uat_override_used
                else "RM Release Note"
            ),
            "uat_rm_release_override_active": uat_override_active,
            "uat_rm_release_override_used": uat_override_used,
            "exclusion_reasons": _unique(reasons),
            "expiry_date": batch.expiry_date if batch else None,
            "manufacturing_date": batch.manufacturing_date if batch else None,
            "first_posting_date": posting_evidence.get(batch_no, {}).get(
                "first_posting_date"
            ),
            "batch_creation": batch.creation if batch else None,
            "evidence_source": {
                "physical": posting_evidence.get(batch_no, {}).get(
                    "stock_modes",
                    ["ERPNext get_auto_batch_nos"],
                ),
                "release": release["records"],
                "wip_lineage": lineage,
                "external_wip_work_orders": external_wip_claims.get(
                    batch_no, {}
                ).get("work_orders", []),
                "pick_lists": [
                    allocation["pick_list"]
                    for allocation in pick_allocations
                    if allocation["batch_no"] == batch_no
                ],
                "stock_reservation_entries": [
                    allocation["stock_reservation_entry"]
                    for allocation in sre_allocations
                    if allocation["batch_no"] == batch_no
                ],
            },
            "ordering_sequence": 0,
        }
        rows.append(row)

    rows.sort(key=_batch_ordering_key)
    _apply_unallocated_external_claims(
        rows,
        max(
            flt(reservations["unallocated_other_work_order_reservation"])
            - sum(flt(row["quantity"]) for row in external_wip_claims.values()),
            0,
        ),
    )
    for sequence, row in enumerate(rows, start=1):
        row["ordering_sequence"] = sequence
        row["eligible"] = row["eligible_quantity"] > 0
        row["eligibility_status"] = (
            "Eligible" if row["eligible"] else "Excluded"
        )
    return rows


def _uses_controlled_wip_authority(warehouse: str, work_order: str | None) -> bool:
    work_order = cstr(work_order).strip()
    if warehouse != PRODUCTION_WIP_WAREHOUSE or not work_order:
        return False
    return bool(
        frappe.db.exists(
            "Stock Reservation Entry",
            {
                "voucher_type": "Work Order",
                "voucher_no": work_order,
                "custom_calco_reservation_set_id": ("is", "set"),
            },
        )
    )


def get_work_order_wip_evidence(work_order: str) -> list:
    """Read each submitted native detail and its own bundle, never a voucher batch map.

    transfer_qty and bundle entry quantities are in stock UOM. A detail remains
    distinct even when another detail has identical item, batch and quantity.
    Returns are classified by the caller; cancelled parents are excluded here.
    """
    rows = frappe.db.sql(
        """select se.name, se.purpose, sed.name as detail_name,
            sed.item_code, sed.original_item, sed.stock_uom,
            sed.s_warehouse, sed.t_warehouse as warehouse,
            sed.transfer_qty as qty, sed.batch_no, sed.serial_and_batch_bundle
        from `tabStock Entry` se
        inner join `tabStock Entry Detail` sed on sed.parent = se.name
        where se.work_order = %s and se.docstatus = 1
          and se.purpose in %s
        order by se.creation, sed.idx, sed.name""",
        (work_order, tuple(sorted({TRANSFER_PURPOSE, *WIP_CONSUMPTION_PURPOSES}))),
        as_dict=True,
    )
    bundles = sorted({r.serial_and_batch_bundle for r in rows if r.serial_and_batch_bundle})
    entries = defaultdict(list)
    if bundles:
        for entry in frappe.get_all(
            "Serial and Batch Entry", filters={"parent": ("in", bundles)},
            fields=["name", "parent", "batch_no", "qty"], limit_page_length=0,
        ):
            entries[entry.parent].append(entry)
    seen_details = set()
    bundle_owners = {}
    evidence = []
    for row in rows:
        identity = (row.name, row.detail_name)
        if identity in seen_details:
            continue
        seen_details.add(identity)
        bundle = row.serial_and_batch_bundle
        if bundle:
            if bundle in bundle_owners and bundle_owners[bundle] != identity:
                frappe.throw("WIP lineage requires review: a batch bundle belongs to multiple stock details.")
            bundle_owners[bundle] = identity
            quantities = defaultdict(float)
            seen_entries = set()
            for entry in entries[bundle]:
                if entry.name in seen_entries:
                    continue
                seen_entries.add(entry.name)
                quantities[cstr(entry.batch_no).strip()] += abs(flt(entry.qty))
            if not seen_entries or abs(sum(quantities.values()) - abs(flt(row.qty))) > 0.000001:
                frappe.throw("WIP lineage requires review: bundle quantity differs from Stock Entry Detail {0}.".format(row.detail_name))
            row.batch_nos = dict(quantities)
            row.bundle_entry_names = sorted(seen_entries)
        evidence.append(row)
    return evidence


def get_work_order_wip_lineage(
    work_order: str | None,
    item_code: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return exact submitted Work Order WIP movement by Item and Batch."""
    work_order = cstr(work_order).strip()
    item_code = cstr(item_code).strip()
    if not work_order:
        return {}

    evidence = get_work_order_wip_evidence(work_order)
    names = sorted(
        {cstr(row.get("name")).strip() for row in evidence if row.get("name")}
    )
    states = (
        {
            row.name: row
            for row in frappe.get_all(
                "Stock Entry",
                filters={"name": ("in", names), "docstatus": 1},
                fields=["name", "is_return"],
                limit_page_length=0,
            )
        }
        if names
        else {}
    )

    totals = defaultdict(
        lambda: {
            "transferred_qty": stock_decimal(0),
            "consumed_qty": stock_decimal(0),
            "returned_qty": stock_decimal(0),
            "net_wip_qty": stock_decimal(0),
            "stock_uom": "",
            "transfer_entries": [],
            "consumption_entries": [],
            "return_entries": [],
        }
    )
    for row in evidence:
        stock_entry = states.get(cstr(row.get("name")).strip())
        if not stock_entry:
            continue
        row_item = cstr(row.get("item_code")).strip()
        if item_code and row_item != item_code:
            continue
        purpose = cstr(row.get("purpose")).strip()
        if purpose == TRANSFER_PURPOSE and stock_entry.is_return:
            if cstr(row.get("s_warehouse")).strip() != PRODUCTION_WIP_WAREHOUSE:
                continue
            bucket, evidence_key = "returned_qty", "return_entries"
        elif purpose == TRANSFER_PURPOSE:
            if cstr(row.get("warehouse")).strip() != PRODUCTION_WIP_WAREHOUSE:
                continue
            bucket, evidence_key = "transferred_qty", "transfer_entries"
        elif purpose in WIP_CONSUMPTION_PURPOSES:
            if cstr(row.get("s_warehouse")).strip() != PRODUCTION_WIP_WAREHOUSE:
                continue
            bucket, evidence_key = "consumed_qty", "consumption_entries"
        else:
            continue

        for batch_no, quantity in _wip_evidence_quantities(row).items():
            key = (row_item, batch_no)
            totals[key][bucket] += abs(stock_decimal(quantity))
            totals[key]["stock_uom"] = cstr(row.get("stock_uom")).strip()
            totals[key][evidence_key].append(stock_entry.name)

    from calco_erp.calco_production.final_consumption import add_posted_wip_consumption
    add_posted_wip_consumption(work_order, totals, item_code=item_code)

    for values in totals.values():
        values["net_wip_qty"] = max(
            values["transferred_qty"]
            - values["consumed_qty"]
            - values["returned_qty"],
            0,
        )
        for quantity_field in ("transferred_qty", "consumed_qty", "returned_qty", "net_wip_qty"):
            values[quantity_field] = float(values[quantity_field])
        for evidence_key in (
            "transfer_entries",
            "consumption_entries",
            "return_entries",
        ):
            values[evidence_key] = sorted(set(values[evidence_key]))
    return dict(totals)


def get_external_wip_lineage_claims(
    item_code: str,
    work_order: str | None,
) -> dict[str, dict[str, Any]]:
    """Return net exact-batch WIP claims belonging to other Work Orders."""
    work_order = cstr(work_order).strip()
    transfer_details = frappe.get_all(
        "Stock Entry Detail",
        filters={
            "item_code": item_code,
            "t_warehouse": PRODUCTION_WIP_WAREHOUSE,
        },
        fields=["parent"],
        distinct=True,
        limit_page_length=0,
    )
    parent_names = sorted(
        {cstr(row.parent).strip() for row in transfer_details if row.parent}
    )
    if not parent_names:
        return {}

    other_work_orders = sorted(
        {
            cstr(row.work_order).strip()
            for row in frappe.get_all(
                "Stock Entry",
                filters={
                    "name": ("in", parent_names),
                    "docstatus": 1,
                    "purpose": TRANSFER_PURPOSE,
                    "is_return": 0,
                },
                fields=["work_order"],
                limit_page_length=0,
            )
            if row.work_order and cstr(row.work_order).strip() != work_order
        }
    )
    claims = defaultdict(lambda: {"quantity": 0.0, "work_orders": []})
    for other_work_order in other_work_orders:
        for (lineage_item, batch_no), values in get_work_order_wip_lineage(
            other_work_order, item_code=item_code
        ).items():
            if lineage_item != item_code or flt(values["net_wip_qty"]) <= 0:
                continue
            claims[batch_no]["quantity"] += flt(values["net_wip_qty"])
            claims[batch_no]["work_orders"].append(other_work_order)
    for claim in claims.values():
        claim["work_orders"] = sorted(set(claim["work_orders"]))
    return dict(claims)


def _wip_evidence_quantities(row) -> dict[str, float]:
    batch_nos = row.get("batch_nos") or {}
    if batch_nos:
        return {
            cstr(batch_no).strip(): flt(quantity)
            for batch_no, quantity in batch_nos.items()
        }
    return {cstr(row.get("batch_no")).strip(): flt(row.get("qty"))}


def _log_uat_release_override(
    *,
    work_order: str | None,
    item_code: str,
    batch_no: str,
    physical_qty: float,
    eligible_qty: float,
    released_qty: float,
) -> None:
    frappe.logger("calco_uat_rm_release_override").error(
        "Recovery UAT Override | Work Order=%s | Item=%s | Batch=%s | "
        "Physical Qty=%s | Qty Made Eligible=%s | Normal Released Qty=%s | "
        "Reason=Recovery UAT Override",
        cstr(work_order).strip() or "<none>",
        item_code,
        batch_no,
        physical_qty,
        eligible_qty,
        released_qty,
    )


def _apply_unallocated_external_claims(
    rows: list[dict[str, Any]], external_claim_qty: float
) -> None:
    remaining_claim = max(flt(external_claim_qty), 0)
    for row in rows:
        if remaining_claim <= 0:
            break
        available = flt(row["available_to_current_work_order"])
        protected = min(flt(row["own_work_order_allocation"]), available)
        deductible = max(available - protected, 0)
        deduction = min(deductible, remaining_claim)
        row["other_work_order_allocation"] += deduction
        row["available_to_current_work_order"] -= deduction
        remaining_claim -= deduction


def _get_physical_batch_rows(
    item_code: str,
    warehouse: str,
    posting_datetime: str | datetime | None = None,
    batch_no: str | None = None,
) -> list[frappe._dict]:
    company = frappe.db.get_value("Warehouse", warehouse, "company")
    effective_datetime = get_datetime(posting_datetime) if posting_datetime else None
    args = frappe._dict(
        {
            "item_code": item_code,
            "warehouse": warehouse,
            "company": company,
            "batch_no": batch_no,
            "posting_datetime": effective_datetime,
            "posting_date": effective_datetime.date() if effective_datetime else None,
            "posting_time": effective_datetime.time() if effective_datetime else None,
            "based_on": "FIFO",
            "qty": 0,
            "against_sales_order": None,
            "for_stock_levels": True,
            "ignore_reserved_stock": True,
            "ignore_voucher_nos": None,
            "consider_negative_batches": False,
            "do_not_check_future_batches": False,
            "creation": None,
        }
    )
    return get_auto_batch_nos(args) or []


def _get_release_evidence(
    item_code: str, batch_names: list[str]
) -> dict[str, dict[str, Any]]:
    result = defaultdict(lambda: {"quantity": 0.0, "records": []})
    if not batch_names:
        return result

    rows = frappe.get_all(
        "RM Release Note",
        filters={
            "docstatus": 1,
            "status": "Released",
            "item_code": item_code,
            "batch_no": ("in", batch_names),
        },
        fields=["name", "batch_no", "release_qty"],
        order_by="creation asc",
    )
    for row in rows:
        result[row.batch_no]["quantity"] += flt(row.release_qty)
        result[row.batch_no]["records"].append(row.name)
    return result


def _get_work_order_reservations(
    item_code: str, warehouse: str
) -> list[dict[str, Any]]:
    total = flt(get_reserved_qty_for_production(item_code, warehouse))
    rows = frappe.get_all(
        "Work Order Item",
        filters={
            "item_code": item_code,
            "source_warehouse": warehouse,
            "docstatus": 1,
        },
        fields=[
            "parent",
            "required_qty",
            "transferred_qty",
            "consumed_qty",
        ],
        order_by="parent asc, idx asc",
    )
    if not rows:
        return (
            [{"work_order": "", "quantity": total, "evidence": "ERPNext total"}]
            if total > 0
            else []
        )

    work_orders = {
        row.name: row
        for row in frappe.get_all(
            "Work Order",
            filters={"name": ("in", _unique(row.parent for row in rows))},
            fields=["name", "status", "skip_transfer"],
        )
    }
    result = []
    for row in rows:
        parent = work_orders.get(row.parent)
        if not parent or parent.status not in ACTIVE_WORK_ORDER_STATUSES:
            continue
        if parent.skip_transfer:
            quantity = max(flt(row.required_qty) - flt(row.consumed_qty), 0)
        elif flt(row.transferred_qty) > flt(row.required_qty):
            quantity = 0
        else:
            quantity = max(flt(row.required_qty) - flt(row.transferred_qty), 0)
        if quantity > 0:
            result.append(
                {
                    "work_order": row.parent,
                    "quantity": quantity,
                    "evidence": "ERPNext Work Order Item",
                }
            )

    attributed = sum(flt(row["quantity"]) for row in result)
    if total > attributed + 1e-9:
        result.append(
            {
                "work_order": "",
                "quantity": total - attributed,
                "evidence": "ERPNext unattributed production reservation",
            }
        )
    return result


def _get_open_pick_list_rows(
    item_code: str, warehouse: str
) -> list[frappe._dict]:
    parents = frappe.get_all(
        "Pick List",
        filters={
            "docstatus": 1,
            "status": ("!=", "Completed"),
            "purpose": TRANSFER_PURPOSE,
        },
        fields=["name", "work_order"],
        order_by="creation asc",
    )
    if not parents:
        return []
    work_orders = {row.name: row.work_order for row in parents}
    rows = frappe.get_all(
        "Pick List Item",
        filters={
            "parent": ("in", list(work_orders)),
            "item_code": item_code,
            "warehouse": warehouse,
        },
        fields=[
            "name",
            "parent",
            "item_code",
            "warehouse",
            "picked_qty",
            "transferred_qty",
            "batch_no",
            "serial_and_batch_bundle",
            "idx",
        ],
        order_by="parent asc, idx asc",
    )
    for row in rows:
        row.work_order = work_orders.get(row.parent) or ""
    return rows


def _get_pick_list_row_batches(row: frappe._dict) -> dict[str, float]:
    if row.serial_and_batch_bundle:
        return {
            batch_no: abs(flt(qty))
            for batch_no, qty in get_batches_from_bundle(
                row.serial_and_batch_bundle
            ).items()
        }
    if row.batch_no:
        return {row.batch_no: flt(row.picked_qty)}
    return {}


def _get_stock_reservation_allocations(
    item_code: str, warehouse: str, work_order: str
) -> list[dict[str, Any]]:
    active = frappe.get_all(
        "Stock Reservation Entry",
        filters={
            "docstatus": 1,
            "item_code": item_code,
            "warehouse": warehouse,
            "status": ("not in", ["Closed", "Delivered"]),
        },
        fields=[
            "name",
            "voucher_type",
            "voucher_no",
            "from_voucher_type",
            "from_voucher_no",
            "reserved_qty",
            "delivered_qty",
            "transferred_qty",
            "consumed_qty",
        ],
        order_by="creation asc",
    )
    result = []
    for row in active:
        remaining = max(
            flt(row.reserved_qty)
            - flt(row.delivered_qty)
            - flt(row.transferred_qty)
            - flt(row.consumed_qty),
            0,
        )
        if remaining <= 0:
            continue
        batches = {
            entry.batch_no: max(
                flt(entry.qty) - flt(entry.get("delivered_qty")), 0
            )
            for entry in get_serial_batch_entries_for_voucher(row.name)
            if entry.batch_no
        }
        if not batches:
            continue

        related_work_order = (
            row.voucher_no if row.voucher_type == "Work Order" else ""
        )
        related_pick_list = (
            row.from_voucher_no
            if row.from_voucher_type == "Pick List"
            else ""
        )
        if not related_work_order and row.from_voucher_type == "Pick List":
            related_work_order = (
                frappe.db.get_value("Pick List", row.from_voucher_no, "work_order")
                or ""
            )
        claim_class = (
            "Work Order"
            if related_work_order
            else "Pick List"
            if row.from_voucher_type == "Pick List"
            else "Independent SRE"
        )
        owner = (
            "Current Work Order"
            if work_order and related_work_order == work_order
            else "External"
        )
        remaining_to_allocate = remaining
        for batch_no, batch_qty in batches.items():
            quantity = min(batch_qty, remaining_to_allocate)
            if quantity <= 0:
                continue
            result.append(
                {
                    "stock_reservation_entry": row.name,
                    "work_order": related_work_order,
                    "pick_list": related_pick_list,
                    "item_code": item_code,
                    "batch_no": batch_no,
                    "warehouse": warehouse,
                    "quantity": quantity,
                    "allocation_owner": owner,
                    "claim_class": claim_class,
                    "eligible": True,
                    "eligibility_status": "Reserved",
                    "exclusion_reasons": [],
                    "evidence_source": {
                        "doctype": "Stock Reservation Entry",
                        "record": row.name,
                    },
                    "ordering_sequence": len(result) + 1,
                }
            )
            remaining_to_allocate -= quantity
            if remaining_to_allocate <= 0:
                break
    return result


def _deduplicated_allocation_claims(
    pick_allocations: list[dict[str, Any]],
    sre_allocations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two representations of one allocation without counting it twice."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for source, rows in (
        ("pick_list", pick_allocations),
        ("stock_reservation_entry", sre_allocations),
    ):
        for row in rows:
            work_order = cstr(row.get("work_order")).strip()
            pick_list = cstr(row.get("pick_list")).strip()
            if work_order:
                claim_type, claim_id = "Work Order", work_order
            elif pick_list:
                claim_type, claim_id = "Pick List", pick_list
            else:
                claim_type = "Stock Reservation Entry"
                claim_id = cstr(row.get("stock_reservation_entry")).strip()

            key = (
                row["allocation_owner"],
                claim_type,
                claim_id,
                row["batch_no"],
            )
            claim = grouped.setdefault(
                key,
                {
                    "allocation_owner": row["allocation_owner"],
                    "claim_type": claim_type,
                    "claim_id": claim_id,
                    "work_order": work_order,
                    "batch_no": row["batch_no"],
                    "pick_list_quantity": 0.0,
                    "stock_reservation_entry_quantity": 0.0,
                },
            )
            claim[f"{source}_quantity"] += flt(row["quantity"])

    result = []
    for claim in grouped.values():
        claim["quantity"] = max(
            flt(claim["pick_list_quantity"]),
            flt(claim["stock_reservation_entry_quantity"]),
        )
        result.append(claim)
    return result

def _get_batch_posting_evidence(
    item_code: str, warehouse: str, batch_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Return ordering and source-mode evidence unavailable from ERPNext's public API.

    ERPNext's get_auto_batch_nos() is authoritative for quantities and supports
    both bundle-backed and legacy stock, but it does not expose first posting
    dates or whether a quantity came from a bundle or a legacy batch_no row.
    This private read is metadata-only and never calculates availability.
    """
    if not batch_names:
        return {}

    rows = frappe.db.sql(
        """
        select
            evidence.batch_no,
            min(evidence.posting_date) as first_posting_date,
            group_concat(distinct evidence.stock_mode order by evidence.stock_mode) as stock_modes
        from (
            select
                sbe.batch_no,
                sle.posting_date,
                'Serial and Batch Bundle' as stock_mode
            from `tabStock Ledger Entry` sle
            inner join `tabSerial and Batch Entry` sbe
                on sbe.parent = sle.serial_and_batch_bundle
            where sle.item_code = %(item_code)s
              and sle.warehouse = %(warehouse)s
              and sle.is_cancelled = 0
              and sbe.batch_no in %(batch_names)s

            union all

            select
                sle.batch_no,
                sle.posting_date,
                'ERPNext legacy batch compatibility' as stock_mode
            from `tabStock Ledger Entry` sle
            where sle.item_code = %(item_code)s
              and sle.warehouse = %(warehouse)s
              and sle.is_cancelled = 0
              and ifnull(sle.batch_no, '') != ''
              and sle.batch_no in %(batch_names)s
        ) evidence
        group by evidence.batch_no
        """,
        {
            "item_code": item_code,
            "warehouse": warehouse,
            "batch_names": tuple(batch_names),
        },
        as_dict=True,
    )
    return {
        row.batch_no: {
            "first_posting_date": row.first_posting_date,
            "stock_modes": cstr(row.stock_modes).split(",") if row.stock_modes else [],
        }
        for row in rows
    }


def _get_item(item_code: str) -> frappe._dict | None:
    return frappe.db.get_value(
        "Item",
        item_code,
        ["name", "disabled", "is_stock_item", "has_batch_no"],
        as_dict=True,
    )


def _get_batch_metadata(batch_names: list[str]) -> dict[str, frappe._dict]:
    if not batch_names:
        return {}
    return {
        row.name: row
        for row in frappe.get_all(
            "Batch",
            filters={"name": ("in", batch_names)},
            fields=[
                "name",
                "item",
                "disabled",
                "expiry_date",
                "manufacturing_date",
                "creation",
            ],
        )
    }


def _get_warehouse(name: str, company: str | None) -> frappe._dict | None:
    row = frappe.db.get_value(
        "Warehouse",
        name,
        ["name", "company", "is_group", "disabled"],
        as_dict=True,
    )
    if not row or row.is_group or row.disabled:
        return None
    if company and row.company != company:
        return None
    return row


def _resolve_source_warehouse(warehouse: str | None) -> str:
    return cstr(warehouse).strip() or PRODUCTION_SOURCE_WAREHOUSE


def _batch_ordering_key(row: dict[str, Any]) -> tuple[Any, ...]:
    expiry = _as_date(row.get("expiry_date")) or date.max
    manufacturing_or_posting = (
        _as_date(row.get("manufacturing_date"))
        or _as_date(row.get("first_posting_date"))
        or date.max
    )
    creation = get_datetime(row["batch_creation"]) if row.get("batch_creation") else datetime.max
    return expiry, manufacturing_or_posting, creation, row["batch_no"]


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return getdate(value)


def _unique(values) -> list[Any]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _empty_batch_result(
    item_code: str, batch_no: str, warehouse: str, reasons: list[str]
) -> dict[str, Any]:
    return {
        "item_code": item_code,
        "batch_no": batch_no,
        "warehouse": warehouse,
        "physical_quantity": 0.0,
        "released_quantity": 0.0,
        "eligible_quantity": 0.0,
        "normal_released_eligible_quantity": 0.0,
        "uat_override_eligible_quantity": 0.0,
        "own_work_order_allocation": 0.0,
        "other_work_order_allocation": 0.0,
        "available_to_current_work_order": 0.0,
        "eligible": False,
        "eligibility_status": "Excluded",
        "eligibility_source": "RM Release Note",
        "uat_rm_release_override_active": False,
        "uat_rm_release_override_used": False,
        "exclusion_reasons": reasons,
        "evidence_source": {},
        "ordering_sequence": 0,
    }


def _empty_item_result(
    item_code: str, warehouse: str, reasons: list[str]
) -> dict[str, Any]:
    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "has_batch_no": False,
        "physical_quantity": 0.0,
        "released_quantity": 0.0,
        "eligible_quantity": 0.0,
        "normal_released_eligible_quantity": 0.0,
        "uat_override_eligible_quantity": 0.0,
        "own_work_order_allocation": 0.0,
        "other_work_order_allocation": 0.0,
        "available_to_current_work_order": 0.0,
        "eligible": False,
        "eligibility_status": "Excluded",
        "eligibility_source": "RM Release Note",
        "uat_rm_release_override_active": False,
        "uat_rm_release_override_used": False,
        "exclusion_reasons": reasons,
        "evidence_source": {},
        "ordering_sequence": 0,
        "batches": [],
    }
