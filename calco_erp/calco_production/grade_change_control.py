from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt, get_datetime, getdate, now_datetime, time_diff_in_seconds

from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION


CLEARANCE_DOCTYPE = "Grade Change Clearance"
CHECKLIST_DOCTYPE = "Job Card Grade Change Checklist"
CONTROLLED_DOCUMENT = "F-GCL-01"
CONTROLLED_REVISION = "REV-02"

CLASSIFICATIONS = ("Natural", "Semi Dark", "Dark")
STATUS_PENDING_EVALUATION = "Pending Evaluation"
STATUS_DRAFT_CHECKLIST = "Draft Checklist"
STATUS_IN_PROGRESS = "In Progress"
STATUS_PENDING_APPROVAL = "Pending Approval"
STATUS_RESET_APPROVAL_REQUIRED = "Reset Approval Required"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_SUPERSEDED = "Superseded"
STATUS_OPTIONS = (
    STATUS_PENDING_EVALUATION,
    STATUS_DRAFT_CHECKLIST,
    STATUS_IN_PROGRESS,
    STATUS_PENDING_APPROVAL,
    STATUS_RESET_APPROVAL_REQUIRED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
)

TRANSITION_LEVELS = {
    ("Dark", "Natural"): "A",
    ("Dark", "Semi Dark"): "B",
    ("Natural", "Dark"): "B",
    ("Natural", "Semi Dark"): "B",
    ("Natural", "Natural"): "C",
    ("Semi Dark", "Semi Dark"): "C",
    ("Dark", "Dark"): "C",
    ("Semi Dark", "Natural"): "A",
    ("Semi Dark", "Dark"): "C",
}

LEVEL_DESCRIPTIONS = {
    "A": "Critical",
    "B": "Semi Critical",
    "C": "Smooth",
}

CLEANING_CODE_DESCRIPTIONS = {
    0: "Cleaning Not Required",
    1: "Emptying / Replacing If Required",
    2: "Emptying / Clean With Air / Cloth",
    3: "Emptying / Wash With Water And Dry Cloth",
}

CHECKLIST_MATRIX = (
    (1, "MIXER / MIXER AREA", 3, 2, 2),
    (2, "RM LOADING STATION", 2, 1, 1),
    (3, "PREMIX CONVEYING TROLLIES", 3, 2, 2),
    (4, "RM SILOS", 2, 1, 1),
    (5, "PRIMIX SILO/FEEDER", 3, 2, 1),
    (6, "FEEDERS", 2, 1, 1),
    (7, "BELLOWS", 3, 2, 1),
    (8, "RM FEED HOOPER AND MAIN FEED PORT", 3, 2, 2),
    (9, "DE-GASING AND VACUUM VENT", 3, 0, 0),
    (10, "SCREEN PACK AND BREAKER PLATE", 1, 1, 1),
    (11, "WATER BATH", 3, 0, 0),
    (12, "PELLETIZER AND CLASSIFIER", 2, 2, 2),
    (13, "VENTURE, INSIDE MAGNET AND F.G. TRAYS", 2, 2, 2),
    (14, "F.G. CONVEYING PIPE LINE/FILTER", 2, 2, 2),
    (15, "F.G. SILOS AND ITS MAGNET", 2, 2, 2),
    (16, "PACKING M/C AND PACKING AREA", 2, 2, 2),
)


def options(values: Iterable[str]) -> str:
    return "\n" + "\n".join(values)


def ensure_grade_change_control_setup():
    if not frappe.db.exists("DocType", "Item"):
        return
    create_custom_fields(
        {
            "Item": [
                {
                    "fieldname": "custom_fg_grade_classification",
                    "label": "FG Grade Classification",
                    "fieldtype": "Select",
                    "options": options(CLASSIFICATIONS),
                    "insert_after": "item_group",
                    "description": "Suggestion only for F-GCL-01. Engineer confirmation remains authoritative.",
                }
            ],
            "Work Order": [
                {
                    "fieldname": "custom_grade_change_clearance",
                    "label": "Grade Change Clearance",
                    "fieldtype": "Link",
                    "options": CLEARANCE_DOCTYPE,
                    "insert_after": "custom_grade_change_required",
                    "read_only": 1,
                    "search_index": 1,
                }
            ],
            "Job Card": [
                {
                    "fieldname": "custom_grade_change_clearance",
                    "label": "Grade Change Clearance",
                    "fieldtype": "Link",
                    "options": CLEARANCE_DOCTYPE,
                    "insert_after": "custom_grade_change_status",
                    "read_only": 1,
                    "search_index": 1,
                }
            ],
            CLEARANCE_DOCTYPE: _clearance_custom_fields(),
            CHECKLIST_DOCTYPE: _checklist_custom_fields(),
        },
        update=True,
    )
    frappe.clear_cache(doctype="Item")
    frappe.clear_cache(doctype="Work Order")
    frappe.clear_cache(doctype="Job Card")
    frappe.clear_cache(doctype=CLEARANCE_DOCTYPE)
    frappe.clear_cache(doctype=CHECKLIST_DOCTYPE)


def _clearance_custom_fields() -> list[dict[str, Any]]:
    fields = [
        ("authority_section", "F-GCL-01 Grade Change Authority", "Section Break", None, None),
        ("work_order", "Work Order", "Link", "Work Order", "authority_section"),
        ("erpnext_job_card", "Compounding Job Card", "Link", "Job Card", "work_order"),
        ("production_line", "Production Line / Workstation", "Link", "Workstation", "erpnext_job_card"),
        ("system_evidence_section", "Immutable System Evidence", "Section Break", None, "production_line"),
        ("previous_erp_evidence_status", "Previous ERP Evidence", "Data", None, "system_evidence_section"),
        ("previous_erp_work_order", "Previous ERP Work Order", "Link", "Work Order", "previous_erp_evidence_status"),
        ("previous_erp_job_card", "Previous Compounding Job Card", "Link", "Job Card", "previous_erp_work_order"),
        ("previous_erp_fg_item", "Previous FG Item", "Link", "Item", "previous_erp_job_card"),
        ("previous_erp_fg_batch", "Previous FG Batch", "Link", "Batch", "previous_erp_fg_item"),
        ("previous_erp_completed_on", "Previous Production Completed On", "Datetime", None, "previous_erp_fg_batch"),
        ("suggested_previous_classification", "Suggested Previous Classification", "Select", options(CLASSIFICATIONS), "previous_erp_completed_on"),
        ("evidence_column_break", "", "Column Break", None, "suggested_previous_classification"),
        ("current_fg_item", "Current FG Item", "Link", "Item", "evidence_column_break"),
        ("suggested_current_classification", "Suggested Current Classification", "Select", options(CLASSIFICATIONS), "current_fg_item"),
        ("evidence_initialized_by", "Evidence Initialized By", "Link", "User", "suggested_current_classification"),
        ("evidence_initialized_on", "Evidence Initialized On", "Datetime", None, "evidence_initialized_by"),
        ("engineer_decision_section", "Production Engineer Classification Decision", "Section Break", None, "evidence_initialized_on"),
        ("actual_previous_classification", "Actual Previous Classification", "Select", options(CLASSIFICATIONS), "engineer_decision_section"),
        ("previous_override", "Previous Suggestion Overridden", "Check", None, "actual_previous_classification"),
        ("previous_override_reason", "Previous Classification Override Reason", "Small Text", None, "previous_override"),
        ("previous_classification_changed_by", "Previous Classification Changed By", "Link", "User", "previous_override_reason"),
        ("previous_classification_changed_on", "Previous Classification Changed On", "Datetime", None, "previous_classification_changed_by"),
        ("classification_column_break", "", "Column Break", None, "previous_classification_changed_on"),
        ("actual_current_classification", "Actual Current Classification", "Select", options(CLASSIFICATIONS), "classification_column_break"),
        ("current_override", "Current Suggestion Overridden", "Check", None, "actual_current_classification"),
        ("current_override_reason", "Current Classification Override Reason", "Small Text", None, "current_override"),
        ("current_classification_changed_by", "Current Classification Changed By", "Link", "User", "current_override_reason"),
        ("current_classification_changed_on", "Current Classification Changed On", "Datetime", None, "current_classification_changed_by"),
        ("classification_confirmed_by", "Classifications Confirmed By", "Link", "User", "current_classification_changed_on"),
        ("classification_confirmed_on", "Classifications Confirmed On", "Datetime", None, "classification_confirmed_by"),
        ("evaluation_section", "Server-Derived F-GCL-01 Evaluation", "Section Break", None, "classification_confirmed_on"),
        ("evaluation_summary_html", "", "HTML", None, "evaluation_section"),
        ("transition", "Grade Transition", "Data", None, "evaluation_summary_html"),
        ("cleaning_level", "Cleaning Level", "Select", options(("A", "B", "C")), "transition"),
        ("cleaning_level_description", "Cleaning Level Description", "Data", None, "cleaning_level"),
        ("controlled_document_no", "Controlled Document No.", "Data", None, "cleaning_level_description"),
        ("controlled_revision", "Controlled Revision", "Data", None, "controlled_document_no"),
        ("evaluation_revision", "Evaluation Revision", "Int", None, "controlled_revision"),
        ("checklist_generated_by", "Checklist Generated By", "Link", "User", "evaluation_revision"),
        ("checklist_generated_on", "Checklist Generated On", "Datetime", None, "checklist_generated_by"),
        ("regeneration_reason", "Checklist Regeneration Reason", "Small Text", None, "checklist_generated_on"),
        ("not_required", "Not Required Exception", "Check", None, "regeneration_reason"),
        ("not_required_reason", "Not Required Reason", "Small Text", None, "not_required"),
        ("checklist_section", "F-GCL-01 REV-02 Checklist", "Section Break", None, "not_required_reason"),
        ("cleaning_code_legend_html", "", "HTML", None, "checklist_section"),
        ("checklist", "Grade Change Checklist", "Table", CHECKLIST_DOCTYPE, "cleaning_code_legend_html"),
        ("additional_controls_section", "Additional F-GCL-01 Controls", "Section Break", None, "checklist"),
        ("line_material_removed", "Raw Material / Loose Bag / Lumps / Rejection Removed", "Check", None, "additional_controls_section"),
        ("line_material_removed_observation", "Material Removal Observation", "Small Text", None, "line_material_removed"),
        ("line_material_removed_by", "Material Removal Confirmed By", "Link", "User", "line_material_removed_observation"),
        ("line_material_removed_on", "Material Removal Confirmed On", "Datetime", None, "line_material_removed_by"),
        ("controls_column_break", "", "Column Break", None, "line_material_removed_on"),
        ("feeder_connection_confirmed", "Feeder Connection With Silo As Per MPDS", "Check", None, "controls_column_break"),
        ("feeder_connection_observation", "Feeder Connection Observation", "Small Text", None, "feeder_connection_confirmed"),
        ("feeder_connection_confirmed_by", "Feeder Connection Confirmed By", "Link", "User", "feeder_connection_observation"),
        ("feeder_connection_confirmed_on", "Feeder Connection Confirmed On", "Datetime", None, "feeder_connection_confirmed_by"),
        ("cotton_cloth_available", "Cotton Cloth Available", "Select", options(("Yes", "No")), "feeder_connection_confirmed_on"),
        ("cotton_cloth_observation", "Cotton Cloth Observation", "Small Text", None, "cotton_cloth_available"),
        ("cotton_cloth_confirmed_by", "Cotton Cloth Confirmed By", "Link", "User", "cotton_cloth_observation"),
        ("cotton_cloth_confirmed_on", "Cotton Cloth Confirmed On", "Datetime", None, "cotton_cloth_confirmed_by"),
        ("completion_section", "Inspection and Approval", "Section Break", None, "cotton_cloth_confirmed_on"),
        ("execution_started_on", "Execution Started On", "Datetime", None, "completion_section"),
        ("execution_completed_on", "Execution Completed On", "Datetime", None, "execution_started_on"),
        ("time_taken_minutes", "Time Taken (Minutes)", "Float", None, "execution_completed_on"),
        ("inspected_by", "Inspected By", "Link", "User", "time_taken_minutes"),
        ("inspected_on", "Inspected On", "Datetime", None, "inspected_by"),
        ("reset_section", "Audit / Revision Details", "Section Break", None, "approved_on"),
        ("audit_details_html", "", "HTML", None, "reset_section"),
        ("reset_reason", "Reset Reason", "Small Text", None, "audit_details_html"),
        ("reset_from_status", "Reset From Status", "Data", None, "reset_reason"),
        ("reset_requested_by", "Reset Requested By", "Link", "User", "reset_from_status"),
        ("reset_requested_on", "Reset Requested On", "Datetime", None, "reset_requested_by"),
        ("reset_approved_by", "Reset Approved By", "Link", "User", "reset_requested_on"),
        ("reset_approved_on", "Reset Approved On", "Datetime", None, "reset_approved_by"),
        ("reset_rejection_reason", "Reset Rejection Reason", "Small Text", None, "reset_approved_on"),
        ("rejection_reason", "Rejection Reason", "Small Text", None, "remarks"),
    ]
    read_only = {
        "production_line", "previous_erp_evidence_status", "previous_erp_work_order", "previous_erp_job_card",
        "previous_erp_fg_item", "previous_erp_fg_batch", "previous_erp_completed_on", "suggested_previous_classification",
        "current_fg_item", "suggested_current_classification", "evidence_initialized_by", "evidence_initialized_on",
        "previous_override", "previous_classification_changed_by", "previous_classification_changed_on", "current_override",
        "current_classification_changed_by", "current_classification_changed_on", "classification_confirmed_by",
        "classification_confirmed_on", "transition", "cleaning_level", "cleaning_level_description", "controlled_document_no",
        "controlled_revision", "evaluation_revision", "checklist_generated_by", "checklist_generated_on",
        "line_material_removed_by", "line_material_removed_on", "feeder_connection_confirmed_by",
        "feeder_connection_confirmed_on", "cotton_cloth_confirmed_by", "cotton_cloth_confirmed_on", "inspected_by",
        "execution_started_on", "execution_completed_on", "time_taken_minutes", "inspected_on",
        "reset_reason", "reset_from_status", "reset_requested_by", "reset_requested_on", "reset_approved_by",
        "reset_approved_on", "reset_rejection_reason", "rejection_reason",
    }
    required = {"work_order", "erpnext_job_card"}
    collapsed_sections = {"system_evidence_section", "reset_section"}
    result = []
    for fieldname, label, fieldtype, field_options, insert_after in fields:
        value = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "insert_after": insert_after}
        if field_options:
            value["options"] = field_options
        if fieldname in read_only:
            value["read_only"] = 1
        if fieldname in required:
            value["reqd"] = 1
        if fieldname in collapsed_sections:
            value["collapsible"] = 1
        if fieldname in {"work_order", "erpnext_job_card"}:
            value["search_index"] = 1
            value["in_list_view"] = 1
        if fieldname == "evaluation_revision":
            value["default"] = "1"
        if fieldname == "controlled_document_no":
            value["default"] = CONTROLLED_DOCUMENT
        if fieldname == "controlled_revision":
            value["default"] = CONTROLLED_REVISION
        if fieldname in {"line_material_removed", "feeder_connection_confirmed"}:
            value["default"] = "0"
        result.append(value)
    return result


def _checklist_custom_fields() -> list[dict[str, Any]]:
    return [
        {"fieldname":"sequence","label":"Sequence","fieldtype":"Int","insert_after":"check_item","read_only":1,"in_list_view":1},
        {"fieldname":"cleaning_level","label":"Cleaning Level","fieldtype":"Data","insert_after":"sequence","read_only":1,"in_list_view":1},
        {"fieldname":"required_cleaning_code","label":"Required Cleaning Code","fieldtype":"Int","insert_after":"cleaning_level","read_only":1,"in_list_view":1},
        {"fieldname":"cleaning_code_description","label":"Cleaning Code Description","fieldtype":"Data","insert_after":"required_cleaning_code","read_only":1,"in_list_view":1},
        {"fieldname":"observation","label":"Observation","fieldtype":"Small Text","insert_after":"cleaning_code_description","in_list_view":1},
        {"fieldname":"evaluation_revision","label":"Evaluation Revision","fieldtype":"Int","insert_after":"checked_on","read_only":1},
        {"fieldname":"evaluation_state","label":"Evaluation State","fieldtype":"Select","options":"Current\nSuperseded","insert_after":"evaluation_revision","read_only":1},
        {"fieldname":"superseded","label":"Superseded","fieldtype":"Check","insert_after":"evaluation_state","read_only":1},
        {"fieldname":"snapshot_previous_classification","label":"Snapshot Previous Classification","fieldtype":"Data","insert_after":"superseded","read_only":1},
        {"fieldname":"snapshot_current_classification","label":"Snapshot Current Classification","fieldtype":"Data","insert_after":"snapshot_previous_classification","read_only":1},
        {"fieldname":"snapshot_transition","label":"Snapshot Transition","fieldtype":"Data","insert_after":"snapshot_current_classification","read_only":1},
        {"fieldname":"snapshot_generated_by","label":"Snapshot Generated By","fieldtype":"Link","options":"User","insert_after":"snapshot_transition","read_only":1},
        {"fieldname":"snapshot_generated_on","label":"Snapshot Generated On","fieldtype":"Datetime","insert_after":"snapshot_generated_by","read_only":1},
        {"fieldname":"evaluation_snapshot_json","label":"Superseded Evaluation Snapshot","fieldtype":"Code","options":"JSON","insert_after":"snapshot_generated_on","read_only":1},
    ]


def validate_item_grade_classification(doc, method=None):
    if not doc.meta.has_field("custom_fg_grade_classification"):
        return
    before = doc.get_doc_before_save() if not doc.is_new() else None
    old_value = before.get("custom_fg_grade_classification") if before else None
    new_value = doc.get("custom_fg_grade_classification")
    if old_value == new_value:
        return
    if not _has_any_role("Technical User", "System Manager"):
        frappe.throw(_("Only Technical User or System Manager may edit FG Grade Classification."), frappe.PermissionError)


def derive_cleaning_level(previous_classification: str, current_classification: str) -> str:
    key = (cstr(previous_classification).strip(), cstr(current_classification).strip())
    level = TRANSITION_LEVELS.get(key)
    if not level:
        frappe.throw(_("Unsupported grade transition: {0} to {1}.").format(key[0] or "blank", key[1] or "blank"))
    return level


def get_checklist_template(level: str) -> list[dict[str, Any]]:
    if level not in LEVEL_DESCRIPTIONS:
        frappe.throw(_("Cleaning Level must be server-derived before checklist generation."))
    level_index = {"A": 2, "B": 3, "C": 4}[level]
    return [
        {
            "sequence": sequence,
            "check_item": checkpoint,
            "cleaning_level": level,
            "required_cleaning_code": row[level_index],
            "cleaning_code_description": CLEANING_CODE_DESCRIPTIONS[row[level_index]],
        }
        for row in CHECKLIST_MATRIX
        for sequence, checkpoint in [(row[0], row[1])]
    ]


def populate_system_evidence(doc):
    if doc.get("evidence_initialized_on"):
        return
    work_order, job_card = _resolve_work_order_and_job_card(doc)
    doc.work_order = work_order.name
    doc.erpnext_job_card = job_card.name
    doc.production_line = (
        job_card.get("custom_production_line")
        or job_card.get("workstation")
        or work_order.get("custom_production_line")
        or work_order.get("custom_machine")
    )
    doc.current_fg_item = work_order.get("production_item")
    doc.suggested_current_classification = _item_classification(doc.current_fg_item)

    previous = get_previous_physical_production(
        production_line=doc.production_line,
        current_work_order=work_order.name,
    )
    if previous:
        doc.previous_erp_work_order = previous.get("work_order")
        doc.previous_erp_job_card = previous.get("job_card")
        doc.previous_erp_fg_item = previous.get("fg_item")
        doc.previous_erp_fg_batch = previous.get("fg_batch")
        doc.previous_erp_completed_on = previous.get("completed_on")
        doc.previous_erp_evidence_status = "Previous ERP Physical Production Found"
        doc.suggested_previous_classification = _item_classification(previous.get("fg_item"))
    else:
        doc.previous_erp_evidence_status = "No Previous ERP Production Evidence"
    doc.evidence_initialized_by = frappe.session.user
    doc.evidence_initialized_on = now_datetime()


def get_previous_physical_production(production_line: str, current_work_order: str) -> dict[str, Any] | None:
    if not production_line:
        return None
    rows = frappe.db.sql(
        """
        select
            jc.name as job_card,
            jc.work_order,
            wo.production_item as fg_item,
            coalesce(jc.custom_fg_batch_no, wo.custom_fg_batch_no, '') as fg_batch,
            coalesce(jc.actual_end_date, jc.modified) as completed_on
        from `tabJob Card` jc
        inner join `tabWork Order` wo on wo.name = jc.work_order
        where jc.docstatus = 1
          and wo.docstatus = 1
          and jc.operation = %s
          and coalesce(jc.total_completed_qty, 0) > 0
          and jc.work_order != %s
          and (
              jc.workstation = %s
              or jc.custom_production_line = %s
              or wo.custom_production_line = %s
          )
        order by coalesce(jc.actual_end_date, jc.modified) desc, jc.creation desc
        limit 1
        """,
        (COMPOUNDING_OPERATION, current_work_order, production_line, production_line, production_line),
        as_dict=True,
    )
    return rows[0] if rows else None


def validate_clearance(doc, method=None):
    doc.status = doc.status or STATUS_PENDING_EVALUATION
    if doc.is_new():
        populate_system_evidence(doc)
        doc.controlled_document_no = CONTROLLED_DOCUMENT
        doc.controlled_revision = CONTROLLED_REVISION
        doc.evaluation_revision = cint(doc.evaluation_revision) or 1

    _validate_immutable_evidence(doc)
    _validate_protected_audit_fields(doc)
    _validate_server_derived_fields(doc)
    _validate_superseded_rows_immutable(doc)
    _validate_checklist_row_identity_and_order(doc)
    _validate_classification_lock(doc)
    _mark_execution_metadata(doc)
    _validate_negative_observations(doc)
    _validate_status_transition(doc)
    _validate_action_state(doc)


def _validate_immutable_evidence(doc):
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or not before.get("evidence_initialized_on"):
        return
    fields = (
        "work_order", "erpnext_job_card", "production_line", "current_fg_item",
        "suggested_current_classification", "previous_erp_evidence_status",
        "previous_erp_work_order", "previous_erp_job_card", "previous_erp_fg_item",
        "previous_erp_fg_batch", "previous_erp_completed_on", "suggested_previous_classification",
        "evidence_initialized_by", "evidence_initialized_on", "controlled_document_no",
        "controlled_revision",
    )
    if any(not _field_values_equal(doc.meta, fieldname, before, doc) for fieldname in fields):
        frappe.throw(_("Grade Change system evidence is immutable after clearance initialization."))


def _validate_protected_audit_fields(doc):
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or getattr(doc.flags, "grade_change_action", None):
        return
    fields = (
        "classification_confirmed_by", "classification_confirmed_on",
        "previous_classification_changed_by", "previous_classification_changed_on",
        "current_classification_changed_by", "current_classification_changed_on",
        "checklist_generated_by", "checklist_generated_on", "inspected_by", "inspected_on",
        "execution_started_on", "execution_completed_on", "time_taken_minutes",
        "approved_by", "approved_on", "reset_reason", "reset_from_status", "reset_requested_by", "reset_requested_on",
        "reset_approved_by", "reset_approved_on", "reset_rejection_reason", "rejection_reason",
    )
    if any(not _field_values_equal(doc.meta, fieldname, before, doc) for fieldname in fields):
        frappe.throw(_("Grade Change audit fields are server-controlled."))


def _field_values_equal(meta, fieldname: str, stored, incoming) -> bool:
    df = meta.get_field(fieldname)
    fieldtype = df.fieldtype if df else "Data"
    return _canonical_field_value(stored.get(fieldname), fieldtype) == _canonical_field_value(
        incoming.get(fieldname), fieldtype
    )


def _canonical_field_value(value, fieldtype: str):
    if fieldtype == "Datetime":
        return get_datetime(value) if value not in (None, "") else None
    if fieldtype == "Date":
        return getdate(value) if value not in (None, "") else None
    if fieldtype in {"Check", "Int"}:
        return cint(value)
    if fieldtype in {"Currency", "Float", "Percent"}:
        return flt(value)
    return cstr(value)


def _validate_server_derived_fields(doc):
    if not (doc.get("actual_previous_classification") and doc.get("actual_current_classification")):
        if doc.get("transition") or doc.get("cleaning_level"):
            frappe.throw(_("Transition and Cleaning Level require confirmed classifications."))
        return
    expected_level = derive_cleaning_level(doc.actual_previous_classification, doc.actual_current_classification)
    expected_transition = f"{doc.actual_previous_classification} -> {doc.actual_current_classification}"
    if doc.get("transition") and doc.transition != expected_transition:
        frappe.throw(_("Transition is server-controlled and cannot be changed directly."))
    if doc.get("cleaning_level") and doc.cleaning_level != expected_level:
        frappe.throw(_("Cleaning Level is server-controlled and cannot be changed directly."))
    if doc.get("checklist_generated_on"):
        _validate_current_checklist(doc, expected_level)


def _validate_current_checklist(doc, level: str):
    expected = {row["sequence"]: row for row in get_checklist_template(level)}
    current = [
        row for row in doc.get("checklist") or []
        if cint(row.get("evaluation_revision")) == cint(doc.evaluation_revision)
        and not cint(row.get("superseded"))
    ]
    if len(current) != len(expected):
        frappe.throw(_("The current F-GCL-01 checklist must contain exactly 16 server-generated rows."))
    for row in current:
        template = expected.get(cint(row.get("sequence")))
        if not template or any(
            row.get(fieldname) != template[fieldname]
            for fieldname in ("check_item", "cleaning_level", "required_cleaning_code", "cleaning_code_description")
        ):
            frappe.throw(_("F-GCL-01 checkpoint and cleaning-code fields are server-controlled."))


def _validate_superseded_rows_immutable(doc):
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or getattr(doc.flags, "grade_change_action", None) or getattr(doc.flags, "grade_change_reset", False):
        return
    stored_sequence = [
        (row.get("name"), cint(row.get("idx")))
        for row in before.get("checklist") or []
        if cint(row.get("superseded"))
    ]
    incoming_sequence = [
        (row.get("name"), cint(row.get("idx")))
        for row in doc.get("checklist") or []
        if cint(row.get("superseded"))
    ]
    if stored_sequence != incoming_sequence:
        frappe.throw(_("Superseded F-GCL-01 checklist evidence is immutable."))
    stored_rows = {row.get("name"): row for row in before.get("checklist") or [] if cint(row.get("superseded"))}
    incoming_rows = {row.get("name"): row for row in doc.get("checklist") or [] if cint(row.get("superseded"))}
    meta = frappe.get_meta(CHECKLIST_DOCTYPE)
    protected_fields = (
        "name", "idx", "parent", "parentfield", "parenttype",
        *(df.fieldname for df in meta.fields if df.fieldname),
    )
    if any(
        not _field_values_equal(meta, fieldname, stored_rows[row_name], incoming_rows[row_name])
        for row_name in stored_rows
        for fieldname in protected_fields
    ):
        frappe.throw(_("Superseded F-GCL-01 checklist evidence is immutable."))


def _validate_checklist_row_identity_and_order(doc):
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or getattr(doc.flags, "grade_change_action", None) or getattr(doc.flags, "grade_change_reset", False):
        return
    before_identity = [(row.get("name"), cint(row.get("idx"))) for row in _current_rows(before)]
    current_identity = [(row.get("name"), cint(row.get("idx"))) for row in _current_rows(doc)]
    if before_identity != current_identity:
        frappe.throw(_("Generated F-GCL-01 checklist rows cannot be inserted, deleted, duplicated, or reordered."))


def _validate_classification_lock(doc):
    if getattr(doc.flags, "grade_change_reset", False):
        return
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or (
        not execution_started(before)
        and before.get("status") not in {
            STATUS_PENDING_APPROVAL, STATUS_RESET_APPROVAL_REQUIRED,
            STATUS_APPROVED, STATUS_REJECTED,
        }
    ):
        return
    for fieldname in ("actual_previous_classification", "actual_current_classification"):
        if before.get(fieldname) != doc.get(fieldname):
            frappe.throw(_("Classifications are locked after checklist execution begins. Request a controlled reset."))


def _mark_execution_metadata(doc):
    current_rows = _current_rows(doc)
    is_new = doc.is_new() if callable(getattr(doc, "is_new", None)) else not doc.get("name")
    before = doc.get_doc_before_save() if not is_new else None
    before_rows = {row.get("name"): row for row in _current_rows(before)} if before else {}
    event_time = None

    def get_event_time():
        nonlocal event_time
        event_time = event_time or now_datetime()
        return event_time

    for row in current_rows:
        previous = before_rows.get(row.get("name"))
        was_completed = cint(previous.get("completed")) if previous else 0
        is_completed = cint(row.get("completed"))
        if is_completed and (not was_completed or not previous or not previous.get("checked_by")):
            row.checked_by = frappe.session.user
            row.checked_on = get_event_time()
        elif previous:
            row.checked_by = previous.get("checked_by")
            row.checked_on = previous.get("checked_on")
    for fieldname, by_field, on_field in (
        ("line_material_removed", "line_material_removed_by", "line_material_removed_on"),
        ("feeder_connection_confirmed", "feeder_connection_confirmed_by", "feeder_connection_confirmed_on"),
    ):
        if cint(doc.get(fieldname)) and not doc.get(by_field):
            doc.set(by_field, frappe.session.user)
            doc.set(on_field, get_event_time())
    if doc.get("cotton_cloth_available") and not doc.get("cotton_cloth_confirmed_by"):
        doc.cotton_cloth_confirmed_by = frappe.session.user
        doc.cotton_cloth_confirmed_on = get_event_time()
    _ensure_execution_started_on(doc, before, event_time)
    if doc.status == STATUS_DRAFT_CHECKLIST and execution_started(doc):
        doc.status = STATUS_IN_PROGRESS


def _has_execution_evidence(doc) -> bool:
    return bool(
        any(
            cstr(row.get("observation")).strip() or cint(row.get("completed"))
            for row in _current_rows(doc)
        )
        or cint(doc.get("line_material_removed"))
        or cint(doc.get("feeder_connection_confirmed"))
        or cstr(doc.get("cotton_cloth_available")).strip()
    )


def _earliest_execution_evidence_timestamp(doc):
    values = [
        row.get("checked_on")
        for row in _current_rows(doc)
        if cint(row.get("completed")) and row.get("checked_on")
    ]
    values.extend(
        doc.get(fieldname)
        for fieldname in (
            "line_material_removed_on", "feeder_connection_confirmed_on", "cotton_cloth_confirmed_on"
        )
        if doc.get(fieldname)
    )
    return min((get_datetime(value) for value in values), default=None)


def _ensure_execution_started_on(doc, before=None, event_time=None):
    if doc.get("execution_started_on"):
        return
    historical_time = _earliest_execution_evidence_timestamp(doc)
    if historical_time:
        doc.execution_started_on = historical_time
        return
    if _has_execution_evidence(doc) and not (before and _has_execution_evidence(before)):
        doc.execution_started_on = event_time or now_datetime()


def _validate_status_transition(doc):
    before = doc.get_doc_before_save() if not doc.is_new() else None
    if not before or before.get("status") == doc.get("status"):
        return
    if before.get("status") == STATUS_DRAFT_CHECKLIST and doc.get("status") == STATUS_IN_PROGRESS:
        return
    if getattr(doc.flags, "grade_change_action", None):
        return
    frappe.throw(_("Grade Change workflow status can change only through its controlled actions."))


def _validate_action_state(doc):
    if doc.status in {STATUS_PENDING_APPROVAL, STATUS_APPROVED}:
        _validate_complete_current_evaluation(doc)
    if doc.status == STATUS_APPROVED:
        if not doc.get("approved_by") or not doc.get("approved_on"):
            frappe.throw(_("Production Head approval evidence is mandatory."))
        if doc.get("inspected_by") == doc.get("approved_by"):
            frappe.throw(_("Inspector and approver must be different authenticated users."))


def _validate_complete_current_evaluation(doc):
    _validate_confirmed_classifications(doc)
    if doc.get("not_required"):
        if not cstr(doc.get("not_required_reason")).strip():
            frappe.throw(_("Not Required reason is mandatory."))
        return
    rows = _current_rows(doc)
    if len(rows) != 16 or any(not cint(row.get("completed")) for row in rows):
        frappe.throw(_("All 16 current F-GCL-01 checklist rows must be completed."))
    for prefix in ("line_material_removed", "feeder_connection_confirmed"):
        if not cint(doc.get(prefix)):
            frappe.throw(_("All additional F-GCL-01 confirmations are mandatory."))
    if doc.get("cotton_cloth_available") not in {"Yes", "No"}:
        frappe.throw(_("Cotton Cloth Available must be confirmed as Yes or No."))
    _validate_negative_observations(doc)
    if doc.get("cotton_cloth_available") == "No":
        frappe.throw(_("Cotton Cloth Available must be resolved to Yes before approval submission."))


def _validate_negative_observations(doc):
    if doc.get("cotton_cloth_available") == "No" and not cstr(doc.get("cotton_cloth_observation")).strip():
        frappe.throw(_("Cotton Cloth Observation is mandatory when Cotton Cloth Available is No."))


def _validate_confirmed_classifications(doc):
    previous = cstr(doc.get("actual_previous_classification")).strip()
    current = cstr(doc.get("actual_current_classification")).strip()
    if previous not in CLASSIFICATIONS or current not in CLASSIFICATIONS:
        frappe.throw(_("Production Engineer must select both Actual Previous and Actual Current Classification."))
    if doc.get("previous_erp_evidence_status") == "No Previous ERP Production Evidence" and not cstr(doc.get("remarks")).strip():
        frappe.throw(_("Supporting remarks are mandatory when no previous ERP production evidence exists."))
    for side in ("previous", "current"):
        suggestion = cstr(doc.get(f"suggested_{side}_classification")).strip()
        selected = cstr(doc.get(f"actual_{side}_classification")).strip()
        if suggestion and suggestion != selected and not cstr(doc.get(f"{side}_override_reason")).strip():
            frappe.throw(_("Override reason is mandatory when {0} classification differs from the Item suggestion.").format(side.title()))


def execution_started(doc) -> bool:
    if doc.get("status") == STATUS_IN_PROGRESS:
        return True
    return _has_execution_evidence(doc)


@frappe.whitelist()
def create_grade_change_clearance(work_order: str | None = None, job_card: str | None = None, **kwargs) -> dict[str, Any]:
    work_order = work_order or kwargs.get("work_order")
    job_card = job_card or kwargs.get("job_card")
    wo, jc = _resolve_named_work_order_and_job_card(work_order, job_card)
    _require_role("Production Engineer", "Production Manager", "System Manager")
    frappe.db.sql("select name from `tabWork Order` where name = %s for update", wo.name)
    existing = get_authoritative_clearance(wo.name, jc.name)
    if existing:
        return {
            "name": existing.name,
            "status": existing.status,
            "created": "0",
            "route": ["Form", CLEARANCE_DOCTYPE, existing.name],
        }
    doc = frappe.new_doc(CLEARANCE_DOCTYPE)
    doc.work_order = wo.name
    doc.erpnext_job_card = jc.name
    doc.insert()
    _link_clearance(doc, wo, jc)
    return {
        "name": doc.name,
        "status": doc.status,
        "created": "1",
        "route": ["Form", CLEARANCE_DOCTYPE, doc.name],
    }


@frappe.whitelist()
def confirm_classifications_and_generate(clearance: str, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Engineer", "System Manager")
    if doc.status not in {STATUS_PENDING_EVALUATION, STATUS_DRAFT_CHECKLIST}:
        frappe.throw(_("Checklist generation is allowed only during Pending Evaluation or Draft Checklist."))
    if execution_started(doc):
        frappe.throw(_("Checklist execution has started. Request a controlled reset."))
    for fieldname in (
        "actual_previous_classification", "actual_current_classification",
        "previous_override_reason", "current_override_reason", "regeneration_reason",
        "remarks", "not_required", "not_required_reason",
    ):
        if fieldname in kwargs:
            doc.set(fieldname, kwargs.get(fieldname))
    _validate_confirmed_classifications(doc)
    level = derive_cleaning_level(doc.actual_previous_classification, doc.actual_current_classification)
    revision = cint(doc.evaluation_revision) or 1
    if doc.get("checklist_generated_on") and not cstr(doc.get("regeneration_reason")).strip():
        frappe.throw(_("Regeneration reason is mandatory before replacing an unstarted checklist."))
    if doc.get("checklist_generated_on"):
        _supersede_current_rows(doc)
        revision += 1
        doc.evaluation_revision = revision
    _stamp_classification_confirmation(doc)
    doc.transition = f"{doc.actual_previous_classification} -> {doc.actual_current_classification}"
    doc.cleaning_level = level
    doc.cleaning_level_description = LEVEL_DESCRIPTIONS[level]
    doc.controlled_document_no = CONTROLLED_DOCUMENT
    doc.controlled_revision = CONTROLLED_REVISION
    doc.checklist_generated_by = frappe.session.user
    doc.checklist_generated_on = now_datetime()
    for row in get_checklist_template(level):
        doc.append("checklist", {
            **row,
            "required": 1,
            "evaluation_revision": revision,
            "evaluation_state": "Current",
            "snapshot_previous_classification": doc.actual_previous_classification,
            "snapshot_current_classification": doc.actual_current_classification,
            "snapshot_transition": doc.transition,
            "snapshot_generated_by": doc.checklist_generated_by,
            "snapshot_generated_on": doc.checklist_generated_on,
        })
    doc.status = STATUS_DRAFT_CHECKLIST
    doc.flags.grade_change_action = "generate"
    doc.save()
    return _action_result(doc)


@frappe.whitelist()
def submit_for_approval(clearance: str, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Engineer", "System Manager")
    if doc.status not in {STATUS_DRAFT_CHECKLIST, STATUS_IN_PROGRESS}:
        frappe.throw(_("Only a current Draft Checklist or In Progress evaluation can be submitted."))
    _validate_complete_current_evaluation(doc)
    _ensure_execution_started_on(doc)
    if not doc.get("execution_started_on"):
        frappe.throw(_("Execution Started On could not be established from controlled execution evidence."))
    completed_on = now_datetime()
    doc.execution_completed_on = completed_on
    doc.time_taken_minutes = round(
        max(0, time_diff_in_seconds(completed_on, doc.execution_started_on)) / 60,
        3,
    )
    doc.inspected_by = frappe.session.user
    doc.inspected_on = completed_on
    doc.status = STATUS_PENDING_APPROVAL
    doc.flags.grade_change_action = "submit"
    doc.save()
    return _action_result(doc)


@frappe.whitelist()
def approve_clearance(clearance: str, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Head", "System Manager")
    if doc.status != STATUS_PENDING_APPROVAL:
        frappe.throw(_("Only a clearance Pending Approval can be approved."))
    if doc.inspected_by == frappe.session.user:
        frappe.throw(_("Inspector and approver must be different authenticated users."))
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.status = STATUS_APPROVED
    doc.flags.grade_change_action = "approve"
    doc.save()
    _sync_linked_grade_change_status(doc)
    return _action_result(doc)


@frappe.whitelist()
def reject_clearance(clearance: str, rejection_reason: str | None = None, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Head", "System Manager")
    if doc.status != STATUS_PENDING_APPROVAL:
        frappe.throw(_("Only a clearance Pending Approval can be rejected."))
    reason = cstr(rejection_reason or kwargs.get("rejection_reason")).strip()
    if not reason:
        frappe.throw(_("Rejection reason is mandatory."))
    doc.rejection_reason = reason
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.status = STATUS_REJECTED
    doc.flags.grade_change_action = "reject"
    doc.save()
    _sync_linked_grade_change_status(doc)
    return _action_result(doc)


@frappe.whitelist()
def request_reset(clearance: str, reset_reason: str | None = None, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Engineer", "System Manager")
    reason = cstr(reset_reason or kwargs.get("reset_reason")).strip()
    if not reason:
        frappe.throw(_("Reset reason is mandatory."))
    if not execution_started(doc):
        frappe.throw(_("Use checklist regeneration before execution begins."))
    if doc.status not in {STATUS_IN_PROGRESS, STATUS_PENDING_APPROVAL, STATUS_APPROVED}:
        frappe.throw(_("A controlled reset may be requested only for an active or approved evaluation."))
    doc.reset_from_status = doc.status
    doc.reset_reason = reason
    doc.reset_requested_by = frappe.session.user
    doc.reset_requested_on = now_datetime()
    doc.status = STATUS_RESET_APPROVAL_REQUIRED
    doc.flags.grade_change_action = "request_reset"
    doc.save()
    return _action_result(doc)


@frappe.whitelist()
def approve_reset(clearance: str, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Head", "System Manager")
    if doc.status != STATUS_RESET_APPROVAL_REQUIRED:
        frappe.throw(_("Only a Reset Approval Required clearance can be reset."))
    if doc.reset_requested_by == frappe.session.user:
        frappe.throw(_("Reset requester and approver must be different authenticated users."))
    _supersede_current_rows(doc)
    doc.flags.grade_change_reset = True
    doc.flags.grade_change_action = "approve_reset"
    doc.evaluation_revision = cint(doc.evaluation_revision) + 1
    doc.reset_approved_by = frappe.session.user
    doc.reset_approved_on = now_datetime()
    for fieldname in (
        "actual_previous_classification", "actual_current_classification", "transition",
        "cleaning_level", "cleaning_level_description", "classification_confirmed_by",
        "classification_confirmed_on", "checklist_generated_by", "checklist_generated_on",
        "previous_override", "previous_override_reason", "previous_classification_changed_by",
        "previous_classification_changed_on", "current_override", "current_override_reason",
        "current_classification_changed_by", "current_classification_changed_on", "regeneration_reason",
        "not_required", "not_required_reason", "line_material_removed",
        "line_material_removed_observation", "line_material_removed_by", "line_material_removed_on",
        "feeder_connection_confirmed", "feeder_connection_observation",
        "feeder_connection_confirmed_by", "feeder_connection_confirmed_on",
        "cotton_cloth_available", "cotton_cloth_observation", "cotton_cloth_confirmed_by",
        "cotton_cloth_confirmed_on", "execution_started_on", "execution_completed_on",
        "time_taken_minutes", "inspected_by", "inspected_on",
        "approved_by", "approved_on", "remarks", "rejection_reason",
    ):
        doc.set(fieldname, None)
    doc.status = STATUS_PENDING_EVALUATION
    doc.save()
    _sync_linked_grade_change_status(doc)
    return _action_result(doc)


@frappe.whitelist()
def reject_reset(clearance: str, rejection_reason: str | None = None, **kwargs) -> dict[str, Any]:
    doc = _get_writable_clearance(clearance)
    _require_role("Production Head", "System Manager")
    if doc.status != STATUS_RESET_APPROVAL_REQUIRED:
        frappe.throw(_("Only a Reset Approval Required clearance can have its reset rejected."))
    reason = cstr(rejection_reason or kwargs.get("rejection_reason")).strip()
    if not reason:
        frappe.throw(_("Reset rejection reason is mandatory."))
    doc.reset_rejection_reason = reason
    doc.status = doc.get("reset_from_status") or STATUS_IN_PROGRESS
    doc.flags.grade_change_action = "reject_reset"
    doc.save()
    return _action_result(doc)


def assert_grade_change_approved(job_card_or_name, action: str = "production execution"):
    job_card = _coerce_job_card(job_card_or_name)
    if cstr(job_card.get("operation")).strip() != COMPOUNDING_OPERATION:
        return
    clearance = get_authoritative_clearance(job_card.get("work_order"), job_card.get("name"))
    if not clearance:
        frappe.throw(_("Grade Change is Pending Evaluation. Create and approve F-GCL-01 REV-02 before {0}.").format(action))
    if clearance.status != STATUS_APPROVED:
        frappe.throw(_("Grade Change Clearance {0} is {1}. Approval is required before {2}.").format(clearance.name, clearance.status, action))
    _validate_complete_current_evaluation(clearance)


def validate_rm_loading_gate(doc, method=None):
    from calco_erp.calco_production.execution_policy import pausing
    if pausing(doc):
        return
    if cstr(doc.get("operation")).strip() != COMPOUNDING_OPERATION:
        return
    started = doc.get("custom_rm_loading_status") not in (None, "", "Not Started") or bool(doc.get("custom_rm_loading_details"))
    if started:
        assert_grade_change_approved(doc, _("RM Loading starts or completes"))


def get_authoritative_clearance(work_order: str | None, job_card: str | None = None):
    filters: dict[str, Any] = {"status": ("!=", STATUS_SUPERSEDED)}
    if job_card:
        filters["erpnext_job_card"] = job_card
    elif work_order:
        filters["work_order"] = work_order
    else:
        return None
    name = frappe.db.get_value(CLEARANCE_DOCTYPE, filters, "name", order_by="creation desc")
    return frappe.get_doc(CLEARANCE_DOCTYPE, name) if name else None


def get_grade_change_journey_state(work_order: str, job_card: str | None = None) -> dict[str, Any]:
    clearance = get_authoritative_clearance(work_order, job_card)
    if not clearance:
        return {
            "status": STATUS_PENDING_EVALUATION,
            "approval_status": STATUS_PENDING_EVALUATION,
            "summary": _("No Grade Change Clearance exists. Engineer classification confirmation is required."),
            "clearance": "",
            "cleaning_level": "",
            "approved": False,
        }
    status_map = {
        STATUS_PENDING_EVALUATION: STATUS_PENDING_EVALUATION,
        STATUS_DRAFT_CHECKLIST: "Required - Checklist Pending",
        STATUS_IN_PROGRESS: STATUS_IN_PROGRESS,
        STATUS_RESET_APPROVAL_REQUIRED: STATUS_RESET_APPROVAL_REQUIRED,
        STATUS_PENDING_APPROVAL: STATUS_PENDING_APPROVAL,
        STATUS_APPROVED: "Completed - Not Required" if cint(clearance.get("not_required")) else "Completed",
        STATUS_REJECTED: STATUS_REJECTED,
    }
    return {
        "status": status_map.get(clearance.status, clearance.status),
        "approval_status": clearance.status,
        "summary": _("F-GCL-01 %(revision)s | %(transition)s | Level %(level)s | %(status)s") % {
            "revision": clearance.get("controlled_revision") or CONTROLLED_REVISION,
            "transition": clearance.get("transition") or _("classification pending"),
            "level": clearance.get("cleaning_level") or "-",
            "status": clearance.status,
        },
        "clearance": clearance.name,
        "cleaning_level": clearance.get("cleaning_level") or "",
        "approved": clearance.status == STATUS_APPROVED,
    }


def _resolve_work_order_and_job_card(doc):
    return _resolve_named_work_order_and_job_card(doc.get("work_order"), doc.get("erpnext_job_card"))


def _resolve_named_work_order_and_job_card(work_order: str | None, job_card: str | None):
    if job_card:
        jc = frappe.get_doc("Job Card", job_card)
        work_order = work_order or jc.get("work_order")
    elif work_order:
        name = frappe.db.get_value(
            "Job Card",
            {"work_order": work_order, "operation": COMPOUNDING_OPERATION, "docstatus": ("<", 2)},
            "name",
            order_by="creation asc",
        )
        if not name:
            frappe.throw(_("A Compounding / Extrusion Job Card is required."))
        jc = frappe.get_doc("Job Card", name)
    else:
        frappe.throw(_("Work Order or Compounding Job Card is required."))
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))
    wo = frappe.get_doc("Work Order", work_order)
    if cstr(jc.get("operation")).strip() != COMPOUNDING_OPERATION:
        frappe.throw(_("Grade Change Clearance must link to the Compounding / Extrusion Job Card."))
    return wo, jc


def _coerce_job_card(value):
    if isinstance(value, str):
        return frappe.get_doc("Job Card", value)
    return value


def _item_classification(item_code: str | None) -> str:
    if not item_code or not frappe.db.exists("Item", item_code):
        return ""
    return cstr(frappe.db.get_value("Item", item_code, "custom_fg_grade_classification") or "").strip()


def _current_rows(doc) -> list[Any]:
    revision = cint(doc.get("evaluation_revision")) or 1
    return [
        row for row in doc.get("checklist") or []
        if cint(row.get("evaluation_revision")) == revision and not cint(row.get("superseded"))
    ]


def _supersede_current_rows(doc):
    snapshot_fields = (
        "actual_previous_classification", "actual_current_classification", "transition",
        "cleaning_level", "cleaning_level_description", "controlled_document_no",
        "controlled_revision", "evaluation_revision", "classification_confirmed_by",
        "classification_confirmed_on", "previous_override", "previous_override_reason",
        "current_override", "current_override_reason", "checklist_generated_by",
        "checklist_generated_on", "not_required", "not_required_reason",
        "line_material_removed", "line_material_removed_observation", "line_material_removed_by",
        "line_material_removed_on", "feeder_connection_confirmed", "feeder_connection_observation",
        "feeder_connection_confirmed_by", "feeder_connection_confirmed_on",
        "cotton_cloth_available", "cotton_cloth_observation", "cotton_cloth_confirmed_by",
        "cotton_cloth_confirmed_on", "execution_started_on", "execution_completed_on",
        "time_taken_minutes", "inspected_by", "inspected_on",
        "approved_by", "approved_on", "remarks", "rejection_reason", "reset_reason",
        "reset_requested_by", "reset_requested_on",
    )
    snapshot = frappe.as_json({fieldname: doc.get(fieldname) for fieldname in snapshot_fields})
    for row in _current_rows(doc):
        row.superseded = 1
        row.evaluation_state = STATUS_SUPERSEDED
        row.evaluation_snapshot_json = snapshot


def _stamp_classification_confirmation(doc):
    now = now_datetime()
    doc.classification_confirmed_by = frappe.session.user
    doc.classification_confirmed_on = now
    for side in ("previous", "current"):
        suggestion = cstr(doc.get(f"suggested_{side}_classification")).strip()
        selected = cstr(doc.get(f"actual_{side}_classification")).strip()
        doc.set(f"{side}_override", cint(bool(suggestion and suggestion != selected)))
        doc.set(f"{side}_classification_changed_by", frappe.session.user)
        doc.set(f"{side}_classification_changed_on", now)
        if suggestion and suggestion != selected:
            continue


def _link_clearance(clearance, work_order, job_card):
    for doctype, doc, fieldname in (
        ("Work Order", work_order, "custom_grade_change_clearance"),
        ("Job Card", job_card, "custom_grade_change_clearance"),
    ):
        if doc.meta.has_field(fieldname):
            frappe.db.set_value(doctype, doc.name, fieldname, clearance.name, update_modified=False)


def _sync_linked_grade_change_status(doc):
    status = get_grade_change_journey_state(doc.work_order, doc.erpnext_job_card)["status"]
    if doc.erpnext_job_card and frappe.db.exists("Job Card", doc.erpnext_job_card):
        frappe.db.set_value("Job Card", doc.erpnext_job_card, "custom_grade_change_status", status, update_modified=False)


def _get_writable_clearance(name: str):
    if not name or not frappe.db.exists(CLEARANCE_DOCTYPE, name):
        frappe.throw(_("Valid Grade Change Clearance is required."))
    if not frappe.has_permission(CLEARANCE_DOCTYPE, "write", doc=name):
        frappe.throw(_("Not permitted to update Grade Change Clearance."), frappe.PermissionError)
    return frappe.get_doc(CLEARANCE_DOCTYPE, name)


def _require_role(*roles: str):
    if not _has_any_role(*roles):
        frappe.throw(_("This action requires one of these roles: {0}.").format(", ".join(roles)), frappe.PermissionError)


def _has_any_role(*roles: str) -> bool:
    user_roles = set(frappe.get_roles(frappe.session.user))
    return bool(user_roles.intersection(roles))


def _action_result(doc) -> dict[str, Any]:
    return {
        "name": doc.name,
        "status": doc.status,
        "evaluation_revision": cint(doc.evaluation_revision),
        "transition": doc.get("transition") or "",
        "cleaning_level": doc.get("cleaning_level") or "",
    }
