from frappe.model.document import Document

from calco_erp.calco_production.bulk_density_monitoring import (
    before_insert_bulk_density_monitor,
    prevent_bulk_density_monitor_delete,
    validate_bulk_density_monitor,
)


class BulkDensityMonitor(Document):
    def before_insert(self):
        before_insert_bulk_density_monitor(self)

    def validate(self):
        validate_bulk_density_monitor(self)

    def on_trash(self):
        prevent_bulk_density_monitor_delete(self)
