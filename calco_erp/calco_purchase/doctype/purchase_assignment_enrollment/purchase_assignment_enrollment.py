"""Append-only authority to manage a specific pre-activation Purchase document."""
import frappe
from frappe.model.document import Document
from calco_erp.planning_upgrade import purchase_assignments as service


class PurchaseAssignmentEnrollment(Document):
    def autoname(self):
        self.name = service.enrollment_name(self.reference_doctype, self.reference_name)

    def before_insert(self):
        service.validate_enrollment(self)

    def validate(self):
        if self.name != service.enrollment_name(self.reference_doctype, self.reference_name):
            frappe.throw("Enrollment identity must match the exact source document")
        if not self.is_new():
            frappe.throw("Purchase assignment enrollment is immutable")

    def on_trash(self):
        frappe.throw("Purchase assignment enrollment audit evidence cannot be deleted")
