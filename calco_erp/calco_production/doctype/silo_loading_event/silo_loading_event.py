from frappe.model.document import Document

from calco_erp.calco_production.silo_control import (
    prevent_silo_event_delete,
    validate_silo_event_document,
)


class SiloLoadingEvent(Document):
    def validate(self):
        validate_silo_event_document(self)

    def on_trash(self):
        prevent_silo_event_delete(self)
