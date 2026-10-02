import frappe
from frappe import _
from frappe.model.document import Document

from calco_erp.calco_maintenance.master_data_utils import cstr, guess_equipment_group


class MaintenanceEquipment(Document):
    def validate(self):
        self.machine_code = cstr(self.machine_code) or cstr(self.equipment_code)
        self.machine_name = cstr(self.machine_name) or cstr(self.equipment_name) or cstr(self.equipment_type) or self.machine_code
        self.active = 1 if self.active is None else self.active

        self.equipment_code = self.machine_code
        self.equipment_name = self.machine_name
        self.equipment_type = cstr(self.equipment_type) or self.equipment_name
        self.location = cstr(self.location)
        self.department = cstr(self.department)
        self.criticality = cstr(self.criticality)
        self.is_active = self.active

        if not self.machine_code:
            frappe.throw(_("Machine Code is mandatory."))

        if not self.equipment_group:
            self.equipment_group = guess_equipment_group(
                equipment_code=self.machine_code,
                equipment_name=self.machine_name,
                equipment_type=self.equipment_type,
            )
