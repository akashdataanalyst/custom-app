from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime


MPDS_DOCTYPE = "Master Process Data Sheet"
SPECIFICATION_DOCTYPE = "MPDS Specification"

IMPORTED_STATUS = "Imported - Unverified"
DRAFT_STATUS = "Draft"
PENDING_STATUS = "Pending Approval"
CURRENT_STATUS = "Approved / Current"
SUPERSEDED_STATUS = "Superseded"
VALID_STATUSES = {
    IMPORTED_STATUS,
    DRAFT_STATUS,
    PENDING_STATUS,
    CURRENT_STATUS,
    SUPERSEDED_STATUS,
}
EDITABLE_STATUSES = {IMPORTED_STATUS, DRAFT_STATUS}
APPROVAL_ROLES = {"Production Head", "Manufacturing Manager"}
REVIEW_ROLES = {"Technical User", "Production Engineer", *APPROVAL_ROLES}

IDENTITY_FIELDS = (
    "fg_item",
    "production_line",
    "source_line_identifier",
    "mpds_no",
    "revision",
)
SOURCE_FIELDS = (
    "source_mpds_no",
    "source_revision_text",
    "source_revision_date",
    "source_workbook_hash",
    "source_file",
    "source_sheet",
    "source_row",
    "import_identity_key",
    "source_payload_hash",
)
SPECIFICATION_AUTHORITY_FIELDS = (
    "domain",
    "parameter_key",
    "exact_source_header",
    "display_label",
    "feeder_or_position",
    "specification_text",
    "unit",
    "applicability",
    "sequence",
    "parser_status",
    "parsed_target",
    "parsed_minimum",
    "parsed_maximum",
    "parsed_tolerance",
    "process_parameter_key",
    "parameter_group",
    "mapping_status",
    "mapping_note",
    "source_column",
    "source_cell",
    "source_value_type",
    "source_raw_value",
)


def before_insert_master_process_data_sheet(doc):
    if getattr(frappe.flags, "mpds_import", False):
        doc.status = IMPORTED_STATUS
        doc.imported_by = frappe.session.user
        doc.imported_on = now_datetime()
    else:
        doc.status = DRAFT_STATUS
    _set_title(doc)
    _validate_status(doc)
    _refresh_data_quality(doc)
    doc.imported_payload_hash = get_authority_payload_hash(doc)


def validate_master_process_data_sheet(doc):
    _validate_status(doc)
    _protect_controlled_transition(doc)
    _protect_source_provenance(doc)
    _protect_approved_record(doc)
    _invalidate_stale_review(doc)
    _validate_item_and_line(doc)
    _validate_current_identity(doc)
    _normalize_specification_sequence(doc)
    _set_title(doc)
    _refresh_data_quality(doc)


def prevent_master_process_data_sheet_delete(doc):
    if cstr(doc.get("status")) in {CURRENT_STATUS, SUPERSEDED_STATUS}:
        frappe.throw(_("Approved or Superseded MPDS records cannot be deleted."))


def _validate_status(doc):
    if cstr(doc.get("status")) not in VALID_STATUSES:
        frappe.throw(_("Invalid MPDS status {0}.").format(doc.get("status")))


def _set_title(doc):
    parts = [cstr(doc.get("fg_item")).strip(), cstr(doc.get("production_line")).strip()]
    control = " / ".join(value for value in (cstr(doc.get("mpds_no")).strip(), cstr(doc.get("revision")).strip()) if value)
    doc.title = " - ".join(value for value in [*parts, control] if value)


def _validate_item_and_line(doc):
    if not frappe.db.exists("Item", doc.get("fg_item")):
        frappe.throw(_("FG Item {0} does not exist.").format(doc.get("fg_item")))
    if cint(frappe.db.get_value("Item", doc.get("fg_item"), "disabled")):
        frappe.throw(_("FG Item {0} is disabled.").format(doc.get("fg_item")))
    if not frappe.db.exists("Workstation", doc.get("production_line")):
        frappe.throw(_("Production Line {0} does not exist.").format(doc.get("production_line")))


def _validate_current_identity(doc):
    if cstr(doc.get("status")) != CURRENT_STATUS:
        return
    filters = {
        "fg_item": doc.get("fg_item"),
        "production_line": doc.get("production_line"),
        "status": CURRENT_STATUS,
        "name": ["!=", doc.name or ""],
    }
    existing = frappe.db.get_value(MPDS_DOCTYPE, filters, "name")
    if existing:
        frappe.throw(
            _("Approved / Current MPDS {0} already exists for {1} on {2}.").format(
                existing, doc.get("fg_item"), doc.get("production_line")
            )
        )


def _protect_controlled_transition(doc):
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if not before or cstr(before.get("status")) == cstr(doc.get("status")):
        return
    if not getattr(frappe.flags, "controlled_mpds_transition", False):
        frappe.throw(_("Use the controlled MPDS lifecycle actions to change status."))


def _protect_source_provenance(doc):
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if not before:
        return
    changed = [field for field in SOURCE_FIELDS if _canonical(before.get(field)) != _canonical(doc.get(field))]
    if changed:
        frappe.throw(_("Imported MPDS source evidence is immutable: {0}.").format(", ".join(changed)))


def _protect_approved_record(doc):
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if not before or cstr(before.get("status")) not in {CURRENT_STATUS, SUPERSEDED_STATUS}:
        return
    if getattr(frappe.flags, "controlled_mpds_transition", False):
        return
    changed = [field for field in (*IDENTITY_FIELDS, "effective_date", "material_status_snapshot") if _canonical(before.get(field)) != _canonical(doc.get(field))]
    if changed or _specification_payload(before.get("specifications") or []) != _specification_payload(doc.get("specifications") or []):
        frappe.throw(_("Approved / Current MPDS is immutable. Create a controlled new revision."))


def _invalidate_stale_review(doc):
    if doc.is_new() or cstr(doc.get("status")) not in EDITABLE_STATUSES:
        return
    if getattr(frappe.flags, "controlled_mpds_review", False) or not doc.get("reviewed_on"):
        return
    before = doc.get_doc_before_save()
    if not before:
        return
    authority_changed = any(
        _canonical(before.get(field)) != _canonical(doc.get(field))
        for field in (*IDENTITY_FIELDS, "effective_date", "material_status_snapshot")
    ) or _specification_payload(before.get("specifications") or []) != _specification_payload(
        doc.get("specifications") or []
    )
    if authority_changed:
        doc.reviewed_by = ""
        doc.reviewed_on = None


def _normalize_specification_sequence(doc):
    seen = set()
    for index, row in enumerate(doc.get("specifications") or [], start=1):
        row.sequence = cint(row.get("sequence")) or index
        key = cstr(row.get("parameter_key")).strip()
        if not key:
            frappe.throw(_("MPDS specification row {0} requires a Parameter Key.").format(index))
        if key in seen:
            frappe.throw(_("Duplicate MPDS Parameter Key {0}.").format(key))
        seen.add(key)


def _refresh_data_quality(doc):
    specifications = list(doc.get("specifications") or [])
    missing_revision = not cstr(doc.get("revision"))
    missing_mpds_no = not cstr(doc.get("mpds_no"))
    incomplete_process = any(
        cstr(row.get("parameter_group")) == "F-PRD-01/01"
        and not cstr(row.get("specification_text"))
        for row in specifications
    )
    ambiguous_count = sum(cstr(row.get("mapping_status")) in {"Ambiguous", "Missing Source"} for row in specifications)
    parser_warning_count = sum(cstr(row.get("parser_status")) == "Warning" for row in specifications)
    missing_unit_count = sum(
        bool(cstr(row.get("specification_text")))
        and cstr(row.get("parameter_group")) == "F-PRD-01/01"
        and not cstr(row.get("unit"))
        for row in specifications
    )
    duplicate_candidate = cint(doc.get("duplicate_candidate"))
    doc.missing_revision = cint(missing_revision)
    doc.missing_mpds_no = cint(missing_mpds_no)
    doc.incomplete_process_specification = cint(incomplete_process)
    flags = []
    if missing_revision:
        flags.append("Missing Revision")
    if missing_mpds_no:
        flags.append("Missing MPDS No.")
    if incomplete_process:
        flags.append("Incomplete Process Specification")
    if duplicate_candidate:
        flags.append("Duplicate Candidate")
    if ambiguous_count:
        flags.append(f"Ambiguous / Missing Mapping ({ambiguous_count})")
    if parser_warning_count:
        flags.append(f"Parser Warning ({parser_warning_count})")
    if missing_unit_count:
        flags.append(f"Missing Unit ({missing_unit_count})")
    doc.data_quality_flags = "\n".join(flags) or "No automated data-quality flags"


def _canonical(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _specification_payload(rows: Iterable) -> list[dict]:
    return [
        {field: _canonical(row.get(field)) for field in SPECIFICATION_AUTHORITY_FIELDS}
        for row in sorted(rows, key=lambda item: (cint(item.get("sequence")), cstr(item.get("parameter_key"))))
    ]


def get_authority_payload_hash(doc) -> str:
    payload = {
        "identity": {field: _canonical(doc.get(field)) for field in (*IDENTITY_FIELDS, "source_revision_date", "material_status_snapshot")},
        "specifications": _specification_payload(doc.get("specifications") or []),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _check_roles(allowed_roles: set[str], message: str):
    roles = set(frappe.get_roles())
    if "System Manager" not in roles and not roles.intersection(allowed_roles):
        frappe.throw(_(message), frappe.PermissionError)


def _load_writable(name: str):
    doc = frappe.get_doc(MPDS_DOCTYPE, name)
    doc.check_permission("write")
    return doc


def _save_transition(doc, status: str):
    frappe.flags.controlled_mpds_transition = True
    try:
        doc.status = status
        doc.save()
    finally:
        frappe.flags.controlled_mpds_transition = False
    return doc


@frappe.whitelist()
def mark_reviewed(name: str, review_notes: str | None = None):
    _check_roles(REVIEW_ROLES, "Technical User or Production Engineer authority is required to review MPDS.")
    doc = _load_writable(name)
    if doc.status not in EDITABLE_STATUSES:
        frappe.throw(_("Only Imported - Unverified or Draft MPDS can be marked Reviewed."))
    doc.review_notes = cstr(review_notes).strip()
    doc.reviewed_by = frappe.session.user
    doc.reviewed_on = now_datetime()
    frappe.flags.controlled_mpds_review = True
    try:
        doc.save()
    finally:
        frappe.flags.controlled_mpds_review = False
    return {"name": doc.name, "status": doc.status, "reviewed_by": doc.reviewed_by, "reviewed_on": doc.reviewed_on}


@frappe.whitelist()
def submit_for_approval(name: str):
    _check_roles(REVIEW_ROLES, "Technical User or Production Engineer authority is required to submit MPDS for approval.")
    doc = _load_writable(name)
    if doc.status not in EDITABLE_STATUSES:
        frappe.throw(_("Only Imported - Unverified or Draft MPDS can be submitted for approval."))
    if not doc.reviewed_on:
        frappe.throw(_("Mark MPDS Reviewed before submitting it for approval."))
    return _save_transition(doc, PENDING_STATUS).as_dict()


@frappe.whitelist()
def approve_and_make_current(name: str, approval_notes: str | None = None):
    _check_roles(APPROVAL_ROLES, "Production Head or Manufacturing Manager authority is required to approve MPDS.")
    doc = _load_writable(name)
    if doc.status != PENDING_STATUS:
        frappe.throw(_("Only Pending Approval MPDS can be Approved / Current."))
    current = frappe.db.get_value(
        MPDS_DOCTYPE,
        {"fg_item": doc.fg_item, "production_line": doc.production_line, "status": CURRENT_STATUS, "name": ["!=", doc.name]},
        "name",
    )
    if current and cstr(doc.get("supersedes")) != cstr(current):
        frappe.throw(_("Set Supersedes to current MPDS {0} before approval.").format(current))
    doc.approval_notes = cstr(approval_notes).strip()
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    if current:
        old = frappe.get_doc(MPDS_DOCTYPE, current)
        old.superseded_by = doc.name
        _save_transition(old, SUPERSEDED_STATUS)
    _save_transition(doc, CURRENT_STATUS)
    return doc.as_dict()


@frappe.whitelist()
def create_revision(name: str, revision: str, revision_reason: str):
    _check_roles(REVIEW_ROLES, "Technical User or Production Engineer authority is required to create an MPDS revision.")
    original = frappe.get_doc(MPDS_DOCTYPE, name)
    original.check_permission("read")
    if original.status != CURRENT_STATUS:
        frappe.throw(_("A controlled revision can be copied only from Approved / Current MPDS."))
    revision = cstr(revision)
    if not revision:
        frappe.throw(_("New Revision is required and must be entered explicitly."))
    if not cstr(revision_reason).strip():
        frappe.throw(_("Revision Reason is required."))
    copy = frappe.copy_doc(original)
    copy.name = None
    copy.status = DRAFT_STATUS
    copy.revision = revision
    copy.supersedes = original.name
    copy.superseded_by = ""
    copy.revision_reason = cstr(revision_reason).strip()
    copy.reviewed_by = ""
    copy.reviewed_on = None
    copy.review_notes = ""
    copy.approved_by = ""
    copy.approved_on = None
    copy.approval_notes = ""
    copy.import_identity_key = ""
    copy.source_payload_hash = ""
    copy.imported_payload_hash = ""
    copy.insert()
    return copy.as_dict()


@frappe.whitelist()
def update_specification(name: str, specification: str, specification_text=None, unit=None, applicability=None, mapping_status=None, mapping_note=None):
    _check_roles(REVIEW_ROLES, "Technical User or Production Engineer authority is required to edit MPDS specifications.")
    doc = _load_writable(name)
    if doc.status not in EDITABLE_STATUSES:
        frappe.throw(_("Only Imported - Unverified or Draft MPDS specifications can be edited."))
    row = next((item for item in doc.specifications if item.name == specification), None)
    if not row:
        frappe.throw(_("MPDS specification row {0} was not found.").format(specification))
    row.specification_text = cstr(specification_text)
    row.unit = cstr(unit)
    row.applicability = cstr(applicability) or _applicability(row.specification_text)
    row.mapping_status = cstr(mapping_status) or row.mapping_status
    row.mapping_note = cstr(mapping_note)
    parsed = parse_specification(row.specification_text)
    for key, value in parsed.items():
        row.set(key, value)
    doc.save()
    return doc.as_dict()


def _applicability(value: str) -> str:
    if value == "":
        return "Blank"
    stripped = value.strip()
    if stripped == "-":
        return "Explicit Dash"
    if stripped.upper() in {"NA", "N/A"}:
        return "Not Applicable"
    return "Specified"


PLUS_MINUS_PATTERN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[±]\s*([+-]?\d+(?:\.\d+)?)\s*$")


def parse_specification(value: str) -> dict:
    result = {
        "parser_status": "Blank" if value == "" else "Not Parsed",
        "parsed_target": None,
        "parsed_minimum": None,
        "parsed_maximum": None,
        "parsed_tolerance": None,
    }
    if value == "":
        return result
    match = PLUS_MINUS_PATTERN.match(value)
    if match:
        target, tolerance = map(float, match.groups())
        result.update(
            parser_status="Parsed",
            parsed_target=target,
            parsed_minimum=target - tolerance,
            parsed_maximum=target + tolerance,
            parsed_tolerance=tolerance,
        )
    return result
