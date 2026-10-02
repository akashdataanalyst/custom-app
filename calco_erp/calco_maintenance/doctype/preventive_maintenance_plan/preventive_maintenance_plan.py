import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate, today

from calco_erp.calco_maintenance.master_data_utils import compute_next_due_date, cstr, get_frequency_details


class PreventiveMaintenancePlan(Document):
    def validate(self):
        self.is_active = 1 if self.is_active is None else self.is_active
        self.sync_equipment_snapshot()
        self.sync_schedule_fields()
        self.sync_tracking_fields()
        self.responsible_role = cstr(self.responsible_role) or "Maintenance"
        self.target_duration_hours = self.target_duration_hours or 0

    def sync_equipment_snapshot(self):
        if not self.equipment:
            frappe.throw(_("Equipment is mandatory for Preventive Maintenance Plan."))

        equipment = frappe.get_doc("Maintenance Equipment", self.equipment)
        self.equipment_code = equipment.equipment_code
        self.equipment_name = equipment.equipment_name
        self.equipment_type = equipment.equipment_type
        self.equipment_group = equipment.equipment_group

    def sync_schedule_fields(self):
        details = get_frequency_details(self.frequency)
        previous_doc = self.get_doc_before_save() if not self.is_new() else None
        previous_frequency = cstr(previous_doc.frequency) if previous_doc else ""
        previous_completed_on = getdate(previous_doc.last_completed_on) if previous_doc and previous_doc.last_completed_on else None

        self.frequency = details["label"]
        self.due_logic = details["due_logic"]
        self.interval_days = details["interval_days"] or 0
        self.runtime_hours_interval = details["runtime_hours"] or 0

        if self.last_completed_on:
            self.last_completed_on = getdate(self.last_completed_on)
        elif not self.next_due_date:
            self.last_completed_on = None

        should_reset_due_date = (
            self.is_new()
            or not self.next_due_date
            or (previous_doc and previous_frequency != self.frequency)
            or (previous_doc and previous_completed_on != self.last_completed_on)
        )

        if self.last_completed_on:
            self.next_due_date = compute_next_due_date(self.frequency, self.last_completed_on)
        elif should_reset_due_date:
            self.next_due_date = compute_next_due_date(self.frequency, getdate(today()))
        elif self.next_due_date:
            self.next_due_date = getdate(self.next_due_date)

    def sync_tracking_fields(self):
        status, days_until_due = get_schedule_tracking_values(self.next_due_date, self.is_active)
        self.schedule_status = status
        self.days_until_due = days_until_due


@frappe.whitelist()
def create_maintenance_ticket(plan_name):
    from calco_erp.calco_maintenance.automation import create_or_get_pm_ticket

    result = create_or_get_pm_ticket(plan_name)
    return {"doctype": "Maintenance Ticket", "name": result["ticket"]}


def get_schedule_tracking_values(next_due_date, is_active=1):
    if not is_active:
        return "Inactive", 0

    if not next_due_date:
        return "Manual", 0

    due_date = getdate(next_due_date)
    days_until_due = date_diff(due_date, getdate(today()))

    if days_until_due < 0:
        return "Overdue", days_until_due

    if days_until_due == 0:
        return "Due Today", days_until_due

    return "Upcoming", days_until_due
