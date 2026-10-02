from frappe.model.document import Document

from calco_erp.calco_production.feeder_run import READING_DOCTYPE, READING_FIELDS, prevent_feeder_child_delete, validate_feeder_child_document


class FeederReading(Document):
    def validate(self):
        validate_feeder_child_document(
            self, READING_DOCTYPE, READING_FIELDS, "controlled_feeder_reading_write"
        )

    def on_trash(self):
        prevent_feeder_child_delete(self)
