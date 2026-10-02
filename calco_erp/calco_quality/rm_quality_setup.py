from __future__ import annotations

from collections import defaultdict
import re

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint, cstr

from calco_erp.calco_quality.rm_testing_utils import (
    MANUAL_APPROVAL_RULES,
    NUMERIC_APPROVAL_RULES,
    NUMERIC_RECORDING_APPROVAL_RULE,
    OBSERVATION_APPROVAL_RULE,
    PASS_FAIL_APPROVAL_RULE,
    REFERENCE_ONLY_APPROVAL_RULE,
    evaluate_observation,
    evaluate_pass_fail,
    normalize_text,
    parse_float,
)


def ensure_rm_quality_setup():
    create_custom_fields(get_rm_quality_custom_fields(), update=True, ignore_validate=True)
    from calco_erp.calco_quality.automatic_rm_release_setup import (
        ensure_automatic_rm_release_fields,
    )

    ensure_automatic_rm_release_fields()
    sync_legacy_critical_test_flags()
    ensure_rm_reading_grid_properties()
    frappe.clear_cache()


def get_rm_quality_custom_fields():
    return {
        "Item": [
            {
                "fieldname": "custom_enable_rm_qc",
                "fieldtype": "Check",
                "label": "Enable RM QC",
                "insert_after": "inspection_required_before_purchase",
                "default": "0",
            },
        ],
        "Purchase Receipt Item": [
            {
                "fieldname": "custom_qc_status",
                "fieldtype": "Select",
                "label": "QC Status",
                "options": "\nPending\nIn Progress\nAccepted\nHold\nRejected\nAccepted Under Deviation\nReleased",
                "insert_after": "batch_no",
                "read_only": 1,
                "in_list_view": 1,
                "allow_on_submit": 1,
            },
            {
                "fieldname": "custom_accepted_qty",
                "fieldtype": "Float",
                "label": "Accepted Qty",
                "insert_after": "custom_qc_status",
                "read_only": 1,
                "allow_on_submit": 1,
            },
            {
                "fieldname": "custom_rejected_qty",
                "fieldtype": "Float",
                "label": "Rejected Qty",
                "insert_after": "custom_accepted_qty",
                "read_only": 1,
                "allow_on_submit": 1,
            },
              {
                  "fieldname": "custom_quality_inspection",
                  "fieldtype": "Link",
                  "label": "Quality Inspection",
                  "options": "Quality Inspection",
                  "insert_after": "custom_rejected_qty",
                  "read_only": 1,
                  "allow_on_submit": 1,
              },
              {
                  "fieldname": "custom_rm_deviation_approval",
                  "fieldtype": "Link",
                  "label": "RM Deviation Approval",
                  "options": "RM Deviation Approval",
                  "insert_after": "custom_quality_inspection",
                  "read_only": 1,
                  "allow_on_submit": 1,
              },
        ],
        "Purchase Receipt": [
            {
                "fieldname": "custom_supplier_documents_section",
                "fieldtype": "Section Break",
                "label": "Supplier Documents & RM Storage",
                "insert_after": "items",
            },
            {
                "fieldname": "custom_supplier_purchase_invoice_attachment",
                "fieldtype": "Attach",
                "label": "Supplier Purchase Invoice Attachment",
                "insert_after": "custom_supplier_documents_section",
                "reqd": 1,
                "hidden": 0,
                "read_only": 0,
            },
            {
                "fieldname": "custom_supplier_test_certificate_attachment",
                "fieldtype": "Attach",
                "label": "Supplier Test Certificate Attachment",
                "insert_after": "custom_supplier_purchase_invoice_attachment",
                "reqd": 1,
                "hidden": 0,
                "read_only": 0,
            },
            {
                "fieldname": "custom_raw_material_storage_photo",
                "fieldtype": "Attach",
                "label": "Raw Material Storage Photo",
                "insert_after": "custom_supplier_test_certificate_attachment",
                "reqd": 1,
                "hidden": 0,
                "read_only": 0,
            },
            {
                "fieldname": "custom_rm_expiry_date",
                "fieldtype": "Date",
                "label": "RM Expiry Date",
                "insert_after": "custom_raw_material_storage_photo",
                "reqd": 1,
                "hidden": 0,
                "read_only": 0,
            },
        ],
        "RM Release Note": [
            {
                "fieldname": "custom_purchase_receipt",
                "fieldtype": "Link",
                "label": "Purchase Receipt",
                "options": "Purchase Receipt",
                "insert_after": "rm_qc_decision",
                "read_only": 1,
            },
            {
                "fieldname": "custom_quality_inspection",
                "fieldtype": "Link",
                "label": "Quality Inspection",
                "options": "Quality Inspection",
                "insert_after": "custom_purchase_receipt",
                "read_only": 1,
            },
            {
                "fieldname": "custom_supplier",
                "fieldtype": "Link",
                "label": "Supplier",
                "options": "Supplier",
                "insert_after": "custom_quality_inspection",
                "read_only": 1,
            },
            {
                "fieldname": "custom_item_name",
                "fieldtype": "Data",
                "label": "Item Name",
                "insert_after": "item_code",
                "read_only": 1,
            },
            {
                "fieldname": "custom_rm_deviation_approval",
                "fieldtype": "Link",
                "label": "RM Deviation Approval",
                "options": "RM Deviation Approval",
                "insert_after": "rm_qc_decision",
            },
        ],
        "RM QC Decision": [
            {
                "fieldname": "custom_material_request",
                "fieldtype": "Link",
                "label": "Material Request",
                "options": "Material Request",
                "insert_after": "purchase_receipt",
                "read_only": 1,
            },
            {
                "fieldname": "custom_material_request_item",
                "fieldtype": "Data",
                "label": "Material Request Item",
                "insert_after": "custom_material_request",
                "read_only": 1,
                "hidden": 1,
            },
            {
                "fieldname": "custom_supplier",
                "fieldtype": "Link",
                "label": "Supplier",
                "options": "Supplier",
                "insert_after": "custom_material_request_item",
                "read_only": 1,
            },
            {
                "fieldname": "custom_item_name",
                "fieldtype": "Data",
                "label": "Item Name",
                "insert_after": "item_code",
                "read_only": 1,
            },
            {
                "fieldname": "custom_received_qty",
                "fieldtype": "Float",
                "label": "Received Qty",
                "insert_after": "sample_qty",
                "read_only": 1,
            },
            {
                "fieldname": "custom_accepted_qty",
                "fieldtype": "Float",
                "label": "Accepted Qty",
                "insert_after": "custom_received_qty",
                "read_only": 1,
            },
            {
                "fieldname": "custom_rejected_or_hold_qty",
                "fieldtype": "Float",
                "label": "Rejected / Hold Qty",
                "insert_after": "custom_accepted_qty",
                "read_only": 1,
            },
            {
                "fieldname": "custom_failed_parameters",
                "fieldtype": "Long Text",
                "label": "QC Failed Parameters",
                "insert_after": "status",
                "read_only": 1,
            },
            {
                "fieldname": "custom_deviation_attachment",
                "fieldtype": "Attach",
                "label": "Deviation Attachment",
                "insert_after": "remarks",
            },
            {
                "fieldname": "custom_decision_reason",
                "fieldtype": "Text",
                "label": "Decision Reason / Justification",
                "insert_after": "custom_deviation_attachment",
            },
        ],
        "RM Deviation Approval": [
            {
                "fieldname": "custom_material_request",
                "fieldtype": "Link",
                "label": "Material Request",
                "options": "Material Request",
                "insert_after": "purchase_order",
                "read_only": 1,
            },
            {
                "fieldname": "custom_material_request_item",
                "fieldtype": "Data",
                "label": "Material Request Item",
                "insert_after": "custom_material_request",
                "read_only": 1,
                "hidden": 1,
            },
        ],
        "Quality Inspection Parameter": [
            {
                "fieldname": "custom_is_raw_material_test",
                "fieldtype": "Check",
                "label": "Raw Material Testing Type",
                "insert_after": "description",
                "default": "0",
            },
            {
                "fieldname": "custom_test_standard",
                "fieldtype": "Data",
                "label": "Test Standard",
                "insert_after": "custom_is_raw_material_test",
            },
            {
                "fieldname": "custom_cppl_method",
                "fieldtype": "Data",
                "label": "CPPL Method",
                "insert_after": "custom_test_standard",
            },
            {
                "fieldname": "custom_test_condition",
                "fieldtype": "Data",
                "label": "Test Condition",
                "insert_after": "custom_cppl_method",
            },
            {
                "fieldname": "custom_unit",
                "fieldtype": "Data",
                "label": "Unit",
                "insert_after": "custom_test_condition",
            },
            {
                "fieldname": "custom_result_type",
                "fieldtype": "Select",
                "label": "Result Type",
                "options": "\nNumeric\nQualitative\nManual Review",
                "insert_after": "custom_unit",
            },
            {
                "fieldname": "critical_test",
                "fieldtype": "Check",
                "label": "Critical Test",
                "insert_after": "custom_result_type",
                "default": "0",
            },
            {
                "fieldname": "custom_critical_test",
                "fieldtype": "Check",
                "label": "Legacy Critical Test",
                "insert_after": "critical_test",
                "default": "0",
                "hidden": 1,
            },
        ],
        "Quality Inspection": [
            {
                "fieldname": "custom_overall_result",
                "fieldtype": "Data",
                "label": "Overall Result",
                "insert_after": "status",
                "read_only": 1,
            },
        ],
        "Quality Inspection Reading": [
            {
                "fieldname": "custom_rm_testing_standard",
                "fieldtype": "Link",
                "label": "RM Testing Standard",
                "options": "RM Testing Standard",
                "insert_after": "value",
                "read_only": 1,
                "hidden": 1,
            },
            {
                "fieldname": "custom_unit",
                "fieldtype": "Data",
                "label": "Unit",
                "insert_after": "custom_rm_testing_standard",
                "read_only": 1,
            },
            {
                "fieldname": "custom_test_standard",
                "fieldtype": "Data",
                "label": "Test Standard",
                "insert_after": "custom_unit",
                "read_only": 1,
            },
            {
                "fieldname": "custom_cppl_method",
                "fieldtype": "Data",
                "label": "CPPL Method",
                "insert_after": "custom_test_standard",
                "read_only": 1,
            },
            {
                "fieldname": "custom_test_condition",
                "fieldtype": "Data",
                "label": "Test Condition",
                "insert_after": "custom_cppl_method",
                "read_only": 1,
            },
            {
                "fieldname": "custom_approval_rule",
                "fieldtype": "Data",
                "label": "Approval Rule",
                "insert_after": "custom_test_condition",
                "read_only": 1,
            },
            {
                "fieldname": "custom_critical_test",
                "fieldtype": "Check",
                "label": "Critical Test",
                "insert_after": "custom_approval_rule",
                "read_only": 1,
            },
            {
                "fieldname": "custom_result_label",
                "fieldtype": "Data",
                "label": "Result",
                "insert_after": "custom_critical_test",
                "read_only": 1,
            },
        ],
    }


def apply_rm_testing_context(doc, method=None):
    if doc.doctype != "Quality Inspection":
        return

    validate_rm_reference_source(doc)

    if not is_rm_quality_context(doc):
        return

    if not frappe.db.exists("DocType", "RM Testing Standard"):
        return

    if not doc.item_code or not doc.readings:
        return

    enforce_rm_template_structure(doc)

    standards = frappe.get_all(
        "RM Testing Standard",
        filters={"rm_item": doc.item_code, "is_active": 1},
        fields=[
            "name",
            "testing_type",
            "unit",
            "test_standard",
            "cppl_method",
            "test_condition",
            "approval_rule",
            "target_value",
            "acceptable_min",
            "acceptable_max",
        ],
        order_by="modified desc",
    )
    if not standards:
        return

    standards_by_spec = build_rm_standard_lookup(standards)
    parameter_flags = {
        row.name: row
        for row in frappe.get_all(
            "Quality Inspection Parameter",
            filters={"name": ("in", [row.testing_type for row in standards])},
            fields=["name", "critical_test", "custom_critical_test"],
            limit_page_length=0,
        )
    }
    manual_row_present = False
    pending_measurement_present = False
    row_statuses = []
    missing_measurement_rows = []
    missing_manual_status_rows = []
    is_submit = is_quality_inspection_submit(doc)

    for index, reading in enumerate(doc.readings, start=1):
        standard = get_matching_rm_standard(reading.specification, standards_by_spec)
        if not standard:
            continue
        parameter_meta = parameter_flags.get(standard.testing_type) or {}
        is_critical = get_parameter_critical_flag(parameter_meta)

        reading.custom_rm_testing_standard = standard.name
        reading.min_value = (
            get_numeric_limit(standard.acceptable_min)
            if standard.approval_rule in {"Numeric Range", "Minimum Only"}
            else None
        )
        reading.max_value = (
            get_numeric_limit(standard.acceptable_max)
            if standard.approval_rule in {"Numeric Range", "Maximum Only"}
            else None
        )
        reading.custom_unit = standard.unit
        reading.custom_test_standard = standard.test_standard
        reading.custom_cppl_method = standard.cppl_method
        reading.custom_test_condition = standard.test_condition
        reading.custom_approval_rule = standard.approval_rule
        reading.custom_target_value = standard.target_value
        reading.custom_critical_test = is_critical
        reading.numeric = 1 if standard.approval_rule in NUMERIC_APPROVAL_RULES else 0
        if standard.approval_rule == OBSERVATION_APPROVAL_RULE:
            reading.value = standard.target_value or ""
        has_measurement = reading_has_measurement(reading)

        if standard.approval_rule in MANUAL_APPROVAL_RULES:
            manual_row_present = True
            reading.manual_inspection = 1
            if not reading.value:
                reading.value = standard.target_value or standard.acceptable_max or standard.acceptable_min

            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            status_value = canonical_rm_row_status(reading.status)
            if is_submit:
                if not status_value:
                    reading.status = ""
                    reading.custom_result_label = ""
                    missing_manual_status_rows.append(format_rm_row_label(reading, index))
                    continue
                reading.status = status_value
            else:
                reading.status = status_value or "Review Required"

            row_statuses.append(reading.status)
            update_rm_result_label(reading)

        elif standard.approval_rule == REFERENCE_ONLY_APPROVAL_RULE:
            reading.manual_inspection = 0
            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            reading.status = "Accepted"
            row_statuses.append(reading.status)
            update_rm_result_label(reading)

        elif standard.approval_rule == NUMERIC_RECORDING_APPROVAL_RULE:
            reading.manual_inspection = 0
            sync_numeric_reading_fields(reading)
            normalize_numeric_recording_readings(reading)
            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            recording_status = evaluate_numeric_recording_row_status(reading)
            if not recording_status:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            reading.status = recording_status
            row_statuses.append(reading.status)
            update_rm_result_label(reading)

        elif standard.approval_rule == PASS_FAIL_APPROVAL_RULE:
            reading.manual_inspection = 0
            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            pass_fail_status = evaluate_pass_fail_row_status(reading)
            if not pass_fail_status:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            reading.status = pass_fail_status
            row_statuses.append(reading.status)
            update_rm_result_label(reading)

        elif standard.approval_rule == OBSERVATION_APPROVAL_RULE:
            reading.manual_inspection = 0
            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            observation_status = evaluate_observation_row_status(reading)
            if not observation_status:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            reading.status = observation_status
            row_statuses.append(reading.status)
            update_rm_result_label(reading)

        else:
            reading.manual_inspection = 0
            sync_numeric_reading_fields(reading)
            if not has_measurement:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            numeric_result = evaluate_numeric_row_status(reading)
            if numeric_result is None:
                reading.status = ""
                reading.custom_result_label = ""
                pending_measurement_present = True
                missing_measurement_rows.append(format_rm_row_label(reading, index))
                continue

            reading.status = "Accepted" if numeric_result else "Rejected"
            row_statuses.append(reading.status)
            update_rm_result_label(reading)


    if manual_row_present:
        doc.manual_inspection = 1
    else:
        doc.manual_inspection = 0

    parent_status = derive_rm_parent_status(row_statuses)
    if pending_measurement_present and parent_status not in ("Rejected", "Review Required"):
        parent_status = ""
    doc.status = parent_status

    if is_submit:
        validate_rm_submission_rows(missing_measurement_rows, missing_manual_status_rows)

    overall_result = derive_rm_overall_result(row_statuses, pending_measurement_present)

    doc.custom_overall_result = overall_result


@frappe.whitelist()
def refresh_draft_from_rm_testing_standard(quality_inspection):
    doc = frappe.get_doc("Quality Inspection", quality_inspection)
    doc.check_permission("write")

    if doc.docstatus != 0 or normalize_text(doc.status) == "Cancelled":
        frappe.throw(_("Only a draft Quality Inspection can be refreshed from RM Testing Standard."))
    if not is_rm_quality_context(doc):
        frappe.throw(_("This action is available only for Raw Material Quality Inspections."))

    apply_rm_testing_context(doc)
    active_standard_names = set(
        frappe.get_all(
            "RM Testing Standard",
            filters={"rm_item": doc.item_code, "is_active": 1},
            pluck="name",
            limit_page_length=0,
        )
    )
    missing_rows = [
        format_rm_row_label(reading, index)
        for index, reading in enumerate(doc.readings, start=1)
        if reading.custom_rm_testing_standard not in active_standard_names
    ]
    if missing_rows:
        frappe.throw(
            _("Current RM Testing Standard is missing for: {0}").format(", ".join(missing_rows[:10]))
        )

    doc.save()
    doc.reload()
    return {
        "quality_inspection": doc.name,
        "synchronized_parameters": len(doc.readings),
        "status": doc.status,
        "overall_result": doc.custom_overall_result,
    }


def sync_legacy_critical_test_flags():
    if not frappe.db.has_column("Quality Inspection Parameter", "critical_test"):
        return
    if not frappe.db.has_column("Quality Inspection Parameter", "custom_critical_test"):
        return

    for row in frappe.get_all(
        "Quality Inspection Parameter",
        filters={"custom_critical_test": 1},
        fields=["name", "critical_test"],
        limit_page_length=0,
    ):
        if not cint(row.critical_test):
            frappe.db.set_value(
                "Quality Inspection Parameter",
                row.name,
                "critical_test",
                1,
                update_modified=False,
            )


def ensure_rm_reading_grid_properties():
    properties = [
        ("min_value", "in_list_view", "1", "Check"),
        ("min_value", "read_only", "1", "Check"),
        ("max_value", "in_list_view", "1", "Check"),
        ("max_value", "read_only", "1", "Check"),
        ("status", "reqd", "0", "Check"),
        ("status", "options", "\nAccepted\nRejected\nReview Required", "Text"),
        (
            "status",
            "options",
            "\nPending\nAccepted\nRejected\nReview Required\nCancelled",
            "Text",
            "Quality Inspection",
        ),
        ("decision", "reqd", "0", "Check", "RM QC Decision"),
        ("decision", "options", "\nDeviation Required\nReturn to Supplier\nHold for Review", "Text", "RM QC Decision"),
        (
            "approval_status",
            "options",
            "\nDraft\nPending Operations Approval\nApproved\nRejected",
            "Text",
            "RM Deviation Approval",
        ),
        ("rm_qc_decision", "reqd", "0", "Check", "RM Release Note"),
        (
            "rm_qc_decision",
            "description",
            "Not required for directly accepted Incoming QC.",
            "Text",
            "RM Release Note",
        ),
    ]

    for property_row in properties:
        if len(property_row) == 4:
            fieldname, property_name, value, property_type = property_row
            doctype = "Quality Inspection Reading"
        else:
            fieldname, property_name, value, property_type, doctype = property_row
        ensure_property_setter(
            doctype=doctype,
            fieldname=fieldname,
            property_name=property_name,
            value=value,
            property_type=property_type,
        )


def ensure_property_setter(doctype, fieldname, property_name, value, property_type):
    existing_name = frappe.db.get_value(
        "Property Setter",
        {
            "doc_type": doctype,
            "field_name": fieldname,
            "property": property_name,
        },
        "name",
    )

    if existing_name:
        if frappe.db.get_value("Property Setter", existing_name, "value") != value:
            frappe.db.set_value("Property Setter", existing_name, "value", value, update_modified=False)
        return existing_name

    property_setter = make_property_setter(
        doctype=doctype,
        fieldname=fieldname,
        property=property_name,
        value=value,
        property_type=property_type,
        validate_fields_for_doctype=False,
    )
    return property_setter.name


def get_parameter_critical_flag(parameter_meta):
    return cint(parameter_meta.get("critical_test") or parameter_meta.get("custom_critical_test"))


def get_numeric_limit(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    return parse_float(value)


def derive_rm_parent_status(row_statuses):
    normalized_statuses = [normalize_text(status) for status in row_statuses if normalize_text(status)]
    if not normalized_statuses:
        return ""

    if "Rejected" in normalized_statuses:
        return "Rejected"

    if "Review Required" in normalized_statuses:
        return "Review Required"

    if all(status == "Accepted" for status in normalized_statuses):
        return "Accepted"

    return ""


def derive_rm_overall_result(row_statuses, has_incomplete=False):
    parent_status = derive_rm_parent_status(row_statuses)
    if has_incomplete and parent_status == "Accepted":
        return ""

    mapping = {
        "Accepted": "ACCEPTED",
        "Review Required": "REVIEW REQUIRED",
        "Rejected": "REJECTED",
    }
    return mapping.get(parent_status, "")


def canonical_rm_row_status(value):
    normalized = normalize_text(value).lower()
    mapping = {
        "accepted": "Accepted",
        "rejected": "Rejected",
        "review required": "Review Required",
    }
    return mapping.get(normalized, "")


def update_rm_result_label(reading):
    if reading.status == "Accepted":
        reading.custom_result_label = "PASS"
    elif reading.status == "Rejected":
        reading.custom_result_label = "FAIL"
    elif reading.status == "Review Required":
        reading.custom_result_label = "REVIEW REQUIRED"
    else:
        reading.custom_result_label = ""


def format_rm_row_label(reading, index):
    specification = normalize_text(reading.get("specification")) or _("Parameter")
    return _("Row #{0} {1}").format(index, specification)


def validate_rm_submission_rows(missing_measurement_rows, missing_manual_status_rows):
    if not missing_measurement_rows and not missing_manual_status_rows:
        return

    messages = []
    if missing_measurement_rows:
        messages.append(
            _("Complete all required RM test readings before submitting: {0}").format(
                ", ".join(missing_measurement_rows[:10])
            )
        )
    if missing_manual_status_rows:
        messages.extend(
            _("{0}: Status is mandatory. Please enter reading/status.").format(row_label)
            for row_label in missing_manual_status_rows[:10]
        )

    frappe.throw("<br>".join(messages))


def is_quality_inspection_submit(doc):
    return normalize_text(getattr(doc, "_action", "")).lower() == "submit"


def get_rm_quality_template(doc):
    template_name = normalize_text(doc.quality_inspection_template)
    if template_name:
        return template_name

    if doc.item_code:
        return frappe.db.get_value("Item", doc.item_code, "quality_inspection_template")

    return ""


def get_rm_template_rows(doc):
    template_name = get_rm_quality_template(doc)
    if not template_name:
        return []

    from erpnext.stock.doctype.quality_inspection_template.quality_inspection_template import (
        get_template_details,
    )

    return get_template_details(template_name)


def build_rm_template_lookup(template_rows):
    lookup = {}
    for row in template_rows:
        for key in get_rm_matching_keys(row.specification):
            lookup.setdefault(key, row)
    return lookup


def get_matching_rm_template_row(specification, template_lookup):
    for key in get_rm_matching_keys(specification):
        if key in template_lookup:
            return template_lookup[key]

    return None


def enforce_rm_template_structure(doc):
    template_rows = get_rm_template_rows(doc)
    if not template_rows:
        return

    template_lookup = build_rm_template_lookup(template_rows)
    expected_specs = [normalize_text(row.specification) for row in template_rows]
    matched_specs = []
    invalid_specs = []

    for reading in doc.readings:
        template_row = get_matching_rm_template_row(reading.specification, template_lookup)
        if not template_row:
            invalid_specs.append(reading.specification or _("(blank row)"))
            continue

        canonical_specification = template_row.specification
        matched_specs.append(normalize_text(canonical_specification))

        reading.specification = canonical_specification
        reading.numeric = cint(template_row.numeric)
        reading.min_value = get_numeric_limit(template_row.min_value)
        reading.max_value = get_numeric_limit(template_row.max_value)
        reading.value = template_row.value
        reading.acceptance_formula = template_row.acceptance_formula
        reading.formula_based_criteria = cint(template_row.formula_based_criteria)

    if invalid_specs:
        frappe.throw(
            _("RM inspection parameters must come from the linked Quality Inspection Template. Invalid row(s): {0}").format(
                ", ".join(invalid_specs)
            )
        )

    missing_specs = []
    duplicates = []
    matched_counts = defaultdict(int)
    for spec in matched_specs:
        matched_counts[spec] += 1

    expected_counts = defaultdict(int)
    for spec in expected_specs:
        expected_counts[spec] += 1

    for spec, expected_count in expected_counts.items():
        actual_count = matched_counts.get(spec, 0)
        if actual_count == 0:
            missing_specs.append(spec)
        elif actual_count > expected_count:
            duplicates.append(spec)

    if len(doc.readings) != len(template_rows) or missing_specs or duplicates:
        details = []
        if missing_specs:
            details.append(
                _("Missing template parameter(s): {0}").format(", ".join(sorted(set(missing_specs))))
            )
        if duplicates:
            details.append(
                _("Duplicate parameter(s): {0}").format(", ".join(sorted(set(duplicates))))
            )
        if len(doc.readings) != len(template_rows):
            details.append(
                _("Template row count is {0}, but inspection row count is {1}.").format(
                    len(template_rows), len(doc.readings)
                )
            )

        frappe.throw(
            _("RM inspection rows must match the linked Quality Inspection Template exactly.")
            + ("<br>" + "<br>".join(details) if details else "")
        )


def build_rm_standard_lookup(standards):
    lookup = {}
    for row in standards:
        for key in get_rm_matching_keys(row.testing_type):
            lookup.setdefault(key, row)
    return lookup


def get_matching_rm_standard(specification, standards_by_spec):
    for key in get_rm_matching_keys(specification):
        if key in standards_by_spec:
            return standards_by_spec[key]

    return None


def build_rm_context_payload(row):
    return {
        "testing_type": row.testing_type,
        "min_value": (
            get_numeric_limit(row.acceptable_min)
            if row.approval_rule in {"Numeric Range", "Minimum Only"}
            else None
        ),
        "max_value": (
            get_numeric_limit(row.acceptable_max)
            if row.approval_rule in {"Numeric Range", "Maximum Only"}
            else None
        ),
        "approval_rule": row.approval_rule,
        "unit": row.unit,
        "numeric": 1 if row.approval_rule in NUMERIC_APPROVAL_RULES else 0,
    }


def add_rm_context_aliases(result, key, payload):
    normalized = normalize_text(key)
    if not normalized:
        return

    aliases = [normalized]
    lower = normalized.lower()
    if lower not in aliases:
        aliases.append(lower)

    for alias in get_rm_matching_keys(normalized):
        if alias and alias not in aliases:
            aliases.append(alias)

    for alias in aliases:
        result[alias] = payload


def get_rm_matching_keys(value):
    normalized = normalize_text(value).lower()
    if not normalized:
        return []

    collapsed = collapse_rm_matching_text(normalized)
    aliases = [normalized]

    if collapsed and collapsed not in aliases:
        aliases.append(collapsed)

    without_brackets = normalize_text(re.sub(r"\([^)]*\)", " ", normalized)).lower()
    if without_brackets and without_brackets not in aliases:
        aliases.append(without_brackets)

    collapsed_without_brackets = collapse_rm_matching_text(without_brackets)
    if collapsed_without_brackets and collapsed_without_brackets not in aliases:
        aliases.append(collapsed_without_brackets)

    return aliases


def collapse_rm_matching_text(value):
    if not value:
        return ""

    collapsed = re.sub(r"\([^)]*\)", " ", value)
    collapsed = re.sub(r"[^a-z0-9]+", " ", collapsed)
    collapsed = normalize_text(collapsed).lower()

    replacements = [
        (r"\bcontent\b", ""),
        (r"\bvalues\b", ""),
        (r"\bgm\b", ""),
        (r"\bmin\b", ""),
        (r"\bpercentage\b", ""),
    ]
    for pattern, replacement in replacements:
        collapsed = re.sub(pattern, replacement, collapsed)

    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    return collapsed


def has_rm_input_value(value):
    if value is None:
        return False
    if value == 0 and not isinstance(value, bool):
        return True
    return bool(normalize_text(value))


def reading_has_measurement(reading):
    if has_rm_input_value(reading.reading_value):
        return True

    if has_rm_input_value(reading.get("value")):
        return True

    return any(has_rm_input_value(reading.get(f"reading_{index}")) for index in range(1, 11))


def sync_numeric_reading_fields(reading):
    primary_value_present = has_rm_input_value(reading.reading_value)
    has_series_value = any(has_rm_input_value(reading.get(f"reading_{index}")) for index in range(1, 11))

    if primary_value_present and not has_series_value and reading.get("reading_1") != reading.reading_value:
        reading.reading_1 = reading.reading_value


def normalize_numeric_recording_readings(reading):
    for fieldname in ["reading_value", *(f"reading_{index}" for index in range(1, 11))]:
        value = reading.get(fieldname)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(reading, fieldname, cstr(value))


def get_numeric_measurements(reading):
    sync_numeric_reading_fields(reading)
    measurements = []

    for index in range(1, 11):
        raw_value = reading.get(f"reading_{index}")
        if not has_rm_input_value(raw_value):
            continue
        numeric_value = get_numeric_limit(raw_value)
        if numeric_value is None:
            return None
        measurements.append(numeric_value)

    if measurements:
        return measurements

    primary_value = reading.reading_value
    if not has_rm_input_value(primary_value):
        return []

    numeric_value = get_numeric_limit(primary_value)
    if numeric_value is None:
        return None

    return [numeric_value]


def get_observation_values(reading):
    values = []
    for fieldname in [*(f"reading_{index}" for index in range(1, 11)), "reading_value"]:
        raw_value = reading.get(fieldname)
        if has_rm_input_value(raw_value):
            values.append(raw_value)
    return values


def evaluate_observation_row_status(reading):
    results = [evaluate_observation(value) for value in get_observation_values(reading)]
    if not results or any(not result for result in results):
        return ""
    if "Rejected" in results:
        return "Rejected"
    if all(result == "Accepted" for result in results):
        return "Accepted"
    return ""


def evaluate_pass_fail_row_status(reading):
    results = [evaluate_pass_fail(value) for value in get_observation_values(reading)]
    if not results or any(not result for result in results):
        return ""
    if "Rejected" in results:
        return "Rejected"
    return "Accepted" if all(result == "Accepted" for result in results) else ""


def evaluate_numeric_recording_row_status(reading):
    measurements = get_numeric_measurements(reading)
    return "Accepted" if measurements else ""


def evaluate_numeric_row_status(reading):
    min_value = get_numeric_limit(reading.min_value)
    max_value = get_numeric_limit(reading.max_value)
    if min_value is None and max_value is None:
        return True if reading_has_measurement(reading) else None

    measurements = get_numeric_measurements(reading)
    if measurements is None or not measurements:
        return None

    return all(
        (min_value is None or value >= min_value)
        and (max_value is None or value <= max_value)
        for value in measurements
    )


def validate_rm_reference_source(doc):
    if doc.inspection_type != "Incoming" or doc.reference_type != "Purchase Invoice":
        if (
            doc.inspection_type == "Incoming"
            and doc.reference_type == "Purchase Receipt"
            and is_rm_quality_context(doc)
            and doc.reference_name
            and doc.item_code
        ):
            if doc.name and frappe.db.exists(
                "Quality Inspection",
                {
                    "name": doc.name,
                    "reference_type": "Purchase Receipt",
                    "reference_name": doc.reference_name,
                    "item_code": doc.item_code,
                    "batch_no": doc.batch_no or "",
                    "docstatus": ("<", 2),
                },
            ):
                return

            from calco_erp.calco_quality.quality_inspection_queries import (
                get_pending_rm_reference_validation_message,
                is_pending_rm_purchase_receipt_candidate,
            )

            if not is_pending_rm_purchase_receipt_candidate(
                doc.reference_name,
                doc.item_code,
                doc.batch_no,
            ):
                frappe.throw(
                    get_pending_rm_reference_validation_message(
                        doc.reference_name,
                        doc.item_code,
                        doc.batch_no,
                    )
                )
        return

    if not is_rm_quality_context(doc):
        return

    frappe.throw(_("Raw Material incoming Quality Inspection must use Purchase Receipt as the Reference Type."))


def is_rm_quality_context(doc):
    if (doc.quality_inspection_template or "").startswith("Calco RM QC -"):
        return True

    if doc.item_code and frappe.db.exists("RM Testing Standard", {"rm_item": doc.item_code, "is_active": 1}):
        return True

    return False


@frappe.whitelist()
def get_rm_reading_context(item_code, specifications=None):
    if not item_code:
        return {}

    if isinstance(specifications, str):
        specifications = frappe.parse_json(specifications)

    requested_specs = {normalize_text(spec) for spec in (specifications or []) if normalize_text(spec)}

    standards = frappe.get_all(
        "RM Testing Standard",
        filters={"rm_item": item_code, "is_active": 1},
        fields=[
            "testing_type",
            "acceptable_min",
            "acceptable_max",
            "approval_rule",
            "unit",
        ],
        limit_page_length=0,
        order_by="modified desc",
    )

    standards_by_spec = build_rm_standard_lookup(standards)
    result = {}

    if requested_specs:
        for spec in requested_specs:
            row = get_matching_rm_standard(spec, standards_by_spec)
            if not row:
                continue

            payload = build_rm_context_payload(row)
            add_rm_context_aliases(result, spec, payload)
            add_rm_context_aliases(result, row.testing_type, payload)
    else:
        for row in standards:
            payload = build_rm_context_payload(row)
            add_rm_context_aliases(result, row.testing_type, payload)

    return result
