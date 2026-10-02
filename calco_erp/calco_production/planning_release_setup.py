from __future__ import annotations

import frappe


PRODUCTION_PLAN_FIELDS = [
    {"fieldname": "custom_calco_release_to_production", "label": "Calco Release to Production", "fieldtype": "Check", "insert_after": "status", "default": 0, "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_key", "label": "Production Release Key", "fieldtype": "Data", "insert_after": "custom_calco_release_to_production", "read_only": 1, "hidden": 1, "unique": 1, "no_copy": 1},
    {"fieldname": "custom_release_requirement_key", "label": "Planning Requirement Key", "fieldtype": "Data", "insert_after": "custom_release_key", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_authority", "label": "Released Production Requirement", "fieldtype": "Link", "options": "Production Requirement", "insert_after": "custom_release_requirement_key", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_item_code", "label": "Released FG", "fieldtype": "Link", "options": "Item", "insert_after": "custom_release_requirement_key", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_qty", "label": "Released Quantity", "fieldtype": "Float", "insert_after": "custom_release_item_code", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_priority", "label": "Production Priority", "fieldtype": "Select", "options": "High\nNormal\nLow", "insert_after": "custom_release_qty", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_required_date", "label": "Released Required Date", "fieldtype": "Date", "insert_after": "custom_release_priority", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_period_start", "label": "Planning Period Start", "fieldtype": "Date", "insert_after": "custom_release_required_date", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_period_end", "label": "Planning Period End", "fieldtype": "Date", "insert_after": "custom_release_period_start", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_source_details", "label": "Planning Source Details", "fieldtype": "Long Text", "insert_after": "custom_release_period_end", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_released_by", "label": "Released By", "fieldtype": "Link", "options": "User", "insert_after": "custom_release_source_details", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_released_on", "label": "Released On", "fieldtype": "Datetime", "insert_after": "custom_released_by", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_reviewed_by", "label": "Release Reviewed By", "fieldtype": "Link", "options": "User", "insert_after": "custom_released_on", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_reviewed_on", "label": "Release Reviewed On", "fieldtype": "Datetime", "insert_after": "custom_release_reviewed_by", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_review_remarks", "label": "Release Review Remarks", "fieldtype": "Small Text", "insert_after": "custom_release_reviewed_on", "read_only": 1, "hidden": 1, "no_copy": 1},
]

PRODUCTION_REQUIREMENT_FIELDS = [
    {"fieldname": "custom_calco_release_to_production", "label": "Calco Release to Production", "fieldtype": "Check", "insert_after": "status", "default": 0, "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_key", "label": "Production Release Key", "fieldtype": "Data", "insert_after": "custom_calco_release_to_production", "read_only": 1, "hidden": 1, "unique": 1, "no_copy": 1},
    {"fieldname": "custom_release_requirement_key", "label": "Planning Requirement Key", "fieldtype": "Data", "insert_after": "custom_release_key", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_company", "label": "Release Company", "fieldtype": "Link", "options": "Company", "insert_after": "custom_release_requirement_key", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_priority", "label": "Production Priority", "fieldtype": "Select", "options": "High\nNormal\nLow", "insert_after": "custom_release_company", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_source_details", "label": "Planning Source Details", "fieldtype": "Long Text", "insert_after": "custom_release_priority", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_released_by", "label": "Released By", "fieldtype": "Link", "options": "User", "insert_after": "custom_release_source_details", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_released_on", "label": "Released On", "fieldtype": "Datetime", "insert_after": "custom_released_by", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_reviewed_by", "label": "Release Reviewed By", "fieldtype": "Link", "options": "User", "insert_after": "custom_released_on", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_reviewed_on", "label": "Release Reviewed On", "fieldtype": "Datetime", "insert_after": "custom_release_reviewed_by", "read_only": 1, "hidden": 1, "no_copy": 1},
    {"fieldname": "custom_release_review_remarks", "label": "Release Review Remarks", "fieldtype": "Small Text", "insert_after": "custom_release_reviewed_on", "read_only": 1, "hidden": 1, "no_copy": 1},
]


def ensure_planning_release_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    if not frappe.db.exists("DocType", "Production Plan") or not frappe.db.exists("DocType", "Production Requirement"):
        return
    create_custom_fields({
        "Production Plan": PRODUCTION_PLAN_FIELDS,
        "Production Requirement": PRODUCTION_REQUIREMENT_FIELDS,
    }, update=True)
    frappe.clear_cache(doctype="Production Plan")
    frappe.clear_cache(doctype="Production Requirement")
