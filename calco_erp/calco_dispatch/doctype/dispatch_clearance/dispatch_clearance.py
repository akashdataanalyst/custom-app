import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class DispatchClearance(Document):
    def validate(self):
        if self.final_qc_release:
            release = frappe.get_doc("Final QC Release", self.final_qc_release)
            self.item_code = release.item_code
            self.batch_no = release.batch_no
            self.coa_record = self.coa_record or release.coa_record

        if not self.status:
            self.status = "Pending"

    def before_submit(self):
        if self.status != "Cleared":
            frappe.throw("Dispatch Clearance must be submitted with status Cleared.")

        release = frappe.get_doc("Final QC Release", self.final_qc_release)
        if release.docstatus != 1 or release.status != "Released":
            frappe.throw("Dispatch Clearance requires a submitted Released Final QC Release.")

        if not self.coa_record or not frappe.db.exists("COA Record", self.coa_record):
            frappe.throw("COA Record is mandatory before Dispatch Clearance can be submitted.")

        self.cleared_by = self.cleared_by or frappe.session.user
        self.cleared_on = self.cleared_on or now_datetime()

