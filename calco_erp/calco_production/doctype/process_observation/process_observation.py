from frappe.model.document import Document

from calco_erp.calco_production.process_parameter_monitoring import (
    before_insert_process_observation,
    prevent_process_observation_delete,
    validate_process_observation,
)


class ProcessObservation(Document):
    def before_insert(self):
        before_insert_process_observation(self)

    def validate(self):
        validate_process_observation(self)

    def on_trash(self):
        prevent_process_observation_delete(self)
