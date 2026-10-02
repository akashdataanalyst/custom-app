from __future__ import annotations

from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from calco_erp.calco_purchase.supplier_last_purchase_price import LPP_CLIENT_SCRIPT_NAME


def get_supplier_quotation_lpp_custom_fields():
    return {
        "Supplier Quotation Item": [
            {
                "fieldname": "custom_last_purchase_price",
                "fieldtype": "Currency",
                "label": "Last Purchase Price",
                "options": "currency",
                "insert_after": "rate",
                "precision": "6",
                "read_only": 1,
                "no_copy": 1,
                "in_list_view": 1,
                "columns": 2,
                "description": "Blank means no comparable submitted, non-return Purchase Receipt exists for this Supplier, Item and compatible UOM/currency context.",
            },
            {
                "fieldname": "custom_price_difference",
                "fieldtype": "Currency",
                "label": "Difference",
                "options": "currency",
                "insert_after": "custom_last_purchase_price",
                "precision": "6",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_price_difference_percent",
                "fieldtype": "Percent",
                "label": "Difference %",
                "insert_after": "custom_price_difference",
                "precision": "2",
                "read_only": 1,
                "no_copy": 1,
                "in_list_view": 1,
                "columns": 1,
            },
            {
                "fieldname": "custom_last_purchase_po",
                "fieldtype": "Link",
                "label": "Last Purchase PO",
                "options": "Purchase Order",
                "insert_after": "custom_price_difference_percent",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_last_purchase_date",
                "fieldtype": "Date",
                "label": "Last Purchase Date",
                "insert_after": "custom_last_purchase_po",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_last_purchase_currency",
                "fieldtype": "Link",
                "label": "Last Purchase Currency",
                "options": "Currency",
                "insert_after": "custom_last_purchase_date",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "custom_last_purchase_uom",
                "fieldtype": "Link",
                "label": "Last Purchase UOM",
                "options": "UOM",
                "insert_after": "custom_last_purchase_currency",
                "read_only": 1,
                "no_copy": 1,
            },
        ]
    }


def ensure_supplier_quotation_lpp_setup():
    create_custom_fields(
        get_supplier_quotation_lpp_custom_fields(),
        update=True,
        ignore_validate=True,
    )
    ensure_supplier_quotation_lpp_client_script()
    frappe.clear_cache()


def ensure_supplier_quotation_lpp_client_script():
    if not frappe.db.exists("DocType", "Client Script"):
        return

    script_path = Path(frappe.get_app_path("calco_erp", "public", "js", "supplier_quotation_lpp.js"))
    script = script_path.read_text(encoding="utf-8")
    values = {"dt": "Supplier Quotation", "enabled": 1, "view": "Form", "script": script}
    if frappe.db.exists("Client Script", LPP_CLIENT_SCRIPT_NAME):
        doc = frappe.get_doc("Client Script", LPP_CLIENT_SCRIPT_NAME)
        for fieldname, value in values.items():
            doc.set(fieldname, value)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "Client Script", "name": LPP_CLIENT_SCRIPT_NAME, **values}).insert(
            ignore_permissions=True
        )
