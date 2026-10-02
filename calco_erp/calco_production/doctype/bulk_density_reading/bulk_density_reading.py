from frappe.model.document import Document

from calco_erp.calco_production.bulk_density_monitoring import (
    prevent_bulk_density_reading_delete,
    validate_bulk_density_reading_document,
)


class BulkDensityReading(Document):
    def validate(self):
        validate_bulk_density_reading_document(self)

    def on_trash(self):
        prevent_bulk_density_reading_delete(self)
