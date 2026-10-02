import frappe
from frappe.model.document import Document


class SupplierApprovalMatrix(Document):
    def validate(self):
        if self.item_code:
            self.item_code = (self.item_code or "").strip()

        if self.approval_status == "Expired" and not self.expiry_date:
            frappe.throw("Expiry Date is required when Approval Status is Expired.")

