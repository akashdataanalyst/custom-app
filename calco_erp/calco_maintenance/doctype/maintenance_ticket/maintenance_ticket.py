import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, time_diff_in_hours

from calco_erp.calco_maintenance.master_data_utils import (
    get_equipment_aliases,
    reference_matches_equipment,
)


WORKFLOW_STAGES = [
    "Open",
    "Inspection",
    "Spare Required",
    "Spare Available",
    "In Progress",
    "Completed",
    "Closed",
]

SLA_HOURS = {
    "Red": 24,
    "Yellow": 48,
    "Green": 72,
}


class MaintenanceTicket(Document):
    def validate(self):
        self.set_defaults()
        self.sync_master_links()
        self.sync_spare_request()
        self.normalize_datetime_fields()
        self.apply_sla()
        self.validate_open_pm_ticket_uniqueness()
        self.validate_workflow()

    def set_defaults(self):
        if not self.raised_on:
            self.raised_on = now_datetime()

        if not self.raised_by:
            self.raised_by = frappe.session.user

        if not self.ticket_owner:
            self.ticket_owner = self.raised_by

        if not self.status:
            self.status = "Open"

        if self.pm_plan and not self.maintenance_type:
            self.maintenance_type = "Preventive"
        elif not self.maintenance_type:
            self.maintenance_type = "Breakdown"

    def normalize_datetime_fields(self):
        for fieldname in (
            "raised_on",
            "due_date",
            "inspection_time",
            "actual_start_time",
            "actual_end_time",
        ):
            value = self.get(fieldname)
            if value:
                self.set(fieldname, get_datetime(value))
            else:
                self.set(fieldname, None)

    def sync_master_links(self):
        if self.pm_plan:
            plan_equipment = frappe.db.get_value("Preventive Maintenance Plan", self.pm_plan, "equipment")
            if plan_equipment and not self.machine_name:
                self.machine_name = plan_equipment
            elif plan_equipment and self.machine_name and plan_equipment != self.machine_name:
                frappe.throw(_("The selected Preventive Maintenance Plan is linked to a different equipment record."))

        if self.machine_name and not self.equipment:
            self.equipment = self.machine_name
        elif self.equipment and not self.machine_name:
            self.machine_name = self.equipment

        if self.machine_name:
            if not frappe.db.exists("Maintenance Equipment", self.machine_name):
                frappe.throw(_("Machine Name must be selected from the Machine master."))

            equipment = frappe.get_doc("Maintenance Equipment", self.machine_name)
            self.equipment = equipment.name
            if not self.location and equipment.location:
                self.location = equipment.location

    def sync_spare_request(self):
        self.requested_spare_item = (self.requested_spare_item or "").strip()
        self.requested_spare_qty = flt(self.requested_spare_qty) or 0

        if not self.requested_spare_item:
            if not self.requested_spare_qty:
                self.requested_spare_qty = 0
            return

        if not self.machine_name:
            frappe.throw(_("Select Machine Name before requesting a spare item."))

        if not is_spare_mapped_to_machine(self.machine_name, self.requested_spare_item):
            frappe.throw(_("Selected spare item is not mapped to the chosen machine."))

        if not self.requested_spare_qty:
            standard_qty = frappe.db.get_value(
                "Maintenance Spare Mapping",
                {"machine": self.machine_name, "spare_item": self.requested_spare_item},
                "standard_qty",
            )
            self.requested_spare_qty = flt(standard_qty) or 1

        self.spare_required = 1

        if not self.spare_details:
            item_name = frappe.db.get_value("Item", self.requested_spare_item, "item_name") or self.requested_spare_item
            self.spare_details = _("{0} x {1}").format(self.requested_spare_qty, item_name)

    def apply_sla(self):
        if self.priority not in SLA_HOURS:
            self.due_date = None
            self.is_overdue = 0
            self.delay_hours = 0
            return

        raised_on = get_datetime(self.raised_on)
        self.due_date = get_datetime(
            add_to_date(raised_on, hours=SLA_HOURS[self.priority], as_datetime=True)
        )

        reference_time = get_datetime(self.actual_end_time) if self.actual_end_time else now_datetime()
        if self.due_date and reference_time > self.due_date:
            self.delay_hours = round(flt(time_diff_in_hours(reference_time, self.due_date)), 2)
            self.is_overdue = 1
        else:
            self.delay_hours = 0
            self.is_overdue = 0

    def validate_workflow(self):
        previous_status = self.get_previous_status()
        status_changed = previous_status != self.status

        self.validate_initial_status(previous_status)
        self.validate_status_value()

        if status_changed:
            self.validate_status_transition(previous_status)
            self.validate_stage_requirements()

    def get_previous_status(self):
        if self.is_new():
            return None

        previous_doc = self.get_doc_before_save()
        if previous_doc:
            return previous_doc.status

        return frappe.db.get_value(self.doctype, self.name, "status")

    def validate_initial_status(self, previous_status):
        if previous_status is None and self.status != "Open":
            frappe.throw(_("New Maintenance Tickets must start in Open status."))

    def validate_status_value(self):
        if self.status not in WORKFLOW_STAGES:
            frappe.throw(_("Status {0} is not a valid maintenance workflow stage.").format(self.status))

    def validate_status_transition(self, previous_status):
        if previous_status is None:
            return

        allowed_statuses = self.get_allowed_next_statuses(previous_status)
        if self.status not in allowed_statuses:
            frappe.throw(
                _("Invalid transition from {0} to {1}. Allowed next stage: {2}.").format(
                    previous_status,
                    self.status,
                    ", ".join(allowed_statuses) or _("None"),
                )
            )

    def get_allowed_next_statuses(self, previous_status):
        if previous_status == "Open":
            return ["Inspection"]

        if previous_status == "Inspection":
            return ["Spare Required"] if cint(self.spare_required) else ["In Progress"]

        if previous_status == "Spare Required":
            return ["Spare Available"]

        if previous_status == "Spare Available":
            return ["In Progress"]

        if previous_status == "In Progress":
            return ["Completed"]

        if previous_status == "Completed":
            return ["Closed"]

        return []

    def validate_stage_requirements(self):
        if self.status in WORKFLOW_STAGES[2:]:
            self.validate_inspection_details()

        if self.status in {"Spare Required", "Spare Available"} and not cint(self.spare_required):
            frappe.throw(_("Spare Required must be checked before moving into spare handling stages."))

        if self.status in {"Completed", "Closed"}:
            self.validate_completion_details()

        if self.status == "Closed":
            self.validate_closure_details()

    def validate_open_pm_ticket_uniqueness(self):
        if not self.pm_plan or self.status == "Closed":
            return

        filters = {
            "pm_plan": self.pm_plan,
            "status": ("in", WORKFLOW_STAGES[:-1]),
        }
        if self.name:
            filters["name"] = ("!=", self.name)

        existing_ticket = frappe.db.get_value("Maintenance Ticket", filters, "name")
        if existing_ticket:
            frappe.throw(
                _("An open Maintenance Ticket already exists for this Preventive Maintenance Plan: {0}").format(
                    existing_ticket
                )
            )

    def validate_inspection_details(self):
        missing_fields = []

        if not self.inspection_done_by:
            missing_fields.append(_("Inspection Done By"))

        if not self.inspection_time:
            missing_fields.append(_("Inspection Time"))

        if not self.inspection_observation:
            missing_fields.append(_("Inspection Observation"))

        if missing_fields:
            frappe.throw(
                _("Inspection stage requires: {0}.").format(", ".join(missing_fields))
            )

    def validate_completion_details(self):
        actual_start_time = get_datetime(self.actual_start_time) if self.actual_start_time else None
        actual_end_time = get_datetime(self.actual_end_time) if self.actual_end_time else None

        if actual_start_time and actual_end_time and actual_end_time < actual_start_time:
            frappe.throw(_("Actual End Time cannot be earlier than Actual Start Time."))

        has_actual_times = actual_start_time and actual_end_time
        has_work_done = bool(self.work_done)

        if not has_actual_times and not has_work_done:
            frappe.throw(
                _(
                    "Completed stage requires either both Actual Start Time and Actual End Time, "
                    "or Work Done."
                )
            )

    def validate_closure_details(self):
        if not cint(self.resolved_confirmation) and not self.closure_remarks:
            frappe.throw(
                _("Closed stage requires either Resolved Confirmation or Closure Remarks.")
            )


@frappe.whitelist()
def get_suggested_spares(equipment):
    if not equipment:
        return []

    return get_machine_spare_rows(equipment)


def get_machine_spare_rows(equipment):
    if not equipment:
        return []

    equipment_doc = frappe.get_doc("Maintenance Equipment", equipment)
    aliases = get_equipment_aliases(
        equipment_code=equipment_doc.equipment_code,
        equipment_name=equipment_doc.equipment_name,
        equipment_type=equipment_doc.equipment_type,
        equipment_group=equipment_doc.equipment_group,
    )

    direct_rows = frappe.get_all(
        "Maintenance Spare Mapping",
        filters={"is_active": 1, "machine": equipment_doc.name},
        fields=["machine", "spare_item", "critical", "standard_qty", "source_machine_name"],
        limit_page_length=0,
        order_by="spare_item asc",
    )

    if not direct_rows:
        fallback_rows = frappe.get_all(
            "Maintenance Spare Mapping",
            filters={"is_active": 1},
            fields=["machine", "equipment", "equipment_group", "source_machine_name", "spare_item", "critical", "standard_qty"],
            limit_page_length=0,
            order_by="spare_item asc",
        )

        filtered_rows = []
        for row in fallback_rows:
            if row.machine and row.machine != equipment_doc.name:
                continue

            group_match = bool(row.equipment_group and row.equipment_group == equipment_doc.equipment_group)
            alias_match = reference_matches_equipment(row.source_machine_name, aliases)
            direct_match = row.equipment == equipment_doc.name

            if direct_match or group_match or alias_match:
                filtered_rows.append(row)

        direct_rows = filtered_rows

    item_codes = [row.spare_item for row in direct_rows if row.spare_item]
    if not item_codes:
        return []

    item_rows = frappe.get_all(
        "Item",
        filters={"name": ("in", list(dict.fromkeys(item_codes))), "disabled": 0},
        fields=["name", "item_name", "stock_uom", "disabled", "custom_maintenance_critical"],
        limit_page_length=len(set(item_codes)),
    )
    item_by_name = {row.name: row for row in item_rows}

    seen = set()
    results = []
    for row in direct_rows:
        if row.spare_item in seen or row.spare_item not in item_by_name:
            continue

        item_row = item_by_name[row.spare_item]
        results.append(
            {
                "name": item_row.name,
                "item_name": item_row.item_name,
                "stock_uom": item_row.stock_uom,
                "disabled": item_row.disabled,
                "critical": row.critical if row.critical is not None else item_row.custom_maintenance_critical,
                "standard_qty": row.standard_qty or 1,
                "source_machine_name": row.source_machine_name,
            }
        )
        seen.add(row.spare_item)

    return results


def is_spare_mapped_to_machine(machine, spare_item):
    return any(row["name"] == spare_item for row in get_machine_spare_rows(machine))


@frappe.whitelist()
def get_machine_spare_query(doctype, txt, searchfield, start, page_len, filters):
    machine = (filters or {}).get("machine")
    rows = get_machine_spare_rows(machine)
    txt = (txt or "").lower()

    filtered = [
        row for row in rows
        if txt in row["name"].lower() or txt in (row["item_name"] or "").lower()
    ]

    window = filtered[start:start + page_len]
    return [(row["name"], row["item_name"]) for row in window]
