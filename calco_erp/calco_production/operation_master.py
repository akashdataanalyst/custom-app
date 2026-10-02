from __future__ import annotations

from copy import deepcopy

import frappe
from frappe import _
from frappe.utils import cint, flt, get_timedelta


PILOT_ITEM = "710C3031"
PILOT_WORKSTATION = "Line 1"
PILOT_HOLIDAY_LIST = "Holiday List -2026 (MF2)"
COMPOUNDING_OPERATION = "Compounding / Extrusion"
PACKING_OPERATION = "Packing"
COMPOUNDING_RATE_KG_PER_HOUR = 25.0
PACKING_MINUTES_PER_100_KG = 15.0


def ensure_phase_7a_m1_foundation() -> dict:
    from calco_erp.release_profile import require_recovery_distribution
    require_recovery_distribution()
    operations = ensure_operation_masters()
    workstation = ensure_line_1_calendar()
    source_bom = get_authoritative_pilot_bom()
    pilot_bom = ensure_pilot_bom_revision(source_bom)
    return {
        "operations": operations,
        "workstation": workstation,
        "source_bom": source_bom.name,
        "pilot_bom": pilot_bom.name,
        "pilot_bom_is_default": flt(pilot_bom.is_default),
        "times": calculate_operation_times(pilot_bom.quantity),
    }


def ensure_operation_masters() -> list[str]:
    from calco_erp.release_profile import require_recovery_distribution
    require_recovery_distribution()
    names = []
    for operation_name in (COMPOUNDING_OPERATION, PACKING_OPERATION):
        if not frappe.db.exists("Operation", operation_name):
            operation = frappe.new_doc("Operation")
            operation.name = operation_name
            operation.operation = operation_name
            operation.workstation = ""
            operation.insert()
        names.append(operation_name)
    return names


def ensure_line_1_calendar() -> str:
    from calco_erp.release_profile import require_recovery_distribution
    require_recovery_distribution()
    if not frappe.db.exists("Workstation", PILOT_WORKSTATION):
        frappe.throw(_("Required pilot Workstation {0} does not exist.").format(PILOT_WORKSTATION))
    if not frappe.db.exists("Holiday List", PILOT_HOLIDAY_LIST):
        frappe.throw(_("Approved manufacturing Holiday List {0} does not exist.").format(PILOT_HOLIDAY_LIST))
    holiday_list = frappe.get_doc("Holiday List", PILOT_HOLIDAY_LIST)
    if holiday_list.weekly_off != "Sunday":
        frappe.throw(_("Pilot Holiday List must use Sunday as the weekly off day."))

    workstation = frappe.get_doc("Workstation", PILOT_WORKSTATION)
    hours = workstation.get("working_hours") or []
    if (
        workstation.holiday_list == PILOT_HOLIDAY_LIST
        and len(hours) == 1
        and get_timedelta(hours[0].start_time) == get_timedelta("00:00:00")
        and get_timedelta(hours[0].end_time) == get_timedelta("23:59:59")
        and cint(hours[0].enabled)
    ):
        return workstation.name
    workstation.holiday_list = PILOT_HOLIDAY_LIST
    workstation.set("working_hours", [])
    workstation.append("working_hours", {"start_time": "00:00:00", "end_time": "23:59:59", "enabled": 1})
    workstation.save()
    return workstation.name


def get_authoritative_pilot_bom():
    bom_name = frappe.db.get_value(
        "BOM",
        {"item": PILOT_ITEM, "docstatus": 1, "is_active": 1, "is_default": 1},
        "name",
        order_by="modified desc",
    )
    if not bom_name:
        frappe.throw(_("No active default submitted BOM exists for {0}.").format(PILOT_ITEM))
    return frappe.get_doc("BOM", bom_name)


def calculate_operation_times(bom_qty: float) -> dict[str, float]:
    quantity = flt(bom_qty)
    if quantity <= 0:
        frappe.throw(_("Pilot BOM quantity must be greater than zero."))
    return {
        COMPOUNDING_OPERATION: quantity / COMPOUNDING_RATE_KG_PER_HOUR * 60,
        PACKING_OPERATION: quantity / 100 * PACKING_MINUTES_PER_100_KG,
    }


def ensure_pilot_bom_revision(source_bom):
    from calco_erp.release_profile import require_recovery_distribution
    require_recovery_distribution()
    times = calculate_operation_times(source_bom.quantity)
    existing = find_matching_pilot_bom(source_bom, times)
    if existing:
        return frappe.get_doc("BOM", existing)

    pilot = frappe.copy_doc(source_bom)
    pilot.name = None
    pilot.docstatus = 0
    pilot.is_default = 0
    pilot.custom_default_authority_policy = "Keep Non-Default"
    pilot.custom_default_authority_reason = "Controlled operations pilot; preserve existing production default."
    pilot.is_active = 1
    pilot.with_operations = 1
    pilot.set("operations", [])
    pilot.append("operations", {
        "operation": COMPOUNDING_OPERATION,
        "sequence_id": 1,
        "workstation": PILOT_WORKSTATION,
        "time_in_mins": times[COMPOUNDING_OPERATION],
        "quality_inspection_required": 1,
        "hour_rate": 0,
    })
    pilot.insert()
    pilot.submit()
    validate_bom_preservation(source_bom, pilot)
    return pilot


def find_matching_pilot_bom(source_bom, times: dict[str, float]) -> str | None:
    candidates = frappe.get_all(
        "BOM",
        filters={"item": PILOT_ITEM, "docstatus": 1, "is_active": 1, "with_operations": 1},
        pluck="name",
        order_by="creation desc",
    )
    for name in candidates:
        candidate = frappe.get_doc("BOM", name)
        if operation_signature(candidate) != expected_operation_signature(times):
            continue
        if material_signature(candidate) == material_signature(source_bom) and scrap_signature(candidate) == scrap_signature(source_bom):
            return name
    return None


def expected_operation_signature(times: dict[str, float]) -> list[tuple]:
    return [
        (1, COMPOUNDING_OPERATION, PILOT_WORKSTATION, flt(times[COMPOUNDING_OPERATION], 6), 1),
    ]


def operation_signature(bom) -> list[tuple]:
    return [
        (int(row.sequence_id or row.idx), row.operation, row.workstation, flt(row.time_in_mins, 6), int(row.quality_inspection_required or 0))
        for row in sorted(bom.operations or [], key=lambda row: (row.sequence_id or row.idx, row.idx))
    ]


def material_signature(bom) -> list[dict]:
    return [
        deepcopy({
            "item_code": row.item_code,
            "qty": flt(row.qty, 9),
            "uom": row.uom,
            "stock_uom": row.stock_uom,
            "conversion_factor": flt(row.conversion_factor, 9),
            "source_warehouse": row.source_warehouse,
            "allow_alternative_item": int(row.allow_alternative_item or 0),
            "do_not_explode": int(row.do_not_explode or 0),
        })
        for row in bom.items or []
    ]


def scrap_signature(bom) -> list[dict]:
    return [
        {"item_code": row.item_code, "stock_qty": flt(row.stock_qty, 9), "stock_uom": row.stock_uom}
        for row in bom.get("scrap_items") or []
    ]


def validate_bom_preservation(source_bom, pilot_bom):
    if material_signature(source_bom) != material_signature(pilot_bom):
        frappe.throw(_("Pilot BOM material composition differs from the approved source BOM."))
    if scrap_signature(source_bom) != scrap_signature(pilot_bom):
        frappe.throw(_("Pilot BOM scrap/by-product configuration differs from the approved source BOM."))
    for fieldname in ("item", "quantity", "uom", "transfer_material_against"):
        if source_bom.get(fieldname) != pilot_bom.get(fieldname):
            frappe.throw(_("Pilot BOM does not preserve source field {0}.").format(fieldname))
