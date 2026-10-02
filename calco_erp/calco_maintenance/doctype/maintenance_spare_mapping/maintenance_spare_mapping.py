import frappe
from frappe.model.document import Document

from calco_erp.calco_maintenance.master_data_utils import cstr, guess_equipment_group


class MaintenanceSpareMapping(Document):
    def validate(self):
        self.machine = cstr(self.machine) or cstr(self.equipment)
        self.equipment = self.machine
        self.source_machine_name = cstr(self.source_machine_name)
        self.equipment_group = cstr(self.equipment_group)
        self.standard_qty = self.standard_qty or 1
        self.critical = 1 if self.critical else 0
        self.is_active = 1 if self.is_active is None else self.is_active

        if self.machine and not self.equipment_group:
            self.equipment_group = cstr(
                frappe.db.get_value("Maintenance Equipment", self.machine, "equipment_group")
            )

        if not self.equipment_group:
            self.equipment_group = guess_equipment_group(source_reference=self.source_machine_name)
