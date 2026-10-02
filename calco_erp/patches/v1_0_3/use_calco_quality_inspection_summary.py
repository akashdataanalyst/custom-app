import json
from pathlib import Path

import frappe
from frappe.modules.import_file import import_file_by_path


STANDARD_CHART = "Quality Inspection Analysis"
CALCO_CHART = "Calco Quality Inspection Analysis"
MANUFACTURING_DASHBOARD = "Manufacturing"
APP_ROOT = Path(frappe.get_app_path("calco_erp"))
REPORT_PATH = (
    APP_ROOT
    / "calco_quality"
    / "report"
    / "calco_quality_inspection_summary"
    / "calco_quality_inspection_summary.json"
)
CHART_PATH = (
    APP_ROOT
    / "calco_quality"
    / "dashboard_chart"
    / "calco_quality_inspection_analysis"
    / "calco_quality_inspection_analysis.json"
)
CHART_MANAGED_FIELDS = (
    "chart_name",
    "chart_type",
    "custom_options",
    "filters_json",
    "is_public",
    "is_standard",
    "module",
    "number_of_groups",
    "report_name",
    "time_interval",
    "timeseries",
    "timespan",
    "type",
    "use_report_chart",
)


def execute():
    ensure_quality_summary_dependencies()
    replace_manufacturing_quality_chart()


def ensure_quality_summary_dependencies():
    _ensure_packaged_record(REPORT_PATH, "Report", "Calco Quality Inspection Summary")
    _ensure_packaged_record(
        CHART_PATH,
        "Dashboard Chart",
        CALCO_CHART,
        managed_fields=CHART_MANAGED_FIELDS,
    )


def _ensure_packaged_record(path, doctype, name, managed_fields=None):
    definition = _load_definition(path, doctype, name)
    exists = frappe.db.exists(doctype, name)
    needs_import = not exists

    if exists and managed_fields:
        current = frappe.get_doc(doctype, name)
        needs_import = any(
            current.get(fieldname) != definition.get(fieldname)
            for fieldname in managed_fields
        )

    if needs_import:
        import_file_by_path(str(path), force=True, ignore_version=True)

    if not frappe.db.exists(doctype, name):
        frappe.throw(f"Required {doctype} {name} could not be installed from {path}.")


def _load_definition(path, expected_doctype, expected_name):
    if not path.is_file():
        frappe.throw(f"Packaged migration dependency is missing: {path}")

    with path.open(encoding="utf-8") as handle:
        definition = json.load(handle)

    if (
        definition.get("doctype") != expected_doctype
        or definition.get("name") != expected_name
    ):
        frappe.throw(
            f"Invalid packaged migration dependency {path}: expected "
            f"{expected_doctype} {expected_name}."
        )

    return definition


def replace_manufacturing_quality_chart():
    if not frappe.db.exists("Dashboard Chart", CALCO_CHART):
        frappe.throw(f"Required Dashboard Chart {CALCO_CHART} is not installed.")

    if get_chart_link(CALCO_CHART):
        return

    link_name = get_chart_link(STANDARD_CHART)
    if not link_name:
        frappe.throw(
            f"Dashboard {MANUFACTURING_DASHBOARD} has neither the standard nor the Calco quality chart."
        )

    frappe.db.set_value(
        "Dashboard Chart Link",
        link_name,
        "chart",
        CALCO_CHART,
        update_modified=False,
    )


def restore_standard_quality_chart():
    if get_chart_link(STANDARD_CHART):
        return

    link_name = get_chart_link(CALCO_CHART)
    if not link_name:
        frappe.throw(
            f"Dashboard {MANUFACTURING_DASHBOARD} has neither the Calco nor the standard quality chart."
        )

    frappe.db.set_value(
        "Dashboard Chart Link",
        link_name,
        "chart",
        STANDARD_CHART,
        update_modified=False,
    )


def get_chart_link(chart_name):
    return frappe.db.get_value(
        "Dashboard Chart Link",
        {"parent": MANUFACTURING_DASHBOARD, "chart": chart_name},
        "name",
    )
