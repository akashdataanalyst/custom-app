import frappe
from frappe.model.document import Document
from calco_erp.calco_quality.quality_master_versions import validate_version

class ManufacturingQualityMasterVersion(Document):
    def validate(self):
        validate_version(self)

    def on_trash(self):
        frappe.throw("Manufacturing master version history cannot be deleted.")
