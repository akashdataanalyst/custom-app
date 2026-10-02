from __future__ import annotations

import csv
from pathlib import Path

import frappe


PLANT_DEPARTMENTS = (
    "Production",
    "Quality",
    "Maintenance",
    "Purchase",
    "Stores",
    "Sales",
    "HR",
)

PLANT_DESIGNATIONS = (
    "Extruder Operator",
    "Shift Supervisor",
    "QC Engineer",
    "Maintenance Technician",
    "Store Executive",
)

SHIFT_TYPES = (
    {"name": "A Shift", "start_time": "06:00:00", "end_time": "14:00:00"},
    {"name": "B Shift", "start_time": "14:00:00", "end_time": "22:00:00"},
    {"name": "C Shift", "start_time": "22:00:00", "end_time": "06:00:00"},
)

SALARY_COMPONENTS = (
    {"name": "Basic", "abbr": "BASIC", "type": "Earning", "depends_on_payment_days": 1},
    {"name": "House Rent Allowance", "abbr": "HRA", "type": "Earning", "depends_on_payment_days": 1},
    {"name": "Conveyance Allowance", "abbr": "CONV", "type": "Earning", "depends_on_payment_days": 0},
    {"name": "Shift Allowance", "abbr": "SHIFT", "type": "Earning", "depends_on_payment_days": 0},
    {"name": "Special Allowance", "abbr": "SPEC", "type": "Earning", "depends_on_payment_days": 0},
    {"name": "Overtime", "abbr": "OT", "type": "Earning", "depends_on_payment_days": 0},
    {"name": "Provident Fund", "abbr": "PF", "type": "Deduction", "depends_on_payment_days": 0},
    {"name": "Professional Tax", "abbr": "PT", "type": "Deduction", "depends_on_payment_days": 0},
    {"name": "Income Tax", "abbr": "IT", "type": "Deduction", "depends_on_payment_days": 0},
    {"name": "ESI", "abbr": "ESI", "type": "Deduction", "depends_on_payment_days": 0},
)

PAYROLL_TEMPLATE_NAME = "Plant Employees Monthly"
EMPLOYEE_LINK_TEMPLATE = "employee_link_template.csv"


def company_name() -> str | None:
    return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


def hrms_installed() -> bool:
    return "hrms" in frappe.get_installed_apps()


def template_dir() -> Path:
    return Path(__file__).resolve().parent / "hrms_integration"


def ensure_department(department_name: str, company: str) -> None:
    existing = frappe.db.get_value(
        "Department",
        {"department_name": department_name, "company": company},
        "name",
    )
    if existing:
        return

    frappe.get_doc(
        {
            "doctype": "Department",
            "department_name": department_name,
            "company": company,
            "parent_department": "All Departments",
        }
    ).insert(ignore_permissions=True)


def ensure_designation(designation_name: str) -> None:
    existing = frappe.db.get_value("Designation", {"designation_name": designation_name}, "name")
    if existing:
        return

    frappe.get_doc(
        {
            "doctype": "Designation",
            "designation_name": designation_name,
        }
    ).insert(ignore_permissions=True)


def ensure_shift_type(shift: dict[str, str]) -> None:
    if frappe.db.exists("Shift Type", shift["name"]):
        doc = frappe.get_doc("Shift Type", shift["name"])
    else:
        doc = frappe.new_doc("Shift Type")
        doc.name = shift["name"]
        doc.shift_type_name = shift["name"]

    doc.start_time = shift["start_time"]
    doc.end_time = shift["end_time"]
    if hasattr(doc, "enable_auto_attendance"):
        doc.enable_auto_attendance = 0
    if hasattr(doc, "working_hours"):
        doc.working_hours = 8

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def ensure_salary_component(component: dict[str, object]) -> None:
    if frappe.db.exists("Salary Component", component["name"]):
        doc = frappe.get_doc("Salary Component", component["name"])
    else:
        doc = frappe.new_doc("Salary Component")
        doc.salary_component = component["name"]

    doc.salary_component_abbr = component["abbr"]
    doc.type = component["type"]
    doc.depends_on_payment_days = component["depends_on_payment_days"]
    if hasattr(doc, "is_tax_applicable"):
        doc.is_tax_applicable = 1 if component["name"] == "Income Tax" else 0
    if hasattr(doc, "variable_based_on_taxable_salary"):
        doc.variable_based_on_taxable_salary = 1 if component["name"] == "Income Tax" else 0

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def ensure_salary_structure(company: str) -> None:
    earnings = [
        "Basic",
        "House Rent Allowance",
        "Conveyance Allowance",
        "Shift Allowance",
        "Special Allowance",
        "Overtime",
    ]
    deductions = [
        "Provident Fund",
        "Professional Tax",
        "Income Tax",
        "ESI",
    ]

    if frappe.db.exists("Salary Structure", PAYROLL_TEMPLATE_NAME):
        doc = frappe.get_doc("Salary Structure", PAYROLL_TEMPLATE_NAME)
    else:
        doc = frappe.new_doc("Salary Structure")
        doc.name = PAYROLL_TEMPLATE_NAME

    doc.company = company
    doc.currency = frappe.db.get_value("Company", company, "default_currency") or "INR"
    doc.is_active = "Yes"
    doc.payroll_frequency = "Monthly"
    doc.salary_slip_based_on_timesheet = 0
    doc.earnings = []
    doc.deductions = []

    for component in earnings:
        doc.append(
            "earnings",
            {
                "salary_component": component,
                "abbr": frappe.db.get_value("Salary Component", component, "salary_component_abbr"),
                "default_amount": 0,
                "amount": 0,
                "depends_on_payment_days": 1 if component in ("Basic", "House Rent Allowance") else 0,
            },
        )

    for component in deductions:
        doc.append(
            "deductions",
            {
                "salary_component": component,
                "abbr": frappe.db.get_value("Salary Component", component, "salary_component_abbr"),
                "default_amount": 0,
                "amount": 0,
                "depends_on_payment_days": 0,
            },
        )

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def ensure_employee_link_template() -> None:
    directory = template_dir()
    directory.mkdir(parents=True, exist_ok=True)
    template_path = directory / EMPLOYEE_LINK_TEMPLATE
    if template_path.exists():
        return

    with template_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["employee", "department", "designation", "default_shift"],
        )
        writer.writeheader()


def apply_employee_links_from_template() -> int:
    template_path = template_dir() / EMPLOYEE_LINK_TEMPLATE
    if not template_path.exists():
        return 0

    updated = 0
    with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            employee = (row.get("employee") or "").strip()
            if not employee or not frappe.db.exists("Employee", employee):
                continue

            doc = frappe.get_doc("Employee", employee)
            changed = False

            for fieldname in ("department", "designation", "default_shift"):
                value = (row.get(fieldname) or "").strip()
                if value and doc.get(fieldname) != value:
                    doc.set(fieldname, value)
                    changed = True

            if changed:
                doc.save(ignore_permissions=True)
                updated += 1

    return updated


def ensure_hrms_integration_records() -> None:
    if not hrms_installed():
        return

    company = company_name()
    if not company:
        return

    for department in PLANT_DEPARTMENTS:
        ensure_department(department, company)

    for designation in PLANT_DESIGNATIONS:
        ensure_designation(designation)

    for shift in SHIFT_TYPES:
        ensure_shift_type(shift)

    for component in SALARY_COMPONENTS:
        ensure_salary_component(component)

    ensure_salary_structure(company)
    ensure_employee_link_template()
    apply_employee_links_from_template()
    frappe.db.commit()
