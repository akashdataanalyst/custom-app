import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


ITEM_GROUP = "Maintenance Spares"
ITEM_CUSTOM_FIELDS = {
    "Item": [
        {
            "fieldname": "custom_maintenance_spare_section",
            "label": "Maintenance Spare",
            "fieldtype": "Section Break",
            "insert_after": "stock_uom",
        },
        {
            "fieldname": "custom_is_maintenance_spare",
            "label": "Is Maintenance Spare",
            "fieldtype": "Check",
            "insert_after": "custom_maintenance_spare_section",
        },
        {
            "fieldname": "custom_maintenance_min_stock",
            "label": "Maintenance Min Stock",
            "fieldtype": "Float",
            "insert_after": "custom_is_maintenance_spare",
        },
        {
            "fieldname": "custom_maintenance_reorder_level",
            "label": "Maintenance Reorder Level",
            "fieldtype": "Float",
            "insert_after": "custom_maintenance_min_stock",
        },
        {
            "fieldname": "custom_maintenance_critical",
            "label": "Critical Spare",
            "fieldtype": "Check",
            "insert_after": "custom_maintenance_reorder_level",
        },
    ]
}


def execute():
    ensure_item_group()
    ensure_item_custom_fields()
    frappe.clear_cache(doctype="Item")


def ensure_item_group():
    if frappe.db.exists("Item Group", ITEM_GROUP):
        return

    frappe.get_doc(
        {
            "doctype": "Item Group",
            "item_group_name": ITEM_GROUP,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }
    ).insert(ignore_permissions=True)


def ensure_item_custom_fields():
    create_custom_fields(ITEM_CUSTOM_FIELDS, update=True)
