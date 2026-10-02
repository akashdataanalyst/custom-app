from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime, nowdate

from calco_erp.calco_production.compounding_execution import (
    is_controlled_compounding_job_card,
    job_card_has_started,
)
from calco_erp.calco_production.wip_consumption import get_wip_consumption_preview


FEEDER_RUN_DOCTYPE = "Feeder Run"
SETUP_DOCTYPE = "Feeder Run Setup"
ONLINE_CHANGE_DOCTYPE = "Feeder Online Change"
READING_DOCTYPE = "Feeder Reading"
DOCUMENT_CODE = "F-FRS-01"
DOCUMENT_REVISION = "REV-03"
SOURCE_SYSTEM = "Feeder HMI"
FEEDER_NUMBERS = tuple(str(value) for value in range(1, 7))
RAW_MATERIAL_SOURCE = "Raw Material"
PREMIX_SOURCE = "Premix"
BLENDED_MATERIAL_SOURCE = "Blended Material"
MATERIAL_SOURCES = {
    RAW_MATERIAL_SOURCE,
    PREMIX_SOURCE,
    BLENDED_MATERIAL_SOURCE,
}
VALID_STATUSES = {"Draft", "Active", "Awaiting Approval", "Completed", "Superseded"}
UNRESOLVED_STATUSES = {"Draft", "Active", "Awaiting Approval"}
FROZEN_STATUSES = {"Awaiting Approval", "Completed", "Superseded"}
PREPARE_ROLES = {"Production Engineer", "Manufacturing Manager", "System Manager"}
APPROVE_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}
CORRECTION_ROLES = APPROVE_ROLES
SETUP_FIELDS = (
    "sequence",
    "feeder_no",
    "used",
    "material_source",
    "item_code",
    "premix_run",
    "blending_run",
    "item_name",
    "bulk_density",
    "max_value_feed_rate",
    "screw_size",
    "barrel_size",
    "min_level",
    "max_level",
)
ONLINE_FIELDS = (
    "sequence",
    "event_datetime",
    "feeder_1_pct",
    "feeder_2_pct",
    "feeder_3_pct",
    "feeder_4_pct",
    "feeder_5_pct",
    "feeder_6_pct",
    "total_pct",
    "observation",
    "shift",
    "recorded_by",
    "recorded_on",
    "back_entry_reason",
)
READING_FIELDS = (
    "sequence",
    "event_datetime",
    "feeder_1_pct",
    "feeder_1_kg",
    "feeder_2_pct",
    "feeder_2_kg",
    "feeder_3_pct",
    "feeder_3_kg",
    "feeder_4_pct",
    "feeder_4_kg",
    "feeder_5_pct",
    "feeder_5_kg",
    "feeder_6_pct",
    "feeder_6_kg",
    "shift",
    "recorded_by",
    "recorded_on",
    "back_entry_reason",
)


def before_insert_feeder_run(doc):
    if not getattr(frappe.flags, "controlled_feeder_run_creation", False):
        frappe.throw(
            _("Create Feeder Run from a running Compounding / Extrusion Job Card cockpit.")
        )
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _set_context(doc, job_card, work_order)
    _validate_duplicate_identity(doc, lock=True)
    _set_document_control(doc)
    doc.status = "Draft"
    doc.production_date = nowdate()
    doc.shift = job_card.get("custom_shift_type") or ""
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.prepared_by = ""
    doc.prepared_on = None
    doc.approved_by = ""
    doc.approved_on = None
    if not doc.get("setup_rows"):
        for sequence in range(1, 7):
            doc.append(
                "setup_rows",
                {"sequence": sequence, "feeder_no": str(sequence), "used": 0},
            )
    doc.set("online_changes", [])
    doc.set("readings", [])


def validate_feeder_run(doc):
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_identity(doc)
    if _protect_frozen_evidence(doc):
        return
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    _validate_fixed_setup(doc, work_order)
    _validate_online_changes(doc, job_card)
    _validate_readings(doc, job_card)
    _set_status_from_evidence(doc)
    _protect_status_transition(doc)
    if doc.status == "Awaiting Approval":
        _validate_completion_evidence(doc)


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(
            _("Feeder Run is available only for a controlled Compounding / Extrusion Job Card.")
        )
    if not job_card.get("work_order") or not frappe.db.exists(
        "Work Order", job_card.work_order
    ):
        frappe.throw(_("The Job Card must reference a valid Work Order."))
    return job_card, frappe.get_doc("Work Order", job_card.work_order)


def _job_card_status(job_card) -> str:
    return cstr(job_card.get("status")).strip().casefold()


def _validate_creation_authority(job_card, work_order):
    if cint(job_card.get("docstatus")) == 2:
        frappe.throw(_("Feeder Run cannot be created for a cancelled Job Card."))
    if not job_card_has_started(job_card):
        frappe.throw(
            _("Start Compounding / Extrusion Job Card {0} before creating Feeder Run.").format(
                job_card.name
            )
        )
    if _job_card_status(job_card) != "work in progress":
        frappe.throw(
            _("New Feeder Run requires Job Card {0} to be Work In Progress.").format(
                job_card.name
            )
        )
    if cstr(job_card.work_order).strip() != cstr(work_order.name).strip():
        frappe.throw(_("Feeder Run Work Order lineage does not match its Job Card."))
    if not cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip():
        frappe.throw(_("FG Batch must be allocated before creating Feeder Run."))


def _validate_event_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) != "work in progress":
        frappe.throw(_("New Feeder evidence requires the Job Card to be Work In Progress."))


def _validate_resolution_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) in {
        "completed",
        "closed",
        "cancelled",
    }:
        frappe.throw(_("Feeder Run cannot be resolved after its Job Card is closed or cancelled."))


def _set_context(doc, job_card, work_order):
    context = {
        "work_order": job_card.work_order,
        "operation": job_card.operation,
        "production_line": job_card.get("custom_production_line") or "",
        "machine": job_card.get("custom_machine") or job_card.get("workstation") or "",
        "fg_item": work_order.production_item,
        "fg_batch": job_card.get("custom_fg_batch_no")
        or work_order.get("custom_fg_batch_no")
        or "",
        "bom_no": job_card.get("bom_no") or work_order.bom_no,
        "planned_job_qty": job_card.get("for_quantity") or work_order.qty,
        "company": work_order.company,
    }
    for fieldname, value in context.items():
        doc.set(fieldname, value)


def _set_document_control(doc):
    if doc.is_new():
        doc.controlled_document_code = DOCUMENT_CODE
        doc.controlled_revision = DOCUMENT_REVISION
        doc.source_system = SOURCE_SYSTEM
    elif (
        cstr(doc.get("controlled_document_code")) != DOCUMENT_CODE
        or cstr(doc.get("controlled_revision")) != DOCUMENT_REVISION
        or cstr(doc.get("source_system")) != SOURCE_SYSTEM
    ):
        frappe.throw(_("Controlled document identity and source cannot be changed."))


def _protect_identity(doc):
    if doc.is_new() or not frappe.db.exists(FEEDER_RUN_DOCTYPE, doc.name):
        return
    protected = ("job_card", "work_order", "fg_item", "fg_batch")
    previous = frappe.db.get_value(
        FEEDER_RUN_DOCTYPE, doc.name, list(protected), as_dict=True
    )
    if any(cstr(doc.get(field)) != cstr(previous.get(field)) for field in protected):
        frappe.throw(_("Feeder Run Job Card, Work Order, FG Item and FG Batch cannot be changed."))


def _validate_duplicate_identity(doc, lock: bool = False):
    if lock:
        frappe.db.sql("SELECT name FROM `tabJob Card` WHERE name = %s FOR UPDATE", doc.job_card)
    filters = {
        "job_card": doc.job_card,
        "fg_batch": doc.fg_batch,
        "status": ("!=", "Superseded"),
    }
    existing = frappe.get_all(
        FEEDER_RUN_DOCTYPE,
        filters=filters,
        pluck="name",
    )
    allowed_original = cstr(doc.get("supersedes")).strip()
    conflicts = [name for name in existing if name != doc.get("name") and name != allowed_original]
    if conflicts:
        frappe.throw(
            _("Feeder Run {0} already owns this Job Card and FG Batch context.").format(
                conflicts[0]
            )
        )


def prevent_feeder_setup_delete(doc):
    if not getattr(frappe.flags, "controlled_feeder_run_delete", False):
        frappe.throw(_("Controlled Feeder Setup rows cannot be deleted."))


def _allowed_wip_items(work_order: str) -> set[str]:
    return {
        cstr(row.get("item_code")).strip()
        for row in get_wip_consumption_preview(work_order).get("rows") or []
        if cstr(row.get("item_code")).strip()
    }


def _persisted_source_reference(row, fieldname: str, value: str) -> bool:
    if not _child_exists(SETUP_DOCTYPE, row):
        return False
    persisted = frappe.db.get_value(
        SETUP_DOCTYPE,
        row.name,
        ["material_source", fieldname],
        as_dict=True,
    )
    return bool(
        persisted
        and cstr(persisted.get("material_source")).strip()
        == cstr(row.get("material_source")).strip()
        and cstr(persisted.get(fieldname)).strip() == value
    )


def _unchanged_legacy_setup_without_source(row) -> bool:
    if not _child_exists(SETUP_DOCTYPE, row):
        return False
    comparable_fields = tuple(
        fieldname for fieldname in SETUP_FIELDS if fieldname != "item_name"
    )
    persisted = frappe.db.get_value(
        SETUP_DOCTYPE,
        row.name,
        list(comparable_fields),
        as_dict=True,
    )
    return bool(
        persisted
        and not cstr(persisted.get("material_source")).strip()
        and all(
            cstr(persisted.get(fieldname)).strip()
            == cstr(row.get(fieldname)).strip()
            for fieldname in comparable_fields
        )
    )


def _validate_process_source(doc, row, doctype: str, fieldname: str):
    reference = cstr(row.get(fieldname)).strip()
    if not reference:
        frappe.throw(
            _("Feeder {0}: {1} is required for Material Source {2}.").format(
                row.feeder_no, doctype, row.material_source
            )
        )

    # Preserve an exact historical assignment even if its source is corrected later.
    if _persisted_source_reference(row, fieldname, reference):
        source = frappe.get_doc(doctype, reference)
        return source

    if not frappe.db.exists(doctype, reference):
        frappe.throw(_("Feeder {0}: {1} {2} does not exist.").format(row.feeder_no, doctype, reference))
    source = frappe.get_doc(doctype, reference)
    source.check_permission("read")
    expected = {
        "job_card": doc.job_card,
        "work_order": doc.work_order,
        "fg_item": doc.fg_item,
        "fg_batch": doc.fg_batch,
    }
    mismatched = [
        fieldname
        for fieldname, expected_value in expected.items()
        if cstr(source.get(fieldname)).strip() != cstr(expected_value).strip()
    ]
    if mismatched:
        frappe.throw(
            _("Feeder {0}: {1} {2} does not match this Job Card, Work Order, FG Item and FG Batch context.").format(
                row.feeder_no, doctype, reference
            )
        )
    if cstr(source.get("status")).strip() != "Completed" or cstr(
        source.get("superseded_by")
    ).strip():
        frappe.throw(
            _("Feeder {0}: {1} {2} must be current Completed evidence.").format(
                row.feeder_no, doctype, reference
            )
        )
    return source


def _validate_fixed_setup(doc, work_order):
    rows = doc.get("setup_rows") or []
    if len(rows) != 6:
        frappe.throw(_("F-FRS-01 requires exactly six controlled Feeder Setup rows."))
    actual = [(cint(row.get("sequence")), cstr(row.get("feeder_no"))) for row in rows]
    expected = [(value, str(value)) for value in range(1, 7)]
    if actual != expected:
        frappe.throw(
            _("Feeder Setup rows are fixed as Feeders 1 through 6 and cannot be added, deleted, duplicated, moved or reordered.")
        )
    allowed_items = _allowed_wip_items(work_order.name)
    for row in rows:
        source = cstr(row.get("material_source")).strip()
        item_code = cstr(row.get("item_code")).strip()
        premix_run = cstr(row.get("premix_run")).strip()
        blending_run = cstr(row.get("blending_run")).strip()
        if not cint(row.get("used")):
            if source or item_code or premix_run or blending_run:
                frappe.throw(_("Feeder {0}: Unused feeders cannot retain a material assignment.").format(row.feeder_no))
            row.item_name = ""
        elif not source and _unchanged_legacy_setup_without_source(row):
            pass
        elif source not in MATERIAL_SOURCES:
            frappe.throw(_("Feeder {0}: Material Source is required when Used is selected.").format(row.feeder_no))
        elif source == RAW_MATERIAL_SOURCE:
            if premix_run or blending_run:
                frappe.throw(_("Feeder {0}: Raw Material cannot contain Premix or Blending references.").format(row.feeder_no))
            if not item_code:
                frappe.throw(_("Feeder {0}: Raw Material Item is required.").format(row.feeder_no))
            if item_code not in allowed_items:
                frappe.throw(
                    _("Feeder {0}: Material {1} is not available in exact WO-attributed WIP.").format(
                        row.feeder_no, item_code
                    )
                )
            row.item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
        elif source == PREMIX_SOURCE:
            if item_code or blending_run:
                frappe.throw(_("Feeder {0}: Premix cannot contain Raw Material or Blending references.").format(row.feeder_no))
            source_doc = _validate_process_source(doc, row, "Premix Run", "premix_run")
            row.item_name = cstr(source_doc.get("run_number") or source_doc.name).strip()
        else:
            if item_code or premix_run:
                frappe.throw(_("Feeder {0}: Blended Material cannot contain Raw Material or Premix references.").format(row.feeder_no))
            source_doc = _validate_process_source(doc, row, "Blending Run", "blending_run")
            row.item_name = cstr(source_doc.get("run_number") or source_doc.name).strip()
        if _child_exists(SETUP_DOCTYPE, row) and not getattr(
            frappe.flags, "controlled_feeder_setup_write", False
        ):
            _protect_child(row, SETUP_DOCTYPE, SETUP_FIELDS, _("Use the controlled Edit Setup action."))


def _normalize_number(value):
    if value in (None, ""):
        return None
    value = flt(value)
    if value < 0:
        frappe.throw(_("Feeder percentage and Kg evidence cannot be negative."))
    return value


def _validate_online_changes(doc, job_card):
    for sequence, row in enumerate(doc.get("online_changes") or [], start=1):
        existing = _child_exists(ONLINE_CHANGE_DOCTYPE, row)
        if not existing:
            if not getattr(frappe.flags, "controlled_feeder_online_write", False):
                frappe.throw(_("Add Online Changes through the controlled action."))
            _validate_event_authority(job_card)
            row.event_datetime = now_datetime()
            row.recorded_by = frappe.session.user
            row.recorded_on = now_datetime()
            row.shift = job_card.get("custom_shift_type") or ""
            row.back_entry_reason = ""
        else:
            _protect_child(
                row,
                ONLINE_CHANGE_DOCTYPE,
                ONLINE_FIELDS,
                _("Saved Feeder Online Changes are append-only and cannot be rewritten."),
            )
        row.sequence = sequence
        total = 0.0
        for number in FEEDER_NUMBERS:
            fieldname = f"feeder_{number}_pct"
            row.set(fieldname, _normalize_number(row.get(fieldname)))
            total += flt(row.get(fieldname))
        row.total_pct = total
        if not cstr(row.get("observation")).strip():
            frappe.throw(_("Reason / Observation is mandatory for every Online Change."))


def _validate_readings(doc, job_card):
    for sequence, row in enumerate(doc.get("readings") or [], start=1):
        existing = _child_exists(READING_DOCTYPE, row)
        if not existing:
            if not getattr(frappe.flags, "controlled_feeder_reading_write", False):
                frappe.throw(_("Add Feeder HMI Readings through the controlled action."))
            _validate_event_authority(job_card)
            row.event_datetime = now_datetime()
            row.recorded_by = frappe.session.user
            row.recorded_on = now_datetime()
            row.shift = job_card.get("custom_shift_type") or ""
            row.back_entry_reason = ""
        else:
            _protect_child(
                row,
                READING_DOCTYPE,
                READING_FIELDS,
                _("Saved Feeder HMI Readings are append-only and cannot be rewritten."),
            )
        row.sequence = sequence
        has_value = False
        for number in FEEDER_NUMBERS:
            for suffix in ("pct", "kg"):
                fieldname = f"feeder_{number}_{suffix}"
                if row.get(fieldname) not in (None, ""):
                    has_value = True
                row.set(fieldname, _normalize_number(row.get(fieldname)))
        if not has_value:
            frappe.throw(_("A Feeder HMI Reading requires at least one Percentage or Kg value."))


def _child_exists(doctype: str, row) -> bool:
    return bool(row.get("name") and frappe.db.exists(doctype, row.name))


def _protect_child(row, doctype: str, fields: tuple[str, ...], message: str):
    previous = frappe.db.get_value(doctype, row.name, list(fields), as_dict=True)
    if previous and any(row.get(field) != previous.get(field) for field in fields):
        frappe.throw(message)


def _set_status_from_evidence(doc):
    if doc.status == "Draft" and (doc.get("online_changes") or doc.get("readings")):
        doc.status = "Active"


def _protect_status_transition(doc):
    status = cstr(doc.get("status") or "Draft")
    if status not in VALID_STATUSES:
        frappe.throw(_("Invalid Feeder Run status {0}.").format(status))
    doc.status = status
    if doc.is_new() or not frappe.db.exists(FEEDER_RUN_DOCTYPE, doc.name):
        if status != "Draft":
            frappe.throw(_("New Feeder Run must begin in Draft status."))
        return
    previous = frappe.db.get_value(FEEDER_RUN_DOCTYPE, doc.name, "status")
    if status == previous:
        return
    allowed = (
        previous == "Draft"
        and status == "Active"
        and (
            getattr(frappe.flags, "controlled_feeder_online_write", False)
            or getattr(frappe.flags, "controlled_feeder_reading_write", False)
        )
    ) or (
        previous in {"Draft", "Active"}
        and status == "Awaiting Approval"
        and getattr(frappe.flags, "controlled_feeder_prepare", False)
    ) or (
        previous == "Awaiting Approval"
        and status == "Completed"
        and getattr(frappe.flags, "controlled_feeder_approve", False)
    )
    if allowed:
        return
    frappe.throw(_("Use the controlled Feeder Run lifecycle actions to change status."))


def _protect_frozen_evidence(doc) -> bool:
    if doc.is_new() or not frappe.db.exists(FEEDER_RUN_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(FEEDER_RUN_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    if (
        previous.status == "Awaiting Approval"
        and doc.status == "Completed"
        and getattr(frappe.flags, "controlled_feeder_approve", False)
    ):
        allowed = {"status", "approved_by", "approved_on", "modified", "modified_by"}
        _assert_only_fields_changed(doc, previous, allowed)
        return False
    _assert_only_fields_changed(doc, previous, {"modified", "modified_by"})
    return True


def _assert_only_fields_changed(doc, previous, allowed: set[str]):
    for field in doc.meta.fields:
        if field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"}:
            continue
        if field.fieldname in allowed:
            continue
        current_value = _comparable_field_value(doc.get(field.fieldname))
        previous_value = _comparable_field_value(previous.get(field.fieldname))
        if current_value != previous_value:
            frappe.throw(
                _("Prepared or completed Feeder Run evidence is immutable. Field {0} changed; create a correction instead.").format(
                    field.label or field.fieldname
                )
            )


def _comparable_field_value(value):
    """Compare reloaded child documents by persisted values, not object identity."""
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        return [_comparable_field_value(row) for row in value]
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    if isinstance(value, dict):
        return {
            key: _comparable_field_value(child_value)
            for key, child_value in value.items()
            if not str(key).startswith("_") and key not in {"modified", "modified_by"}
        }
    return value


def _validate_completion_evidence(doc):
    _validate_fixed_setup(doc, frappe.get_doc("Work Order", doc.work_order))
    if not any(cint(row.get("used")) for row in doc.setup_rows):
        frappe.throw(_("At least one Feeder Setup row must be marked Used before preparation."))
    if not (doc.get("online_changes") or doc.get("readings")):
        frappe.throw(_("At least one Feeder Reading or Online Change is required before preparation."))


def validate_feeder_child_document(doc, doctype: str, fields: tuple[str, ...], flag: str):
    if not doc.get("parent"):
        frappe.throw(_("Controlled Feeder evidence requires a Feeder Run parent."))
    if doc.is_new() and not getattr(frappe.flags, flag, False):
        frappe.throw(_("Add Feeder evidence through its controlled Feeder Run action."))
    if not doc.is_new() and frappe.db.exists(doctype, doc.name) and not getattr(
        frappe.flags, flag, False
    ):
        _protect_child(doc, doctype, fields, _("Saved Feeder evidence is append-only."))


def prevent_feeder_child_delete(doc):
    frappe.throw(_("Saved Feeder evidence is append-only and cannot be deleted."))


def prevent_feeder_run_delete(doc):
    if doc.status != "Draft" or doc.get("online_changes") or doc.get("readings"):
        frappe.throw(_("Only an unevidenced Draft Feeder Run can be deleted."))
    previous = getattr(frappe.flags, "controlled_feeder_run_delete", False)
    frappe.flags.controlled_feeder_run_delete = True
    doc.flags._restore_controlled_feeder_delete = previous


def _insert_controlled(doc):
    previous = getattr(frappe.flags, "controlled_feeder_run_creation", False)
    previous_setup = getattr(frappe.flags, "controlled_feeder_setup_write", False)
    frappe.flags.controlled_feeder_run_creation = True
    frappe.flags.controlled_feeder_setup_write = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_feeder_run_creation = previous
        frappe.flags.controlled_feeder_setup_write = previous_setup
    return doc


def _check_roles(allowed: set[str], message: str):
    if not allowed.intersection(set(frappe.get_roles())):
        frappe.throw(_(message), frappe.PermissionError)


@frappe.whitelist()
def create_feeder_run(job_card: str):
    if not frappe.has_permission(FEEDER_RUN_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create Feeder Run."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    doc = frappe.new_doc(FEEDER_RUN_DOCTYPE)
    doc.job_card = job_card_doc.name
    _insert_controlled(doc)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def update_feeder_setup(
    name: str,
    feeder_no: str,
    used=0,
    material_source: str = "",
    item_code: str = "",
    premix_run: str = "",
    blending_run: str = "",
    **values,
):
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Feeder Setup can be edited only in Draft or Active status."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_event_authority(job_card)
    row = next((item for item in doc.setup_rows if item.feeder_no == cstr(feeder_no)), None)
    if not row:
        frappe.throw(_("A valid controlled Feeder number is required."))
    allowed = {
        "bulk_density",
        "max_value_feed_rate",
        "screw_size",
        "barrel_size",
        "min_level",
        "max_level",
    }
    row.used = cint(used)
    row.material_source = cstr(material_source).strip() if row.used else ""
    row.item_code = (
        cstr(item_code).strip()
        if row.material_source == RAW_MATERIAL_SOURCE
        else ""
    )
    row.premix_run = (
        cstr(premix_run).strip() if row.material_source == PREMIX_SOURCE else ""
    )
    row.blending_run = (
        cstr(blending_run).strip()
        if row.material_source == BLENDED_MATERIAL_SOURCE
        else ""
    )
    for fieldname in allowed:
        row.set(fieldname, cstr(values.get(fieldname)).strip())
    previous = getattr(frappe.flags, "controlled_feeder_setup_write", False)
    frappe.flags.controlled_feeder_setup_write = True
    try:
        doc.save()
    finally:
        frappe.flags.controlled_feeder_setup_write = previous
    return {"name": doc.name, "status": doc.status, "feeder_no": row.feeder_no}


@frappe.whitelist()
def add_online_change(name: str, observation: str, **percentages):
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Online Changes can be added only in Draft or Active status."))
    values = {f"feeder_{number}_pct": percentages.get(f"feeder_{number}_pct") for number in FEEDER_NUMBERS}
    values["observation"] = cstr(observation).strip()
    previous = getattr(frappe.flags, "controlled_feeder_online_write", False)
    frappe.flags.controlled_feeder_online_write = True
    try:
        doc.append("online_changes", values)
        doc.save()
    finally:
        frappe.flags.controlled_feeder_online_write = previous
    row = doc.online_changes[-1]
    return {"name": doc.name, "status": doc.status, "event": row.name, "total_pct": flt(row.total_pct)}


@frappe.whitelist()
def add_feeder_reading(name: str, **values):
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Readings can be added only in Draft or Active status."))
    row_values = {}
    for number in FEEDER_NUMBERS:
        row_values[f"feeder_{number}_pct"] = values.get(f"feeder_{number}_pct")
        row_values[f"feeder_{number}_kg"] = values.get(f"feeder_{number}_kg")
    previous = getattr(frappe.flags, "controlled_feeder_reading_write", False)
    frappe.flags.controlled_feeder_reading_write = True
    try:
        doc.append("readings", row_values)
        doc.save()
    finally:
        frappe.flags.controlled_feeder_reading_write = previous
    row = doc.readings[-1]
    return {"name": doc.name, "status": doc.status, "event": row.name, "sequence": row.sequence}


@frappe.whitelist()
def prepare_feeder_run(name: str):
    _check_roles(PREPARE_ROLES, "Production Engineer or Manufacturing Manager authority is required to prepare Feeder Run.")
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only Draft or Active Feeder Run can be prepared."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_resolution_authority(job_card)
    _validate_completion_evidence(doc)
    previous = getattr(frappe.flags, "controlled_feeder_prepare", False)
    frappe.flags.controlled_feeder_prepare = True
    try:
        doc.status = "Awaiting Approval"
        doc.prepared_by = frappe.session.user
        doc.prepared_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_feeder_prepare = previous
    return {"name": doc.name, "status": doc.status, "prepared_by": doc.prepared_by, "prepared_on": doc.prepared_on}


@frappe.whitelist()
def approve_feeder_run(name: str):
    _check_roles(APPROVE_ROLES, "Production Head or Manufacturing Manager authority is required to approve Feeder Run.")
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "Awaiting Approval":
        frappe.throw(_("Only an Awaiting Approval Feeder Run can be approved."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_resolution_authority(job_card)
    previous = getattr(frappe.flags, "controlled_feeder_approve", False)
    frappe.flags.controlled_feeder_approve = True
    try:
        doc.status = "Completed"
        doc.approved_by = frappe.session.user
        doc.approved_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_feeder_approve = previous
    return {"name": doc.name, "status": doc.status, "approved_by": doc.approved_by, "approved_on": doc.approved_on}


@frappe.whitelist()
def make_feeder_run_correction(name: str, reason: str):
    _check_roles(CORRECTION_ROLES, "Production Head or Manufacturing Manager authority is required for correction.")
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only Completed Feeder Run can be corrected."))
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.status = "Draft"
    correction.set("online_changes", [])
    correction.set("readings", [])
    correction.prepared_by = ""
    correction.prepared_on = None
    correction.approved_by = ""
    correction.approved_on = None
    correction.supersedes = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    _insert_controlled(correction)
    frappe.db.set_value(
        FEEDER_RUN_DOCTYPE,
        original.name,
        {"status": "Superseded", "superseded_by": correction.name},
        update_modified=True,
    )
    return {"name": correction.name, "supersedes": original.name}


def get_feeder_run_module(job_card: str, creation_allowed: bool, current_shift: str = "") -> dict[str, Any]:
    rows = frappe.get_all(
        FEEDER_RUN_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "status", "modified", "prepared_by", "approved_by"],
        order_by="modified desc",
    )
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    latest = rows[0] if rows else None
    parent_names = [row.name for row in rows]
    setup_rows = (
        frappe.get_all(SETUP_DOCTYPE, filters={"parent": latest.name, "used": 1}, pluck="name")
        if latest
        else []
    )
    reading_rows = (
        frappe.get_all(READING_DOCTYPE, filters={"parent": ("in", parent_names)}, fields=["event_datetime"], order_by="event_datetime desc", limit=1)
        if parent_names
        else []
    )
    change_rows = (
        frappe.get_all(ONLINE_CHANGE_DOCTYPE, filters={"parent": ("in", parent_names)}, fields=["event_datetime"], order_by="event_datetime desc", limit=1)
        if parent_names
        else []
    )
    status = next(
        (candidate for candidate in ("Awaiting Approval", "Active", "Draft") if counts[candidate]),
        "Completed" if counts["Completed"] else "Superseded" if rows else "Optional",
    )
    return {
        "key": "feeder_run",
        "label": "Feeder Run",
        "document_code": DOCUMENT_CODE,
        "revision": DOCUMENT_REVISION,
        "source": SOURCE_SYSTEM,
        "status": status,
        "optional": True,
        "record_count": len(rows),
        "counts": {key: counts[key] for key in VALID_STATUSES},
        "active_feeders": len(setup_rows),
        "reading_count": frappe.db.count(READING_DOCTYPE, {"parent": ("in", parent_names)}) if parent_names else 0,
        "online_change_count": frappe.db.count(ONLINE_CHANGE_DOCTYPE, {"parent": ("in", parent_names)}) if parent_names else 0,
        "last_reading": reading_rows[0].event_datetime if reading_rows else "",
        "last_change": change_rows[0].event_datetime if change_rows else "",
        "latest_record": latest.name if latest else "",
        "prepared_by": latest.prepared_by if latest else "",
        "approved_by": latest.approved_by if latest else "",
        "action_enabled": bool(creation_allowed),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "shared_dataset": False,
    }


def validate_feeder_run_completion(doc):
    unresolved = frappe.get_all(
        FEEDER_RUN_DOCTYPE,
        filters={"job_card": doc.name, "status": ("in", sorted(UNRESOLVED_STATUSES))},
        fields=["name", "status"],
        order_by="creation asc",
    )
    if unresolved:
        details = ", ".join(f"{row.name} ({row.status})" for row in unresolved)
        frappe.throw(_("Resolve Feeder Run record(s) before completing this Job Card: {0}.").format(details))


def _eligible_process_sources(doctype: str, doc, txt: str = "") -> list[dict[str, Any]]:
    filters = {
        "job_card": doc.job_card,
        "work_order": doc.work_order,
        "fg_item": doc.fg_item,
        "fg_batch": doc.fg_batch,
        "status": "Completed",
        "superseded_by": ("is", "not set"),
    }
    or_filters = None
    if txt:
        like = f"%{txt}%"
        or_filters = {"name": ("like", like), "run_number": ("like", like)}
    return frappe.get_list(
        doctype,
        filters=filters,
        or_filters=or_filters,
        fields=["name", "run_number", "status"],
        order_by="creation asc, name asc",
    )


@frappe.whitelist()
def get_feeder_material_eligibility(name: str):
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, name)
    doc.check_permission("read")
    raw_items = sorted(_allowed_wip_items(doc.work_order))
    item_names = dict(
        frappe.get_all(
            "Item",
            filters={"name": ("in", raw_items)},
            fields=["name", "item_name"],
            as_list=True,
        )
    ) if raw_items else {}
    return {
        "sources": [RAW_MATERIAL_SOURCE, PREMIX_SOURCE, BLENDED_MATERIAL_SOURCE],
        "raw_materials": [
            {"value": item, "label": item, "description": item_names.get(item) or item}
            for item in raw_items
        ],
        "premix_runs": [
            {"value": row.name, "label": row.run_number or row.name}
            for row in _eligible_process_sources("Premix Run", doc)
        ],
        "blending_runs": [
            {"value": row.name, "label": row.run_number or row.name}
            for row in _eligible_process_sources("Blending Run", doc)
        ],
    }


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def feeder_item_query(doctype, txt, searchfield, start, page_len, filters):
    rows = get_wip_consumption_preview((filters or {}).get("work_order")).get("rows") or []
    items = sorted(
        {
            cstr(row.get("item_code"))
            for row in rows
            if txt.casefold() in cstr(row.get("item_code")).casefold()
        }
    )
    return [(item,) for item in items[start : start + page_len]]


def _feeder_process_query(source_doctype, txt, start, page_len, filters):
    feeder_run_name = cstr((filters or {}).get("feeder_run")).strip()
    if not feeder_run_name:
        return []
    doc = frappe.get_doc(FEEDER_RUN_DOCTYPE, feeder_run_name)
    doc.check_permission("read")
    rows = _eligible_process_sources(source_doctype, doc, txt)
    return [(row.name, row.run_number or row.name) for row in rows[start : start + page_len]]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def feeder_premix_query(doctype, txt, searchfield, start, page_len, filters):
    return _feeder_process_query("Premix Run", txt, start, page_len, filters)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def feeder_blending_query(doctype, txt, searchfield, start, page_len, filters):
    return _feeder_process_query("Blending Run", txt, start, page_len, filters)
