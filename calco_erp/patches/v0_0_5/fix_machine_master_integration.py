import frappe

from calco_erp.calco_maintenance.master_data_utils import cstr


def execute():
    if not frappe.db.exists("DocType", "Maintenance Equipment"):
        return

    sync_machine_master_fields()
    preserve_existing_ticket_machine_values()
    frappe.clear_cache(doctype="Maintenance Equipment")
    frappe.clear_cache(doctype="Maintenance Ticket")


def sync_machine_master_fields():
    rows = frappe.get_all(
        "Maintenance Equipment",
        fields=["name", "equipment_code", "equipment_name", "machine_code", "machine_name", "is_active", "active"],
        limit_page_length=0,
    )

    for row in rows:
        updates = {}
        machine_code = cstr(row.machine_code) or cstr(row.equipment_code) or cstr(row.name)
        machine_name = cstr(row.machine_name) or cstr(row.equipment_name) or machine_code
        active = int(row.active if row.active is not None else (row.is_active if row.is_active is not None else 1))

        if cstr(row.machine_code) != machine_code:
            updates["machine_code"] = machine_code
        if cstr(row.machine_name) != machine_name:
            updates["machine_name"] = machine_name
        if cstr(row.equipment_code) != machine_code:
            updates["equipment_code"] = machine_code
        if cstr(row.equipment_name) != machine_name:
            updates["equipment_name"] = machine_name
        if int(row.active if row.active is not None else 1) != active:
            updates["active"] = active
        if int(row.is_active if row.is_active is not None else 1) != active:
            updates["is_active"] = active

        if updates:
            frappe.db.set_value("Maintenance Equipment", row.name, updates, update_modified=False)


def preserve_existing_ticket_machine_values():
    if not frappe.db.exists("DocType", "Maintenance Ticket"):
        return

    rows = frappe.get_all(
        "Maintenance Ticket",
        fields=["name", "machine_name", "equipment"],
        limit_page_length=0,
    )

    for row in rows:
        machine_value = cstr(row.machine_name) or cstr(row.equipment)
        if not machine_value:
            continue

        machine_docname = resolve_machine_docname(machine_value)
        if not machine_docname:
            machine_docname = create_placeholder_machine(machine_value)

        updates = {}
        if cstr(row.machine_name) != machine_docname:
            updates["machine_name"] = machine_docname
        if cstr(row.equipment) != machine_docname:
            updates["equipment"] = machine_docname

        if updates:
            frappe.db.set_value("Maintenance Ticket", row.name, updates, update_modified=False)


def resolve_machine_docname(value):
    value = cstr(value)
    if not value:
        return None

    if frappe.db.exists("Maintenance Equipment", value):
        return value

    for filters in (
        {"machine_code": value},
        {"equipment_code": value},
        {"machine_name": value},
        {"equipment_name": value},
    ):
        match = frappe.db.get_value("Maintenance Equipment", filters, "name")
        if match:
            return match

    return None


def create_placeholder_machine(value):
    machine_code = cstr(value)
    doc = frappe.get_doc(
        {
            "doctype": "Maintenance Equipment",
            "machine_code": machine_code,
            "machine_name": machine_code,
            "equipment_code": machine_code,
            "equipment_name": machine_code,
            "equipment_type": machine_code,
            "active": 1,
            "is_active": 1,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
