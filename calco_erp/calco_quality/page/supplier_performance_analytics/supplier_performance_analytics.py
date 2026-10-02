from __future__ import annotations

import frappe

from calco_erp.calco_quality import supplier_performance_analytics


@frappe.whitelist()
def get_dashboard_data(*args, **kwargs):
    return supplier_performance_analytics.get_supplier_performance_data()

