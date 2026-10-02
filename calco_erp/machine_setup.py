from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import getdate, nowdate


MACHINE_FIELD = "custom_machine"
OPERATOR_FIELD = "custom_operator"
SHIFT_FIELD = "custom_shift_type"
PLANT_MACHINES = [
    {"name": "Line 1", "alias": "Extruder 1"},
    {"name": "Line 2", "alias": "Extruder 2"},
    {"name": "Line 3", "alias": "Extruder 3"},
    {"name": "Line 4", "alias": "Extruder 4"},
    {"name": "Line 5", "alias": "Extruder 5"},
    {"name": "Line 6", "alias": "Extruder 6"},
]


def ensure_machine_tracking_setup():
    ensure_workstations()
    ensure_custom_fields()
    frappe.clear_cache()


def ensure_workstations():
    if not frappe.db.exists("DocType", "Workstation"):
        return

    for machine in PLANT_MACHINES:
        workstation = get_or_create_workstation(machine["name"])
        changed = False

        if workstation.workstation_name != machine["name"]:
            workstation.workstation_name = machine["name"]
            changed = True

        if hasattr(workstation, "description") and workstation.description != machine["alias"]:
            workstation.description = machine["alias"]
            changed = True

        if changed:
            if workstation.is_new():
                workstation.insert(ignore_permissions=True)
            else:
                workstation.save(ignore_permissions=True)
        elif workstation.is_new():
            workstation.insert(ignore_permissions=True)


def get_or_create_workstation(workstation_name: str):
    existing_name = frappe.db.get_value("Workstation", {"workstation_name": workstation_name}, "name")
    if existing_name:
        return frappe.get_doc("Workstation", existing_name)

    if frappe.db.exists("Workstation", workstation_name):
        return frappe.get_doc("Workstation", workstation_name)

    doc = frappe.new_doc("Workstation")
    doc.workstation_name = workstation_name
    return doc


def ensure_custom_fields():
    custom_fields = {
        "Work Order": [
            {
                "fieldname": MACHINE_FIELD,
                "label": "Machine",
                "fieldtype": "Link",
                "options": "Workstation",
                "insert_after": "bom_no",
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            },
            {
                "fieldname": OPERATOR_FIELD,
                "label": "Operator",
                "fieldtype": "Link",
                "options": "Employee",
                "insert_after": MACHINE_FIELD,
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            },
            {
                "fieldname": SHIFT_FIELD,
                "label": "Shift",
                "fieldtype": "Link",
                "options": "Shift Type",
                "insert_after": OPERATOR_FIELD,
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            }
        ],
        "Stock Entry": [
            {
                "fieldname": MACHINE_FIELD,
                "label": "Machine",
                "fieldtype": "Link",
                "options": "Workstation",
                "insert_after": "work_order",
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            },
            {
                "fieldname": OPERATOR_FIELD,
                "label": "Operator",
                "fieldtype": "Link",
                "options": "Employee",
                "insert_after": MACHINE_FIELD,
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            },
            {
                "fieldname": SHIFT_FIELD,
                "label": "Shift",
                "fieldtype": "Link",
                "options": "Shift Type",
                "insert_after": OPERATOR_FIELD,
                "in_list_view": 1,
                "search_index": 1,
                "no_copy": 1,
            }
        ],
    }
    create_custom_fields(custom_fields, update=True)


def validate_work_order_machine(doc, method=None):
    machine = (doc.get(MACHINE_FIELD) or "").strip()
    operator = (doc.get(OPERATOR_FIELD) or "").strip()
    shift_type = (doc.get(SHIFT_FIELD) or "").strip()

    if machine:
        ensure_valid_machine(machine)
    if operator:
        ensure_valid_operator(operator)
    if shift_type:
        ensure_valid_shift(shift_type)


def validate_work_order_machine_required(doc, method=None):
    machine = (doc.get(MACHINE_FIELD) or "").strip()
    if not machine:
        frappe.throw(_("Machine is mandatory before submitting the Work Order."))

    ensure_valid_machine(machine)


def validate_stock_entry_machine(doc, method=None):
    if get_stock_entry_purpose(doc) != "Manufacture":
        return

    machine = (doc.get(MACHINE_FIELD) or "").strip()
    operator = (doc.get(OPERATOR_FIELD) or "").strip()
    shift_type = (doc.get(SHIFT_FIELD) or "").strip()
    if not machine and doc.get("work_order"):
        machine = frappe.db.get_value("Work Order", doc.work_order, MACHINE_FIELD) or ""
        if machine:
            doc.set(MACHINE_FIELD, machine)
    if not operator and doc.get("work_order"):
        operator = frappe.db.get_value("Work Order", doc.work_order, OPERATOR_FIELD) or ""
        if operator:
            doc.set(OPERATOR_FIELD, operator)
    if not shift_type and doc.get("work_order"):
        shift_type = frappe.db.get_value("Work Order", doc.work_order, SHIFT_FIELD) or ""
        if shift_type:
            doc.set(SHIFT_FIELD, shift_type)

    if not machine:
        frappe.throw("Machine is mandatory for Manufacture Stock Entry.")
    if not operator:
        frappe.throw("Operator is mandatory for Manufacture Stock Entry.")
    if not shift_type:
        frappe.throw("Shift is mandatory for Manufacture Stock Entry.")

    ensure_valid_machine(machine)
    ensure_valid_operator(operator)
    ensure_valid_shift(shift_type)


def get_stock_entry_purpose(doc) -> str:
    return (doc.get("stock_entry_type") or doc.get("purpose") or "").strip()


def ensure_valid_machine(machine: str):
    if not frappe.db.exists("Workstation", machine):
        frappe.throw(f"Machine {machine} does not exist in Workstation master.")


def ensure_valid_operator(operator: str):
    if not frappe.db.exists("Employee", operator):
        frappe.throw(f"Operator {operator} does not exist in Employee master.")


def ensure_valid_shift(shift_type: str):
    if not frappe.db.exists("Shift Type", shift_type):
        frappe.throw(f"Shift {shift_type} does not exist in Shift Type master.")


@frappe.whitelist()
def machine_tracking_status() -> dict[str, object]:
    work_order_meta = frappe.get_meta("Work Order")
    stock_entry_meta = frappe.get_meta("Stock Entry")
    batch_record_meta = frappe.get_meta("Batch Production Record")
    return {
        "machine_field": MACHINE_FIELD,
        "operator_field": OPERATOR_FIELD,
        "shift_field": SHIFT_FIELD,
        "work_order_field_exists": work_order_meta.has_field(MACHINE_FIELD),
        "work_order_operator_field_exists": work_order_meta.has_field(OPERATOR_FIELD),
        "work_order_shift_field_exists": work_order_meta.has_field(SHIFT_FIELD),
        "stock_entry_field_exists": stock_entry_meta.has_field(MACHINE_FIELD),
        "stock_entry_operator_field_exists": stock_entry_meta.has_field(OPERATOR_FIELD),
        "stock_entry_shift_field_exists": stock_entry_meta.has_field(SHIFT_FIELD),
        "batch_production_record_field_exists": batch_record_meta.has_field("machine"),
        "batch_production_record_operator_field_exists": batch_record_meta.has_field("operator"),
        "batch_production_record_shift_field_exists": batch_record_meta.has_field("shift_type"),
        "workstations": frappe.get_all(
            "Workstation",
            filters={"name": ("in", [machine["name"] for machine in PLANT_MACHINES])},
            fields=["name", "workstation_name", "description"],
            order_by="name asc",
        ),
        "shift_types": frappe.get_all(
            "Shift Type",
            filters={"name": ("in", ["A Shift", "B Shift", "C Shift"])},
            fields=["name", "start_time", "end_time"],
            order_by="name asc",
        ),
        "employee_count": frappe.db.count("Employee"),
    }


@frappe.whitelist()
def production_by_shift(report_date: str | None = None) -> dict[str, object]:
    report_date = getdate(report_date or nowdate())
    rows = get_output_by_shift_rows(report_date)
    return {
        "report_date": str(report_date),
        "rows": rows,
    }


@frappe.whitelist()
def production_by_machine(report_date: str | None = None) -> dict[str, object]:
    report_date = getdate(report_date or nowdate())
    return {
        "report_date": str(report_date),
        "rows": get_output_by_machine_rows(report_date),
    }


@frappe.whitelist()
def production_by_operator(report_date: str | None = None) -> dict[str, object]:
    report_date = getdate(report_date or nowdate())
    return {
        "report_date": str(report_date),
        "rows": get_output_by_operator_rows(report_date),
    }


def get_output_by_machine_rows(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        f"""
        select
            coalesce(nullif(bpr.machine, ''), nullif(se.{MACHINE_FIELD}, ''), nullif(wo.{MACHINE_FIELD}, '')) as machine,
            round(sum(bpr.produced_qty), 3) as produced_qty,
            count(*) as batch_count
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        left join `tabWork Order` wo on wo.name = bpr.work_order
        where bpr.docstatus = 1
          and coalesce(nullif(bpr.machine, ''), nullif(se.{MACHINE_FIELD}, ''), nullif(wo.{MACHINE_FIELD}, '')) is not null
          and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        group by machine
        order by produced_qty desc, machine asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_output_by_operator_rows(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        f"""
        select
            coalesce(nullif(bpr.operator, ''), nullif(se.{OPERATOR_FIELD}, ''), nullif(wo.{OPERATOR_FIELD}, '')) as operator,
            coalesce(emp.employee_name, coalesce(nullif(bpr.operator, ''), nullif(se.{OPERATOR_FIELD}, ''), nullif(wo.{OPERATOR_FIELD}, ''))) as operator_name,
            round(sum(bpr.produced_qty), 3) as produced_qty,
            count(*) as batch_count
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        left join `tabWork Order` wo on wo.name = bpr.work_order
        left join `tabEmployee` emp on emp.name = coalesce(nullif(bpr.operator, ''), nullif(se.{OPERATOR_FIELD}, ''), nullif(wo.{OPERATOR_FIELD}, ''))
        where bpr.docstatus = 1
          and coalesce(nullif(bpr.operator, ''), nullif(se.{OPERATOR_FIELD}, ''), nullif(wo.{OPERATOR_FIELD}, '')) is not null
          and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        group by operator, operator_name
        order by produced_qty desc, operator asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_output_by_shift_rows(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        f"""
        select
            coalesce(nullif(bpr.shift_type, ''), nullif(se.{SHIFT_FIELD}, ''), nullif(wo.{SHIFT_FIELD}, '')) as shift_type,
            round(sum(bpr.produced_qty), 3) as produced_qty,
            count(*) as batch_count
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        left join `tabWork Order` wo on wo.name = bpr.work_order
        where bpr.docstatus = 1
          and coalesce(nullif(bpr.shift_type, ''), nullif(se.{SHIFT_FIELD}, ''), nullif(wo.{SHIFT_FIELD}, '')) is not null
          and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        group by shift_type
        order by shift_type asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )
