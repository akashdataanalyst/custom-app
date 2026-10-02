from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate, now_datetime

from calco_erp.calco_production.compounding_execution import (
    is_controlled_compounding_job_card,
    job_card_has_started,
)


SHIFT_REPORT_DOCTYPE = "Shift Report"
FEEDER_SETTING_DOCTYPE = "Shift Feeder Setting"
DOWNTIME_EVENT_DOCTYPE = "Shift Downtime Event"
DOCUMENT_CODE = "F-PRD-01"
DOCUMENT_REVISION = "REV-01"
VALID_STATUSES = {"Draft", "Active", "Completed", "Superseded", "Abandoned"}
FROZEN_STATUSES = {"Completed", "Superseded", "Abandoned"}
DOWNTIME_CODES = {"P", "Q", "M", "S", "C", "GC", "E"}
ENGINEER_ROLES = {"Production Engineer", "Production Head", "Manufacturing Manager", "System Manager"}
SENIOR_ENGINEER_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}
CORRECTION_ROLES = SENIOR_ENGINEER_ROLES
EPSILON = 0.000001

CONTEXT_FIELDS = (
    "controlled_document_code",
    "controlled_revision",
    "job_card",
    "work_order",
    "operation",
    "production_line",
    "machine",
    "fg_item",
    "fg_item_name",
    "fg_batch",
    "bom_no",
    "planned_job_qty",
    "company",
    "process_parameter_monitor",
    "mpds",
    "mpds_no",
    "mpds_revision",
    "operational_shift_date",
    "shift",
    "shift_start",
    "shift_end",
    "recorded_by",
    "recorded_on",
    "correction_of",
)

OPERATIONAL_FIELDS = (
    "line_operator",
    "mixer_operator",
    "packing_operator",
    "lineman",
    "number_of_casual",
    "bag_no_from",
    "bag_no_to",
    "total_no_of_bags",
    "quantity",
    "loose_quantity",
    "others",
    "total_quantity",
    "cumulative_production",
    "start_up",
    "lumps",
    "floor_sweeping",
    "ud_generated",
    "online_ud_used",
    "balance_ud",
    "shift_remarks",
    "handover_remarks",
    "pending_issues",
    "next_shift_instructions",
    "spy_qty", "tpy_qty", "metal_separator_qty",
    "lab_samples", "other_new_output", "other_output_reason", "repacked_qty", "repacking_source",
    "special_observations",
)

NON_NEGATIVE_FIELDS = (
    "number_of_casual",
    "total_no_of_bags",
    "quantity",
    "loose_quantity",
    "others",
    "total_quantity",
    "cumulative_production",
    "start_up",
    "lumps",
    "floor_sweeping",
    "ud_generated",
    "online_ud_used",
    "balance_ud", "lab_samples", "other_new_output", "repacked_qty", "spy_qty", "tpy_qty", "metal_separator_qty",
)

FEEDER_FIELDS = (
    "sequence",
    "event_time",
    "f1_set_percent",
    "f1_actual_kg",
    "f2_set_percent",
    "f2_actual_kg",
    "f3_set_percent",
    "f3_actual_kg",
    "f4_set_percent",
    "f4_actual_kg",
    "f5_set_percent",
    "f5_actual_kg",
    "f6_set_percent",
    "f6_actual_kg",
    "set_throughput_kg_hr",
    "actual_throughput_kg_hr",
    "shift",
    "entered_by",
    "entered_on",
    "observation",
)

DOWNTIME_FIELDS = (
    "sequence",
    "stop_time",
    "start_time",
    "total_time_minutes",
    "code",
    "description",
    "recorded_by",
    "recorded_on",
)


def before_insert_shift_report(doc):
    if not getattr(frappe.flags, "controlled_shift_report_creation", False):
        frappe.throw(_("Create Shift Report from a running Compounding / Extrusion Job Card cockpit."))
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    if doc.get("correction_of"):
        _validate_existing_report_context(job_card)
    else:
        _validate_creation_authority(job_card, work_order)
    _set_document_control(doc)
    _set_shift_identity(doc, job_card)
    _validate_unique_identity(doc)
    if not doc.get("correction_of"):
        from calco_erp.calco_production.shift_schedule import resolve
        resolve(job_card, doc.shift, doc.operational_shift_date)
        _set_context(doc, job_card, work_order)
    doc.status = "Draft"
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.set("feeder_settings", [])
    doc.set("downtime_events", [])
    doc.set("output_readings", [])
    _calculate_time_summary(doc)


def validate_shift_report(doc):
    from calco_erp.calco_production.shift_correction_abandonment import protect_abandonment
    protect_abandonment(doc)
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _protect_context(doc)
    from calco_erp.calco_production.shift_production_output import validate_report
    validate_report(doc, job_card, work_order)
    if _protect_frozen_evidence(doc):
        return
    _set_document_control(doc)
    if doc.is_new() and not doc.get("correction_of"):
        _set_context(doc, job_card, work_order)
    _validate_shift_identity(doc)
    _validate_unique_identity(doc)
    _protect_child_sets(doc)
    _validate_feeder_settings(doc, job_card)
    _validate_downtime_events(doc)
    _validate_operational_values(doc)
    _calculate_time_summary(doc)
    _set_status_from_evidence(doc)
    _protect_status_transition(doc)
    _protect_signature_changes(doc)
    if doc.status == "Completed":
        _validate_completion_evidence(doc)


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(_("Shift Reporting is available only for a controlled Compounding / Extrusion Job Card."))
    if not job_card.get("work_order") or not frappe.db.exists("Work Order", job_card.work_order):
        frappe.throw(_("The Job Card must reference a valid Work Order."))
    return job_card, frappe.get_doc("Work Order", job_card.work_order)


def _job_card_status(job_card) -> str:
    return cstr(job_card.get("status")).strip().casefold()


def _validate_creation_authority(job_card, work_order):
    status = _job_card_status(job_card)
    if cint(job_card.get("docstatus")) == 2 or status in {"cancelled", "completed", "closed"}:
        frappe.throw(_("A new Shift Report cannot be created after the Job Card is completed or cancelled."))
    if not job_card_has_started(job_card):
        frappe.throw(_("Start Compounding / Extrusion Job Card {0} before creating Shift Report.").format(job_card.name))
    if status == "on hold" or cint(job_card.get("is_paused")):
        frappe.throw(_("A new Shift Report cannot be created while Job Card {0} is On Hold.").format(job_card.name))
    if status != "work in progress":
        frappe.throw(_("New Shift Report requires Job Card {0} to be Work In Progress.").format(job_card.name))
    if cstr(job_card.work_order) != cstr(work_order.name):
        frappe.throw(_("Shift Report Work Order lineage does not match its Job Card."))
    if not cstr(job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")).strip():
        frappe.throw(_("FG Batch must be allocated before creating Shift Report."))


def _validate_existing_report_context(job_card):
    status = _job_card_status(job_card)
    if cint(job_card.get("docstatus")) == 2 or status == "cancelled":
        frappe.throw(_("Shift Report evidence cannot be changed for a cancelled Job Card."))


def _validate_new_feeder_context(job_card):
    _validate_existing_report_context(job_card)
    if _job_card_status(job_card) != "work in progress" or cint(job_card.get("is_paused")):
        frappe.throw(_("New Extruder HMI feeder evidence requires the Job Card to be running."))


def _set_document_control(doc):
    if doc.is_new():
        doc.controlled_document_code = DOCUMENT_CODE
        doc.controlled_revision = DOCUMENT_REVISION
    elif cstr(doc.get("controlled_document_code")) != DOCUMENT_CODE or cstr(doc.get("controlled_revision")) != DOCUMENT_REVISION:
        frappe.throw(_("Controlled document identity cannot be changed."))


def _set_shift_identity(doc, job_card):
    shift = cstr(doc.get("shift") or job_card.get("custom_shift_type")).strip()
    if not shift:
        frappe.throw(_("Select an existing Shift Type for this Shift Report."))
    doc.shift = shift
    if doc.get("operational_shift_date"):
        doc.operational_shift_date = getdate(doc.operational_shift_date)
    _validate_shift_identity(doc)


def _validate_shift_identity(doc):
    shift = cstr(doc.get("shift")).strip()
    if not shift or not frappe.db.exists("Shift Type", shift):
        frappe.throw(_("Shift must reference an existing Shift Type."))
    if not doc.get("operational_shift_date"):
        frappe.throw(_("Operational Shift Date is mandatory."))
    shift_start, shift_end = _get_shift_window(shift, doc.operational_shift_date)
    doc.shift_start = shift_start
    doc.shift_end = shift_end


def _get_shift_times(shift: str):
    values = frappe.db.get_value("Shift Type", shift, ["start_time", "end_time"], as_dict=True)
    if not values or values.start_time is None or values.end_time is None:
        frappe.throw(_("Shift Type {0} must define Start Time and End Time.").format(shift))
    return values.start_time, values.end_time


def _combine(day, value) -> datetime:
    day = getdate(day)
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return datetime.combine(day, datetime.min.time()) + timedelta(seconds=seconds)
    return get_datetime(f"{day} {value}")


def _get_shift_window(shift: str, operational_date):
    start_time, end_time = _get_shift_times(shift)
    start = _combine(operational_date, start_time)
    end = _combine(operational_date, end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _current_operational_date(shift: str, timestamp=None):
    timestamp = get_datetime(timestamp or now_datetime())
    start_time, end_time = _get_shift_times(shift)
    start_today = _combine(timestamp.date(), start_time)
    end_today = _combine(timestamp.date(), end_time)
    if end_today <= start_today and timestamp < end_today:
        return timestamp.date() - timedelta(days=1)
    return timestamp.date()



def _set_context(doc, job_card, work_order):
    mpds = frappe.db.get_value(
        "Process Parameter Monitor",
        {"job_card": job_card.name, "status": ("not in", ["Superseded", "Abandoned"])},
        ["name", "mpds", "mpds_no", "mpds_revision"],
        as_dict=True,
        order_by="creation desc",
    ) or frappe._dict()
    context = {
        "work_order": job_card.work_order,
        "operation": job_card.operation,
        "production_line": job_card.get("custom_production_line") or "",
        "machine": job_card.get("custom_machine") or job_card.get("workstation") or "",
        "fg_item": work_order.production_item,
        "fg_item_name": frappe.db.get_value("Item", work_order.production_item, "item_name") or work_order.production_item,
        "fg_batch": job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no") or "",
        "bom_no": job_card.get("bom_no") or work_order.bom_no,
        "planned_job_qty": job_card.get("for_quantity") or work_order.qty,
        "company": work_order.company,
        "process_parameter_monitor": mpds.get("name") or "",
        "mpds": mpds.get("mpds") or "",
        "mpds_no": mpds.get("mpds_no") or "",
        "mpds_revision": mpds.get("mpds_revision") or "",
    }
    for fieldname, value in context.items():
        doc.set(fieldname, value)


def _validate_unique_identity(doc):
    filters = {
        "job_card": doc.get("job_card"),
        "operational_shift_date": doc.get("operational_shift_date"),
        "shift": doc.get("shift"),
        "status": ("not in", ["Superseded", "Abandoned"]),
    }
    existing = frappe.get_all(SHIFT_REPORT_DOCTYPE, filters=filters, pluck="name", limit=5)
    allowed = {cstr(doc.get("name")), cstr(doc.get("correction_of"))}
    conflict = next((name for name in existing if name not in allowed), "")
    if conflict:
        frappe.throw(_("Shift Report {0} already owns this Job Card, Operational Shift Date and Shift.").format(conflict))


def _protect_context(doc):
    if doc.is_new() or not frappe.db.exists(SHIFT_REPORT_DOCTYPE, doc.name):
        return
    previous = frappe.db.get_value(SHIFT_REPORT_DOCTYPE, doc.name, list(CONTEXT_FIELDS), as_dict=True)
    if any(
        _context_value(doc, field, doc.get(field))
        != _context_value(doc, field, previous.get(field))
        for field in CONTEXT_FIELDS
    ):
        frappe.throw(_("Shift Report execution context cannot be changed."))


def _context_value(doc, fieldname, value):
    if value in (None, ""):
        return None
    field = doc.meta.get_field(fieldname)
    if field and field.fieldtype == "Date":
        return getdate(value)
    if field and field.fieldtype == "Datetime":
        return get_datetime(value)
    return value


def _protect_child_sets(doc):
    if doc.is_new() or not frappe.db.exists(SHIFT_REPORT_DOCTYPE, doc.name):
        return
    for doctype, fieldname, label in (
        (FEEDER_SETTING_DOCTYPE, "feeder_settings", _("Feeder Setting")),
        (DOWNTIME_EVENT_DOCTYPE, "downtime_events", _("Downtime Event")),
    ):
        saved = set(frappe.get_all(doctype, filters={"parent": doc.name}, pluck="name"))
        current = {cstr(row.get("name")) for row in doc.get(fieldname) or [] if row.get("name")}
        if not saved.issubset(current):
            frappe.throw(_("Saved {0} rows are append-only and cannot be deleted.").format(label))


def _validate_feeder_settings(doc, job_card):
    for sequence, row in enumerate(doc.get("feeder_settings") or [], start=1):
        if row.get("name") and frappe.db.exists(FEEDER_SETTING_DOCTYPE, row.name):
            _protect_saved_child(row, FEEDER_SETTING_DOCTYPE, FEEDER_FIELDS, "controlled_shift_feeder_write")
            continue
        if not getattr(frappe.flags, "controlled_shift_feeder_write", False):
            frappe.throw(_("Add feeder settings through the controlled Add Feeder Setting action."))
        _validate_new_feeder_context(job_card)
        row.sequence = sequence
        if not row.get("event_time"):
            frappe.throw(_("Feeder Setting Event Time is mandatory."))
        row.event_time = get_datetime(row.event_time)
        row.shift = doc.shift
        row.entered_by = frappe.session.user
        row.entered_on = now_datetime()
        _validate_feeder_values(row)
        _validate_event_inside_shift(doc, row.event_time, _("Feeder Setting time"))


def _validate_feeder_values(row):
    for feeder in range(1, 7):
        for suffix, label in (("set_percent", _("Set %")), ("actual_kg", _("Actual Kg"))):
            fieldname = f"f{feeder}_{suffix}"
            if row.get(fieldname) in (None, ""):
                frappe.throw(_("Feeder {0} {1} is mandatory.").format(feeder, label))
            value = flt(row.get(fieldname))
            if value < 0:
                frappe.throw(_("Feeder values cannot be negative."))
            row.set(fieldname, round(value, 6))
    for fieldname, label in (
        ("set_throughput_kg_hr", _("Set Throughput (kg/hr)")),
        ("actual_throughput_kg_hr", _("Actual Throughput (kg/hr)")),
    ):
        if row.get(fieldname) in (None, "") or flt(row.get(fieldname)) < 0:
            frappe.throw(_("{0} must be a non-negative value.").format(label))
        row.set(fieldname, round(flt(row.get(fieldname)), 6))


def _validate_downtime_events(doc):
    intervals = []
    for sequence, row in enumerate(doc.get("downtime_events") or [], start=1):
        if row.get("name") and frappe.db.exists(DOWNTIME_EVENT_DOCTYPE, row.name):
            _protect_saved_child(row, DOWNTIME_EVENT_DOCTYPE, DOWNTIME_FIELDS, "controlled_shift_downtime_write")
        elif not getattr(frappe.flags, "controlled_shift_downtime_write", False):
            frappe.throw(_("Add downtime through the controlled Add Downtime Event action."))
        else:
            row.sequence = sequence
            row.recorded_by = frappe.session.user
            row.recorded_on = now_datetime()
        stop_time = get_datetime(row.get("stop_time")) if row.get("stop_time") else None
        start_time = get_datetime(row.get("start_time")) if row.get("start_time") else None
        if not stop_time or not start_time or start_time <= stop_time:
            frappe.throw(_("Downtime Start Time must be after Stop Time."))
        _validate_event_inside_shift(doc, stop_time, _("Downtime Stop Time"))
        _validate_event_inside_shift(doc, start_time, _("Downtime Start Time"), allow_shift_end=True)
        code = cstr(row.get("code")).strip().upper()
        if code not in DOWNTIME_CODES:
            frappe.throw(_("Downtime Code must be P, Q, M, S, C, GC or E."))
        row.code = code
        row.total_time_minutes = round((start_time - stop_time).total_seconds() / 60.0, 6)
        intervals.append((stop_time, start_time))
    intervals.sort()
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] < previous[1]:
            frappe.throw(_("Downtime events cannot overlap within one Shift Report."))


def _validate_event_inside_shift(doc, timestamp, label, allow_shift_end=False):
    timestamp = get_datetime(timestamp)
    start = get_datetime(doc.shift_start)
    end = get_datetime(doc.shift_end)
    valid = start <= timestamp <= end if allow_shift_end else start <= timestamp < end
    if not valid:
        frappe.throw(_("{0} must fall within {1} to {2}.").format(label, start, end))


def _protect_saved_child(row, doctype, fields, flag):
    previous = frappe.db.get_value(doctype, row.name, list(fields), as_dict=True)
    # Browser JSON serializes dates as strings; representation is not a rewrite.
    meta = frappe.get_meta(doctype)
    def evidence_value(field, value):
        df = meta.get_field(field)
        if df and df.fieldtype in {"Datetime", "Date"} and value not in (None, ""):
            return get_datetime(value) if df.fieldtype == "Datetime" else getdate(value)
        return value
    if any(evidence_value(field, row.get(field)) != evidence_value(field, previous.get(field)) for field in fields) and not getattr(frappe.flags, flag, False):
        frappe.throw(_("Saved Shift Report evidence is append-only and cannot be rewritten."))


def _validate_operational_values(doc):
    for fieldname in NON_NEGATIVE_FIELDS:
        if flt(doc.get(fieldname)) < 0:
            frappe.throw(_("{0} cannot be negative.").format(doc.meta.get_label(fieldname)))


def _calculate_time_summary(doc):
    if not doc.get("shift_start") or not doc.get("shift_end"):
        return
    available_minutes = (get_datetime(doc.shift_end) - get_datetime(doc.shift_start)).total_seconds() / 60.0
    downtime_minutes = sum(flt(row.get("total_time_minutes")) for row in doc.get("downtime_events") or [])
    doc.total_available_hours = round(max(available_minutes, 0) / 60.0, 6)
    doc.total_downtime_hours = round(max(downtime_minutes, 0) / 60.0, 6)
    doc.extruder_run_hours = round(max(available_minutes - downtime_minutes, 0) / 60.0, 6)


def _has_operational_evidence(doc):
    if doc.get("feeder_settings") or doc.get("downtime_events") or doc.get("output_readings"):
        return True
    return any(doc.get(field) not in (None, "", 0, 0.0) for field in OPERATIONAL_FIELDS)


def _set_status_from_evidence(doc):
    if not doc.is_new() and doc.status == "Draft" and _has_operational_evidence(doc):
        doc.status = "Active"


def _protect_status_transition(doc):
    status = cstr(doc.get("status") or "Draft")
    if status not in VALID_STATUSES:
        frappe.throw(_("Invalid Shift Report status {0}.").format(status))
    doc.status = status
    if doc.is_new() or not frappe.db.exists(SHIFT_REPORT_DOCTYPE, doc.name):
        if status != "Draft":
            frappe.throw(_("New Shift Report must begin in Draft status."))
        return
    previous = frappe.db.get_value(SHIFT_REPORT_DOCTYPE, doc.name, "status")
    if status == previous:
        return
    if previous == "Draft" and status == "Active" and _has_operational_evidence(doc):
        return
    if previous in {"Draft", "Active"} and status == "Completed" and getattr(frappe.flags, "controlled_shift_completion", False):
        return
    frappe.throw(_("Use controlled Shift Report lifecycle actions to change status."))


def _protect_signature_changes(doc):
    if doc.is_new() or not frappe.db.exists(SHIFT_REPORT_DOCTYPE, doc.name):
        return
    fields = ("engineer_signed_by", "engineer_signed_on", "senior_engineer_signed_by", "senior_engineer_signed_on")
    previous = frappe.db.get_value(SHIFT_REPORT_DOCTYPE, doc.name, list(fields), as_dict=True)
    if any(
        _context_value(doc, field, doc.get(field))
        != _context_value(doc, field, previous.get(field))
        for field in fields
    ) and not getattr(frappe.flags, "controlled_shift_signature", False):
        frappe.throw(_("Use controlled Engineer and Sr. Engineer sign-off actions."))


def _validate_completion_evidence(doc):
    if not _has_operational_evidence(doc):
        frappe.throw(_("Record at least one item of shift operational evidence before Completion."))
    if not doc.get("engineer_signed_by") or not doc.get("engineer_signed_on"):
        frappe.throw(_("Engineer sign-off is mandatory before Sr. Engineer completion."))
    if not doc.get("senior_engineer_signed_by") or not doc.get("senior_engineer_signed_on"):
        frappe.throw(_("Sr. Engineer sign-off is mandatory for Completion."))


def _protect_frozen_evidence(doc):
    if doc.is_new() or not frappe.db.exists(SHIFT_REPORT_DOCTYPE, doc.name):
        return False
    previous = frappe.get_doc(SHIFT_REPORT_DOCTYPE, doc.name)
    if previous.status not in FROZEN_STATUSES:
        return False
    allowed = {"modified", "modified_by"}
    for field in doc.meta.fields:
        if field.fieldtype in {"Section Break", "Column Break", "Tab Break", "HTML"} or field.fieldname in allowed:
            continue
        if doc.get(field.fieldname) != previous.get(field.fieldname):
            frappe.throw(_("Completed Shift Report evidence is immutable. Create a correction instead."))
    return True


def validate_shift_child_document(doc, doctype, fields, flag):
    if not doc.get("parent"):
        frappe.throw(_("Shift Report child evidence requires a Shift Report parent."))
    if doc.is_new() and not getattr(frappe.flags, flag, False):
        frappe.throw(_("Add Shift Report evidence through the controlled parent action."))
    if doc.is_new() or not frappe.db.exists(doctype, doc.name):
        return
    _protect_saved_child(doc, doctype, fields, flag)


def prevent_shift_child_delete(doc):
    frappe.throw(_("Saved Shift Report evidence is append-only and cannot be deleted."))


def prevent_shift_report_delete(doc):
    if doc.status != "Draft" or doc.get("feeder_settings") or doc.get("downtime_events") or _has_operational_evidence(doc):
        frappe.throw(_("Only an empty Draft Shift Report can be deleted."))


def _insert_controlled(doc):
    previous = getattr(frappe.flags, "controlled_shift_report_creation", False)
    frappe.flags.controlled_shift_report_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_shift_report_creation = previous
    return doc


def _check_roles(allowed, message):
    if not allowed.intersection(set(frappe.get_roles())):
        frappe.throw(_(message), frappe.PermissionError)


@frappe.whitelist()
def create_shift_report(job_card: str, shift: str = "", operational_shift_date: str | None = None):
    if not frappe.has_permission(SHIFT_REPORT_DOCTYPE, "create"):
        frappe.throw(_("You do not have permission to create Shift Report."), frappe.PermissionError)
    job_card_doc, work_order = _get_controlled_context(job_card)
    _validate_creation_authority(job_card_doc, work_order)
    from calco_erp.calco_production.in_process_quality import _lock
    from calco_erp.calco_production.shift_schedule import resolve
    _lock(work_order.name)
    job_card_doc.reload()
    _validate_creation_authority(job_card_doc, work_order)
    occurrence = resolve(job_card_doc, shift, operational_shift_date)
    selected_shift = occurrence["shift"]
    selected_date = getdate(occurrence["operational_shift_date"])
    from calco_erp.calco_production.shift_schedule import matching_report
    reports = frappe.get_all(SHIFT_REPORT_DOCTYPE,
        filters={"job_card": job_card_doc.name, "operational_shift_date": selected_date, "shift": selected_shift},
        fields=["name", "status", "shift", "operational_shift_date", "shift_start", "shift_end", "correction_of"])
    existing = matching_report(reports, occurrence)
    if existing:
        return {"name": existing.name, "status": existing.status, "existing": True}
    doc = frappe.new_doc(SHIFT_REPORT_DOCTYPE)
    doc.job_card = job_card_doc.name
    doc.shift = selected_shift
    doc.operational_shift_date = selected_date
    _insert_controlled(doc)
    return {"name": doc.name, "status": doc.status, "existing": False}


@frappe.whitelist()
def add_shift_feeder_setting(name: str, **values):
    doc = frappe.get_doc(SHIFT_REPORT_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Feeder settings can be added only to an open Shift Report."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_new_feeder_context(job_card)
    payload = {
        field: values.get(field)
        for field in FEEDER_FIELDS
        if field.startswith("f")
        or field in {"event_time", "set_throughput_kg_hr", "actual_throughput_kg_hr", "observation"}
    }
    previous = getattr(frappe.flags, "controlled_shift_feeder_write", False)
    frappe.flags.controlled_shift_feeder_write = True
    try:
        doc.append("feeder_settings", payload)
        doc.save()
    finally:
        frappe.flags.controlled_shift_feeder_write = previous
    row = doc.feeder_settings[-1]
    return {"name": doc.name, "status": doc.status, "row": row.name, "sequence": row.sequence}


@frappe.whitelist()
def add_shift_downtime_event(name: str, stop_time: str, start_time: str, code: str, description: str = ""):
    doc = frappe.get_doc(SHIFT_REPORT_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Downtime can be added only to an open Shift Report."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_existing_report_context(job_card)
    previous = getattr(frappe.flags, "controlled_shift_downtime_write", False)
    frappe.flags.controlled_shift_downtime_write = True
    try:
        doc.append("downtime_events", {"stop_time": stop_time, "start_time": start_time, "code": code, "description": cstr(description).strip()})
        doc.save()
    finally:
        frappe.flags.controlled_shift_downtime_write = previous
    row = doc.downtime_events[-1]
    return {"name": doc.name, "status": doc.status, "row": row.name, "minutes": row.total_time_minutes}


@frappe.whitelist()
def sign_shift_report_engineer(name: str):
    _check_roles(ENGINEER_ROLES, "Production Engineer authority is required for Engineer sign-off.")
    doc = frappe.get_doc(SHIFT_REPORT_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only an open Shift Report can receive Engineer sign-off."))
    if doc.get("engineer_signed_by") or doc.get("engineer_signed_on"):
        frappe.throw(_("Engineer sign-off is already recorded."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_existing_report_context(job_card)
    if not _has_operational_evidence(doc):
        frappe.throw(_("Record shift operational evidence before Engineer sign-off."))
    previous = getattr(frappe.flags, "controlled_shift_signature", False)
    frappe.flags.controlled_shift_signature = True
    try:
        doc.engineer_signed_by = frappe.session.user
        doc.engineer_signed_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_shift_signature = previous
    return {"name": doc.name, "status": doc.status, "engineer_signed_by": doc.engineer_signed_by, "engineer_signed_on": doc.engineer_signed_on}


@frappe.whitelist()
def complete_shift_report(name: str):
    _check_roles(SENIOR_ENGINEER_ROLES, "Production Head or Manufacturing Manager authority is required for Sr. Engineer sign-off.")
    doc = frappe.get_doc(SHIFT_REPORT_DOCTYPE, name)
    doc.check_permission("write")
    if doc.status not in {"Draft", "Active"}:
        frappe.throw(_("Only an open Shift Report can be completed."))
    job_card, _work_order = _get_controlled_context(doc.job_card)
    _validate_existing_report_context(job_card)
    signature_flag = getattr(frappe.flags, "controlled_shift_signature", False)
    completion_flag = getattr(frappe.flags, "controlled_shift_completion", False)
    frappe.flags.controlled_shift_signature = True
    frappe.flags.controlled_shift_completion = True
    try:
        doc.senior_engineer_signed_by = frappe.session.user
        doc.senior_engineer_signed_on = now_datetime()
        doc.status = "Completed"
        _validate_completion_evidence(doc)
        doc.save()
        if doc.correction_of:
            frappe.db.set_value(SHIFT_REPORT_DOCTYPE, doc.correction_of, {"status": "Superseded", "superseded_by": doc.name}, update_modified=True)
    finally:
        frappe.flags.controlled_shift_signature = signature_flag
        frappe.flags.controlled_shift_completion = completion_flag
    return {"name": doc.name, "status": doc.status, "completed_by": doc.senior_engineer_signed_by, "completed_on": doc.senior_engineer_signed_on}


@frappe.whitelist()
def make_shift_report_correction(name: str, reason: str):
    _check_roles(CORRECTION_ROLES, "Production Head or Manufacturing Manager authority is required for correction.")
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(SHIFT_REPORT_DOCTYPE, name)
    original.check_permission("write")
    from calco_erp.calco_production.shift_output_snapshot import activation, INPUTS
    snapshot_mode = bool(activation(frappe.get_doc("Job Card", original.job_card)))
    open_snapshot = snapshot_mode and original.status in {"Draft", "Active"} and (original.engineer_signed_by or original.output_readings)
    if (original.status != "Completed" and not open_snapshot) or original.get("superseded_by"):
        frappe.throw(_("Only the current Completed Shift Report can be corrected."))
    existing = frappe.db.get_value(SHIFT_REPORT_DOCTYPE, {"correction_of": original.name, "status": ("not in", ["Superseded", "Abandoned"])}, "name")
    if existing:
        return {"name": existing, "correction_of": original.name, "existing": True}
    correction = frappe.copy_doc(original)
    correction.name = None
    correction.status = "Draft"
    correction.set("feeder_settings", [])
    correction.set("downtime_events", [])
    for fieldname in OPERATIONAL_FIELDS:
        correction.set(fieldname, 0 if fieldname in NON_NEGATIVE_FIELDS else "")
    if snapshot_mode:
        from calco_erp.calco_production.shift_unique_output import FIELDS as UNIQUE_FIELDS
        for fieldname in (*INPUTS, *UNIQUE_FIELDS):
            correction.set(fieldname, original.get(fieldname))
    correction.engineer_output_revision = ""
    correction.engineer_signed_by = ""
    correction.engineer_signed_on = None
    correction.senior_engineer_signed_by = ""
    correction.senior_engineer_signed_on = None
    correction.correction_of = original.name
    correction.superseded_by = ""
    correction.correction_reason = reason
    _insert_controlled(correction)
    return {"name": correction.name, "correction_of": original.name, "existing": False}


def get_shift_reporting_module(job_card: str, creation_allowed: bool, current_shift: str = "") -> dict[str, Any]:
    reports = frappe.get_all(
        SHIFT_REPORT_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "status", "operational_shift_date", "shift", "shift_start", "shift_end", "correction_of", "total_quantity", "modified"],
        order_by="operational_shift_date desc, modified desc",
    )
    counts = defaultdict(int)
    for report in reports:
        counts[report.status] += 1
    from calco_erp.calco_production.shift_schedule import current_report_resolution
    card = frappe.get_doc("Job Card", job_card)
    resolution = current_report_resolution(card, reports=reports)
    current = next((row for row in reports if row.name == resolution["current_report"]), None)
    latest = reports[0] if reports else None
    from calco_erp.calco_production.shift_production_output import run_state
    output = run_state(frappe.get_doc("Job Card", job_card))
    return {
        "controlled_output": output,
        "key": "shift_reporting",
        "label": "Shift Reporting",
        "document_code": DOCUMENT_CODE,
        "revision": DOCUMENT_REVISION,
        "source": "Shift Reporting Register / Extruder HMI",
        "status": current.status if current else "Optional",
        "record_count": len(reports),
        "counts": dict(counts),
        "latest_record": current.name if current else "",
        "current_report": current.name if current else "",
        "current_occurrence": resolution["occurrence"],
        "current_shift_choices": resolution["choices"],
        "current_shift_choice_required": resolution["choice_required"],
        "current_action": resolution["action"],
        "latest_shift": latest.shift if latest else "",
        "latest_date": latest.operational_shift_date if latest else "",
        "latest_production_qty": flt(latest.total_quantity) if latest else 0,
        "last_event": latest.modified if latest else "",
        "action_enabled": bool(current or (creation_allowed and resolution["choices"])),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "optional": True,
    }


def validate_shift_reporting_completion(doc):
    reports = frappe.get_all(
        SHIFT_REPORT_DOCTYPE,
        filters={"job_card": doc.name},
        fields=["name", "status", "superseded_by"],
        order_by="creation asc",
    )
    unresolved = [row for row in reports if row.status in {"Draft", "Active"}]
    broken = [row for row in reports if row.status == "Superseded" and not row.superseded_by]
    problems = [f"{row.name} ({row.status})" for row in unresolved]
    problems.extend(f"{row.name} (Superseded without resolved correction)" for row in broken)
    if problems:
        frappe.throw(_("Resolve Shift Report evidence before completing this Job Card: {0}.").format(", ".join(problems)))
