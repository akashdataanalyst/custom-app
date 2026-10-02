from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from calco_erp.calco_production.work_order_lifecycle import (
    FINAL_QC_PENDING,
    INITIAL_QC_PENDING,
    STAGE_MATERIAL_TRANSFER_PENDING,
)


PLANNING_REVIEW_FIELDS = (
    "custom_planning_review_status",
    "custom_planning_reviewed_qty",
    "custom_planning_reviewed_by",
    "custom_planning_reviewed_on",
    "custom_planning_review_remarks",
)
PLANNING_SOURCE_FIELDS = (
    "custom_planning_source_type",
    "custom_planning_source_doctype",
    "custom_planning_source_name",
    "custom_planning_source_row",
)


def validate_work_order_plan_context(doc, method=None):
    plan_item = _get_managed_plan_item(doc)
    if not plan_item:
        return

    _validate_plan_context(doc, plan_item)
    _apply_plan_context(doc, plan_item)


def initialize_work_order_from_production_plan(doc, method=None):
    """Initialize context skipped by ERPNext Production Plan's ignore_validate insert."""
    plan_item = _get_managed_plan_item(doc)
    if not plan_item:
        return

    _validate_plan_context(doc, plan_item)
    _apply_plan_context(doc, plan_item)
    _set_default_execution_state(doc)

    updates = {}
    for fieldname in (
        *PLANNING_REVIEW_FIELDS,
        "expected_delivery_date",
        "custom_production_stage",
        "custom_initial_qc_status",
        "custom_final_qc_status",
    ):
        if doc.meta.has_field(fieldname):
            updates[fieldname] = doc.get(fieldname)

    if updates:
        frappe.db.set_value(
            "Work Order",
            doc.name,
            updates,
            update_modified=False,
        )


def _get_managed_plan_item(doc):
    production_plan = (doc.get("production_plan") or "").strip()
    production_plan_item = (doc.get("production_plan_item") or "").strip()
    if not production_plan and not production_plan_item:
        return None
    if not production_plan or not production_plan_item:
        frappe.throw(
            _("Work Order must contain both Production Plan and Production Plan Item links.")
        )

    plan_item = frappe.db.get_value(
        "Production Plan Item",
        production_plan_item,
        [
            "name",
            "parent",
            "item_code",
            "bom_no",
            "planned_qty",
            "sales_order",
            "sales_order_item",
            "custom_required_delivery_date",
            *PLANNING_REVIEW_FIELDS,
            *PLANNING_SOURCE_FIELDS,
        ],
        as_dict=True,
    )
    if not plan_item:
        frappe.throw(
            _("Production Plan Item {0} does not exist.").format(production_plan_item)
        )

    if not any(plan_item.get(fieldname) for fieldname in PLANNING_SOURCE_FIELDS):
        return None
    return plan_item


def _validate_plan_context(doc, plan_item):
    if plan_item.parent != doc.production_plan:
        frappe.throw(
            _("Production Plan Item {0} does not belong to Production Plan {1}.").format(
                plan_item.name,
                doc.production_plan,
            )
        )

    if frappe.db.get_value("Production Plan", doc.production_plan, "docstatus") != 1:
        frappe.throw(
            _("Production Plan {0} must be submitted before creating Work Orders.").format(
                doc.production_plan
            )
        )

    if plan_item.item_code != doc.production_item:
        frappe.throw(
            _("Work Order item must match Production Plan Item {0}.").format(plan_item.name)
        )
    if plan_item.bom_no and plan_item.bom_no != doc.bom_no:
        frappe.throw(
            _("Work Order BOM must match Production Plan Item {0}.").format(plan_item.name)
        )
    if (plan_item.sales_order or "") != (doc.get("sales_order") or ""):
        frappe.throw(
            _("Work Order Sales Order lineage does not match Production Plan Item {0}.").format(
                plan_item.name
            )
        )
    if (plan_item.sales_order_item or "") != (doc.get("sales_order_item") or ""):
        frappe.throw(
            _("Work Order Sales Order Item lineage does not match Production Plan Item {0}.").format(
                plan_item.name
            )
        )

    if plan_item.custom_planning_review_status != "Confirmed":
        frappe.throw(
            _("Production Plan Item {0} does not have a Confirmed planning review.").format(
                plan_item.name
            )
        )

    reviewed_qty = flt(plan_item.custom_planning_reviewed_qty)
    if reviewed_qty <= 0 or flt(doc.qty) <= 0 or flt(doc.qty) > reviewed_qty:
        frappe.throw(
            _("Work Order quantity must be greater than zero and cannot exceed reviewed quantity {0}.").format(
                reviewed_qty
            )
        )

    existing_qty = frappe.db.sql(
        """
        select coalesce(sum(qty), 0)
        from `tabWork Order`
        where production_plan_item = %(production_plan_item)s
          and name != %(name)s
          and docstatus < 2
        """,
        {
            "production_plan_item": plan_item.name,
            "name": doc.name or "",
        },
    )[0][0]
    if flt(existing_qty) + flt(doc.qty) > flt(plan_item.planned_qty) + 1e-9:
        frappe.throw(
            _("Open Work Order quantity for Production Plan Item {0} cannot exceed planned quantity {1}.").format(
                plan_item.name,
                plan_item.planned_qty,
            )
        )


def _apply_plan_context(doc, plan_item):
    for fieldname in PLANNING_REVIEW_FIELDS:
        if doc.meta.has_field(fieldname):
            doc.set(fieldname, plan_item.get(fieldname))

    if doc.meta.has_field("expected_delivery_date"):
        doc.expected_delivery_date = plan_item.custom_required_delivery_date


def _set_default_execution_state(doc):
    defaults = {
        "custom_production_stage": STAGE_MATERIAL_TRANSFER_PENDING,
        "custom_initial_qc_status": INITIAL_QC_PENDING,
        "custom_final_qc_status": FINAL_QC_PENDING,
    }
    for fieldname, value in defaults.items():
        if doc.meta.has_field(fieldname) and not doc.get(fieldname):
            doc.set(fieldname, value)
