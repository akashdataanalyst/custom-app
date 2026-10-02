from frappe.model.document import Document

from calco_erp.calco_production.silo_control import (
    before_insert_silo_control,
    prevent_silo_control_delete,
    validate_silo_control,
)


class SiloControl(Document):
    def before_insert(self):
        before_insert_silo_control(self)

    def validate(self):
        validate_silo_control(self)

    def on_trash(self):
        prevent_silo_control_delete(self)
