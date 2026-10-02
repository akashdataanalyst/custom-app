from __future__ import annotations

import math
import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime
from calco_erp.calco_production import in_process_quality as qc

AUTHORITY = "shift-output-v1"
DOCTYPE = "Shift Production Output Reading"
FIELDS = ("sequence", "reading_time", "cumulative_qty", "uom", "entered_by", "entered_on", "correction_of", "correction_reason")
from calco_erp.calco_production import shift_output_snapshot as snapshots
FIELDS = (*FIELDS, *snapshots.EXTRA_FIELDS)
WRITE_FLAG = "controlled_shift_output_write"


def enabled_plan(wo):
    if not qc.is_parallel(wo) or not wo.get(qc.SNAPSHOT):
        return None
    plan = qc.frozen_plan(wo)
    return plan if plan.get("quantity_authority") == AUTHORITY else None


def readings_for(job_card, for_update=False):
    return frappe.db.sql("""
        select r.name, r.parent, r.sequence, r.reading_time, r.cumulative_qty, r.uom,
               r.entered_by, r.entered_on, r.correction_of, r.correction_reason,
               r.calculation_version, r.logical_shift, r.source_revision, r.shift_qty, r.components_json, r.event_type, r.source_pool_json,
               s.status as report_status, s.correction_of as report_correction_of,
               s.shift, s.operational_shift_date
        from `tabShift Production Output Reading` r
        inner join `tabShift Report` s on s.name=r.parent
        where s.job_card=%s and r.parenttype='Shift Report' and r.parentfield='output_readings'
        order by r.sequence asc, r.name asc
    """ + (" for update" if for_update else ""), (job_card,), as_dict=True)


def reduce_readings(rows, activate_report=None):
    """Corrections replace an effective reading, never its immutable evidence.

    Completed correction reports activate their children; superseding a parent
    does not erase its output history. Historical crossed thresholds are retained.
    """
    effective = []
    high_water = 0.0
    sequences = set()
    for row in sorted(rows, key=lambda r: cint(r.get("sequence"))):
        sequence = cint(row.get("sequence"))
        if sequence in sequences:
            frappe.throw(_("Duplicate run output sequence; investigate the output audit."))
        sequences.add(sequence)
        if row.get("report_correction_of") and row.get("report_status") not in {"Completed", "Superseded"} and row.get("parent") != activate_report:
            continue
        if row.get("report_status") == "Abandoned":
            continue
        target = row.get("correction_of")
        if target:
            index = next((i for i, previous in enumerate(effective) if previous["name"] == target), None)
            if index is None:
                frappe.throw(_("Output correction must reference the current effective version of a reading."))
            effective[index] = row
        else:
            effective.append(row)
        values = [flt(r.get("cumulative_qty")) for r in effective]
        if any(not math.isfinite(v) or v < 0 for v in values) or any(a > b for a, b in zip(values, values[1:])):
            frappe.throw(_("Cumulative output must remain non-decreasing across the run. Correct the affected evidence through authorized correction."))
        high_water = max(high_water, flt(row.get("cumulative_qty")))
    latest = effective[-1] if effective else None
    return {"current_cumulative": flt(latest.get("cumulative_qty")) if latest else 0.0,
            "high_water": high_water, "last_reading": latest, "effective": effective}


def run_state(card, plan=None):
    if plan is None:
        plan = enabled_plan(frappe.get_doc("Work Order", card.work_order))
    if not plan:
        return {"enabled": False}
    if plan["job_card"] != card.name:
        frappe.throw(_("Output authority must reference the frozen Compounding Job Card."))
    rows = readings_for(card.name)
    snapshot_mode = bool(snapshots.activation(card))
    state = snapshots.reduce_snapshots(rows) if snapshot_mode else reduce_readings(rows)
    from calco_erp.calco_production.shift_unique_output import enabled as unique_enabled
    unique_mode = unique_enabled(card)
    planned = flt(card.for_quantity)
    return {**state, "enabled": True, "authority": AUTHORITY, "snapshot_mode": snapshot_mode, "unique_mode": unique_mode, "calculation_version": snapshots.VERSION if snapshot_mode else AUTHORITY, "uom": plan["quantity_uom"],
            "job_card": card.name, "planned_qty": planned,
            "remaining_qty": max(planned - state["current_cumulative"], 0),
            "over_plan_qty": max(state["current_cumulative"] - planned, 0),
            "current_shift": card.get("custom_shift_type") or "", "readings": rows}


def normalized(field, value):
    if field in {"reading_time", "entered_on"}:
        return get_datetime(value) if value else None
    if field in {"cumulative_qty", "sequence", "source_revision", "shift_qty"}:
        return flt(value)
    return cstr(value)


def protect_child(row):
    previous = frappe.db.get_value(DOCTYPE, row.name, [*FIELDS, "parent", "parenttype", "parentfield", "idx"], as_dict=True) if row.get("name") else None
    if previous:
        if any(normalized(f, row.get(f)) != normalized(f, previous.get(f)) for f in [*FIELDS, "parent", "parenttype", "parentfield", "idx"]):
            frappe.throw(_("Saved production output is immutable. Record an authorized correction."))
    elif not getattr(frappe.flags, WRITE_FLAG, False):
        frappe.throw(_("Use Record Production Output to append output evidence."))


def prevent_delete(doc, method=None):
    frappe.throw(_("Production output evidence cannot be deleted; use authorized correction."))


def validate_report(doc, card, wo):
    plan = enabled_plan(wo)
    rows = doc.get("output_readings") or []
    if not plan:
        if rows:
            frappe.throw(_("This historical run does not use the controlled output authority."))
        return
    qc._lock(wo.name)
    if not doc.is_new() and getattr(doc, "_original_modified", None):
        from frappe.utils import get_datetime
        current = frappe.db.sql("select modified from `tabShift Report` where name=%s for update", (doc.name,))
        if current and get_datetime(current[0][0]) != get_datetime(doc._original_modified):
            frappe.throw(_("Shift Report changed concurrently. Reload before saving."), frappe.TimestampMismatchError)
    if plan["job_card"] != doc.job_card:
        frappe.throw(_("Output belongs to the frozen Compounding run only."))
    saved = frappe.get_all(DOCTYPE, filters={"parent": doc.name}, pluck="name") if not doc.is_new() else []
    if not set(saved).issubset({r.name for r in rows}):
        prevent_delete(doc)
    for row in rows:
        protect_child(row)
    if snapshots.activation(card):
        snapshots.validate_snapshot(doc, card, wo, plan)
        return
    # This legacy editable scalar is not an output authority for new-model runs.
    if flt(doc.get("cumulative_production")):
        frappe.throw(_("Use Record Production Output; the legacy cumulative field is not editable for this run."))
    if doc.status == "Completed" and doc.get("correction_of"):
        reduce_readings(readings_for(card.name), activate_report=doc.name)


def validate_entry(card, report, state, quantity, target, reason, work_order=None):
    from calco_erp.calco_production import shift_reporting as sr
    if report.status not in {"Draft", "Active"} or report.get("engineer_signed_by"):
        frappe.throw(_("Output cannot be appended after sign-off. Use the authorized Shift Report correction process."))
    if target:
        sr._check_roles(sr.CORRECTION_ROLES, "Production Head or Manufacturing Manager authority is required for output correction.")
        if not cstr(reason).strip():
            frappe.throw(_("Output Correction Reason is mandatory."))
        if not any(r["name"] == target for r in state["effective"]):
            frappe.throw(_("Select the current effective reading from this run."))
    else:
        if report.get("correction_of"):
            frappe.throw(_("A correction Shift Report can contain corrections only."))
        sr._validate_creation_authority(card, work_order or frappe._dict(name=card.work_order))
        if not any(r.get("from_time") and not r.get("to_time") for r in card.get("time_logs") or []):
            frappe.throw(_("An active Job Card time log is required to record live output."))
        if quantity < state["current_cumulative"]:
            frappe.throw(_("Cumulative output cannot decrease. Use an authorized correction."))


@frappe.whitelist()
def record_production_output(shift_report, cumulative_qty, correction_of="", reason=""):
    from calco_erp.calco_production import shift_reporting as sr
    report = frappe.get_doc("Shift Report", shift_report)
    report.check_permission("write")
    qc._lock(report.work_order)
    report.reload()
    card, wo = sr._get_controlled_context(report.job_card)
    plan = enabled_plan(wo)
    if snapshots.activation(card):
        frappe.throw(_("Enter production quantities once in Shift Report Packing Details and Save."))
    if not plan:
        frappe.throw(_("Live output is enabled only for newly activated controlled-output runs."))
    if wo.docstatus != 1 or wo.status in {"Closed", "Stopped", "Cancelled"} or cint(card.docstatus) == 2:
        frappe.throw(_("An active Work Order and non-cancelled Compounding Job Card are required."))
    if frappe.db.exists("Stock Entry", {"work_order": wo.name, "purpose": "Manufacture", "docstatus": 1}):
        frappe.throw(_("Output cannot be changed after Manufacture."))
    qc.assert_no_hold(wo.name)
    try:
        quantity = float(cumulative_qty)
    except (ValueError, TypeError):
        frappe.throw(_("Enter a valid cumulative output quantity."))
    if not math.isfinite(quantity) or quantity < 0:
        frappe.throw(_("Cumulative output must be finite and non-negative."))
    state = run_state(card, plan)
    validate_entry(card, report, state, quantity, correction_of, reason, wo)
    if correction_of:
        target = next(r for r in state["effective"] if r["name"] == correction_of)
        if any(r.get("correction_of") == correction_of for r in state["readings"]):
            frappe.throw(_("A correction already references this reading. Complete or review that correction first."))
        original = frappe.get_doc("Shift Report", target["parent"])
        original.check_permission("read")
        if original.get("engineer_signed_by") or original.status in {"Completed", "Superseded"}:
            if report.get("correction_of") != original.name:
                frappe.throw(_("Create the existing authorized correction Shift Report for the signed source report."))
        elif report.name != original.name:
            frappe.throw(_("Correct unsigned output in its original Shift Report."))
    timestamp = now_datetime()
    if not correction_of:
        sr._validate_event_inside_shift(report, timestamp, _("Output reading time"))
    event = {"name": frappe.generate_hash(length=10), "sequence": max([cint(r["sequence"]) for r in state["readings"]] or [0]) + 1,
             "reading_time": timestamp, "cumulative_qty": quantity, "uom": plan["quantity_uom"],
             "entered_by": frappe.session.user, "entered_on": timestamp,
             "correction_of": correction_of or "", "correction_reason": cstr(reason).strip()}
    preview = {**event, "parent": report.name, "report_status": report.status, "report_correction_of": report.get("correction_of")}
    reduce_readings([*state["readings"], preview], activate_report=report.name)
    previous_flag = getattr(frappe.flags, WRITE_FLAG, False)
    frappe.flags[WRITE_FLAG] = True
    try:
        report.append("output_readings", {**event, "__islocal": 1})
        report.save()
        report.add_comment("Info", frappe.utils.escape_html(f"Production output #{event['sequence']}: {quantity:g} {event['uom']} cumulative; correction of {correction_of or '-'}; {reason or ''}"))
    finally:
        frappe.flags[WRITE_FLAG] = previous_flag
    return {"name": report.name, "reading": event["name"], "output": run_state(card, plan)}


@frappe.whitelist()
def get_shift_output(shift_report):
    from calco_erp.calco_production import shift_reporting as sr
    report = frappe.get_doc("Shift Report", shift_report)
    report.check_permission("read")
    card, wo = sr._get_controlled_context(report.job_card)
    state = run_state(card, enabled_plan(wo))
    state["can_record"] = bool(state.get("enabled") and report.has_permission("write") and report.status in {"Draft", "Active"} and not report.get("engineer_signed_by") and not report.get("correction_of") and card.status == "Work In Progress" and not card.get("is_paused"))
    state["can_correct"] = bool(state.get("enabled") and report.has_permission("write") and report.status in {"Draft", "Active"} and not report.get("engineer_signed_by") and sr.CORRECTION_ROLES.intersection(frappe.get_roles()))
    if state.get("snapshot_mode"):
        state["can_record"] = state["can_correct"] = False
        state["shift_qty"] = report.total_quantity
        state["signed"] = bool(report.engineer_signed_by)
    state["current_shift"] = report.shift
    return state
