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
from calco_erp.calco_production.wip_consumption import get_execution_wip_context


MONITOR_DOCTYPE = "Bulk Density Monitor"
READING_DOCTYPE = "Bulk Density Reading"
DOCUMENT_CODE = "F-BDMS-01"
DOCUMENT_REVISION = "REV-01"
VALID_STATUSES = {"Draft", "Active", "Completed", "Superseded"}
FROZEN_STATUSES = {"Completed", "Superseded"}
FEEDER_NUMBERS = {str(value) for value in range(1, 7)}
CONFIRMATION_ROLES = {
    "Production Engineer",
    "Production Head",
    "Manufacturing Manager",
    "System Manager",
}
COMPLETION_ROLES = CONFIRMATION_ROLES
CORRECTION_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}
EPSILON = 0.000001


def before_insert_bulk_density_monitor(doc):
    if not getattr(frappe.flags, "controlled_bulk_density_creation", False):
        frappe.throw(
            _(
                "Create Bulk Density Monitor from a running Compounding / Extrusion Job Card cockpit."
            )
        )
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _validate_current_monitor_identity(doc, job_card, work_order)
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    doc.status = "Draft"
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.set("readings", [])


def validate_bulk_density_monitor(doc):
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_parent_identity(doc)
    if _protect_frozen_evidence(doc):
        return
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    _protect_reading_set(doc)
    _validate_readings(doc, job_card, work_order)
    _set_status_from_evidence(doc)
    _protect_status_transition(doc)
    if doc.status == "Completed":
        _validate_completion_evidence(doc)


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(
            _(
                "Bulk Density Monitoring is available only for a controlled Compounding / Extrusion Job Card."
            )
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
        frappe.throw(_("Bulk Density Monitor cannot be created for a cancelled Job Card."))
    if not job_card_has_started(job_card):
        frappe.throw(
            _(
                "Start Compounding / Extrusion Job Card {0} before creating Bulk Density Monitor."
            ).format(job_card.name)
        )
    if _job_card_status(job_card) != "work in progress":
        frappe.throw(
            _(
                "New Bulk Density Monitor requires Job Card {0} to be Work In Progress."
            ).format(job_card.name)
        )
    if cstr(job_card.work_order).strip() != cstr(work_order.name).strip():
        frappe.throw(_("Bulk Density Monitor Work Order lineage does not match its Job Card."))
    if not cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip():
        frappe.throw(_("FG Batch must be allocated before creating Bulk Density Monitor."))


def _validate_new_reading_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) != "work in progress":
        frappe.throw(_("New Bulk Density readings require the Job Card to be Work In Progress."))


def _validate_confirmation_context(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) in {
        "completed",
        "closed",
        "cancelled",
    }:
        frappe.throw(_("Bulk Density readings cannot be confirmed after the Job Card is closed or cancelled."))


def _validate_completion_context(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) != "work in progress":
        frappe.throw(_("Bulk Density Monitor can be completed only while the Job Card is Work In Progress."))


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
        "production_date": job_card.get("posting_date") or work_order.get("planned_start_date") or nowdate(),
        "shift": job_card.get("custom_shift_type") or "",
    }
    for fieldname, value in context.items():
        doc.set(fieldname, value)


def _set_document_control(doc):
    if doc.is_new():
        doc.controlled_document_code = DOCUMENT_CODE
        doc.controlled_revision = DOCUMENT_REVISION
    elif (
        cstr(doc.get("controlled_document_code")) != DOCUMENT_CODE
        or cstr(doc.get("controlled_revision")) != DOCUMENT_REVISION
    ):
        frappe.throw(_("Controlled document identity cannot be changed."))


def _validate_current_monitor_identity(doc, job_card, work_order):
    fg_batch = cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip()
    if doc.get("correction_of"):
        return
    existing = frappe.db.get_value(
        MONITOR_DOCTYPE,
        {
            "job_card": job_card.name,
            "fg_batch": fg_batch,
            "status": ("!=", "Superseded"),
        },
        "name",
    )
    if existing and existing != doc.get("name"):
        frappe.throw(
            _(
                "Bulk Density Monitor {0} already owns this Job Card and FG Batch context."
            ).format(existing)
        )


PARENT_IDENTITY_FIELDS = (
    "job_card",
    "work_order",
    "operation",
    "production_line",
    "machine",
    "fg_item",
    "fg_batch",
    "bom_no",
    "planned_job_qty",
    "company",
    "production_date",
    "shift",
    "recorded_by",
    "recorded_on",
    "correction_of",
)


def _protect_parent_identity(doc):
    if doc.is_new() or not frappe.db.exists(MONITOR_DOCTYPE, doc.name):
        return
    previous = frappe.db.get_value(
        MONITOR_DOCTYPE, doc.name, list(PARENT_IDENTITY_FIELDS), as_dict=True
    )
    if any(doc.get(field) != previous.get(field) for field in PARENT_IDENTITY_FIELDS):
        frappe.throw(_("Bulk Density Monitor execution context cannot be changed."))


def _reading_exists(row) -> bool:
    return bool(row.get("name") and frappe.db.exists(READING_DOCTYPE, row.name))


READING_EVIDENCE_FIELDS = (
    "sequence",
    "event_datetime",
    "rm_item",
    "rm_item_name",
    "rm_batch",
    "feeder_no",
    "w1_kg",
    "w2_kg",
    "bulk_density_kg_l",
    "observation",
    "shift",
    "recorded_by",
    "recorded_on",
    "correction_of_reading",
    "correction_reason",
)
READING_CONFIRMATION_FIELDS = ("confirmed", "confirmed_by", "confirmed_on")


def _protect_reading_set(doc):
    if doc.is_new() or not frappe.db.exists(MONITOR_DOCTYPE, doc.name):
        return
    saved_names = set(
        frappe.get_all(READING_DOCTYPE, filters={"parent": doc.name}, pluck="name")
    )
    current_names = {cstr(row.get("name")) for row in doc.get("readings") or [] if row.get("name")}
    if not saved_names.issubset(current_names):
        frappe.throw(_("Saved Bulk Density readings are append-only and cannot be deleted."))


def _validate_readings(doc, job_card, work_order):
    wip_rows = get_execution_wip_context(work_order.name).get("rows") or []
    options = {
        (cstr(row.get("item_code")).strip(), cstr(row.get("batch_no")).strip()): row
        for row in wip_rows
    }
    for sequence, row in enumerate(doc.get("readings") or [], start=1):
        is_existing = _reading_exists(row)
        if not is_existing:
            if not getattr(frappe.flags, "controlled_bulk_density_reading_write", False):
                frappe.throw(_("Add readings through the controlled Add Bulk Density Reading action."))
            _validate_new_reading_authority(job_card)
            row.sequence = sequence
            row.event_datetime = now_datetime()
            row.recorded_by = frappe.session.user
            row.recorded_on = now_datetime()
            row.shift = job_card.get("custom_shift_type") or ""
            row.confirmed = 0
            row.confirmed_by = ""
            row.confirmed_on = None
            _validate_new_reading_values(row, options, doc)
        else:
            _protect_saved_reading(row)
            _validate_stored_reading_values(row)


def _validate_new_reading_values(row, options, doc):
    row.rm_item = cstr(row.get("rm_item")).strip()
    row.rm_batch = cstr(row.get("rm_batch")).strip()
    row.feeder_no = cstr(row.get("feeder_no")).strip()
    if (row.rm_item, row.rm_batch) not in options:
        frappe.throw(
            _(
                "RM Item {0}, Batch {1} is not exact WO-attributed WIP for this Job Card."
            ).format(row.rm_item or _("blank"), row.rm_batch or _("blank"))
        )
    if row.feeder_no not in FEEDER_NUMBERS:
        frappe.throw(_("Feeder No. must be one of 1, 2, 3, 4, 5 or 6."))
    row.rm_item_name = frappe.db.get_value("Item", row.rm_item, "item_name") or row.rm_item
    _validate_manual_values(row)
    correction_of = cstr(row.get("correction_of_reading")).strip()
    if correction_of:
        original = next(
            (
                item
                for item in doc.get("readings") or []
                if cstr(item.get("name")) == correction_of
            ),
            None,
        )
        if not original:
            frappe.throw(_("Correction must reference a saved reading in the same monitor."))
        if not cstr(row.get("correction_reason")).strip():
            frappe.throw(_("Reading Correction Reason is mandatory."))


def _validate_stored_reading_values(row):
    if cstr(row.get("feeder_no")).strip() not in FEEDER_NUMBERS:
        frappe.throw(_("Stored Feeder No. must remain one of 1, 2, 3, 4, 5 or 6."))
    _validate_manual_values(row)


def _validate_manual_values(row):
    for fieldname, label in (
        ("w1_kg", _("Empty Weight of Equipment W1 (Kg)")),
        ("w2_kg", _("Filled Equipment + Material W2 (Kg)")),
        ("bulk_density_kg_l", _("Bulk Density (Kg/Ltr)")),
    ):
        if row.get(fieldname) in (None, "") or flt(row.get(fieldname)) <= EPSILON:
            frappe.throw(_("{0} must be a positive manually entered value.").format(label))
        row.set(fieldname, round(flt(row.get(fieldname)), 6))


def _protect_saved_reading(row):
    previous = frappe.db.get_value(
        READING_DOCTYPE,
        row.name,
        list(READING_EVIDENCE_FIELDS + READING_CONFIRMATION_FIELDS),
        as_dict=True,
    )
    if any(row.get(field) != previous.get(field) for field in READING_EVIDENCE_FIELDS):
        frappe.throw(_("Saved Bulk Density readings are append-only and cannot be rewritten."))
    if any(row.get(field) != previous.get(field) for field in READING_CONFIRMATION_FIELDS) and not getattr(
        frappe.flags, "controlled_bulk_density_confirmation", False
    ):
        frappe.throw(_("Use the controlled Confirm Reading action."))


def _effective_readings(doc):
    corrected = {
        cstr(row.get("correction_of_reading")).strip()
        for row in doc.get("readings") or []
        if row.get("correction_of_reading")
    }
    return [row for row in doc.get("readings") or [] if cstr(row.get("name")) not in corrected]


def _set_status_from_evidence(doc):
    if doc.status == "Draft" and doc.get("readings"):
        doc.status = "Active"


def _protect_status_transition(doc):
    status = cstr(doc.get("status") or "Draft")
    if status not in VALID_STATUSES:
        frappe.throw(_("Invalid Bulk Density Monitor status {0}.").format(status))
    doc.status = status
    if doc.is_new() or not frappe.db.exists(MONITOR_DOCTYPE, doc.name):
        if status != "Draft":
            frappe.throw(_("New Bulk Density Monitor must begin in Draft status."))
        return
    previous = frappe.db.get_value(MONITOR_DOCTYPE, doc.name, "status")
    if status == previous:
        return
    if previous == "Draft" and status == "Active" and getattr(
        frappe.flags, "controlled_bulk_density_reading_write", False
    ):
        return
    if previous in {"Draft", "Active"} and status == "Completed" and getattr(
        frappe.flags, "controlled_bulk_density_completion", False
    ):
        return
    frappe.throw(_("Use controlled Bulk Density Monitor lifecycle actions to change status."))


def _validate_completion_evidence(doc):
    readings = _effective_readings(doc)
    if not readings:
        frappe.throw(_("At least one valid Bulk Density reading is required before Completion."))
    unresolved = [str(row.sequence) for row in readings if not cint(row.get("confirmed"))]
    if unresolved:
        frappe.throw(
            _("Confirm effective Bulk Density reading(s) before Completion: {0}.").format(
                ", ".join(unresolved)
            )
        )


def _protect_frozen_evidence(doc):
    if doc.is_new() or not frappe.db.exists(MONITOR_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(MONITOR_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    allowed = {"modified", "modified_by"}
    for field in doc.meta.fields:
        if field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"} or field.fieldname in allowed:
            continue
        if doc.get(field.fieldname) != previous.get(field.fieldname):
            frappe.throw(_("Completed Bulk Density Monitor evidence is immutable. Create a correction instead."))
    return True


def validate_bulk_density_reading_document(doc):
    if not doc.get("parent"):
        frappe.throw(_("Bulk Density Reading requires a Bulk Density Monitor parent."))
    if doc.is_new() and not getattr(frappe.flags, "controlled_bulk_density_reading_write", False):
        frappe.throw(_("Add readings through the controlled Bulk Density Monitor action."))
    if doc.is_new() or not frappe.db.exists(READING_DOCTYPE, doc.name):
        return
    previous = frappe.db.get_value(
        READING_DOCTYPE,
        doc.name,
        list(READING_EVIDENCE_FIELDS + READING_CONFIRMATION_FIELDS),
        as_dict=True,
    )
    if any(doc.get(field) != previous.get(field) for field in READING_EVIDENCE_FIELDS):
        frappe.throw(_("Saved Bulk Density readings are append-only and cannot be rewritten."))
    if any(doc.get(field) != previous.get(field) for field in READING_CONFIRMATION_FIELDS) and not getattr(
        frappe.flags, "controlled_bulk_density_confirmation", False
    ):
        frappe.throw(_("Use the controlled Confirm Reading action."))


def prevent_bulk_density_reading_delete(doc):
    frappe.throw(_("Saved Bulk Density readings are append-only and cannot be deleted."))


def prevent_bulk_density_monitor_delete(doc):
    if doc.status != "Draft" or doc.get("readings"):
        frappe.throw(_("Only an empty Draft Bulk Density Monitor can be deleted."))


def _insert_controlled(doc):
    previous = getattr(frappe.flags, "controlled_bulk_density_creation", False)
    frappe.flags.controlled_bulk_density_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_bulk_density_creation = previous
    return doc


@frappe.whitelist()
def create_bulk_density_monitor(job_card: str):
    if not frappe.has_permission(MONITOR_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create Bulk Density Monitor."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    existing = frappe.db.get_value(
        MONITOR_DOCTYPE,
        {
            "job_card": job_card_doc.name,
            "fg_batch": cstr(job_card_doc.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")),
            "status": ("!=", "Superseded"),
        },
        "name",
    )
    if existing:
        return {"name": existing, "status": frappe.db.get_value(MONITOR_DOCTYPE, existing, "status")}
    doc = frappe.new_doc(MONITOR_DOCTYPE)
    doc.job_card = job_card_doc.name
    _insert_controlled(doc)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def add_bulk_density_reading(
    name: str,
    rm_item: str,
    rm_batch: str,
    feeder_no: str,
    w1_kg: float | str,
    w2_kg: float | str,
    bulk_density_kg_l: float | str,
    observation: str = "",
):
    doc = frappe.get_doc(MONITOR_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Readings can be added only to Draft or Active Bulk Density Monitor."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_new_reading_authority(job_card)
    previous = getattr(frappe.flags, "controlled_bulk_density_reading_write", False)
    frappe.flags.controlled_bulk_density_reading_write = True
    try:
        doc.append(
            "readings",
            {
                "rm_item": cstr(rm_item).strip(),
                "rm_batch": cstr(rm_batch).strip(),
                "feeder_no": cstr(feeder_no).strip(),
                "w1_kg": w1_kg,
                "w2_kg": w2_kg,
                "bulk_density_kg_l": bulk_density_kg_l,
                "observation": cstr(observation).strip(),
            },
        )
        doc.save()
    finally:
        frappe.flags.controlled_bulk_density_reading_write = previous
    row = doc.readings[-1]
    return {"name": doc.name, "status": doc.status, "reading": row.name, "sequence": row.sequence}


def _check_confirmation_authority():
    if not CONFIRMATION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(
            _("Production Engineer, Production Head or Manufacturing Manager authority is required."),
            frappe.PermissionError,
        )


@frappe.whitelist()
def confirm_bulk_density_reading(name: str, reading: str):
    _check_confirmation_authority()
    doc = frappe.get_doc(MONITOR_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only an open Bulk Density Monitor reading can be confirmed."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_confirmation_context(job_card)
    row = next((item for item in doc.readings if item.name == reading), None)
    if not row:
        frappe.throw(_("A valid Bulk Density reading is required."))
    if row.name in {cstr(item.get("correction_of_reading")) for item in doc.readings}:
        frappe.throw(_("A corrected historical reading cannot be confirmed as current evidence."))
    if cint(row.confirmed):
        return {"name": doc.name, "reading": row.name, "confirmed_by": row.confirmed_by, "confirmed_on": row.confirmed_on}
    previous = getattr(frappe.flags, "controlled_bulk_density_confirmation", False)
    frappe.flags.controlled_bulk_density_confirmation = True
    try:
        row.confirmed = 1
        row.confirmed_by = frappe.session.user
        row.confirmed_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_bulk_density_confirmation = previous
    return {"name": doc.name, "reading": row.name, "confirmed_by": row.confirmed_by, "confirmed_on": row.confirmed_on}


def _check_completion_authority():
    if not COMPLETION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(_("Production Engineer authority is required to complete Bulk Density Monitor."), frappe.PermissionError)


@frappe.whitelist()
def complete_bulk_density_monitor(name: str):
    _check_completion_authority()
    doc = frappe.get_doc(MONITOR_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only Draft or Active Bulk Density Monitor can be completed."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_completion_context(job_card)
    _validate_completion_evidence(doc)
    previous = getattr(frappe.flags, "controlled_bulk_density_completion", False)
    frappe.flags.controlled_bulk_density_completion = True
    try:
        doc.status = "Completed"
        doc.completed_by = frappe.session.user
        doc.completed_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_bulk_density_completion = previous
    return {"name": doc.name, "status": doc.status, "completed_by": doc.completed_by, "completed_on": doc.completed_on}


def _check_correction_authority():
    if not CORRECTION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(_("Production Head or Manufacturing Manager authority is required for correction."), frappe.PermissionError)


@frappe.whitelist()
def correct_bulk_density_reading(
    name: str,
    reading: str,
    reason: str,
    rm_item: str,
    rm_batch: str,
    feeder_no: str,
    w1_kg: float | str,
    w2_kg: float | str,
    bulk_density_kg_l: float | str,
    observation: str = "",
):
    _check_confirmation_authority()
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Reading Correction Reason is mandatory."))
    doc = frappe.get_doc(MONITOR_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "Active":
        frappe.throw(_("Reading correction is available only while Bulk Density Monitor is Active."))
    if not any(row.name == reading for row in doc.readings):
        frappe.throw(_("A valid saved Bulk Density reading is required for correction."))
    if reading in {cstr(row.get("correction_of_reading")) for row in doc.readings}:
        frappe.throw(_("Correct the latest effective reading instead of an already corrected reading."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_new_reading_authority(job_card)
    previous = getattr(frappe.flags, "controlled_bulk_density_reading_write", False)
    frappe.flags.controlled_bulk_density_reading_write = True
    try:
        doc.append(
            "readings",
            {
                "rm_item": cstr(rm_item).strip(),
                "rm_batch": cstr(rm_batch).strip(),
                "feeder_no": cstr(feeder_no).strip(),
                "w1_kg": w1_kg,
                "w2_kg": w2_kg,
                "bulk_density_kg_l": bulk_density_kg_l,
                "observation": cstr(observation).strip(),
                "correction_of_reading": reading,
                "correction_reason": reason,
            },
        )
        doc.save()
    finally:
        frappe.flags.controlled_bulk_density_reading_write = previous
    row = doc.readings[-1]
    return {"name": doc.name, "reading": row.name, "sequence": row.sequence, "corrects": reading}


@frappe.whitelist()
def make_bulk_density_correction(name: str, reason: str):
    _check_correction_authority()
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(MONITOR_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only a Completed Bulk Density Monitor can be corrected."))
    job_card, work_order = _get_controlled_context(original.job_card)
    _validate_creation_authority(job_card, work_order)
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.status = "Draft"
    correction.set("readings", [])
    correction.completed_by = ""
    correction.completed_on = None
    correction.correction_of = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    _insert_controlled(correction)
    frappe.db.set_value(
        MONITOR_DOCTYPE,
        original.name,
        {"status": "Superseded", "superseded_by": correction.name},
        update_modified=True,
    )
    return {"name": correction.name, "correction_of": original.name}


def get_bulk_density_module(job_card: str, creation_allowed: bool, current_shift: str = "") -> dict[str, Any]:
    monitors = frappe.get_all(
        MONITOR_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "status", "modified", "fg_batch"],
        order_by="modified desc",
    )
    counts = defaultdict(int)
    for monitor in monitors:
        counts[monitor.status] += 1
    names = [monitor.name for monitor in monitors]
    readings = frappe.get_all(
        READING_DOCTYPE,
        filters={"parent": ("in", names)},
        fields=[
            "name", "parent", "event_datetime", "rm_item", "rm_batch", "feeder_no",
            "bulk_density_kg_l", "confirmed", "correction_of_reading",
        ],
        order_by="event_datetime desc",
    ) if names else []
    corrected = {cstr(row.correction_of_reading) for row in readings if row.correction_of_reading}
    effective = [row for row in readings if row.name not in corrected]
    latest = effective[0] if effective else None
    current = next((row for row in monitors if row.status != "Superseded"), None)
    status = current.status if current else "Optional"
    return {
        "key": "bulk_density",
        "label": "Bulk Density",
        "document_code": DOCUMENT_CODE,
        "revision": DOCUMENT_REVISION,
        "status": status,
        "record_count": len(monitors),
        "reading_count": len(effective),
        "confirmed_count": sum(cint(row.confirmed) for row in effective),
        "latest_record": current.name if current else "",
        "latest_time": latest.event_datetime if latest else "",
        "latest_rm": latest.rm_item if latest else "",
        "latest_batch": latest.rm_batch if latest else "",
        "latest_feeder": latest.feeder_no if latest else "",
        "latest_bulk_density": round(flt(latest.bulk_density_kg_l), 6) if latest else None,
        "last_event": latest.event_datetime if latest else "",
        "action_enabled": bool(creation_allowed and not current),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "optional": True,
    }


def _unresolved_monitor_detail(row):
    if row.status == "Draft":
        return f"{row.name} (Draft: no valid saved reading)"
    monitor = frappe.get_doc(MONITOR_DOCTYPE, row.name)
    effective = _effective_readings(monitor)
    if not effective:
        return f"{row.name} (Active: no effective reading)"
    unconfirmed = [str(item.sequence) for item in effective if not cint(item.get("confirmed"))]
    if unconfirmed:
        return f"{row.name} (Active: unconfirmed reading(s) {', '.join(unconfirmed)})"
    return f"{row.name} (Active: monitor completion pending)"


def validate_bulk_density_completion(doc):
    monitors = frappe.get_all(
        MONITOR_DOCTYPE,
        filters={"job_card": doc.name},
        fields=["name", "status", "superseded_by"],
        order_by="creation asc",
    )
    unresolved = [row for row in monitors if row.status in {"Draft", "Active"}]
    broken = [row for row in monitors if row.status == "Superseded" and not row.superseded_by]
    problems = [_unresolved_monitor_detail(row) for row in unresolved]
    problems.extend(f"{row.name} (Superseded without resolved correction)" for row in broken)
    if problems:
        frappe.throw(
            _("Resolve Bulk Density Monitor evidence before completing this Job Card: {0}.").format(
                ", ".join(problems)
            )
        )

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def bulk_density_item_query(doctype, txt, searchfield, start, page_len, filters):
    rows = get_execution_wip_context((filters or {}).get("work_order")).get("rows") or []
    items = sorted(
        {
            (cstr(row.get("item_code")), frappe.db.get_value("Item", row.get("item_code"), "item_name") or "")
            for row in rows
            if txt.casefold() in cstr(row.get("item_code")).casefold()
        }
    )
    return items[start : start + page_len]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def bulk_density_batch_query(doctype, txt, searchfield, start, page_len, filters):
    filters = filters or {}
    rows = get_execution_wip_context(filters.get("work_order")).get("rows") or []
    batches = [
        (cstr(row.get("batch_no")), flt(row.get("wo_attributed_available_qty")))
        for row in rows
        if cstr(row.get("item_code")) == cstr(filters.get("item_code"))
        and txt.casefold() in cstr(row.get("batch_no")).casefold()
    ]
    return batches[start : start + page_len]
