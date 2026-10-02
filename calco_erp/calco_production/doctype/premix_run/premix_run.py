from frappe.model.document import Document

from calco_erp.calco_production.premix_execution import before_insert_premix_run, validate_premix_run


class PremixRun(Document):
    def before_insert(self):
        before_insert_premix_run(self)

    def validate(self):
        validate_premix_run(self)

    def on_trash(self):
        import frappe
        from frappe import _

        frappe.throw(_("Premix Run evidence cannot be deleted. Use controlled correction where required."))
