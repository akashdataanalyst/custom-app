"""Terminal abandonment of ineffective corrections; never an output approval."""
import json
import frappe
from frappe import _
from frappe.utils import cstr, now_datetime

ROLES = {"Production Head", "Manufacturing Manager"}
FIELDS = ("abandoned_by", "abandoned_on", "abandonment_reason", "abandonment_evidence")

def protect_abandonment(doc):
    old = frappe.get_doc("Shift Report", doc.name) if not doc.is_new() else None
    if old and old.status == "Abandoned":
        frappe.throw(_("Abandoned correction evidence is immutable."))
    if doc.status == "Abandoned" or any(cstr(doc.get(f)) != cstr(old.get(f) if old else None) for f in FIELDS):
        frappe.throw(_("Use Abandon Pending Correction to record abandonment evidence."))

def eligible(doc, original, wo):
    if not doc.correction_of or doc.status not in {"Draft", "Active"} or doc.docstatus != 0:
        frappe.throw(_("Only an open, unapproved correction may be abandoned."))
    if doc.senior_engineer_signed_by or doc.superseded_by or original.superseded_by or original.status not in {"Draft", "Active", "Completed"}:
        frappe.throw(_("An effective or superseded correction cannot be abandoned."))
    if any(r.event_type == "Correction Approval" for r in doc.output_readings):
        frappe.throw(_("A Correction Approval already made this correction authoritative."))
    if wo.docstatus != 1 or wo.status == "Cancelled" or original.job_card != doc.job_card or original.work_order != doc.work_order:
        frappe.throw(_("Correction execution lineage is not valid for abandonment."))

@frappe.whitelist()
def abandon_pending_correction(name, reason):
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw(_("Production Head or Manufacturing Manager authority is required."), frappe.PermissionError)
    reason = cstr(reason).strip()
    if not reason: frappe.throw(_("Abandonment Reason is mandatory."))
    from calco_erp.calco_production import in_process_quality as qc
    wo_name = frappe.db.get_value("Shift Report", name, "work_order")
    if not wo_name: frappe.throw(_("Shift Report was not found."))
    qc._lock(wo_name)
    doc = frappe.get_doc("Shift Report", name)
    doc.check_permission("write")
    if not doc.correction_of: frappe.throw(_("Select a Correction Shift Report."))
    original = frappe.get_doc("Shift Report", doc.correction_of)
    wo = frappe.get_doc("Work Order", wo_name)
    eligible(doc, original, wo)
    before = doc.as_dict()
    timestamp = now_datetime()
    evidence = {"action":"Abandon Pending Correction", "correction":doc.name,
        "original":original.name,"job_card":doc.job_card,"work_order":wo.name,
        "actor":frappe.session.user,"timestamp":str(timestamp),"reason":reason,
        "snapshot_ids":[r.name for r in doc.output_readings],"effective_before":False}
    # Normal document API, narrowly updating lifecycle evidence only. Saving the
    # output form would touch historical child metadata or create a new snapshot.
    doc.db_set({"status":"Abandoned","abandoned_by":frappe.session.user,
        "abandoned_on":timestamp,"abandonment_reason":reason,
        "abandonment_evidence":json.dumps(evidence,sort_keys=True)},commit=False)
    after = frappe.get_doc("Shift Report", name).as_dict()
    allowed = {"status","modified","modified_by",*FIELDS}
    if frappe.as_json({k:v for k,v in before.items() if k not in allowed}) != frappe.as_json({k:v for k,v in after.items() if k not in allowed}):
        frappe.throw(_("Unexpected mutation during correction abandonment; action aborted."))
    return {"name":name,"status":"Abandoned","original":original.name,"audit":evidence}
