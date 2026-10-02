import frappe
from frappe import _
from frappe.model.document import Document

from calco_erp.calco_production.blending_execution import (
    before_insert_blending_run,
    validate_blending_run,
)


class BlendingRun(Document):
    def before_insert(self):
        before_insert_blending_run(self)

    def validate(self):
        validate_blending_run(self)

    def on_trash(self):
        frappe.throw(_("Blending Run evidence cannot be deleted. Use controlled correction where required."))
