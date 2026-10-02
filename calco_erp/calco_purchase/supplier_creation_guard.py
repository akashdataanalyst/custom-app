from __future__ import annotations

import frappe
from frappe import _

SUPPLIER_ONBOARDING_MESSAGE = "Proposed suppliers must be created through the Supplier Approval Request workflow."
REQUEST_FORM_ROUTES = ("/app/new-rm-request", "/app/new-supplier-request")


def validate_supplier_not_created_from_request_form(doc, method=None):
    if getattr(frappe.flags, "allow_supplier_creation_from_supplier_request", False):
        return

    referer = frappe.get_request_header("Referer") or ""
    if not referer:
        return

    if any(route in referer for route in REQUEST_FORM_ROUTES):
        frappe.throw(_(SUPPLIER_ONBOARDING_MESSAGE))
