import frappe
from frappe.model.document import Document


class COARecord(Document):
    def validate(self):
        if not self.status:
            self.status = "Issued"

        required = ["final_qc_release", "item_code", "batch_no"]
        missing = [field for field in required if not self.get(field)]
        if missing:
            frappe.throw("Final QC Release, Item Code, and Batch No are mandatory for COA Record.")

