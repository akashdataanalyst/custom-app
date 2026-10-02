from frappe.model.document import Document

from calco_erp.calco_production.feeder_run import ONLINE_CHANGE_DOCTYPE, ONLINE_FIELDS, prevent_feeder_child_delete, validate_feeder_child_document


class FeederOnlineChange(Document):
    def validate(self):
        validate_feeder_child_document(
            self, ONLINE_CHANGE_DOCTYPE, ONLINE_FIELDS, "controlled_feeder_online_write"
        )

    def on_trash(self):
        prevent_feeder_child_delete(self)
