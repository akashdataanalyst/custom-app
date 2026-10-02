from frappe.model.document import Document

from calco_erp.calco_production.process_parameter_monitoring import (
    before_insert_process_parameter_monitor,
    prevent_process_parameter_monitor_delete,
    validate_process_parameter_monitor,
)


class ProcessParameterMonitor(Document):
    def before_insert(self):
        before_insert_process_parameter_monitor(self)

    def validate(self):
        validate_process_parameter_monitor(self)

    def on_trash(self):
        prevent_process_parameter_monitor_delete(self)
