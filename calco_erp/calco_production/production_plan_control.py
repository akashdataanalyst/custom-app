from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


SOURCE_RULES = {
    "Sales Order": {
        "doctype": "Sales Order",
        "row_doctype": "Sales Order Item",
        "row_required": True,
    },
    "Forecast": {
        "doctype": "Sales Forecast",
        "row_doctype": "Sales Forecast Item",
        "row_required": True,
    },
    "Balance Return": {
        "doctype": "Work Order",
        "row_doctype": "",
        "row_required": False,
    },
}

MANAGED_FIELDS = (
    "custom_planning_source_type",
    "custom_planning_source_doctype",
    "custom_planning_source_name",
    "custom_planning_source_row",
    "custom_required_delivery_date",
    "custom_planning_review_status",
    "custom_planning_reviewed_qty",
    "custom_planning_reviewed_by",
    "custom_planning_reviewed_on",
)


def validate_production_plan(doc, method=None) -> None:
    """Protect Planning Center lineage without changing standard Production Plans."""
    if doc.get("custom_calco_release_to_production"):
        _validate_internal_release_plan(doc)

    seen_sources: dict[str, int] = {}
    for row in doc.get("po_items") or []:
        if not _is_planning_center_row(row):
            continue

        source_key = _validate_managed_row(row)
        if source_key in seen_sources:
            frappe.throw(
                _("Production Plan rows {0} and {1} contain duplicate planning source {2}.").format(
                    seen_sources[source_key],
                    row.idx,
                    source_key,
                )
            )
        seen_sources[source_key] = row.idx


def _validate_internal_release_plan(doc) -> None:
    required_fields = (
        "custom_release_key",
        "custom_release_requirement_key",
        "custom_release_authority",
        "custom_release_item_code",
        "custom_release_qty",
        "custom_release_period_start",
        "custom_release_period_end",
        "custom_release_source_details",
        "custom_released_by",
        "custom_released_on",
    )
    missing = [fieldname for fieldname in required_fields if not doc.get(fieldname)]
    if missing:
        frappe.throw(
            _("Internal Production Release is missing controlled metadata: {0}.").format(
                ", ".join(missing)
            )
        )

    rows = doc.get("po_items") or []
    if not rows and doc.docstatus == 0:
        return
    if len(rows) != 1:
        frappe.throw(_("Internal Production Release must contain exactly one FG row before submission."))

    row = rows[0]
    if row.get("item_code") != doc.get("custom_release_item_code"):
        frappe.throw(_("Internal Production Release FG must match the released planning requirement."))
    if abs(flt(row.get("planned_qty")) - flt(doc.get("custom_release_qty"))) > 1e-9:
        frappe.throw(_("Internal Production Plan quantity must equal the Customer Service released quantity."))
    if not _is_planning_center_row(row):
        frappe.throw(_("Internal Production Release row is missing Planning Center lineage."))


def _is_planning_center_row(row) -> bool:
    return any(row.get(fieldname) not in (None, "", 0) for fieldname in MANAGED_FIELDS)


def _validate_managed_row(row) -> str:
    source_type = (row.get("custom_planning_source_type") or "").strip()
    rule = SOURCE_RULES.get(source_type)
    if not rule:
        frappe.throw(
            _("Production Plan row {0} has an invalid Planning Source Type.").format(row.idx)
        )

    source_doctype = (row.get("custom_planning_source_doctype") or "").strip()
    source_name = (row.get("custom_planning_source_name") or "").strip()
    source_row = (row.get("custom_planning_source_row") or "").strip()
    if source_doctype != rule["doctype"]:
        frappe.throw(
            _("Production Plan row {0} must use source DocType {1}.").format(
                row.idx,
                rule["doctype"],
            )
        )
    if not source_name:
        frappe.throw(_("Production Plan row {0} is missing its planning source.").format(row.idx))
    if rule["row_required"] and not source_row:
        frappe.throw(
            _("Production Plan row {0} is missing its planning source row.").format(row.idx)
        )
    if not row.get("custom_required_delivery_date"):
        frappe.throw(
            _("Production Plan row {0} is missing Required Delivery Date.").format(row.idx)
        )

    _validate_review(row)
    _validate_source_record(row, rule, source_name, source_row)
    _validate_standard_sales_order_lineage(row, source_type, source_name, source_row)
    return f"{source_type}:{source_row or source_name}"


def _validate_review(row) -> None:
    if row.get("custom_planning_review_status") != "Confirmed":
        frappe.throw(
            _("Production Plan row {0} requires a Confirmed Production Planning Review.").format(
                row.idx
            )
        )

    reviewed_qty = flt(row.get("custom_planning_reviewed_qty"))
    planned_qty = flt(row.get("planned_qty"))
    if reviewed_qty <= 0:
        frappe.throw(
            _("Production Plan row {0} has no approved planning quantity.").format(row.idx)
        )
    if planned_qty <= 0 or planned_qty - reviewed_qty > 1e-9:
        frappe.throw(
            _("Planned quantity for row {0} must be greater than zero and cannot exceed {1}.").format(
                row.idx,
                reviewed_qty,
            )
        )
    if not row.get("custom_planning_reviewed_by") or not row.get(
        "custom_planning_reviewed_on"
    ):
        frappe.throw(
            _("Production Plan row {0} is missing planning review audit details.").format(row.idx)
        )


def _validate_source_record(row, rule, source_name: str, source_row: str) -> None:
    source_docstatus = frappe.db.get_value(rule["doctype"], source_name, "docstatus")
    if source_docstatus != 1:
        frappe.throw(
            _("Planning source {0} {1} must exist and be submitted.").format(
                rule["doctype"],
                source_name,
            )
        )

    row_doctype = rule["row_doctype"]
    if not row_doctype:
        return

    source_values = frappe.db.get_value(
        row_doctype,
        source_row,
        ["parent", "item_code"],
        as_dict=True,
    )
    if (
        not source_values
        or source_values.parent != source_name
        or source_values.item_code != row.get("item_code")
    ):
        frappe.throw(
            _("Production Plan row {0} does not match its planning source row.").format(row.idx)
        )


def _validate_standard_sales_order_lineage(
    row,
    source_type: str,
    source_name: str,
    source_row: str,
) -> None:
    if source_type == "Sales Order":
        if row.get("sales_order") != source_name or row.get("sales_order_item") != source_row:
            frappe.throw(
                _("Production Plan row {0} has inconsistent Sales Order lineage.").format(
                    row.idx
                )
            )
        return

    if row.get("sales_order") or row.get("sales_order_item"):
        frappe.throw(
            _("Production Plan row {0} must not carry Sales Order links for source type {1}.").format(
                row.idx,
                source_type,
            )
        )
