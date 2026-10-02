from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime, nowdate

from calco_erp.calco_production.compounding_execution import (
    is_controlled_compounding_job_card,
    job_card_has_started,
)
from calco_erp.calco_production.wip_consumption import get_wip_consumption_preview


BLENDING_RUN_DOCTYPE = "Blending Run"
DOCUMENT_CODE = "F-BR-01"
DOCUMENT_REVISION = "REV-01"
FROZEN_STATUSES = {"Completed", "Superseded"}
VALID_STATUSES = {"Draft", "In Progress", "Completed", "Superseded"}
CORRECTION_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}
EPSILON = 0.000001


def validate_blending_run(doc):
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_job_card_link(doc)
    if _protect_completed_evidence(doc):
        return
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    _set_run_identity(doc)
    _normalize_planned_timer(doc)
    _validate_materials(doc, work_order)
    _set_totals(doc)
    _validate_status(doc, job_card)


def before_insert_blending_run(doc):
    if not getattr(frappe.flags, "controlled_blending_creation", False):
        frappe.throw(
            _("Create Blending Run from a running Compounding / Extrusion Job Card cockpit.")
        )
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _set_context(doc, job_card, work_order)
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.production_date = doc.production_date or nowdate()
    doc.shift = doc.shift or job_card.get("custom_shift_type") or ""
    _set_document_control(doc)
    _set_run_identity(doc, lock=True)


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(
            _("Blending Run is available only for a controlled Compounding / Extrusion Job Card.")
        )
    if not job_card.get("work_order") or not frappe.db.exists("Work Order", job_card.work_order):
        frappe.throw(_("The Job Card must reference a valid Work Order."))
    return job_card, frappe.get_doc("Work Order", job_card.work_order)


def _job_card_status(job_card) -> str:
    return cstr(job_card.get("status")).strip().casefold()


def _validate_creation_authority(job_card, work_order):
    if cint(job_card.get("docstatus")) == 2:
        frappe.throw(_("Blending Run cannot be created for a cancelled Job Card."))
    if not job_card_has_started(job_card):
        frappe.throw(
            _("Start Compounding / Extrusion Job Card {0} before creating a Blending Run.").format(
                job_card.name
            )
        )
    if _job_card_status(job_card) != "work in progress":
        frappe.throw(
            _("New Blending Run requires Job Card {0} to be Work In Progress.").format(
                job_card.name
            )
        )
    if cstr(job_card.work_order).strip() != cstr(work_order.name).strip():
        frappe.throw(_("Blending Run Work Order lineage does not match its Job Card."))
    if not cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip():
        frappe.throw(_("FG Batch must be allocated before creating a Blending Run."))


def _validate_completion_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) in {
        "completed",
        "closed",
        "cancelled",
    }:
        frappe.throw(_("Blending Run cannot be completed after its Job Card is closed or cancelled."))


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


def _protect_job_card_link(doc):
    if doc.is_new() or not frappe.db.exists(BLENDING_RUN_DOCTYPE, doc.name):
        return
    existing = frappe.db.get_value(BLENDING_RUN_DOCTYPE, doc.name, "job_card")
    if cstr(doc.get("job_card")).strip() != cstr(existing).strip():
        frappe.throw(_("Blending Run Job Card linkage cannot be changed."))


def _set_document_control(doc):
    if doc.is_new():
        doc.controlled_document_code = DOCUMENT_CODE
        doc.controlled_revision = DOCUMENT_REVISION
    elif (
        cstr(doc.get("controlled_document_code")) != DOCUMENT_CODE
        or cstr(doc.get("controlled_revision")) != DOCUMENT_REVISION
    ):
        frappe.throw(_("Controlled document identity cannot be changed."))


def _set_run_identity(doc, lock: bool = False):
    if doc.get("run_sequence") and doc.get("run_number"):
        return
    job_card = cstr(doc.get("job_card")).strip()
    if not job_card:
        return
    if lock:
        frappe.db.sql("SELECT name FROM `tabJob Card` WHERE name = %s FOR UPDATE", job_card)
    maximum = frappe.db.sql(
        """SELECT COALESCE(MAX(run_sequence), 0)
             FROM `tabBlending Run`
            WHERE job_card = %s""",
        job_card,
    )[0][0]
    doc.run_sequence = cint(maximum) + 1
    doc.run_number = f"B-{doc.run_sequence:03d}"


def get_material_variance(row) -> float:
    return flt(row.get("actual_qty")) - flt(row.get("planned_qty"))


def has_material_variance(row) -> bool:
    return Decimal(cstr(row.get("actual_qty") or 0)) != Decimal(
        cstr(row.get("planned_qty") or 0)
    )


def _validate_materials(doc, work_order):
    preview = get_wip_consumption_preview(work_order.name)
    options = {
        (cstr(row.get("item_code")).strip(), cstr(row.get("batch_no")).strip()): row
        for row in preview.get("rows") or []
    }
    planned_by_key = defaultdict(float)
    actual_by_key = defaultdict(float)
    for sequence, row in enumerate(doc.get("materials") or [], start=1):
        row.sequence = sequence
        key = (cstr(row.get("item_code")).strip(), cstr(row.get("batch_no")).strip())
        evidence = options.get(key)
        if not evidence:
            frappe.throw(
                _("Row {0}: Item {1}, Batch {2} is not available in WO-attributed WIP.").format(
                    sequence, key[0] or _("blank"), key[1] or _("blank")
                )
            )
        if flt(row.get("planned_qty")) < 0 or flt(row.get("actual_qty")) < 0:
            frappe.throw(_("Planned Qty and Actual Qty cannot be negative."))
        row.item_name = frappe.db.get_value("Item", key[0], "item_name") or key[0]
        row.uom = evidence.get("stock_uom") or ""
        row.wip_available_qty = flt(evidence.get("available_qty"))
        row.variance_qty = get_material_variance(row)
        if (
            cstr(doc.get("status")) == "Completed"
            and has_material_variance(row)
            and not cstr(row.get("observation")).strip()
        ):
            frappe.throw(
                _("Row {0}: Variance Reason is mandatory when Actual Qty differs from Planned Qty.").format(
                    sequence
                )
            )
        planned_by_key[key] += flt(row.get("planned_qty"))
        actual_by_key[key] += flt(row.get("actual_qty"))
    for key, planned in planned_by_key.items():
        available = flt(options[key].get("available_qty"))
        if max(planned, actual_by_key[key]) > available + EPSILON:
            frappe.throw(
                _("Blending evidence for Item {0}, Batch {1} exceeds current WO-attributed WIP {2}.").format(
                    key[0], key[1], available
                )
            )


def _normalize_planned_timer(doc):
    value = doc.get("planned_minutes")
    if value in (None, ""):
        doc.planned_minutes = None
        return
    if flt(value) < 0:
        frappe.throw(_("Planned Timer must be greater than zero when specified."))
    if abs(flt(value)) <= EPSILON:
        doc.planned_minutes = None


def _set_totals(doc):
    doc.total_planned_qty = sum(flt(row.get("planned_qty")) for row in doc.get("materials") or [])
    doc.total_actual_qty = sum(flt(row.get("actual_qty")) for row in doc.get("materials") or [])
    if doc.get("actual_started_on") and doc.get("completed_on"):
        seconds = max(
            (
                get_datetime(doc.completed_on) - get_datetime(doc.actual_started_on)
            ).total_seconds(),
            0,
        )
        doc.actual_duration_minutes = seconds / 60


def _validate_status(doc, job_card):
    status = cstr(doc.get("status") or "Draft")
    if status not in VALID_STATUSES:
        frappe.throw(_("Invalid Blending Run status {0}.").format(status))
    doc.status = status
    if status in {"In Progress", "Completed"}:
        if not job_card_has_started(job_card):
            frappe.throw(_("Start the standard Job Card before starting Blending execution."))
        if not doc.get("materials"):
            frappe.throw(_("At least one Blending material row is required before starting Blending."))


def _protect_completed_evidence(doc):
    if doc.is_new() or not frappe.db.exists(BLENDING_RUN_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(BLENDING_RUN_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    allowed = {"modified", "modified_by"}
    for field in doc.meta.fields:
        if (
            field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"}
            or field.fieldname in allowed
        ):
            continue
        if doc.get(field.fieldname) != previous.get(field.fieldname):
            frappe.throw(
                _("Completed Blending evidence is immutable. Create a controlled correction instead.")
            )
    return True


def protect_completed_child_evidence(doc, protected_fields: tuple[str, ...]):
    if doc.is_new() or not doc.get("parent") or not frappe.db.exists(doc.doctype, doc.name):
        return
    parent_status = frappe.db.get_value(BLENDING_RUN_DOCTYPE, doc.parent, "status")
    if parent_status not in FROZEN_STATUSES:
        return
    previous = frappe.db.get_value(
        doc.doctype, doc.name, list(protected_fields), as_dict=True
    )
    if any(doc.get(fieldname) != previous.get(fieldname) for fieldname in protected_fields):
        frappe.throw(
            _("Completed Blending evidence is immutable. Create a controlled correction instead.")
        )


def _insert_from_controlled_path(doc):
    previous = getattr(frappe.flags, "controlled_blending_creation", False)
    frappe.flags.controlled_blending_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_blending_creation = previous
    return doc


@frappe.whitelist()
def create_blending_run(job_card: str):
    if not frappe.has_permission(BLENDING_RUN_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create a Blending Run."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    doc = frappe.new_doc(BLENDING_RUN_DOCTYPE)
    doc.job_card = job_card_doc.name
    doc.shift = job_card_doc.get("custom_shift_type") or ""
    _insert_from_controlled_path(doc)
    return {"name": doc.name, "run_number": doc.run_number}


@frappe.whitelist()
def start_blending_run(name: str):
    doc = frappe.get_doc(BLENDING_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "Draft":
        frappe.throw(_("Only a Draft Blending Run can be started."))
    job_card, work_order = _get_controlled_context(doc.job_card)
    _validate_creation_authority(job_card, work_order)
    doc.actual_started_on = now_datetime()
    doc.started_by = frappe.session.user
    doc.status = "In Progress"
    doc.save()
    return _run_result(doc)


@frappe.whitelist()
def complete_blending_run(name: str):
    doc = frappe.get_doc(BLENDING_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "In Progress":
        frappe.throw(_("Only an In Progress Blending Run can be completed."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_completion_authority(job_card)
    doc.completed_on = now_datetime()
    doc.completed_by = frappe.session.user
    doc.status = "Completed"
    doc.save()
    return _run_result(doc)


def _run_result(doc):
    return {
        "name": doc.name,
        "run_number": doc.run_number,
        "status": doc.status,
        "actual_started_on": doc.actual_started_on,
        "completed_on": doc.completed_on,
        "actual_duration_minutes": flt(doc.actual_duration_minutes),
    }


def get_blending_module(
    job_card: str, creation_allowed: bool, current_shift: str = ""
) -> dict[str, Any]:
    rows = frappe.get_all(
        BLENDING_RUN_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "run_number", "status", "modified"],
        order_by="run_sequence desc",
    )
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    latest = rows[0] if rows else None
    status = (
        "In Progress"
        if counts["In Progress"]
        else "Draft"
        if counts["Draft"]
        else "Completed"
        if rows
        else "Optional"
    )
    return {
        "key": "blending",
        "label": "Blending",
        "document_code": DOCUMENT_CODE,
        "revision": DOCUMENT_REVISION,
        "status": status,
        "record_count": len(rows),
        "counts": {
            "Draft": counts["Draft"],
            "In Progress": counts["In Progress"],
            "Completed": counts["Completed"],
        },
        "last_event": latest.modified if latest else "",
        "latest_record": latest.name if latest else "",
        "latest_run_number": latest.run_number if latest else "",
        "open_issue": "",
        "action": "New Blending Run",
        "action_enabled": bool(creation_allowed),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "optional": True,
    }


def validate_blending_completion(doc):
    unresolved = frappe.get_all(
        BLENDING_RUN_DOCTYPE,
        filters={"job_card": doc.name, "status": ("in", ["Draft", "In Progress"])},
        fields=["name", "run_number", "status"],
        order_by="run_sequence asc",
    )
    if not unresolved:
        return
    details = ", ".join(
        f"{row.run_number or row.name} ({row.status})" for row in unresolved
    )
    frappe.throw(_("Resolve Blending Run(s) before completing this Job Card: {0}.").format(details))


def _check_correction_authority():
    if not CORRECTION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(
            _("Production Head or Manufacturing Manager authority is required for correction."),
            frappe.PermissionError,
        )


@frappe.whitelist()
def make_blending_correction(name: str, reason: str):
    _check_correction_authority()
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(BLENDING_RUN_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only a Completed Blending Run can be corrected."))
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.run_sequence = 0
    correction.run_number = ""
    correction.status = "Draft"
    correction.actual_started_on = None
    correction.started_by = ""
    correction.completed_on = None
    correction.completed_by = ""
    correction.actual_duration_minutes = 0
    correction.total_actual_qty = 0
    correction.supersedes = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    for row in correction.get("materials") or []:
        row.actual_qty = 0
        row.variance_qty = -flt(row.planned_qty)
        row.observation = ""
    _insert_from_controlled_path(correction)
    frappe.db.set_value(
        BLENDING_RUN_DOCTYPE,
        original.name,
        {"status": "Superseded", "superseded_by": correction.name},
        update_modified=True,
    )
    return {"name": correction.name, "run_number": correction.run_number}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def blending_item_query(doctype, txt, searchfield, start, page_len, filters):
    rows = get_wip_consumption_preview((filters or {}).get("work_order")).get("rows") or []
    items = sorted(
        {
            cstr(row.get("item_code"))
            for row in rows
            if txt.lower() in cstr(row.get("item_code")).lower()
        }
    )
    return [(item,) for item in items[start : start + page_len]]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def blending_batch_query(doctype, txt, searchfield, start, page_len, filters):
    filters = filters or {}
    rows = get_wip_consumption_preview(filters.get("work_order")).get("rows") or []
    batches = [
        (cstr(row.get("batch_no")), flt(row.get("available_qty")))
        for row in rows
        if cstr(row.get("item_code")) == cstr(filters.get("item_code"))
        and txt.lower() in cstr(row.get("batch_no")).lower()
    ]
    return batches[start : start + page_len]
