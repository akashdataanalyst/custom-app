from __future__ import annotations

import frappe
from frappe.utils import cstr, flt

from calco_erp.calco_purchase.master_data_governance_dashboard import (
    get_operational_readiness_by_item,
)
from calco_erp.inventory.availability import (
    PRODUCTION_SOURCE_WAREHOUSE,
    get_eligible_batches,
)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_production_rm_batches(doctype, txt, searchfield, start, page_len, filters):
    filters = frappe.parse_json(filters) or {}
    item_code = cstr(filters.get("item_code")).strip()
    warehouse = cstr(filters.get("warehouse")).strip() or PRODUCTION_SOURCE_WAREHOUSE
    work_order = cstr(filters.get("work_order")).strip() or None
    posting_date = filters.get("posting_date") or frappe.utils.today()

    if not item_code or warehouse != PRODUCTION_SOURCE_WAREHOUSE:
        return []

    readiness = get_operational_readiness_by_item([item_code]).get(item_code) or {}
    if not readiness.get("ready"):
        return []

    search_text = cstr(txt).strip().casefold()
    rows = [
        (row["batch_no"], flt(row["available_to_current_work_order"]))
        for row in get_eligible_batches(
            item_code=item_code,
            warehouse=warehouse,
            work_order=work_order,
            posting_datetime=posting_date,
        )
        if flt(row.get("available_to_current_work_order")) > 0
        and (not search_text or search_text in cstr(row.get("batch_no")).casefold())
    ]
    start = max(int(start or 0), 0)
    page_len = max(int(page_len or 0), 0)
    return rows[start : start + page_len] if page_len else []
