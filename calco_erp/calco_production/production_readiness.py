from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime

from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

from calco_erp.calco_purchase.master_data_governance_dashboard import (
    get_operational_readiness_by_item,
)
from calco_erp.inventory.availability import (
    get_item_availability,
    get_production_warehouses,
)


READY = "Ready for Material Issue"
NOT_READY = "Not Ready"
BLOCKED_WORKSTATION_STATUSES = {"Off", "Problem", "Maintenance"}
PARTIAL_APPROVER_ROLES = {"Production Head"}

READINESS_FIELDS = {
    "status": "custom_production_readiness_status",
    "checked_on": "custom_production_readiness_checked_on",
    "checked_by": "custom_production_readiness_checked_by",
}
PARTIAL_FIELDS = {
    "approved": "custom_partial_production_approved",
    "quantity": "custom_partial_production_qty",
    "reason": "custom_partial_production_reason",
    "approved_by": "custom_partial_production_approved_by",
    "approved_on": "custom_partial_production_approved_on",
}


def ensure_production_readiness_fields():
    create_custom_fields(
        {
            "Work Order": [
                {
                    "fieldname": "custom_production_readiness_section",
                    "label": "Production Readiness",
                    "fieldtype": "Section Break",
                    "insert_after": "custom_production_journey_html",
                },
                {
                    "fieldname": READINESS_FIELDS["status"],
                    "label": "Overall Result",
                    "fieldtype": "Select",
                    "options": f"\n{NOT_READY}\n{READY}",
                    "read_only": 1,
                    "insert_after": "custom_production_readiness_section",
                },
                {
                    "fieldname": READINESS_FIELDS["checked_on"],
                    "label": "Readiness Checked On",
                    "fieldtype": "Datetime",
                    "read_only": 1,
                    "insert_after": READINESS_FIELDS["status"],
                },
                {
                    "fieldname": READINESS_FIELDS["checked_by"],
                    "label": "Readiness Checked By",
                    "fieldtype": "Link",
                    "options": "User",
                    "read_only": 1,
                    "insert_after": READINESS_FIELDS["checked_on"],
                },
                {
                    "fieldname": "custom_production_readiness_summary",
                    "label": "Production Readiness",
                    "fieldtype": "HTML",
                    "insert_after": READINESS_FIELDS["checked_by"],
                },
                {
                    "fieldname": "custom_partial_production_section",
                    "label": "Partial Production Approval",
                    "fieldtype": "Section Break",
                    "collapsible": 1,
                    "insert_after": "custom_production_readiness_summary",
                },
                {
                    "fieldname": PARTIAL_FIELDS["approved"],
                    "label": "Partial Production Approved",
                    "fieldtype": "Check",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "insert_after": "custom_partial_production_section",
                },
                {
                    "fieldname": PARTIAL_FIELDS["quantity"],
                    "label": "Approved Production Quantity",
                    "fieldtype": "Float",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "insert_after": PARTIAL_FIELDS["approved"],
                },
                {
                    "fieldname": PARTIAL_FIELDS["reason"],
                    "label": "Partial Production Reason",
                    "fieldtype": "Small Text",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "insert_after": PARTIAL_FIELDS["quantity"],
                },
                {
                    "fieldname": PARTIAL_FIELDS["approved_by"],
                    "label": "Partial Production Approved By",
                    "fieldtype": "Link",
                    "options": "User",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "insert_after": PARTIAL_FIELDS["reason"],
                },
                {
                    "fieldname": PARTIAL_FIELDS["approved_on"],
                    "label": "Partial Production Approved On",
                    "fieldtype": "Datetime",
                    "read_only": 1,
                    "allow_on_submit": 1,
                    "insert_after": PARTIAL_FIELDS["approved_by"],
                },
            ]
        },
        update=True,
    )


def _check(key, label, ready, owner, blockers=None, details=None):
    return {
        "key": key,
        "label": label,
        "ready": bool(ready),
        "status": "Ready" if ready else NOT_READY,
        "owner": owner,
        "blockers": blockers or [],
        "details": details or {},
    }


def _effective_quantity(work_order):
    planned_qty = flt(work_order.get("qty"))
    if not cint(work_order.get(PARTIAL_FIELDS["approved"])):
        return planned_qty, False

    approved_qty = flt(work_order.get(PARTIAL_FIELDS["quantity"]))
    approval_complete = all(
        [
            approved_qty > 0,
            approved_qty < planned_qty,
            work_order.get(PARTIAL_FIELDS["reason"]),
            work_order.get(PARTIAL_FIELDS["approved_by"]),
            work_order.get(PARTIAL_FIELDS["approved_on"]),
        ]
    )
    return (approved_qty, True) if approval_complete else (planned_qty, False)


def _get_bom_requirements(work_order, bom_no, evaluation_qty):
    if not bom_no or evaluation_qty <= 0:
        return {}
    return get_bom_items_as_dict(
        bom_no,
        work_order.company,
        qty=evaluation_qty,
        fetch_exploded=cint(work_order.get("use_multi_level_bom")),
    )


def _evaluate_bom(work_order, bom_no, evaluation_qty):
    blockers = []
    if not bom_no or not frappe.db.exists("BOM", bom_no):
        return _check("bom", "BOM", False, "Engineering / Production Head", ["BOM is required."])

    bom = frappe.db.get_value(
        "BOM",
        bom_no,
        ["item", "quantity", "is_active", "docstatus"],
        as_dict=True,
    )
    if cint(bom.docstatus) != 1:
        blockers.append(_("BOM {0} is not submitted.").format(bom_no))
    if not cint(bom.is_active):
        blockers.append(_("BOM {0} is inactive.").format(bom_no))
    if bom.item != work_order.production_item:
        blockers.append(
            _("BOM {0} belongs to {1}, not {2}.").format(
                bom_no, bom.item or "-", work_order.production_item or "-"
            )
        )
    if flt(bom.quantity) <= 0:
        blockers.append(_("BOM {0} has an invalid output quantity.").format(bom_no))

    requirements = {}
    if not blockers:
        requirements = _get_bom_requirements(work_order, bom_no, evaluation_qty)
        if not requirements:
            blockers.append(_("BOM {0} has no required components.").format(bom_no))

    item_codes = list(requirements)
    if item_codes:
        disabled_items = frappe.get_all(
            "Item",
            filters={"name": ("in", item_codes), "disabled": 1},
            pluck="name",
        )
        for item_code in sorted(disabled_items):
            blockers.append(_("BOM Item {0} is disabled.").format(item_code))

    return _check(
        "bom",
        "BOM",
        not blockers,
        "Engineering / Production Head",
        blockers,
        {"bom_no": bom_no, "component_count": len(requirements)},
    )


def _evaluate_material(work_order, requirements, warehouse_role="source"):
    blockers = []
    details = []
    warehouses = get_production_warehouses(work_order.get("company"))
    source_warehouse = (
        warehouses.get("wip_warehouse")
        if warehouse_role in {"wip", "consumed"}
        else warehouses.get("source_warehouse")
    )
    warehouse_display = (
        "{0} + {1}".format(
            warehouses.get("source_warehouse"),
            warehouses.get("wip_warehouse"),
        )
        if warehouse_role == "hybrid"
        else source_warehouse
    )
    if not warehouses.get("eligible"):
        return _check(
            "material",
            "Material",
            False,
            "Stores / Purchase / Quality",
            warehouses.get("exclusion_reasons"),
            {"items": [], "warehouse_evidence": warehouses},
        )

    item_codes = list(requirements)
    item_rows = {
        row.name: row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", item_codes)},
            fields=["name", "item_group", "is_stock_item", "has_batch_no", "disabled"],
        )
    }
    raw_materials = [
        item_code
        for item_code, row in item_rows.items()
        if row.item_group == "Raw Material"
    ]
    governance = get_operational_readiness_by_item(raw_materials)
    consumed_by_item = (
        _consumed_quantity_by_item(work_order) if warehouse_role == "consumed" else {}
    )

    # Credit only submitted WO-attributed consumption during WIP/hybrid
    # readiness. This is accounted material, never available stock to issue.
    consumption_evidence = {}
    if warehouse_role in {"wip", "hybrid"}:
        from calco_erp.inventory.availability import get_work_order_wip_lineage
        consumption_evidence = get_work_order_wip_lineage(work_order.get("name"))

    for item_code, requirement in requirements.items():
        required_qty = flt(requirement.get("qty"))
        item = item_rows.get(item_code)
        if not item:
            blockers.append(_("BOM Item {0} does not exist.").format(item_code))
            continue
        if cint(item.disabled):
            blockers.append(_("BOM Item {0} is disabled.").format(item_code))
            continue
        if not cint(item.is_stock_item):
            continue

        if item.item_group == "Raw Material":
            readiness = governance.get(item_code) or {}
            if not readiness.get("ready"):
                blockers.append(
                    _("RM {0} is not Operational Ready ({1}%).").format(
                        item_code, cint(readiness.get("percent"))
                    )
                )

        if warehouse_role == "consumed":
            available_qty = flt(consumed_by_item.get(item_code))
            availability = {
                "physical_quantity": 0,
                "released_quantity": 0,
                "eligible_quantity": 0,
                "normal_released_eligible_quantity": 0,
                "uat_override_eligible_quantity": 0,
                "uat_rm_release_override_active": False,
                "uat_rm_release_override_used": False,
                "eligibility_source": "Not applicable after consumption",
                "own_work_order_allocation": 0,
                "other_work_order_allocation": 0,
                "batches": [],
                "exclusion_reasons": [],
                "evidence_source": {
                    "quantity": "ERPNext Work Order Item consumed_qty"
                },
            }
        elif warehouse_role == "hybrid":
            stores_availability = get_item_availability(
                item_code=item_code,
                warehouse=warehouses.get("source_warehouse"),
                work_order=cstr(work_order.get("name")).strip() or None,
            )
            wip_availability = get_item_availability(
                item_code=item_code,
                warehouse=warehouses.get("wip_warehouse"),
                work_order=cstr(work_order.get("name")).strip() or None,
            )
            availability = _combine_material_availability(
                stores_availability, wip_availability
            )
            available_qty = flt(availability.get("available_to_current_work_order"))
        else:
            availability = get_item_availability(
                item_code=item_code,
                warehouse=source_warehouse,
                work_order=cstr(work_order.get("name")).strip() or None,
            )
            available_qty = flt(availability.get("available_to_current_work_order"))
        consumed_qty = sum(
            max(flt(row.get("consumed_qty")), 0)
            for (lineage_item, _batch), row in consumption_evidence.items()
            if lineage_item == item_code
        )
        accounted_qty = available_qty + consumed_qty
        details.append(
            {
                "item_code": item_code,
                "required_qty": required_qty,
                "available_qty": available_qty,
                "consumed_qty": consumed_qty,
                "accounted_qty": accounted_qty,
                "consumption_evidence": [
                    {"batch_no": batch, "qty": flt(row.get("consumed_qty")),
                     "stock_entries": row.get("consumption_entries") or []}
                    for (lineage_item, batch), row in consumption_evidence.items()
                    if lineage_item == item_code and flt(row.get("consumed_qty")) > 0
                ],
                "physical_qty": flt(availability.get("physical_quantity")),
                "released_qty": flt(availability.get("released_quantity")),
                "eligible_qty": flt(availability.get("eligible_quantity")),
                "normal_released_eligible_qty": flt(
                    availability.get("normal_released_eligible_quantity")
                ),
                "uat_override_eligible_qty": flt(
                    availability.get("uat_override_eligible_quantity")
                ),
                "uat_rm_release_override_active": bool(
                    availability.get("uat_rm_release_override_active")
                ),
                "uat_rm_release_override_used": bool(
                    availability.get("uat_rm_release_override_used")
                ),
                "eligibility_source": availability.get("eligibility_source") or "",
                "own_work_order_allocation": flt(
                    availability.get("own_work_order_allocation")
                ),
                "other_work_order_allocation": flt(
                    availability.get("other_work_order_allocation")
                ),
                "eligible_batches": [
                    {
                        "batch_no": row.get("batch_no"),
                        "qty": flt(row.get("available_to_current_work_order")),
                        "normal_released_eligible_qty": flt(
                            row.get("normal_released_eligible_quantity")
                        ),
                        "uat_override_eligible_qty": flt(
                            row.get("uat_override_eligible_quantity")
                        ),
                        "eligibility_source": row.get("eligibility_source") or "",
                        "uat_rm_release_override_used": bool(
                            row.get("uat_rm_release_override_used")
                        ),
                        "ordering_sequence": row.get("ordering_sequence"),
                    }
                    for row in availability.get("batches") or []
                    if row.get("eligible")
                ],
                "exclusion_reasons": availability.get("exclusion_reasons") or [],
                "evidence_source": availability.get("evidence_source") or {},
            }
        )
        if accounted_qty + 1e-9 < required_qty:
            if warehouse_role in {"wip", "hybrid"}:
                blockers.append(_("{0}: Required Qty {1}; Available Qty {2}; Consumed Qty {3}; Accounted Qty {4}; Warehouse {5}.").format(
                    item_code, round(required_qty, 3), round(available_qty, 3), round(consumed_qty, 3), round(accounted_qty, 3), warehouse_display))
            elif warehouse_role == "consumed":
                blockers.append(
                    _("{0}: Required Qty {1}; Consumed Qty {2}.").format(
                        item_code,
                        round(required_qty, 3),
                        round(available_qty, 3),
                    )
                )
            else:
                blockers.append(
                    _(
                        "{0}: Required Qty {1}; Available Qty {2}; Warehouse {3}."
                    ).format(
                        item_code,
                        round(required_qty, 3),
                        round(available_qty, 3),
                        warehouse_display,
                    )
                )

    return _check(
        "material",
        "Material",
        not blockers,
        "Stores / Purchase / Quality",
        blockers,
        {"items": details, "warehouse_evidence": warehouses},
    )


def _combine_material_availability(stores, wip):
    quantity_fields = (
        "physical_quantity",
        "released_quantity",
        "eligible_quantity",
        "normal_released_eligible_quantity",
        "uat_override_eligible_quantity",
        "own_work_order_allocation",
        "other_work_order_allocation",
        "available_to_current_work_order",
    )
    combined = {
        fieldname: flt(stores.get(fieldname)) + flt(wip.get(fieldname))
        for fieldname in quantity_fields
    }
    combined.update(
        {
            "batches": list(stores.get("batches") or []) + list(wip.get("batches") or []),
            "eligibility_source": "{0} + {1}".format(
                stores.get("eligibility_source") or "Stores authority",
                wip.get("eligibility_source") or "WIP authority",
            ),
            "uat_rm_release_override_active": bool(
                stores.get("uat_rm_release_override_active")
            ),
            "uat_rm_release_override_used": bool(
                stores.get("uat_rm_release_override_used")
            ),
            "exclusion_reasons": list(stores.get("exclusion_reasons") or [])
            + list(wip.get("exclusion_reasons") or []),
            "evidence_source": {
                "stores": stores.get("evidence_source") or {},
                "wip": wip.get("evidence_source") or {},
            },
        }
    )
    return frappe._dict(combined)


def _evaluate_machine(work_order, machine, check_planned_capacity=True):
    blockers = []
    if not machine:
        return _check(
            "machine",
            "Machine",
            False,
            "Production / Maintenance",
            ["Machine is required."],
        )

    workstation = frappe.db.get_value(
        "Workstation",
        machine,
        ["name", "disabled", "status", "production_capacity"],
        as_dict=True,
    )
    if not workstation:
        blockers.append(_("Machine {0} does not exist in Workstation.").format(machine))
    else:
        if cint(workstation.disabled):
            blockers.append(_("Machine {0} is disabled.").format(machine))
        if workstation.status in BLOCKED_WORKSTATION_STATUSES:
            blockers.append(
                _("Machine {0} status is {1}.").format(machine, workstation.status)
            )

    production_line = (work_order.get("custom_production_line") or "").strip()
    if production_line and production_line != machine:
        blockers.append(
            _("Machine {0} does not match Production Line {1}.").format(
                machine, production_line
            )
        )

    operation_machines = {
        row.workstation
        for row in work_order.get("operations") or []
        if row.get("workstation")
    }
    if operation_machines and machine not in operation_machines:
        blockers.append(
            _("Machine {0} is not assigned to the Work Order operations.").format(machine)
        )

    planned_start = work_order.get("planned_start_date")
    planned_end = work_order.get("planned_end_date")
    if check_planned_capacity and workstation and planned_start and planned_end:
        overlap_count = frappe.db.sql(
            """
            select count(distinct woo.parent)
            from `tabWork Order Operation` woo
            inner join `tabWork Order` wo on wo.name = woo.parent
            where woo.workstation = %(machine)s
              and wo.name != %(work_order)s
              and wo.docstatus < 2
              and wo.status not in ('Completed', 'Stopped', 'Cancelled')
              and woo.planned_start_time < %(planned_end)s
              and woo.planned_end_time > %(planned_start)s
            """,
            {
                "machine": machine,
                "work_order": work_order.name or "",
                "planned_start": get_datetime(planned_start),
                "planned_end": get_datetime(planned_end),
            },
        )[0][0]
        if cint(overlap_count) >= max(cint(workstation.production_capacity), 1):
            blockers.append(
                _("Machine {0} has no available capacity in the planned period.").format(
                    machine
                )
            )

    return _check(
        "machine",
        "Machine",
        not blockers,
        "Production / Maintenance",
        blockers,
        {"machine": machine},
    )


def _evaluate_planning(work_order, bom_no):
    from calco_erp.calco_production.fg_planning_authority import authority
    direct = authority(work_order, bom_no)
    if direct is not None:
        return _check("planning", "Planning", direct["ready"], "Customer Service / Production", direct["blockers"], direct)
    blockers = []
    if not work_order.production_plan or not work_order.production_plan_item:
        blockers.append(_("Submitted Production Plan lineage is required."))
        return _check(
            "planning", "Planning", False, "Planning / Production Head", blockers
        )

    plan_docstatus = frappe.db.get_value(
        "Production Plan", work_order.production_plan, "docstatus"
    )
    if cint(plan_docstatus) != 1:
        blockers.append(
            _("Production Plan {0} is not submitted.").format(
                work_order.production_plan
            )
        )

    plan_item = frappe.db.get_value(
        "Production Plan Item",
        work_order.production_plan_item,
        [
            "parent",
            "item_code",
            "bom_no",
            "planned_qty",
            "custom_planning_review_status",
            "custom_planning_reviewed_qty",
        ],
        as_dict=True,
    )
    if not plan_item:
        blockers.append(
            _("Production Plan Item {0} does not exist.").format(
                work_order.production_plan_item
            )
        )
    else:
        if plan_item.parent != work_order.production_plan:
            blockers.append(_("Production Plan Item belongs to another Production Plan."))
        if plan_item.item_code != work_order.production_item:
            blockers.append(_("Production Plan Item does not match the FG Item."))
        if plan_item.bom_no and plan_item.bom_no != bom_no:
            blockers.append(_("Work Order BOM does not match the Production Plan Item."))
        if plan_item.custom_planning_review_status != "Confirmed":
            blockers.append(_("Planning Review is not Confirmed."))
        max_qty = min(
            flt(plan_item.planned_qty),
            flt(plan_item.custom_planning_reviewed_qty),
        )
        if flt(work_order.qty) <= 0 or flt(work_order.qty) > max_qty + 1e-9:
            blockers.append(
                _("Work Order quantity exceeds the approved planned quantity {0}.").format(
                    round(max_qty, 3)
                )
            )

    if cint(work_order.get(PARTIAL_FIELDS["approved"])):
        effective_qty, partial_valid = _effective_quantity(work_order)
        if not partial_valid:
            blockers.append(_("Partial Production approval is incomplete or invalid."))

    return _check(
        "planning",
        "Planning",
        not blockers,
        "Planning / Production Head",
        blockers,
    )


def evaluate_production_readiness(work_order, overrides=None):
    if isinstance(work_order, str):
        work_order = frappe.get_doc("Work Order", work_order)
    overrides = frappe._dict(overrides or {})

    bom_no = overrides.get("bom_no") or work_order.get("bom_no")
    machine = (
        overrides.get("machine")
        if "machine" in overrides
        else work_order.get("custom_machine")
    )
    original_qty = work_order.qty
    try:
        if overrides.get("qty") is not None:
            work_order.qty = flt(overrides.qty)
        evaluation_qty, partial_approved = _effective_quantity(work_order)
        material_warehouse = overrides.get("material_warehouse")
        if not material_warehouse:
            transferred_qty = flt(
                work_order.get("material_transferred_for_manufacturing")
            )
            material_warehouse = (
                "wip"
                if transferred_qty >= evaluation_qty - 1e-9
                else "hybrid"
                if transferred_qty > 1e-9
                else "source"
            )

        bom_check = _evaluate_bom(work_order, bom_no, evaluation_qty)
        requirements = (
            _get_bom_requirements(work_order, bom_no, evaluation_qty)
            if bom_check["ready"]
            else {}
        )
        checks = [
            _evaluate_material(work_order, requirements, material_warehouse)
            if requirements
            else _check(
                "material",
                "Material",
                False,
                "Stores / Purchase / Quality",
                ["Material readiness cannot be evaluated until BOM is ready."],
            ),
            _evaluate_machine(work_order, machine, check_planned_capacity=False) if overrides.get("physical_execution") else _evaluate_machine(work_order, machine),
            bom_check,
            _evaluate_planning(work_order, bom_no),
        ]
        blockers = [
            {"check": check["label"], "reason": reason, "owner": check["owner"]}
            for check in checks
            for reason in check["blockers"]
        ]
        material_items = checks[0].get("details", {}).get("items", [])
        uat_override_active = any(
            row.get("uat_rm_release_override_active") for row in material_items
        )
        uat_override_used = any(
            row.get("uat_rm_release_override_used") for row in material_items
        )
        ready = all(check["ready"] for check in checks)
        return {
            "work_order": work_order.name,
            "overall_result": READY if ready else NOT_READY,
            "ready": ready,
            "evaluation_qty": evaluation_qty,
            "planned_qty": flt(work_order.qty),
            "partial_production_approved": partial_approved,
            "uat_rm_release_override_active": uat_override_active,
            "uat_rm_release_override_used": uat_override_used,
            "checks": checks,
            "blockers": blockers,
        }
    finally:
        work_order.qty = original_qty

def _persist_readiness(work_order, result):
    if not work_order.name or not frappe.db.exists("Work Order", work_order.name):
        return
    frappe.db.set_value(
        "Work Order",
        work_order.name,
        {
            READINESS_FIELDS["status"]: result["overall_result"],
            READINESS_FIELDS["checked_on"]: now_datetime(),
            READINESS_FIELDS["checked_by"]: frappe.session.user,
        },
        update_modified=False,
    )


@frappe.whitelist()
def get_production_readiness(
    work_order: str,
    bom_no: str | None = None,
    qty: float | None = None,
    machine: str | None = None,
):
    overrides = {"bom_no": bom_no, "qty": qty}
    if machine is not None:
        overrides["machine"] = machine
    return evaluate_production_readiness(work_order, overrides)


@frappe.whitelist()
def refresh_production_readiness(work_order: str):
    doc = frappe.get_doc("Work Order", work_order)
    result = evaluate_production_readiness(doc)
    _persist_readiness(doc, result)
    return result


def assert_production_readiness(work_order, persist=True, overrides=None):
    if isinstance(work_order, str):
        work_order = frappe.get_doc("Work Order", work_order)
    result = evaluate_production_readiness(work_order, overrides=overrides)
    if persist:
        _persist_readiness(work_order, result)
    if not result["ready"]:
        reasons = "<br>".join(
            _("{0}: {1} (Owner: {2})").format(
                row["check"], row["reason"], row["owner"]
            )
            for row in result["blockers"]
        )
        frappe.throw(
            _("Production Readiness is Not Ready.<br>{0}").format(reasons),
            title=_("Production Readiness"),
        )
    return result


@frappe.whitelist()
def approve_partial_production(work_order: str, approved_qty: float, reason: str):
    if not PARTIAL_APPROVER_ROLES.intersection(frappe.get_roles()):
        frappe.throw(
            _("Only Production Head can approve partial production."),
            frappe.PermissionError,
        )
    doc = frappe.get_doc("Work Order", work_order)
    if cint(doc.docstatus) == 2:
        frappe.throw(_("Cancelled Work Order cannot receive partial approval."))

    approved_qty = flt(approved_qty)
    reason = (reason or "").strip()
    if approved_qty <= 0 or approved_qty >= flt(doc.qty):
        frappe.throw(
            _("Approved quantity must be greater than zero and less than Work Order quantity {0}.").format(
                doc.qty
            )
        )
    if not reason:
        frappe.throw(_("Partial Production reason is mandatory."))

    frappe.db.set_value(
        "Work Order",
        doc.name,
        {
            PARTIAL_FIELDS["approved"]: 1,
            PARTIAL_FIELDS["quantity"]: approved_qty,
            PARTIAL_FIELDS["reason"]: reason,
            PARTIAL_FIELDS["approved_by"]: frappe.session.user,
            PARTIAL_FIELDS["approved_on"]: now_datetime(),
            READINESS_FIELDS["status"]: NOT_READY,
        },
    )
    result = refresh_production_readiness(doc.name)
    return result


def clear_stale_partial_approval(doc, method=None):
    if doc.is_new() or not cint(doc.get(PARTIAL_FIELDS["approved"])):
        return
    previous = frappe.db.get_value(
        "Work Order", doc.name, ["bom_no", "qty"], as_dict=True
    )
    if not previous:
        return
    if previous.bom_no != doc.bom_no or abs(flt(previous.qty) - flt(doc.qty)) > 1e-9:
        for fieldname in PARTIAL_FIELDS.values():
            doc.set(fieldname, 0 if fieldname == PARTIAL_FIELDS["approved"] else None)


def _get_transferred_production_qty(work_order, exclude_stock_entry=None):
    filters = {
        "work_order": work_order,
        "purpose": "Material Transfer for Manufacture",
        "docstatus": 1,
    }
    if exclude_stock_entry:
        filters["name"] = ("!=", exclude_stock_entry)
    return flt(
        frappe.db.get_value(
            "Stock Entry",
            filters,
            [{"SUM": "fg_completed_qty"}],
        )
    )


def _validate_transfer_quantity(result, transfer_qty, already_transferred=0):
    permitted_qty = flt(result["evaluation_qty"])
    requested_total = flt(already_transferred) + flt(transfer_qty)
    if requested_total > permitted_qty + 1e-9:
        frappe.throw(
            _(
                "Cumulative Material Transfer quantity {0} cannot exceed Production Readiness quantity {1}."
            ).format(round(requested_total, 3), round(permitted_qty, 3))
        )


def validate_material_transfer_readiness(doc, method=None):
    purpose = (doc.get("purpose") or doc.get("stock_entry_type") or "").strip()
    if (
        purpose != "Material Transfer for Manufacture"
        or not doc.get("work_order")
        or doc.get("is_return")
    ):
        return
    result = assert_production_readiness(
        doc.work_order, overrides={"material_warehouse": "source"}
    )
    already_transferred = _get_transferred_production_qty(
        doc.work_order,
        exclude_stock_entry=doc.name if not doc.is_new() else None,
    )
    _validate_transfer_quantity(
        result,
        doc.get("fg_completed_qty"),
        already_transferred,
    )


def _consumed_quantity_by_item(work_order):
    consumed = defaultdict(float)
    for row in work_order.get("required_items") or []:
        consumed[row.item_code] += flt(row.consumed_qty)
    return dict(consumed)


@frappe.whitelist()
def make_stock_entry_with_readiness(
    work_order_id: str,
    purpose: str,
    qty: float | None = None,
    target_warehouse: str | None = None,
    is_additional_transfer_entry: bool = False,
    source_stock_entry: str | None = None,
):
    if purpose == "Material Consumption for Manufacture":
        from calco_erp.calco_production.wip_consumption import (
            is_controlled_work_order,
            make_wip_consumption_stock_entry,
        )

        if is_controlled_work_order(work_order_id):
            return make_wip_consumption_stock_entry(
                work_order=work_order_id,
                basis_qty=qty,
            )

    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

    if purpose == "Material Transfer for Manufacture":
        work_order = frappe.get_doc("Work Order", work_order_id)
        result = assert_production_readiness(
            work_order, overrides={"material_warehouse": "source"}
        )
        already_transferred = _get_transferred_production_qty(work_order_id)
        remaining_qty = max(flt(result["evaluation_qty"]) - already_transferred, 0)
        if qty is None:
            qty = remaining_qty
        _validate_transfer_quantity(result, qty, already_transferred)

    stock_entry = make_stock_entry(
        work_order_id=work_order_id,
        purpose=purpose,
        qty=qty,
        target_warehouse=target_warehouse,
        is_additional_transfer_entry=is_additional_transfer_entry,
        source_stock_entry=source_stock_entry,
    )
    if purpose == "Material Transfer for Manufacture":
        from calco_erp.calco_production.material_reservation_transfer import (
            apply_submitted_reservation_to_stock_entry,
        )

        stock_entry = apply_submitted_reservation_to_stock_entry(stock_entry)
    return stock_entry

class ProductionReadinessJobCardMixin:
    @frappe.whitelist()
    def start_timer(self, **kwargs):
        from calco_erp.calco_production.stopped_execution import lock_execution
        lock_execution(self)
        first_start = self.work_order and not any(
            row.from_time for row in self.get("time_logs") or []
        )
        if first_start:
            from calco_erp.calco_production.operation_master import (
                COMPOUNDING_OPERATION,
                PACKING_OPERATION,
            )

            material_warehouse = (
                "consumed" if getattr(self, "operation", "") == PACKING_OPERATION else "wip"
            )
            if getattr(self, "operation", "") == COMPOUNDING_OPERATION:
                from calco_erp.calco_production.grade_change_control import (
                    assert_grade_change_approved,
                )

                assert_grade_change_approved(self, _("the first Compounding / Extrusion Job Card Start"))
            from calco_erp.calco_production.execution_policy import physical
            readiness_overrides = {"material_warehouse": material_warehouse}
            if physical(self):
                readiness_overrides["physical_execution"] = True
            assert_production_readiness(self.work_order, overrides=readiness_overrides)
            from calco_erp.calco_production.work_order_lifecycle import assert_packing_initial_qc_passed

            assert_packing_initial_qc_passed(self.work_order, getattr(self, "operation", ""))
            if getattr(self, "operation", "") == COMPOUNDING_OPERATION:
                from calco_erp.calco_production.compounding_execution import (
                    validate_compounding_start_extension,
                )

                validate_compounding_start_extension(self)
        from calco_erp.calco_production import in_process_quality as ipqc
        frozen_qc_plan = None
        if self.work_order:
            ipqc.assert_no_hold(self.work_order)
        if first_start and getattr(self, "operation", "") == COMPOUNDING_OPERATION:
            frozen_qc_plan = ipqc.prepare_start(self)
        if frozen_qc_plan:
            self.flags.ipqc_starting = True
        try:
            result = super().start_timer(**kwargs)
        finally:
            if frozen_qc_plan:
                self.flags.ipqc_starting = False
        if first_start and getattr(self, "operation", "") == COMPOUNDING_OPERATION:
            from calco_erp.fg_batch_setup import allocate_production_batch_on_compounding_start

            batch_no = allocate_production_batch_on_compounding_start(
                self,
                start_time=kwargs.get("start_time"),
            )
            if batch_no and self.meta.has_field("custom_fg_batch_no"):
                self.custom_fg_batch_no = batch_no
            ipqc.freeze_after_start(self, frozen_qc_plan)
        return result

    @frappe.whitelist()
    def resume_job(self, **kwargs):
        from calco_erp.calco_production.stopped_execution import lock_execution
        lock_execution(self)
        if self.get("work_order"):
            from calco_erp.calco_production.in_process_quality import assert_no_hold
            assert_no_hold(self.work_order)
        if (
            _is_controlled_compounding_job_card(self)
            and cint(self.docstatus) == 0
            and cint(self.get("is_paused"))
        ):
            _assert_controlled_resume_allowed(self)
        return super().resume_job(**kwargs)

    def validate_inspection(self):
        from calco_erp.calco_production.in_process_quality import is_parallel, assert_no_hold
        if self.get("work_order") and is_parallel(frappe.get_doc("Work Order", self.work_order)):
            # Standard Job Card completion is independent of pending checkpoint results.
            assert_no_hold(self.work_order)
            return
        return super().validate_inspection()

    def update_workstation_status(self):
        if not _is_controlled_compounding_job_card(self):
            return super().update_workstation_status()
        return _sync_controlled_workstation_status(self)


def _is_controlled_compounding_job_card(doc) -> bool:
    from calco_erp.calco_production.compounding_execution import (
        is_controlled_compounding_job_card,
    )

    return is_controlled_compounding_job_card(doc)


def _assert_controlled_resume_allowed(doc):
    state = _derive_controlled_workstation_state(doc)
    if not state["operational_for_resume"]:
        frappe.throw(
            _("Machine {0} is {1} and cannot resume production.").format(
                state["workstation"], state["effective_status"]
            )
        )


def _derive_controlled_workstation_state(doc) -> dict:
    from calco_erp.calco_production.execution_policy import machine_state
    return machine_state(doc)


def _sync_controlled_workstation_status(doc):
    state = _derive_controlled_workstation_state(doc)
    if state["effective_status"] != frappe.db.get_value(
        "Workstation", state["workstation"], "status"
    ):
        frappe.db.set_value(
            "Workstation",
            state["workstation"],
            "status",
            state["effective_status"],
            update_modified=False,
        )
    return state["effective_status"]
