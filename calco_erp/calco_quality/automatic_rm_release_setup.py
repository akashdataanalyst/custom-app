from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def get_automatic_rm_release_custom_fields():
    return {
        "Quality Inspection": [
            {
                "fieldname": "custom_accepted_qty",
                "fieldtype": "Float",
                "label": "Accepted Qty",
                "insert_after": "sample_size",
                "precision": "3",
            },
            {
                "fieldname": "custom_accepted_qty_uom",
                "fieldtype": "Data",
                "label": "Accepted Qty UOM",
                "insert_after": "custom_accepted_qty",
                "read_only": 1,
            },
        ],
        "RM Release Note": [
            {
                "fieldname": "custom_purchase_receipt_item",
                "fieldtype": "Data",
                "label": "PR Item Row",
                "insert_after": "custom_purchase_receipt",
                "read_only": 1,
            },
            {
                "fieldname": "custom_release_authority_key",
                "fieldtype": "Data",
                "label": "Release Authority Key",
                "insert_after": "custom_purchase_receipt_item",
                "read_only": 1,
                "unique": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_release_basis",
                "fieldtype": "Select",
                "label": "Release Basis",
                "options": "\nAccepted Quality Inspection\nAccepted Under Deviation",
                "insert_after": "custom_release_authority_key",
                "read_only": 1,
            },
            {
                "fieldname": "custom_generated_stock_entry",
                "fieldtype": "Link",
                "label": "Generated Stock Entry",
                "options": "Stock Entry",
                "insert_after": "custom_release_basis",
                "read_only": 1,
                "allow_on_submit": 1,
            },
            {
                "fieldname": "custom_trigger_user",
                "fieldtype": "Link",
                "label": "Trigger User",
                "options": "User",
                "insert_after": "custom_generated_stock_entry",
                "read_only": 1,
            },
            {
                "fieldname": "custom_trigger_timestamp",
                "fieldtype": "Datetime",
                "label": "Trigger Timestamp",
                "insert_after": "custom_trigger_user",
                "read_only": 1,
            },
        ],
        "Stock Entry": [
            {
                "fieldname": "custom_rm_release_note",
                "fieldtype": "Link",
                "label": "RM Release Note",
                "options": "RM Release Note",
                "insert_after": "work_order",
                "read_only": 1,
                "allow_on_submit": 1,
                "no_copy": 1,
            },
        ],
    }


def ensure_automatic_rm_release_fields():
    create_custom_fields(
        get_automatic_rm_release_custom_fields(),
        update=True,
        ignore_validate=True,
    )
    frappe.clear_cache()
