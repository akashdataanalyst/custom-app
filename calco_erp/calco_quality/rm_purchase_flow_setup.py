from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.exceptions import DuplicateEntryError


RM_QC_FLAG_FIELD = "custom_enable_rm_qc"
RM_QUARANTINE_WAREHOUSE_FIELD = "custom_rm_quarantine_warehouse"
RM_RELEASED_WAREHOUSE_FIELD = "custom_rm_released_warehouse"
RM_HOLD_WAREHOUSE_FIELD = "custom_rm_hold_warehouse"
RM_REJECTED_WAREHOUSE_FIELD = "custom_rm_rejected_warehouse"


def ensure_rm_purchase_flow_setup():
    ensure_rm_purchase_flow_fields()
    frappe.clear_cache(doctype="Stock Settings")
    frappe.db.set_single_value(
        "Stock Settings",
        "allow_to_make_quality_inspection_after_purchase_or_delivery",
        1,
        update_modified=False,
    )
    sync_rm_item_qc_flags()
    ensure_rm_flow_warehouses()
    frappe.clear_cache()


def ensure_rm_purchase_flow_fields():
    create_custom_fields(
        {
            "Stock Settings": [
                {
                    "fieldname": RM_QUARANTINE_WAREHOUSE_FIELD,
                    "fieldtype": "Link",
                    "label": "RM Quarantine Warehouse",
                    "options": "Warehouse",
                    "insert_after": "allow_to_make_quality_inspection_after_purchase_or_delivery",
                },
                {
                    "fieldname": RM_RELEASED_WAREHOUSE_FIELD,
                    "fieldtype": "Link",
                    "label": "RM Released Warehouse",
                    "options": "Warehouse",
                    "insert_after": RM_QUARANTINE_WAREHOUSE_FIELD,
                },
                {
                    "fieldname": RM_HOLD_WAREHOUSE_FIELD,
                    "fieldtype": "Link",
                    "label": "RM Hold Warehouse",
                    "options": "Warehouse",
                    "insert_after": RM_RELEASED_WAREHOUSE_FIELD,
                },
                {
                    "fieldname": RM_REJECTED_WAREHOUSE_FIELD,
                    "fieldtype": "Link",
                    "label": "RM Rejected Warehouse",
                    "options": "Warehouse",
                    "insert_after": RM_HOLD_WAREHOUSE_FIELD,
                },
            ]
        },
        update=True,
    )


def sync_rm_item_qc_flags():
    item_meta = frappe.get_meta("Item")
    if not item_meta.has_field(RM_QC_FLAG_FIELD):
        return

    rm_items = frappe.get_all(
        "Item",
        filters={"item_group": "Raw Material"},
        fields=["name", "inspection_required_before_purchase", RM_QC_FLAG_FIELD],
        limit_page_length=0,
    )
    for row in rm_items:
        current_custom_flag = int(row.get(RM_QC_FLAG_FIELD) or 0)
        current_native_flag = int(row.get("inspection_required_before_purchase") or 0)

        if current_native_flag and not current_custom_flag:
            frappe.db.set_value("Item", row.name, RM_QC_FLAG_FIELD, 1, update_modified=False)

        if current_custom_flag and not current_native_flag:
            frappe.db.set_value(
                "Item",
                row.name,
                "inspection_required_before_purchase",
                1,
                update_modified=False,
            )


def ensure_rm_flow_warehouses():
    companies = frappe.get_all("Company", fields=["name", "abbr"], limit_page_length=0)
    if not companies:
        return

    for company in companies:
        warehouses = get_or_create_company_rm_warehouses(company.name, company.abbr)
        for fieldname, warehouse in warehouses.items():
            if not get_stock_setting_value(fieldname):
                set_stock_setting_value(fieldname, warehouse)


def get_or_create_company_rm_warehouses(company: str, company_abbr: str) -> dict[str, str]:
    company_suffix = company_abbr or company
    parent_warehouse = (
        frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name")
        or frappe.db.get_value("Warehouse", {"name": ("like", f"% - {company_suffix}")}, "name")
        or frappe.db.get_value("Warehouse", {}, "name")
    )
    released_warehouse = (
        frappe.db.get_value("Warehouse", {"company": company, "name": ("like", f"%Stores% - {company_suffix}")}, "name")
        or frappe.db.get_value("Warehouse", {"company": company, "name": ("like", "%Stores%")}, "name")
        or ensure_leaf_warehouse(f"RM Released - {company_suffix}", company, parent_warehouse)
    )

    return {
        RM_QUARANTINE_WAREHOUSE_FIELD: ensure_leaf_warehouse(
            f"RM Quarantine - {company_suffix}", company, parent_warehouse
        ),
        RM_RELEASED_WAREHOUSE_FIELD: released_warehouse,
        RM_HOLD_WAREHOUSE_FIELD: ensure_leaf_warehouse(f"RM Hold - {company_suffix}", company, parent_warehouse),
        RM_REJECTED_WAREHOUSE_FIELD: ensure_leaf_warehouse(
            f"RM Rejected - {company_suffix}", company, parent_warehouse
        ),
    }


def ensure_leaf_warehouse(warehouse_name: str, company: str, parent_warehouse: str | None) -> str:
    if frappe.db.exists("Warehouse", warehouse_name):
        return warehouse_name

    warehouse = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": warehouse_name.split(" - ")[0],
            "name": warehouse_name,
            "company": company,
            "is_group": 0,
            "parent_warehouse": parent_warehouse,
        }
    )
    try:
        warehouse.insert(ignore_permissions=True)
    except DuplicateEntryError:
        return warehouse_name
    return warehouse.name


def get_stock_setting_value(fieldname: str) -> str:
    result = frappe.db.sql(
        """
        select value
        from tabSingles
        where doctype = 'Stock Settings'
          and field = %s
        limit 1
        """,
        fieldname,
    )
    return result[0][0] if result and result[0] and result[0][0] else ""


def set_stock_setting_value(fieldname: str, value: str) -> None:
    existing = frappe.db.exists("Singles", {"doctype": "Stock Settings", "field": fieldname})
    if existing:
        frappe.db.set_value("Singles", existing, "value", value, update_modified=False)
        return

    frappe.db.sql(
        """
        insert into tabSingles (doctype, field, value)
        values ('Stock Settings', %s, %s)
        """,
        (fieldname, value),
    )
