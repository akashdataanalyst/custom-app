from frappe.model.document import Document
from calco_erp.calco_production.shift_production_output import protect_child, prevent_delete

class ShiftProductionOutputReading(Document):
    def validate(self):
        protect_child(self)

    def on_trash(self):
        prevent_delete(self)
