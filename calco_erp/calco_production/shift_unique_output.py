"""First-recognition quantity rules; pass classifications never add physical mass."""
import json
import math
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe import _
from frappe.utils import cstr, flt, now_datetime

RULE = "unique-physical-output-v1"
ACTIVATION = "custom_shift_unique_activation"
UNIQUE = ("quantity", "lab_samples", "loose_quantity", "metal_separator_qty")
PASSES = ("spy_qty", "tpy_qty")
FIELDS = (*UNIQUE, *PASSES)


def enabled(card):
    raw = card.get(ACTIVATION)
    if not raw:
        return False
    data = json.loads(raw)
    if data.get("rule") != RULE or data.get("job_card") != card.name:
        frappe.throw(_("Invalid unique-production calculation activation."))
    return True


def components(doc):
    from calco_erp.calco_production.shift_output_snapshot import INPUTS
    values = {"quantity_rule": RULE}
    for field in FIELDS:
        try:
            value = float(doc.get(field) or 0)
        except (TypeError, ValueError):
            frappe.throw(_("Output quantities must be numeric."))
        if not math.isfinite(value) or value < 0:
            frappe.throw(_("Output quantities must be finite and non-negative."))
        values[field] = round(value, 6)
    if any(doc.get(f) for f in INPUTS if f not in FIELDS):
        frappe.throw(_("Legacy output categories cannot be entered on this unique-output run. Use the plant output fields."))
    return values


def total(values):
    return round(sum(values[f] for f in UNIQUE), 6)


def source_pool(rows, event):
    """Run/batch-level source evidence, not an invented individual bag allocation.

    The first-recognition record remains the quantity authority after reprocessing.
    Each physical unit can be classified once at each additional pass level.
    """
    from calco_erp.calco_production.shift_output_snapshot import reduce_snapshots
    candidate = dict(event)
    # Validate a correction's proposed effect while it is still pending.
    if candidate.get("report_correction_of"):
        candidate["event_type"] = "Correction Approval"
    effective = reduce_snapshots([*rows, candidate])["effective"]
    unique_qty = spy_qty = tpy_qty = 0.0
    sources = []
    for row in effective:
        values = json.loads(row["components_json"])
        if values.get("quantity_rule") != RULE:
            frappe.throw(_("Historical output cannot be silently reinterpreted as unique output."))
        unique_qty += total(values)
        spy_qty += values["spy_qty"]
        tpy_qty += values["tpy_qty"]
        sources.append({"shift_report":row["parent"],"snapshot":row["name"],"unique_qty":total(values),
                        "spy_qty":values["spy_qty"],"tpy_qty":values["tpy_qty"]})
    if spy_qty > unique_qty + 0.000001:
        frappe.throw(_("SPY exceeds this run's recorded unique production. Record the original physical output first; do not count another extrusion pass as new production."))
    if tpy_qty > spy_qty + 0.000001:
        frappe.throw(_("TPY exceeds recorded SPY for this run. Record the second-pass classification before the third pass."))
    return {"rule":RULE,"scope":"Controlled Job Card / FG batch source pool","sources":sources,
            "unique_qty":round(unique_qty,6),"spy_qty":round(spy_qty,6),"tpy_qty":round(tpy_qty,6)}


def activate(job_card, first_start=False):
    if not first_start:
        from calco_erp.release_profile import require_recovery_distribution
        require_recovery_distribution()
    from calco_erp.calco_production import shift_output_snapshot as snapshots, shift_production_output as out, in_process_quality as qc
    if (first_start and not production_site_allowed()) or (not first_start and (frappe.local.site != "recovery120120.localhost" or frappe.session.user != "Administrator")):
        frappe.throw(_("Recovery Administrator activation is required."), frappe.PermissionError)
    card = frappe.get_doc("Job Card",job_card)
    qc._lock(card.work_order)
    card.reload()
    if enabled(card):
        return json.loads(card.get(ACTIVATION))
    if not snapshots.activation(card) or out.readings_for(card.name):
        frappe.throw(_("Unique-output activation requires a snapshot run with no recorded output."))
    wo = frappe.get_doc("Work Order",card.work_order)
    for name in frappe.get_all("Shift Report",filters={"job_card":card.name},pluck="name"):
        d = frappe.get_doc("Shift Report",name)
        if d.status not in {"Draft","Active"} or d.engineer_signed_by or d.senior_engineer_signed_by or d.correction_of or any(d.get(f) for f in (*snapshots.INPUTS,*FIELDS,"total_quantity","others","cumulative_production")):
            frappe.throw(_("Unique-output transition requires unsigned zero-output reports."))
    if flt(card.total_completed_qty) or card.status != "Work In Progress":
        frappe.throw(_("Unique-output transition requires a running zero-output Job Card."))
    evidence = {"rule":RULE,"job_card":card.name,"work_order":wo.name,"activated_by":frappe.session.user,
                "activated_on":str(now_datetime()),"qc_fingerprint":wo.get(qc.FINGERPRINT),"zero_output_verified":True,
                "basis":"First controlled start" if first_start else "Approved zero-output transition"}
    frappe.db.set_value("Job Card",card.name,ACTIVATION,qc.encode(evidence),update_modified=False)
    return evidence


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({"Job Card":[{"fieldname":ACTIVATION,"fieldtype":"Long Text","label":"Unique Output Rule Activation","read_only":1,"hidden":1,"no_copy":1}]},update=True)

def activate_fresh(card, plan):
    """Called only by the already guarded, successful first-start snapshot activation."""
    from calco_erp.calco_production import in_process_quality as qc
    evidence = {"rule":RULE,"job_card":card.name,"work_order":card.work_order,"activated_by":frappe.session.user,
                "activated_on":str(now_datetime()),"qc_fingerprint":qc.fingerprint(plan),"zero_output_verified":True,
                "basis":"First controlled start"}
    encoded = qc.encode(evidence)
    frappe.db.set_value("Job Card",card.name,ACTIVATION,encoded,update_modified=False)
    setattr(card,ACTIVATION,encoded)
