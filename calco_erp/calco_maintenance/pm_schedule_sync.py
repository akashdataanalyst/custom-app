import frappe

from calco_erp.calco_maintenance.doctype.preventive_maintenance_plan.preventive_maintenance_plan import (
    get_schedule_tracking_values,
)


def refresh_pm_schedule_tracking():
    if not frappe.db.exists("DocType", "Preventive Maintenance Plan"):
        return

    rows = frappe.get_all(
        "Preventive Maintenance Plan",
        fields=["name", "next_due_date", "schedule_status", "days_until_due", "is_active"],
        limit_page_length=0,
    )

    for row in rows:
        status, days_until_due = get_schedule_tracking_values(row.next_due_date, row.is_active)
        updates = {}

        if row.schedule_status != status:
            updates["schedule_status"] = status

        if row.days_until_due != days_until_due:
            updates["days_until_due"] = days_until_due

        if updates:
            frappe.db.set_value("Preventive Maintenance Plan", row.name, updates, update_modified=False)

    frappe.clear_cache(doctype="Preventive Maintenance Plan")
