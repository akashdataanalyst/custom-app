from frappe.model.document import Document

from calco_erp.calco_production.shift_reporting import (
    FEEDER_FIELDS,
    FEEDER_SETTING_DOCTYPE,
    prevent_shift_child_delete,
    validate_shift_child_document,
)


class ShiftFeederSetting(Document):
    def validate(self):
        validate_shift_child_document(self, FEEDER_SETTING_DOCTYPE, FEEDER_FIELDS, "controlled_shift_feeder_write")

    def on_trash(self):
        prevent_shift_child_delete(self)
