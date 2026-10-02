from frappe.model.document import Document
from calco_erp.calco_production import fg_handover

class FGDeliveryNote(Document):
    def validate(self):fg_handover.validate(self)
    def before_submit(self):fg_handover.before_submit(self)
    def before_cancel(self):fg_handover.before_cancel(self)
    def on_trash(self):fg_handover.on_trash(self)
