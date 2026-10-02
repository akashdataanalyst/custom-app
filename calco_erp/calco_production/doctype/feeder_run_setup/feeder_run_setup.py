from frappe.model.document import Document

from calco_erp.calco_production.feeder_run import (
    SETUP_DOCTYPE,
    SETUP_FIELDS,
    prevent_feeder_setup_delete,
    validate_feeder_child_document,
)


class FeederRunSetup(Document):
    def validate(self):
        validate_feeder_child_document(
            self, SETUP_DOCTYPE, SETUP_FIELDS, "controlled_feeder_setup_write"
        )

    def on_trash(self):
        prevent_feeder_setup_delete(self)
