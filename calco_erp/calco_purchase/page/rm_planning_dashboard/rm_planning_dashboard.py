from __future__ import annotations

import frappe

from calco_erp.calco_purchase import rm_planning


@frappe.whitelist()
def get_dashboard_data(*args, **filters):
    return rm_planning.get_dashboard_data(*args, **filters)


@frappe.whitelist()
def create_material_request(*args, **kwargs):
    return rm_planning.create_material_request(*args, **kwargs)


@frappe.whitelist()
def create_bulk_material_requests(*args, **kwargs):
    return rm_planning.create_bulk_material_requests(*args, **kwargs)


@frappe.whitelist()
def search_rm_items(txt="", *args, **kwargs):
    return rm_planning.search_rm_items(txt=txt, *args, **kwargs)
