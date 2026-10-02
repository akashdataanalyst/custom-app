import frappe
from frappe.model.document import Document
from calco_erp.calco_production.parallel_production import TOKEN
class ProductionBatchChange(Document):
    def validate(self):
        if not self.is_new() or self.flags.get('parallel_batch_token') is not TOKEN:
            frappe.throw('FG batch correction audit is immutable and action-controlled.')
    def on_trash(self): frappe.throw('FG batch correction audit cannot be deleted.')
    def before_rename(self,*args): frappe.throw('FG batch correction audit cannot be renamed.')
