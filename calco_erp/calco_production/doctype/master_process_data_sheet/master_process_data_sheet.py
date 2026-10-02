from frappe.model.document import Document

from calco_erp.calco_production.mpds_master import (
    before_insert_master_process_data_sheet,
    prevent_master_process_data_sheet_delete,
    validate_master_process_data_sheet,
)


class MasterProcessDataSheet(Document):
    def before_insert(self):
        before_insert_master_process_data_sheet(self)

    def validate(self):
        validate_master_process_data_sheet(self)

    def on_trash(self):
        prevent_master_process_data_sheet_delete(self)
