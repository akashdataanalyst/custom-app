import frappe
from frappe.model.document import Document
from calco_erp.calco_production import partial_fg_lots as lots

class PartialFGLot(Document):
    def validate(self):
        if not self.is_new():
            old=frappe.get_doc(self.doctype,self.name)
            for field in lots.PROTECTED:
                if frappe.as_json(self.get(field))!=frappe.as_json(old.get(field)):
                    frappe.throw("Partial FG lot confirmation is immutable; cancel and reconfirm if eligible.")
        elif not getattr(frappe.flags,'creating_partial_fg_lot',False):
            frappe.throw("Use Confirm Partial FG Lot from the Compounding Job Card.")
    def before_submit(self):
        lots.validate_confirmation(self)
    def before_cancel(self):
        lots.lock(self.work_order)
        if frappe.db.exists('Stock Entry',{'custom_partial_fg_lot':self.name,'docstatus':1}):
            frappe.throw('Reverse the submitted lot Manufacture and its downstream documents first.')
        if frappe.db.exists('Final QC Release',{'batch_no':self.lot_batch,'docstatus':1}):
            frappe.throw('Reverse the lot release before cancellation.')
    def on_trash(self):
        frappe.throw('Partial lot evidence cannot be deleted; use controlled cancellation.')
