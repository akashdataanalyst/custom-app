from __future__ import annotations

import frappe

from calco_erp.calco_purchase import master_data_governance_dashboard


def get_context(context):
    context.no_cache = 1


@frappe.whitelist()
def get_dashboard_data(*args, **filters):
    return master_data_governance_dashboard.get_dashboard_data(*args, **filters)