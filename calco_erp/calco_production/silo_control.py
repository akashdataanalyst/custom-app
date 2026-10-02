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


SILO_CONTROL_DOCTYPE = "Silo Control"
SILO_EVENT_DOCTYPE = "Silo Loading Event"
DOCUMENT_CODE = "F-SCS-01"
DOCUMENT_REVISION = "REV-02"
ALLOWED_IDENTIFIERS = {str(value) for value in range(1, 7)}
VALID_STATUSES = {"Draft", "Active", "Completed", "Superseded"}
FROZEN_STATUSES = {"Completed", "Superseded"}
REVIEW_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}
COMPLETION_ROLES = {
    "Production Engineer",
    "Production Head",
    "Manufacturing Manager",
    "System Manager",
}
EPSILON = 0.000001


def before_insert_silo_control(doc):
    if not getattr(frappe.flags, "controlled_silo_creation", False):
        frappe.throw(
            _("Create Silo Control from a running Compounding / Extrusion Job Card cockpit.")
        )
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _validate_setup(doc, work_order)
    _validate_duplicate_identity(doc)
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    doc.status = "Draft"
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.production_date = doc.production_date or nowdate()
    doc.shift = doc.shift or job_card.get("custom_shift_type") or ""
    doc.set("loading_events", [])


def validate_silo_control(doc):
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_identity(doc)
    if _protect_frozen_evidence(doc):
        return
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    _validate_setup(doc, work_order)
    _normalize_operational_values(doc)
    _validate_events(doc, job_card, work_order)
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
            _("Silo Control is available only for a controlled Compounding / Extrusion Job Card.")
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
        frappe.throw(_("Silo Control cannot be created for a cancelled Job Card."))
    if not job_card_has_started(job_card):
        frappe.throw(
            _("Start Compounding / Extrusion Job Card {0} before creating Silo Control.").format(
                job_card.name
            )
        )
    if _job_card_status(job_card) != "work in progress":
        frappe.throw(
            _("New Silo Control requires Job Card {0} to be Work In Progress.").format(
                job_card.name
            )
        )
    if cstr(job_card.work_order).strip() != cstr(work_order.name).strip():
        frappe.throw(_("Silo Control Work Order lineage does not match its Job Card."))
    if not cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip():
        frappe.throw(_("FG Batch must be allocated before creating Silo Control."))


def _validate_event_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) != "work in progress":
        frappe.throw(_("New Silo loading events require the Job Card to be Work In Progress."))


def _validate_completion_context(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) in {
        "completed",
        "closed",
        "cancelled",
    }:
        frappe.throw(_("Silo Control cannot be completed after its Job Card is closed or cancelled."))


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


def _validate_setup(doc, work_order):
    doc.silo_no = cstr(doc.get("silo_no")).strip()
    doc.feeder_no = cstr(doc.get("feeder_no")).strip()
    doc.rm_item = cstr(doc.get("rm_item")).strip()
    if doc.silo_no not in ALLOWED_IDENTIFIERS:
        frappe.throw(_("Silo No. must be one of 1, 2, 3, 4, 5 or 6."))
    if doc.feeder_no not in ALLOWED_IDENTIFIERS:
        frappe.throw(_("Feeder No. must be one of 1, 2, 3, 4, 5 or 6."))
    allowed_items = {
        cstr(row.get("item_code")).strip()
        for row in get_wip_consumption_preview(work_order.name).get("rows") or []
    }
    if doc.rm_item not in allowed_items:
        frappe.throw(
            _("RM Item {0} is not available in exact WO-attributed WIP.").format(
                doc.rm_item or _("blank")
            )
        )
    doc.rm_item_name = frappe.db.get_value("Item", doc.rm_item, "item_name") or doc.rm_item


def _normalize_operational_values(doc):
    addition_rate = doc.get("addition_rate")
    if addition_rate in (None, ""):
        doc.addition_rate = None
    elif flt(addition_rate) < 0 or flt(addition_rate) > 100:
        frappe.throw(_("Addition Rate must be between 0 and 100 percent."))
    if cstr(doc.get("blending_required")) not in {"Yes", "No"}:
        frappe.throw(_("Blending Required must be Yes or No."))
    if flt(doc.get("returned_kg")) < 0:
        frappe.throw(_("Returned Kg cannot be negative."))


def _protect_identity(doc):
    if doc.is_new() or not frappe.db.exists(SILO_CONTROL_DOCTYPE, doc.name):
        return
    protected = ("job_card", "rm_item", "silo_no", "feeder_no")
    previous = frappe.db.get_value(
        SILO_CONTROL_DOCTYPE, doc.name, list(protected), as_dict=True
    )
    if any(cstr(doc.get(field)) != cstr(previous.get(field)) for field in protected):
        frappe.throw(_("Job Card, RM Item, Silo No. and Feeder No. cannot be changed."))


def _validate_duplicate_identity(doc):
    if doc.get("supersedes"):
        return
    filters = {
        "job_card": doc.job_card,
        "rm_item": doc.rm_item,
        "silo_no": doc.silo_no,
        "feeder_no": doc.feeder_no,
        "status": ("!=", "Superseded"),
    }
    existing = frappe.db.get_value(SILO_CONTROL_DOCTYPE, filters, "name")
    if existing and existing != doc.get("name"):
        frappe.throw(
            _("Silo Control {0} already owns this Job Card, Silo, Feeder and RM Item context.").format(
                existing
            )
        )


def _event_exists(row) -> bool:
    return bool(row.get("name") and frappe.db.exists(SILO_EVENT_DOCTYPE, row.name))


def _validate_events(doc, job_card, work_order):
    preview = get_wip_consumption_preview(work_order.name)
    options = {
        (cstr(row.get("item_code")).strip(), cstr(row.get("batch_no")).strip()): row
        for row in preview.get("rows") or []
    }
    cumulative_bags = 0.0
    cumulative_kg = 0.0
    kg_by_batch = defaultdict(float)
    for sequence, row in enumerate(doc.get("loading_events") or [], start=1):
        is_existing = _event_exists(row)
        if not is_existing:
            if not getattr(frappe.flags, "controlled_silo_event_write", False):
                frappe.throw(_("Add Silo loading events through the controlled Add Loading Event action."))
            _validate_event_authority(job_card)
            row.event_datetime = now_datetime()
            row.entered_by = frappe.session.user
            row.entered_on = now_datetime()
            row.shift = job_card.get("custom_shift_type") or ""
            row.supervisor_reviewed = 0
            row.reviewed_by = ""
            row.reviewed_on = None
        else:
            _protect_saved_event(row)
        row.sequence = sequence
        key = (doc.rm_item, cstr(row.get("batch_no")).strip())
        evidence = options.get(key)
        if not evidence:
            frappe.throw(
                _("Event {0}: Batch {1} is not exact WO-attributed WIP for RM Item {2}.").format(
                    sequence, key[1] or _("blank"), doc.rm_item
                )
            )
        if flt(row.get("bags_loaded")) < 0 or flt(row.get("kg_loaded")) < 0:
            frappe.throw(_("Event quantities cannot be negative."))
        if flt(row.get("bags_loaded")) <= EPSILON and flt(row.get("kg_loaded")) <= EPSILON:
            frappe.throw(_("Each Silo loading event requires Bags Loaded or Kg Loaded evidence."))
        if cstr(row.get("below_max_level")) not in {"Yes", "No"}:
            frappe.throw(_("Below Max Level must be Yes or No."))
        if row.below_max_level == "No" and not cstr(row.get("observation")).strip():
            frappe.throw(_("Observation is mandatory when material was not poured below max level."))
        cumulative_bags += flt(row.get("bags_loaded"))
        cumulative_kg += flt(row.get("kg_loaded"))
        kg_by_batch[key] += flt(row.get("kg_loaded"))
        row.cumulative_bags = cumulative_bags
        row.cumulative_kg = cumulative_kg
    for key, quantity in kg_by_batch.items():
        available = flt(options[key].get("available_qty"))
        if quantity > available + EPSILON:
            frappe.throw(
                _("Silo loading evidence for RM Item {0}, Batch {1} exceeds WO-attributed WIP {2}.").format(
                    key[0], key[1], available
                )
            )
    doc.total_loaded_bags = cumulative_bags
    doc.total_loaded_kg = cumulative_kg
    if flt(doc.get("returned_kg")) > cumulative_kg + EPSILON:
        frappe.throw(_("Returned Kg cannot exceed Total Loaded Kg evidence."))


def _protect_saved_event(row):
    protected = (
        "sequence",
        "event_datetime",
        "batch_no",
        "bags_loaded",
        "kg_loaded",
        "below_max_level",
        "observation",
        "shift",
        "entered_by",
        "entered_on",
    )
    previous = frappe.db.get_value(SILO_EVENT_DOCTYPE, row.name, list(protected), as_dict=True)
    if any(row.get(field) != previous.get(field) for field in protected):
        frappe.throw(_("Saved Silo loading events are append-only and cannot be rewritten."))
    review_fields = ("supervisor_reviewed", "reviewed_by", "reviewed_on")
    previous_review = frappe.db.get_value(
        SILO_EVENT_DOCTYPE, row.name, list(review_fields), as_dict=True
    )
    if any(row.get(field) != previous_review.get(field) for field in review_fields) and not getattr(
        frappe.flags, "controlled_silo_review", False
    ):
        frappe.throw(_("Use the controlled Review Exception action for supervisory review."))


def _set_status_from_evidence(doc):
    if doc.status == "Draft" and doc.get("loading_events"):
        doc.status = "Active"


def _protect_status_transition(doc):
    status = cstr(doc.get("status") or "Draft")
    if status not in VALID_STATUSES:
        frappe.throw(_("Invalid Silo Control status {0}.").format(status))
    doc.status = status
    if doc.is_new() or not frappe.db.exists(SILO_CONTROL_DOCTYPE, doc.name):
        if status != "Draft":
            frappe.throw(_("New Silo Control must begin in Draft status."))
        return
    previous = frappe.db.get_value(SILO_CONTROL_DOCTYPE, doc.name, "status")
    if status == previous:
        return
    if previous == "Draft" and status == "Active" and getattr(
        frappe.flags, "controlled_silo_event_write", False
    ):
        return
    if previous in {"Draft", "Active"} and status == "Completed" and getattr(
        frappe.flags, "controlled_silo_completion", False
    ):
        return
    frappe.throw(_("Use the controlled Silo Control lifecycle actions to change status."))


def _validate_completion_evidence(doc):
    if not doc.get("loading_events"):
        frappe.throw(_("At least one Silo loading event is required before Completion."))
    unresolved = [
        row.sequence
        for row in doc.loading_events
        if row.below_max_level == "No" and not cint(row.supervisor_reviewed)
    ]
    if unresolved:
        frappe.throw(
            _("Supervisor review is required for Below-Max exception event(s): {0}.").format(
                ", ".join(str(value) for value in unresolved)
            )
        )


def _protect_frozen_evidence(doc):
    if doc.is_new() or not frappe.db.exists(SILO_CONTROL_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(SILO_CONTROL_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    allowed = {"modified", "modified_by"}
    for field in doc.meta.fields:
        if field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"} or field.fieldname in allowed:
            continue
        if doc.get(field.fieldname) != previous.get(field.fieldname):
            frappe.throw(_("Completed Silo Control evidence is immutable. Create a correction instead."))
    return True


def validate_silo_event_document(doc):
    if not doc.get("parent"):
        frappe.throw(_("Silo Loading Event requires a Silo Control parent."))
    if doc.is_new() and not getattr(frappe.flags, "controlled_silo_event_write", False):
        frappe.throw(_("Add events through the controlled Silo Control action."))
    if doc.is_new() or not frappe.db.exists(SILO_EVENT_DOCTYPE, doc.name):
        return
    evidence_fields = (
        "sequence",
        "event_datetime",
        "batch_no",
        "bags_loaded",
        "kg_loaded",
        "cumulative_bags",
        "cumulative_kg",
        "below_max_level",
        "observation",
        "shift",
        "entered_by",
        "entered_on",
    )
    review_fields = ("supervisor_reviewed", "reviewed_by", "reviewed_on")
    fields = evidence_fields + review_fields
    previous = frappe.db.get_value(SILO_EVENT_DOCTYPE, doc.name, list(fields), as_dict=True)
    evidence_changed = any(doc.get(field) != previous.get(field) for field in evidence_fields)
    review_changed = any(doc.get(field) != previous.get(field) for field in review_fields)
    if evidence_changed and not getattr(frappe.flags, "controlled_silo_event_write", False):
        frappe.throw(_("Saved Silo loading events are append-only and cannot be rewritten."))
    if review_changed and not getattr(frappe.flags, "controlled_silo_review", False):
        frappe.throw(_("Use the controlled Review Exception action for supervisory review."))


def prevent_silo_event_delete(doc):
    frappe.throw(_("Saved Silo loading events are append-only and cannot be deleted."))


def prevent_silo_control_delete(doc):
    if doc.status != "Draft" or doc.get("loading_events"):
        frappe.throw(_("Only an empty Draft Silo Control can be deleted."))


def _insert_controlled(doc):
    previous = getattr(frappe.flags, "controlled_silo_creation", False)
    frappe.flags.controlled_silo_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_silo_creation = previous
    return doc


@frappe.whitelist()
def create_silo_control(
    job_card: str,
    rm_item: str,
    silo_no: str,
    feeder_no: str,
    addition_rate: float | str | None = None,
    blending_required: str = "No",
):
    if not frappe.has_permission(SILO_CONTROL_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create Silo Control."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    doc = frappe.new_doc(SILO_CONTROL_DOCTYPE)
    doc.update(
        {
            "job_card": job_card_doc.name,
            "rm_item": cstr(rm_item).strip(),
            "silo_no": cstr(silo_no).strip(),
            "feeder_no": cstr(feeder_no).strip(),
            "addition_rate": addition_rate,
            "blending_required": cstr(blending_required).strip() or "No",
        }
    )
    _insert_controlled(doc)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def add_silo_loading_event(
    name: str,
    batch_no: str,
    bags_loaded: float | str = 0,
    kg_loaded: float | str = 0,
    below_max_level: str = "Yes",
    observation: str = "",
):
    doc = frappe.get_doc(SILO_CONTROL_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Loading events can be added only to Draft or Active Silo Control."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_event_authority(job_card)
    previous = getattr(frappe.flags, "controlled_silo_event_write", False)
    frappe.flags.controlled_silo_event_write = True
    try:
        doc.append(
            "loading_events",
            {
                "batch_no": cstr(batch_no).strip(),
                "bags_loaded": flt(bags_loaded),
                "kg_loaded": flt(kg_loaded),
                "below_max_level": cstr(below_max_level).strip(),
                "observation": cstr(observation).strip(),
            },
        )
        doc.save()
    finally:
        frappe.flags.controlled_silo_event_write = previous
    row = doc.loading_events[-1]
    return {
        "name": doc.name,
        "status": doc.status,
        "event": row.name,
        "sequence": row.sequence,
        "total_loaded_bags": flt(doc.total_loaded_bags),
        "total_loaded_kg": flt(doc.total_loaded_kg),
    }


def _check_review_authority():
    if not REVIEW_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(_("Production Head or Manufacturing Manager authority is required."), frappe.PermissionError)


@frappe.whitelist()
def review_silo_exception(name: str, event: str):
    _check_review_authority()
    doc = frappe.get_doc(SILO_CONTROL_DOCTYPE, name)
    doc.check_permission("write")
    row = next((item for item in doc.loading_events if item.name == event), None)
    if not row or row.below_max_level != "No":
        frappe.throw(_("A valid Below-Max exception event is required."))
    previous = getattr(frappe.flags, "controlled_silo_review", False)
    frappe.flags.controlled_silo_review = True
    try:
        row.supervisor_reviewed = 1
        row.reviewed_by = frappe.session.user
        row.reviewed_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_silo_review = previous
    return {"name": doc.name, "event": row.name, "reviewed_by": row.reviewed_by, "reviewed_on": row.reviewed_on}


def _check_completion_role():
    if not COMPLETION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(_("Production Engineer authority is required to complete Silo Control."), frappe.PermissionError)


@frappe.whitelist()
def complete_silo_control(name: str):
    _check_completion_role()
    doc = frappe.get_doc(SILO_CONTROL_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only Draft or Active Silo Control can be completed."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_completion_context(job_card)
    previous = getattr(frappe.flags, "controlled_silo_completion", False)
    frappe.flags.controlled_silo_completion = True
    try:
        doc.status = "Completed"
        doc.completed_by = frappe.session.user
        doc.completed_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_silo_completion = previous
    return {"name": doc.name, "status": doc.status, "completed_by": doc.completed_by, "completed_on": doc.completed_on}


def _check_correction_authority():
    if not REVIEW_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(_("Production Head or Manufacturing Manager authority is required for correction."), frappe.PermissionError)


@frappe.whitelist()
def make_silo_correction(name: str, reason: str):
    _check_correction_authority()
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(SILO_CONTROL_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only Completed Silo Control can be corrected."))
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.status = "Draft"
    correction.set("loading_events", [])
    correction.total_loaded_bags = 0
    correction.total_loaded_kg = 0
    correction.returned_kg = 0
    correction.completed_by = ""
    correction.completed_on = None
    correction.supersedes = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    _insert_controlled(correction)
    frappe.db.set_value(
        SILO_CONTROL_DOCTYPE,
        original.name,
        {"status": "Superseded", "superseded_by": correction.name},
        update_modified=True,
    )
    return {"name": correction.name, "supersedes": original.name}


def get_silo_module(job_card: str, creation_allowed: bool, current_shift: str = "") -> dict[str, Any]:
    rows = frappe.get_all(
        SILO_CONTROL_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "status", "modified", "silo_no", "feeder_no", "rm_item"],
        order_by="modified desc",
    )
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    latest = rows[0] if rows else None
    latest_events = (
        frappe.get_all(
            SILO_EVENT_DOCTYPE,
            filters={"parent": ("in", [row.name for row in rows])},
            fields=["event_datetime"],
            order_by="event_datetime desc",
            limit=1,
        )
        if rows
        else []
    )
    last_event = latest_events[0].event_datetime if latest_events else ""
    status = "Active" if counts["Active"] else "Draft" if counts["Draft"] else "Completed" if rows else "Optional"
    return {
        "key": "silo_control",
        "label": "Silo Control",
        "document_code": DOCUMENT_CODE,
        "revision": DOCUMENT_REVISION,
        "status": status,
        "record_count": len(rows),
        "counts": {key: counts[key] for key in ("Draft", "Active", "Completed")},
        "last_event": last_event,
        "latest_record": latest.name if latest else "",
        "latest_silo": latest.silo_no if latest else "",
        "latest_feeder": latest.feeder_no if latest else "",
        "latest_material": latest.rm_item if latest else "",
        "action_enabled": bool(creation_allowed),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "optional": True,
    }


def validate_silo_completion(doc):
    unresolved = frappe.get_all(
        SILO_CONTROL_DOCTYPE,
        filters={"job_card": doc.name, "status": ("in", ["Draft", "Active"])},
        fields=["name", "status"],
        order_by="creation asc",
    )
    if unresolved:
        details = ", ".join(f"{row.name} ({row.status})" for row in unresolved)
        frappe.throw(_("Resolve Silo Control record(s) before completing this Job Card: {0}.").format(details))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def silo_item_query(doctype, txt, searchfield, start, page_len, filters):
    rows = get_wip_consumption_preview((filters or {}).get("work_order")).get("rows") or []
    items = sorted({cstr(row.get("item_code")) for row in rows if txt.lower() in cstr(row.get("item_code")).lower()})
    return [(item,) for item in items[start : start + page_len]]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def silo_batch_query(doctype, txt, searchfield, start, page_len, filters):
    filters = filters or {}
    rows = get_wip_consumption_preview(filters.get("work_order")).get("rows") or []
    batches = [
        (cstr(row.get("batch_no")), flt(row.get("available_qty")))
        for row in rows
        if cstr(row.get("item_code")) == cstr(filters.get("item_code"))
        and txt.lower() in cstr(row.get("batch_no")).lower()
    ]
    return batches[start : start + page_len]
