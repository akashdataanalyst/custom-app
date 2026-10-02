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


PREMIX_RUN_DOCTYPE = "Premix Run"
DOCUMENT_CODE = "F-PMS-01"
DOCUMENT_REVISION = "REV-03"
CONFIRMATIONS = (
    ("screw_clamp", "Screw Clamp Used for Fitting Premix Silo to Feeder Pipe is Free From Thread Failure / Bolt Fracture / Shearing"),
    ("timer_alarm", "Premix Timer / Hooter / Alarm Working Condition"),
    ("complete_unloading", "Complete Unloading of Premix Material to Premix Silo"),
    ("polymer_addition", "Polymer Addition in Premix"),
)
EPSILON = 0.000001
FROZEN_STATUSES = {"Completed", "Superseded"}


def validate_premix_run(doc):
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_job_card_link(doc)
    if _protect_completed_evidence(doc):
        return
    _set_context(doc, job_card, work_order)
    _set_document_control(doc)
    _set_run_identity(doc)
    _normalize_planned_timer(doc)
    _set_confirmations(doc)
    _validate_materials(doc, work_order)
    _validate_online_changes(doc)
    _set_totals(doc)
    _validate_status(doc, job_card)


def before_insert_premix_run(doc):
    if not getattr(frappe.flags, "controlled_premix_creation", False):
        frappe.throw(
            _("Create Premix Run from the started Compounding / Extrusion Job Card cockpit.")
        )
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _set_context(doc, job_card, work_order)
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.production_date = doc.production_date or nowdate()
    doc.engineer = doc.engineer or frappe.session.user
    doc.shift = doc.shift or job_card.get("custom_shift_type") or ""
    _set_run_identity(doc, lock=True)
    _set_confirmations(doc)


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(_("Premix Run is available only for a controlled Compounding / Extrusion Job Card."))
    work_order = frappe.get_doc("Work Order", job_card.work_order)
    return job_card, work_order


def _validate_creation_authority(job_card, work_order):
    if not job_card_has_started(job_card):
        frappe.throw(
            _("Start Compounding / Extrusion Job Card {0} before creating a Premix Run.").format(
                job_card.name
            )
        )
    if cstr(job_card.work_order).strip() != cstr(work_order.name).strip():
        frappe.throw(_("Premix Run Work Order lineage does not match its Job Card."))
    fg_batch = cstr(
        job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")
    ).strip()
    if not fg_batch:
        frappe.throw(
            _("FG Batch must be allocated by Job Card Start before creating a Premix Run.")
        )


def _protect_job_card_link(doc):
    if doc.is_new() or not frappe.db.exists(PREMIX_RUN_DOCTYPE, doc.name):
        return
    existing_job_card = frappe.db.get_value(PREMIX_RUN_DOCTYPE, doc.name, "job_card")
    if cstr(doc.get("job_card")).strip() != cstr(existing_job_card).strip():
        frappe.throw(_("Premix Run Job Card linkage cannot be changed."))


def _set_context(doc, job_card, work_order):
    context = {
        "work_order": job_card.work_order,
        "operation": job_card.operation,
        "production_line": job_card.get("custom_production_line") or "",
        "machine": job_card.get("custom_machine") or job_card.get("workstation") or "",
        "fg_item": work_order.production_item,
        "fg_batch": job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no") or "",
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
        cstr(doc.controlled_document_code) != DOCUMENT_CODE
        or cstr(doc.controlled_revision) != DOCUMENT_REVISION
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
             FROM `tabPremix Run`
            WHERE job_card = %s""",
        job_card,
    )[0][0]
    doc.run_sequence = cint(maximum) + 1
    doc.run_number = f"P-{doc.run_sequence:03d}"


def _set_confirmations(doc):
    rows = doc.get("confirmations") or []
    if not rows:
        for key, label in CONFIRMATIONS:
            doc.append("confirmations", {"confirmation_key": key, "confirmation": label})
        return
    expected = list(CONFIRMATIONS)
    actual = [(cstr(row.confirmation_key), cstr(row.confirmation)) for row in rows]
    if actual != expected:
        frappe.throw(
            _(
                "Premix physical confirmations are controlled F-PMS-01 REV-03 evidence and cannot be added, removed, renamed, reordered, or duplicated."
            )
        )
    existing = _existing_child_rows("Premix Run Confirmation", doc)
    for row in rows:
        previous = existing.get(cstr(row.name))
        was_confirmed = cint(previous.get("confirmed")) if previous else 0
        if previous and (was_confirmed or not cint(row.confirmed)):
            row.checked_by = previous.get("checked_by") or ""
            row.checked_on = previous.get("checked_on")
        elif cint(row.confirmed):
            row.checked_by = frappe.session.user
            row.checked_on = now_datetime()
        else:
            row.checked_by = ""
            row.checked_on = None


def _validate_materials(doc, work_order):
    preview = get_wip_consumption_preview(work_order.name)
    options = {
        (cstr(row.get("item_code")), cstr(row.get("batch_no"))): row
        for row in preview.get("rows") or []
    }
    planned_by_key = defaultdict(float)
    actual_by_key = defaultdict(float)
    for sequence, row in enumerate(doc.get("materials") or [], start=1):
        row.sequence = sequence
        key = (cstr(row.item_code).strip(), cstr(row.batch_no).strip())
        evidence = options.get(key)
        if not evidence:
            frappe.throw(
                _("Row {0}: Item {1}, Batch {2} is not available in WO-attributed WIP.").format(
                    sequence, key[0] or _("blank"), key[1] or _("blank")
                )
            )
        row.uom = evidence.get("stock_uom") or ""
        row.wip_available_qty = flt(evidence.get("available_qty"))
        row.item_name = frappe.db.get_value("Item", key[0], "item_name") or key[0]
        if flt(row.specification_percent) < 0 or flt(row.actual_qty) < 0:
            frappe.throw(_("Specification percent and Actual Qty cannot be negative."))
        row.planned_qty = flt(doc.premix_size) * flt(row.specification_percent) / 100
        if doc.status == "Completed" and has_material_variance(row) and not cstr(row.observation).strip():
            frappe.throw(
                _("Row {0}: Variance Reason is mandatory when Actual Qty differs from Planned Qty.").format(
                    sequence
                )
            )
        planned_by_key[key] += flt(row.planned_qty)
        actual_by_key[key] += flt(row.actual_qty)
    for key, planned in planned_by_key.items():
        available = flt(options[key].get("available_qty"))
        if max(planned, actual_by_key[key]) > available + EPSILON:
            frappe.throw(
                _("Premix evidence for Item {0}, Batch {1} exceeds current WO-attributed WIP {2}.").format(
                    key[0], key[1], available
                )
            )


def _validate_online_changes(doc):
    material_percentages = {
        (cstr(row.item_code).strip(), cstr(row.batch_no).strip()): flt(
            row.specification_percent
        )
        for row in doc.get("materials") or []
    }
    existing = _existing_child_rows("Premix Run Online Change", doc)
    for row in doc.get("online_changes") or []:
        previous = existing.get(cstr(row.name))
        item = cstr(row.item_code).strip()
        batch = cstr(row.batch_no).strip()
        if previous and previous.get("item_code") and item != previous.item_code:
            frappe.throw(_("Recorded Online Change material cannot be changed; use the controlled correction process."))
        if previous and previous.get("batch_no"):
            recorded_batch = cstr(previous.batch_no).strip()
            if batch and batch != recorded_batch:
                frappe.throw(_("Recorded Online Change Batch cannot be changed; use the controlled correction process."))
            row.batch_no = recorded_batch
        elif not batch:
            matches = [m for m in doc.get("materials") or [] if cstr(m.item_code).strip() == item]
            if len(matches) == 1 and cstr(matches[0].batch_no).strip():
                row.batch_no = cstr(matches[0].batch_no).strip()
            elif len(matches) > 1:
                frappe.throw(_("Select Batch for Online Change Item {0}: multiple Premix material rows match.").format(item))
        key = (item, cstr(row.batch_no).strip())
        if key not in material_percentages:
            frappe.throw(
                _("Online Change Item {0}, Batch {1} must match a Premix material row.").format(
                    key[0] or _("blank"), key[1] or _("blank")
                )
            )
        previous = existing.get(cstr(row.name))
        row.original_percent = (
            flt(previous.get("original_percent"))
            if previous
            else material_percentages[key]
        )
        if flt(row.original_percent) < 0 or flt(row.revised_percent) < 0:
            frappe.throw(_("Online change percentages cannot be negative."))
        if not cstr(row.reason).strip():
            frappe.throw(_("Reason is mandatory for every Online Change."))
        if previous:
            row.changed_by = previous.get("changed_by") or ""
            row.changed_on = previous.get("changed_on")
        else:
            row.changed_by = frappe.session.user
            row.changed_on = now_datetime()


def get_material_variance(row) -> float:
    return flt(row.get("actual_qty")) - flt(row.get("planned_qty"))


def has_material_variance(row) -> bool:
    return Decimal(cstr(row.get("actual_qty") or 0)) != Decimal(
        cstr(row.get("planned_qty") or 0)
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


def _existing_child_rows(doctype: str, doc) -> dict[str, frappe._dict]:
    if doc.is_new() or not doc.get("name"):
        return {}
    fields = {
        "Premix Run Confirmation": ["name", "confirmed", "checked_by", "checked_on"],
        "Premix Run Online Change": [
            "name",
            "item_code",
            "batch_no",
            "original_percent",
            "changed_by",
            "changed_on",
        ],
    }[doctype]
    return {
        row.name: row
        for row in frappe.get_all(
            doctype,
            filters={"parent": doc.name, "parenttype": PREMIX_RUN_DOCTYPE},
            fields=fields,
        )
    }


def _set_totals(doc):
    doc.total_specification_percent = sum(flt(row.specification_percent) for row in doc.get("materials") or [])
    doc.total_planned_qty = sum(flt(row.planned_qty) for row in doc.get("materials") or [])
    doc.total_actual_qty = sum(flt(row.actual_qty) for row in doc.get("materials") or [])
    if doc.actual_started_on and doc.completed_on:
        seconds = max(
            (get_datetime(doc.completed_on) - get_datetime(doc.actual_started_on)).total_seconds(),
            0,
        )
        doc.actual_duration_minutes = seconds / 60


def _validate_status(doc, job_card):
    status = cstr(doc.status or "Draft")
    if status not in {"Draft", "In Progress", "Completed", "Superseded"}:
        frappe.throw(_("Invalid Premix Run status {0}.").format(status))
    doc.status = status
    if status in {"In Progress", "Completed"}:
        if not job_card_has_started(job_card):
            frappe.throw(_("Start the standard Job Card before starting Premix execution."))
        if flt(doc.premix_size) <= 0:
            frappe.throw(_("Premix Size must be greater than zero before starting Premix."))
        if not doc.get("materials"):
            frappe.throw(_("At least one Premix material row is required before starting Premix."))


def _protect_completed_evidence(doc):
    if doc.is_new() or not frappe.db.exists(PREMIX_RUN_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(PREMIX_RUN_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    allowed = {"modified", "modified_by"}
    for field in doc.meta.fields:
        if field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"} or field.fieldname in allowed:
            continue
        if doc.get(field.fieldname) != previous.get(field.fieldname):
            frappe.throw(_("Completed Premix evidence is immutable. Create a controlled correction instead."))
    return True


def protect_completed_child_evidence(doc, protected_fields: tuple[str, ...]):
    if doc.is_new() or not doc.get("parent") or not frappe.db.exists(doc.doctype, doc.name):
        return
    parent_status = frappe.db.get_value(PREMIX_RUN_DOCTYPE, doc.parent, "status")
    if parent_status not in FROZEN_STATUSES:
        return
    previous = frappe.db.get_value(
        doc.doctype,
        doc.name,
        list(protected_fields),
        as_dict=True,
    )
    if any(doc.get(fieldname) != previous.get(fieldname) for fieldname in protected_fields):
        frappe.throw(_("Completed Premix evidence is immutable. Create a controlled correction instead."))


def _insert_from_controlled_path(doc):
    previous = getattr(frappe.flags, "controlled_premix_creation", False)
    frappe.flags.controlled_premix_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_premix_creation = previous
    return doc


@frappe.whitelist()
def create_premix_run(job_card: str):
    if not frappe.has_permission(PREMIX_RUN_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create a Premix Run."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    doc = frappe.new_doc(PREMIX_RUN_DOCTYPE)
    doc.job_card = job_card_doc.name
    doc.shift = job_card_doc.get("custom_shift_type") or ""
    doc.engineer = frappe.session.user
    _insert_from_controlled_path(doc)
    return {"name": doc.name, "run_number": doc.run_number}


@frappe.whitelist()
def start_premix_run(name: str):
    doc = frappe.get_doc(PREMIX_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "Draft":
        frappe.throw(_("Only a Draft Premix Run can be started."))
    doc.actual_started_on = now_datetime()
    doc.status = "In Progress"
    doc.save()
    return _run_result(doc)


@frappe.whitelist()
def stop_premix_run(name: str):
    doc = frappe.get_doc(PREMIX_RUN_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status != "In Progress":
        frappe.throw(_("Only an In Progress Premix Run can be stopped."))
    doc.completed_on = now_datetime()
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


def get_premix_module(job_card: str, execution_started: bool, current_shift: str = "") -> dict[str, Any]:
    rows = frappe.get_all(PREMIX_RUN_DOCTYPE, filters={"job_card": job_card}, fields=["name", "run_number", "status", "modified"], order_by="run_sequence desc")
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    latest = rows[0] if rows else None
    status = "In Progress" if counts["In Progress"] else "Completed" if rows and counts["Completed"] == len(rows) else "Draft" if rows else "Not Started"
    explicit_counts = {
        "Draft": counts["Draft"],
        "In Progress": counts["In Progress"],
        "Completed": counts["Completed"],
    }
    return {"key":"premix","label":"Premix","document_code":DOCUMENT_CODE,"revision":DOCUMENT_REVISION,"status":status,"record_count":len(rows),"counts":explicit_counts,"last_event":latest.modified if latest else "","latest_record":latest.name if latest else "","latest_run_number":latest.run_number if latest else "","open_issue":"","action":"New Premix Run","action_enabled":bool(execution_started),"view_enabled":True,"execution_available":bool(execution_started),"current_shift":current_shift or ""}


def validate_no_open_premix_runs(doc):
    if frappe.db.exists(PREMIX_RUN_DOCTYPE, {"job_card": doc.name, "status": "In Progress"}):
        frappe.throw(_("Complete the In Progress Premix Run before completing this Job Card."))


@frappe.whitelist()
def make_premix_correction(name: str, reason: str):
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(PREMIX_RUN_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only a Completed Premix Run can be corrected."))
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.run_sequence = 0
    correction.run_number = ""
    correction.status = "Draft"
    correction.actual_started_on = None
    correction.completed_on = None
    correction.actual_duration_minutes = 0
    correction.total_actual_qty = 0
    correction.supersedes = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    correction.set("online_changes", [])
    for row in correction.get("materials") or []:
        row.actual_qty = 0
        row.observation = ""
    for row in correction.get("confirmations") or []:
        row.confirmed = 0
        row.observation = ""
        row.checked_by = ""
        row.checked_on = None
    _insert_from_controlled_path(correction)
    frappe.db.set_value(
        PREMIX_RUN_DOCTYPE,
        original.name,
        {"status": "Superseded", "superseded_by": correction.name},
        update_modified=True,
    )
    return {"name": correction.name, "run_number": correction.run_number}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def premix_item_query(doctype, txt, searchfield, start, page_len, filters):
    rows = get_wip_consumption_preview((filters or {}).get("work_order")).get("rows") or []
    items = sorted({cstr(row.get("item_code")) for row in rows if txt.lower() in cstr(row.get("item_code")).lower()})
    return [(item,) for item in items[start : start + page_len]]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def premix_batch_query(doctype, txt, searchfield, start, page_len, filters):
    filters = filters or {}
    rows = get_wip_consumption_preview(filters.get("work_order")).get("rows") or []
    batches = [(cstr(row.get("batch_no")), flt(row.get("available_qty"))) for row in rows if cstr(row.get("item_code")) == cstr(filters.get("item_code")) and txt.lower() in cstr(row.get("batch_no")).lower()]
    return batches[start : start + page_len]
