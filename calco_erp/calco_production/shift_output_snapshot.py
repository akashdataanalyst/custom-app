"""Versioned shift contributions. No stock posting and no historical reinterpretation."""
import json
import math
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe import _
from frappe.utils import cstr, flt, cint, now_datetime

VERSION = "shift-snapshot-v2"
ACTIVATION = "custom_shift_output_activation"
COMPONENTS = ("quantity", "loose_quantity", "lab_samples", "ud_generated", "lumps", "start_up", "other_new_output")
EXCLUDED = ("floor_sweeping", "online_ud_used", "balance_ud", "repacked_qty")
TEXT = ("other_output_reason", "repacking_source")
INPUTS = (*COMPONENTS, *EXCLUDED, *TEXT)
EXTRA_FIELDS = ("calculation_version", "logical_shift", "source_revision", "shift_qty", "components_json", "event_type", "source_pool_json")


def activation(card):
    raw = card.get(ACTIVATION)
    if not raw:
        return None
    value = json.loads(raw)
    if value.get("version") != VERSION or value.get("job_card") != card.name:
        frappe.throw(_("Invalid controlled shift output activation."))
    return value


def protect_activation(doc, method=None):
    from calco_erp.calco_production.shift_unique_output import ACTIVATION as UNIQUE_ACTIVATION
    for field in (ACTIVATION, UNIQUE_ACTIVATION):
        old = frappe.db.get_value("Job Card", doc.name, field) if not doc.is_new() else None
        if cstr(old) != cstr(doc.get(field)):
            frappe.throw(_("Shift output calculation activation cannot be edited."))


def components(doc):
    values = {}
    for f in (*COMPONENTS, *EXCLUDED):
        try:
            v = float(doc.get(f) or 0)
        except (TypeError, ValueError):
            frappe.throw(_("Production quantities must be numeric."))
        if not math.isfinite(v) or v < 0:
            frappe.throw(_("Production quantities must be finite and non-negative."))
        values[f] = round(v, 6)
    values.update({f: cstr(doc.get(f)).strip() for f in TEXT})
    if values["other_new_output"] and not values["other_output_reason"]:
        frappe.throw(_("Explain Other Newly Produced Output; do not repeat any other category."))
    if values["repacked_qty"] and not values["repacking_source"]:
        frappe.throw(_("Identify the original Shift Report / output for repacking."))
    return values


def shift_total(values):
    return round(sum(values[f] for f in COMPONENTS), 6)


def reduce_snapshots(rows):
    effective = {}
    high_water = 0.0
    sequences = set()
    for row in sorted(rows, key=lambda r: cint(r.get("sequence"))):
        if row.get("calculation_version") != VERSION:
            frappe.throw(_("Mixed output calculation versions require controlled reconciliation."))
        seq = cint(row.get("sequence"))
        if seq in sequences:
            frappe.throw(_("Duplicate run output sequence."))
        sequences.add(seq)
        qty = flt(row.get("shift_qty"))
        if not math.isfinite(qty) or qty < 0 or not row.get("logical_shift"):
            frappe.throw(_("Invalid shift contribution evidence."))
        if row.get("report_status") == "Abandoned":
            continue
        # Pending corrections never affect the run, even when their parent is later completed.
        if row.get("report_correction_of") and row.get("event_type") != "Correction Approval":
            continue
        effective[row["logical_shift"]] = row
        high_water = max(high_water, sum(flt(r["shift_qty"]) for r in effective.values()))
    result = list(effective.values())
    return {"current_cumulative": round(sum(flt(r["shift_qty"]) for r in result), 6),
            "high_water": round(high_water, 6), "effective": result,
            "last_reading": max(result, key=lambda r: cint(r["sequence"])) if result else None}


def validate_snapshot(doc, card, wo, plan):
    from calco_erp.calco_production import shift_production_output as out, shift_reporting as sr, in_process_quality as qc
    from calco_erp.calco_production import shift_unique_output as unique
    unique_mode = unique.enabled(card)
    get_components = unique.components if unique_mode else components
    values = get_components(doc)
    total = unique.total(values) if unique_mode else shift_total(values)
    previous = frappe.get_doc("Shift Report", doc.name) if not doc.is_new() else None
    if previous and previous.status in sr.FROZEN_STATUSES:
        return  # existing full-document immutability check remains authoritative
    old_rows = list(previous.get("output_readings") or []) if previous else []
    latest = old_rows[-1] if old_rows else None
    changed = (json.loads(latest.components_json) != values) if latest else any(v for k, v in values.items() if k != "quantity_rule")
    # Engineer sign-off records review of its bound revision; completion freezes output.
    if previous and previous.get("engineer_output_revision") != doc.get("engineer_output_revision"):
        frappe.throw(_("The Engineer output revision is server controlled."))
    if not previous and doc.get("engineer_output_revision"):
        frappe.throw(_("The Engineer output revision is server controlled."))
    correction = bool(doc.get("correction_of"))
    approve = correction and doc.status == "Completed" and (not previous or previous.status != "Completed")
    signing = bool(doc.get("engineer_signed_by") and not (previous and previous.get("engineer_signed_by")))
    # Derived fields cannot be used as inputs, including through direct API saves.
    doc.others = 0 if unique_mode else round(sum(values[f] for f in COMPONENTS if f not in {"quantity", "loose_quantity"}), 6)
    doc.total_quantity = total
    doc.cumulative_production = 0
    if doc.is_new():
        return  # creation/correction setup never invents an output event
    if not changed and not approve and not (signing and not latest):
        if signing:
            doc.engineer_output_revision = latest.name
        return
    # Live revisions replace the effective contribution, including downward edits.
    # Historical snapshots and the threshold high-water evidence remain intact.
    if frappe.db.exists("Shift Report", {"correction_of":doc.name,"status":("not in",["Superseded","Abandoned"])}):
        frappe.throw(_("A correction is pending for this shift; complete that controlled correction first."))
    if wo.docstatus != 1 or wo.status in {"Closed","Stopped","Cancelled"} or cint(card.docstatus) == 2:
        frappe.throw(_("An active Work Order and non-cancelled Job Card are required."))
    if frappe.db.exists("Stock Entry", {"work_order":wo.name,"purpose":"Manufacture","docstatus":1,"custom_partial_fg_lot":["is","not set"]}):
        frappe.throw(_("Output cannot change after Manufacture."))
    qc.assert_no_hold(wo.name)
    timestamp = now_datetime()
    if correction:
        sr._check_roles(sr.CORRECTION_ROLES, "Production Head or Manufacturing Manager authority is required for output correction.")
        if not cstr(doc.correction_reason).strip():
            frappe.throw(_("Correction Reason is mandatory."))
    elif changed:
        sr._check_roles(sr.ENGINEER_ROLES, "Production Engineer authority is required to record shift output.")
        sr._validate_creation_authority(card, wo)
        if not any(r.get("from_time") and not r.get("to_time") for r in card.get("time_logs") or []):
            frappe.throw(_("An active Job Card time log is required to record live output."))
        sr._validate_event_inside_shift(doc, timestamp, _("Output snapshot time"))
    rows = out.readings_for(card.name, for_update=True)
    original = frappe.get_doc("Shift Report", doc.correction_of) if correction else None
    root = (original.output_readings[-1].logical_shift if original and original.output_readings else doc.correction_of) if correction else doc.name
    revision = cint(latest.source_revision) + (1 if changed or not latest else 0) if latest else 1
    event = {"name":frappe.generate_hash(length=10),"sequence":max([cint(r["sequence"]) for r in rows] or [0])+1,
             "reading_time":timestamp,"entered_on":timestamp,"entered_by":frappe.session.user,"uom":plan["quantity_uom"],
             "calculation_version":VERSION,"logical_shift":root,"source_revision":revision,"shift_qty":total,
             "components_json":qc.encode(values),"event_type":"Correction Approval" if approve else "Snapshot",
             "correction_of":(latest.name if latest else (original.output_readings[-1].name if original and original.output_readings else "")),
             "correction_reason":doc.get("correction_reason") or ""}
    preview = {**event,"parent":doc.name,"report_correction_of":doc.get("correction_of")}
    from calco_erp.calco_production.partial_fg_lots import assert_output_covers_lots
    assert_output_covers_lots(wo.name, [*rows, preview])
    from calco_erp.calco_production.physical_completion import protect_output_floor
    protect_output_floor(card, [*rows, preview])
    if unique_mode:
        event["source_pool_json"] = qc.encode(unique.source_pool(rows, preview))
    event["cumulative_qty"] = reduce_snapshots([*rows,preview])["current_cumulative"]
    doc.append("output_readings", {**event, "__islocal": 1})
    if signing:
        doc.engineer_output_revision = event["name"]


def activate_run(job_card):
    from calco_erp.release_profile import require_recovery_distribution
    require_recovery_distribution()
    """Explicit Administrator-only Recovery transition; no backfill or QC-plan mutation."""
    from calco_erp.calco_production import shift_production_output as out, in_process_quality as qc
    if frappe.local.site != "recovery120120.localhost" or frappe.session.user != "Administrator":
        frappe.throw(_("Recovery Administrator activation is required."), frappe.PermissionError)
    card = frappe.get_doc("Job Card",job_card)
    qc._lock(card.work_order)
    card.reload()
    if activation(card):
        return activation(card)
    wo = frappe.get_doc("Work Order",card.work_order)
    plan = out.enabled_plan(wo)
    if not plan or plan["job_card"] != card.name or out.readings_for(card.name):
        frappe.throw(_("Activation requires a frozen controlled run with no output readings."))
    if card.status != "Work In Progress" or flt(card.total_completed_qty):
        frappe.throw(_("Activation requires a running zero-output Job Card."))
    for name in frappe.get_all("Shift Report",filters={"job_card":card.name},pluck="name"):
        d=frappe.get_doc("Shift Report",name)
        if d.status not in {"Draft","Active"} or d.engineer_signed_by or d.senior_engineer_signed_by or d.correction_of or any(d.get(f) for f in (*INPUTS,"others","total_quantity","cumulative_production")):
            frappe.throw(_("Activation requires unsigned zero-output Shift Reports."))
    evidence={"version":VERSION,"job_card":card.name,"work_order":wo.name,"activated_by":frappe.session.user,
              "activated_on":str(now_datetime()),"qc_fingerprint":wo.get(qc.FINGERPRINT),"zero_output_verified":True}
    frappe.db.set_value("Job Card",card.name,ACTIVATION,qc.encode(evidence),update_modified=False)
    return evidence


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({"Job Card":[{"fieldname":ACTIVATION,"label":"Controlled Shift Output Activation","fieldtype":"Long Text","read_only":1,"hidden":1,"no_copy":1}]},update=True)


def activate_at_first_start(card, plan):
    """Only brand-new successful Recovery starts; resumed frozen runs never call this."""
    from calco_erp.calco_production import in_process_quality as qc, shift_production_output as out
    if not production_site_allowed() or plan.get("quantity_authority") != out.AUTHORITY:
        return
    if card.get(ACTIVATION) or out.readings_for(card.name):
        frappe.throw(_("A new run must not contain previous output evidence."))
    evidence = {"version":VERSION,"job_card":card.name,"work_order":card.work_order,
                "activated_by":frappe.session.user,"activated_on":str(now_datetime()),
                "qc_fingerprint":qc.fingerprint(plan),"zero_output_verified":True,
                "activation_basis":"Successful first controlled Compounding Start"}
    encoded = qc.encode(evidence)
    frappe.db.set_value("Job Card",card.name,ACTIVATION,encoded,update_modified=False)
    setattr(card, ACTIVATION, encoded)
    from calco_erp.calco_production.shift_unique_output import activate_fresh
    activate_fresh(card, plan)

