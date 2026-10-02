import frappe
from frappe.model.document import Document

class TaskTimelinessAppraisalEvidence(Document):
    def validate(self):
        frappe.throw("Task Timeliness evidence can only be maintained through its Appraisal calculation.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Task Timeliness evidence cannot be deleted independently.", frappe.PermissionError)

    def before_update_after_submit(self):
        self.validate()

    def before_submit(self):
        self.validate()

    def before_cancel(self):
        self.validate()
