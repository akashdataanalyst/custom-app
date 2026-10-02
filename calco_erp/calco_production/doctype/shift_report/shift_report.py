from frappe.model.document import Document

from calco_erp.calco_production.shift_reporting import (
    before_insert_shift_report,
    prevent_shift_report_delete,
    validate_shift_report,
)


class ShiftReport(Document):
    def before_insert(self):
        before_insert_shift_report(self)

    def validate(self):
        validate_shift_report(self)

    def on_trash(self):
        prevent_shift_report_delete(self)
