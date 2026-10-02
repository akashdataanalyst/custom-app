from frappe.model.document import Document

from calco_erp.calco_production.shift_reporting import (
    DOWNTIME_EVENT_DOCTYPE,
    DOWNTIME_FIELDS,
    prevent_shift_child_delete,
    validate_shift_child_document,
)


class ShiftDowntimeEvent(Document):
    def validate(self):
        validate_shift_child_document(self, DOWNTIME_EVENT_DOCTYPE, DOWNTIME_FIELDS, "controlled_shift_downtime_write")

    def on_trash(self):
        prevent_shift_child_delete(self)
