from __future__ import annotations

import frappe
from frappe import _

from calco_erp.calco_production.production_execution import (
    PRODUCTION_JOB_CARD_DOCTYPE,
    PRODUCTION_REQUIREMENT_DOCTYPE,
    WORK_ORDER_DOCTYPE,
    get_production_execution_payload,
)


@frappe.whitelist()
def get_tracker(doctype: str, docname: str) -> dict[str, object]:
    if doctype not in {PRODUCTION_REQUIREMENT_DOCTYPE, PRODUCTION_JOB_CARD_DOCTYPE, WORK_ORDER_DOCTYPE}:
        frappe.throw(_("Production tracker is not supported for %(doctype)s.") % {"doctype": doctype})
    return get_production_execution_payload(doctype, docname)
