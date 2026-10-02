from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, nowdate

from calco_erp.calco_production import planning_release, production_planning
from calco_erp.calco_production.manufacture_entry import get_fg_quarantine_warehouse
from calco_erp.inventory.availability import get_production_warehouses


CONTROLLED_OPERATION = "Compounding / Extrusion"


@frappe.whitelist()
def get_work_order_creation_context(
    production_requirement="",
    production_line="",
    **_rpc_kwargs,
):
    planning_release.ensure_production_access()
    frappe.has_permission("Production Plan", ptype="read", throw=True)
    frappe.has_permission("Work Order", ptype="create", throw=True)
    authority = planning_release.get_release_authority(production_requirement)
    return _build_context(authority, production_line)


@frappe.whitelist()
def create_work_order_from_planning_center(
    production_requirement="",
    qty=0,
    production_line="",
    bom_no="",
    planned_start_date="",
    **_rpc_kwargs,
):
    planning_release.ensure_production_access()
    frappe.has_permission("Production Plan", ptype="create", throw=True)
    frappe.has_permission("Production Plan", ptype="submit", throw=True)
    frappe.has_permission("Work Order", ptype="create", throw=True)
    frappe.db.sql(
        "select name from `tabProduction Requirement` where name=%s for update",
        (production_requirement,),
    )
    authority = planning_release.get_release_authority(production_requirement)
    context = _build_context(authority, production_line)
    if context.get("existing_work_order"):
        return _existing_work_order_result(context["existing_work_order"])

    qty = flt(qty)
    if qty <= 0 or qty - flt(context["remaining_qty"]) > 1e-9:
        frappe.throw(
            _("Quantity to manufacture must be greater than zero and cannot exceed {0}.").format(
                context["remaining_qty"]
            )
        )
    candidate = next(
        (
            row for row in context["bom_options"]
            if row["production_line"] == production_line and row["bom_no"] == bom_no
        ),
        None,
    )
    if not candidate:
        frappe.throw(_("No approved operation-enabled BOM is available for this FG and production line."))
    if not planned_start_date:
        frappe.throw(_("Planned Start Date is required."))

    warehouses = _get_warehouses(authority.custom_release_company)
    plan = _get_internal_plan(authority)
    if not plan:
        plan, plan_item = _create_and_submit_plan(
            authority,
            candidate["bom_no"],
            planned_start_date,
            warehouses["fg_warehouse"],
        )
    else:
        plan_item = _get_release_plan_item(plan)

    frappe.db.sql(
        "select name from `tabProduction Plan Item` where name=%s for update",
        (plan_item.name,),
    )
    existing_drafts = frappe.get_all(
        "Work Order",
        filters={"production_plan_item": plan_item.name, "docstatus": 0},
        pluck="name",
        order_by="creation asc",
    )
    if existing_drafts:
        return _existing_work_order_result(existing_drafts[0])
    remaining_qty = _get_remaining_qty(plan_item.name)
    if qty - remaining_qty > 1e-9:
        frappe.throw(_("Quantity to manufacture cannot exceed the current remaining quantity {0}.").format(remaining_qty))

    factory_item = _get_factory_item(plan, plan_item.name)
    factory_item.update({
        "qty": qty,
        "bom_no": candidate["bom_no"],
        "source_warehouse": warehouses["source_warehouse"],
        "wip_warehouse": warehouses["wip_warehouse"],
        "fg_warehouse": warehouses["fg_warehouse"],
        "warehouse": warehouses["fg_warehouse"],
        "planned_start_date": get_datetime(planned_start_date),
    })
    work_order_name = plan.create_work_order(factory_item)
    if not work_order_name:
        frappe.throw(_("ERPNext did not create a Work Order for the selected production release."))

    work_order = frappe.get_doc("Work Order", work_order_name)
    work_order.check_permission("write")
    work_order.source_warehouse = warehouses["source_warehouse"]
    work_order.wip_warehouse = warehouses["wip_warehouse"]
    work_order.fg_warehouse = warehouses["fg_warehouse"]
    work_order.planned_start_date = get_datetime(planned_start_date)
    work_order.custom_production_line = production_line
    work_order.custom_machine = production_line
    for required_item in work_order.get("required_items") or []:
        required_item.source_warehouse = warehouses["source_warehouse"]
    work_order.save()
    return {
        "work_order": work_order.name,
        "production_requirement": authority.name,
        "production_plan": plan.name,
        "production_plan_item": plan_item.name,
        "qty": flt(work_order.qty),
        "fg": work_order.production_item,
        "production_line": production_line,
        "route": ["Form", "Work Order", work_order.name],
        "message": _("Draft Work Order {0} created for {1} Kg of {2} on {3}.").format(
            work_order.name, flt(work_order.qty), work_order.production_item, production_line
        ),
    }


def _build_context(authority, production_line=""):
    release_item = planning_release.get_release_item(authority)
    item_code = release_item.item_code
    candidates = _filter_enabled_boms(_get_controlled_boms(item_code))
    if authority.get('custom_rm_planning_snapshot'):
        import json
        frozen=json.loads(authority.custom_rm_planning_snapshot)
        candidates=[r for r in candidates if r['bom_no']==frozen['bom']]
        if not candidates:
            frappe.throw(_("Default BOM requires manufacturing-master review for this production route."))
    if production_line:
        candidates = [row for row in candidates if row["production_line"] == production_line]
    if not candidates:
        frappe.throw(_("No approved operation-enabled BOM is available for this FG and production line."))

    plan = _get_internal_plan(authority)
    plan_item = _get_release_plan_item(plan, required=False) if plan else None
    if plan_item:
        candidates = [row for row in candidates if row["bom_no"] == plan_item.bom_no]
        if not candidates:
            frappe.throw(_("The submitted Production Plan BOM is not compatible with the selected production line."))
    existing = _get_single_draft_work_order(plan_item.name) if plan_item else ""
    released_qty = flt(release_item.requested_qty)
    remaining = _get_remaining_qty(plan_item.name) if plan_item else released_qty
    if remaining <= 0 and not existing:
        frappe.throw(_("No released production quantity remains for Work Order creation."))
    converted = max(released_qty - remaining, 0)
    return {
        "production_requirement": authority.name,
        "production_plan": plan.name if plan else "",
        "production_plan_item": plan_item.name if plan_item else "",
        "fg": item_code,
        "fg_name": frappe.db.get_value("Item", item_code, "item_name") or "",
        "released_qty": round(released_qty, 3),
        "already_converted_qty": round(converted, 3),
        "remaining_qty": round(remaining, 3),
        "required_date": str(release_item.target_date or ""),
        "priority": authority.custom_release_priority or "Normal",
        "source": planning_release._source_summary(authority.custom_release_source_details),
        "existing_work_order": existing,
        "line_options": sorted({row["production_line"] for row in candidates}),
        "bom_options": candidates,
    }


def _get_controlled_boms(item_code):
    rows = frappe.db.sql(
        """
        select distinct bom.name as bom_no, operation.workstation as production_line
        from `tabBOM` bom
        inner join `tabBOM Operation` operation on operation.parent = bom.name
        where bom.docstatus = 1
          and bom.is_active = 1
          and bom.with_operations = 1
          and bom.item = %(item_code)s
          and operation.operation = %(operation)s
          and coalesce(operation.workstation, '') != ''
        order by operation.workstation asc, bom.modified desc, bom.name asc
        """,
        {"item_code": item_code, "operation": CONTROLLED_OPERATION},
        as_dict=True,
    )
    return [
        {
            "bom_no": row.bom_no,
            "production_line": row.production_line,
            "operation": CONTROLLED_OPERATION,
        }
        for row in rows
    ]


def _filter_enabled_boms(candidates):
    if not candidates:
        return []
    bom_names = sorted({row["bom_no"] for row in candidates})
    rows = frappe.db.sql(
        """
        select bi.parent as bom_no, bi.item_code, item.disabled
        from `tabBOM Item` bi
        left join `tabItem` item on item.name = bi.item_code
        where bi.parent in %(bom_names)s
        order by bi.parent, bi.idx
        """,
        {"bom_names": tuple(bom_names)},
        as_dict=True,
    )
    components = defaultdict(list)
    for row in rows:
        components[row.bom_no].append(row)
    return [
        candidate for candidate in candidates
        if components.get(candidate["bom_no"])
        and not any(row.disabled is None or cint(row.disabled) for row in components[candidate["bom_no"]])
    ]


def _get_warehouses(company):
    production = get_production_warehouses(company)
    if not production.get("eligible"):
        frappe.throw(" ".join(production.get("exclusion_reasons") or []))
    return {
        "source_warehouse": production["source_warehouse"],
        "wip_warehouse": production["wip_warehouse"],
        "fg_warehouse": get_fg_quarantine_warehouse(company),
    }


def _get_internal_plan(authority):
    names = frappe.get_all(
        "Production Plan",
        filters={"custom_release_authority": authority.name, "docstatus": ("<", 2)},
        pluck="name",
        order_by="creation asc",
    )
    if len(names) > 1:
        frappe.throw(_("Multiple active internal Production Plans exist for the same release."))
    return planning_release.get_release_plan(names[0]) if names else None


def _create_and_submit_plan(authority, bom_no, planned_start_date, fg_warehouse):
    release_item = planning_release.get_release_item(authority)
    source = planning_release.get_primary_source(authority)
    plan = frappe.new_doc("Production Plan")
    plan.company = authority.custom_release_company
    plan.posting_date = nowdate()
    plan.from_date = authority.week_start_date
    plan.to_date = authority.week_end_date
    plan.get_items_from = ""
    plan.combine_items = 0
    plan.custom_calco_release_to_production = 1
    plan.custom_release_key = (
        f"{authority.custom_release_key}:PP:{frappe.generate_hash(length=8)}"
    )
    plan.custom_release_requirement_key = authority.custom_release_requirement_key
    plan.custom_release_authority = authority.name
    plan.custom_release_item_code = release_item.item_code
    plan.custom_release_qty = release_item.requested_qty
    plan.custom_release_priority = authority.custom_release_priority
    plan.custom_release_required_date = release_item.target_date
    plan.custom_release_period_start = authority.week_start_date
    plan.custom_release_period_end = authority.week_end_date
    plan.custom_release_source_details = authority.custom_release_source_details
    plan.custom_released_by = authority.custom_released_by
    plan.custom_released_on = authority.custom_released_on
    plan.custom_release_reviewed_by = authority.custom_release_reviewed_by
    plan.custom_release_reviewed_on = authority.custom_release_reviewed_on
    plan.custom_release_review_remarks = authority.custom_release_review_remarks
    row = {
        "item_code": release_item.item_code,
        "item_name": release_item.item_name or frappe.db.get_value(
            "Item", release_item.item_code, "item_name"
        ) or release_item.item_code,
        "stock_uom": frappe.db.get_value("Item", release_item.item_code, "stock_uom") or "Kg",
        "bom_no": bom_no,
        "required_date": release_item.target_date,
        "source_type": source["source_type"],
        "source_doctype": source["source_doctype"],
        "source_name": source["source_name"],
        "source_row": source["source_row"],
        "review_status": "Confirmed",
        "reviewed_by": authority.custom_release_reviewed_by,
        "reviewed_on": authority.custom_release_reviewed_on,
        "review_remarks": authority.custom_release_review_remarks,
    }
    plan_item = plan.append("po_items", {})
    production_planning._set_plan_item(plan_item, row, flt(release_item.requested_qty))
    plan_item.planned_start_date = get_datetime(planned_start_date)
    plan_item.warehouse = fg_warehouse
    if source["source_type"] == "Sales Order":
        plan.append("sales_orders", {"sales_order": source["source_name"]})
    plan.insert()
    plan.submit()
    return plan, _get_release_plan_item(plan)


def _get_release_plan_item(plan, required=True):
    items = [row for row in plan.get("po_items") or [] if row.item_code == plan.custom_release_item_code]
    if len(items) > 1:
        frappe.throw(_("Internal Production Plan contains multiple released FG rows."))
    if not items and required:
        frappe.throw(_("Submitted internal Production Plan has no released FG row."))
    return items[0] if items else None


def _get_factory_item(plan, plan_item_name):
    item = next(
        (
            value for value in plan.get_production_items().values()
            if value.get("production_plan_item") == plan_item_name
        ),
        None,
    )
    if not item:
        frappe.throw(_("ERPNext could not resolve the selected Production Plan Item."))
    return frappe._dict(item)


def _get_remaining_qty(plan_item_name):
    values = frappe.db.get_value(
        "Production Plan Item",
        plan_item_name,
        ["planned_qty", "ordered_qty"],
        as_dict=True,
    )
    draft_qty = frappe.db.sql(
        """
        select coalesce(sum(qty), 0)
        from `tabWork Order`
        where production_plan_item = %(plan_item)s and docstatus = 0
        """,
        {"plan_item": plan_item_name},
    )[0][0]
    return max(flt(values.planned_qty) - flt(values.ordered_qty) - flt(draft_qty), 0)


def _get_single_draft_work_order(plan_item_name):
    names = frappe.get_all(
        "Work Order",
        filters={"production_plan_item": plan_item_name, "docstatus": 0},
        pluck="name",
        order_by="creation asc",
    )
    if len(names) > 1:
        frappe.throw(_("Multiple active Draft Work Orders exist for the same Production Plan Item."))
    return names[0] if names else ""


def _existing_work_order_result(work_order_name):
    work_order = frappe.get_doc("Work Order", work_order_name)
    work_order.check_permission("read")
    return {
        "work_order": work_order.name,
        "production_plan": work_order.production_plan,
        "production_plan_item": work_order.production_plan_item,
        "qty": flt(work_order.qty),
        "fg": work_order.production_item,
        "production_line": work_order.get("custom_production_line") or work_order.get("custom_machine") or "",
        "route": ["Form", "Work Order", work_order.name],
        "existing": 1,
        "message": _("Existing Draft Work Order {0} opened.").format(work_order.name),
    }
