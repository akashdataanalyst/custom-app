from __future__ import annotations

import frappe

from calco_erp.calco_production import fg_planning, fg_dashboard


@frappe.whitelist()
def get_dashboard_data(*args, **filters):
    return fg_dashboard.get_dashboard_data(**filters)


@frappe.whitelist()
def search_fg_items(txt="", *args, **kwargs):
    return fg_planning.search_fg_items(txt=txt, *args, **kwargs)


@frappe.whitelist()
def create_draft_work_orders_from_basket(*args, **kwargs):
    return fg_planning.create_draft_work_orders_from_basket(*args, **kwargs)
