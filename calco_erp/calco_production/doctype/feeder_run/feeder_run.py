import frappe
from frappe.model.document import Document

from calco_erp.calco_production.feeder_run import (
    before_insert_feeder_run,
    prevent_feeder_run_delete,
    validate_feeder_run,
)


class FeederRun(Document):
    def before_insert(self):
        before_insert_feeder_run(self)

    def validate(self):
        validate_feeder_run(self)

    def on_trash(self):
        prevent_feeder_run_delete(self)

    def after_delete(self):
        if hasattr(self.flags, "_restore_controlled_feeder_delete"):
            frappe.flags.controlled_feeder_run_delete = (
                self.flags._restore_controlled_feeder_delete
            )
