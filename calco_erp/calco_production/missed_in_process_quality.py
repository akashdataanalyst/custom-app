from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime
from calco_erp.calco_production import in_process_quality as qc
from calco_erp.calco_quality.purchase_receipt_qc import DEVIATION_APPROVER_ROLES

EVIDENCE = "custom_ipqc_missed_events"
WINDOW = "custom_ipqc_sampling_window"
ESCALATE = "custom_ipqc_missed_escalation"


def events_for(wo):
    return frappe.parse_json(wo.get(EVIDENCE) or "[]")


def disposition_for(events, key):
    matching = [e for e in events if e["checkpoint"] == key]
    if not matching:
        return None
    request = next((e for e in matching if e["action"] == "Requested"), None)
    if not request:
        return None
    approved = next((e for e in matching if e["action"] == "Escalation Approved"), None)
    closed = next((e for e in matching if e["action"] == "Dispositioned"), None)
    resolved = bool(closed and (not request["escalation_required"] or approved))
    return {"reference": request["id"], "resolved": resolved,
            "state": "Missed / Dispositioned" if resolved else "Awaiting Quality Closure" if approved else "Awaiting Escalation",
            "events": matching}


def apply_resolution(obligation, events):
    disposition = disposition_for(events, obligation["key"])
    obligation["disposition"] = disposition
    obligation["dispositioned"] = bool(disposition and disposition["resolved"])
    obligation["state"] = (disposition["state"] if disposition else
        "Satisfied" if obligation["satisfied"] else
        "Overdue" if obligation.get("overdue") else
        "Available" if obligation["available"] else "Awaiting end of batch")
    if disposition:
        obligation["can_create"] = False


def validate_action(wo, checkpoint, events, action, reason, remarks, shift, reference):
    """Authority and state checks are shared by the API and transaction-free tests."""
    if action == "Approve Escalation":
        qc._role(set(DEVIATION_APPROVER_ROLES))
    else:
        qc._role({"Quality Manager"})
    if action not in {"Record Disposition", "Approve Escalation", "Complete Disposition"}:
        frappe.throw(_("Unknown missed-sample action."))
    if not cstr(remarks).strip():
        frappe.throw(_("Quality/approval remarks are required."))
    current = disposition_for(events, checkpoint["key"])
    if action == "Record Disposition":
        if checkpoint["checkpoint"] != "Periodic" or not checkpoint.get("overdue") or checkpoint["satisfied"] or current:
            frappe.throw(_("Record a missed disposition only for an unresolved Overdue Periodic checkpoint."))
        if checkpoint.get("inspections"):
            frappe.throw(_("This checkpoint already has inspection evidence. Resolve it through the existing QI review/follow-up process."))
        if not cstr(reason).strip() or not cstr(shift).strip():
            frappe.throw(_("Disposition reason and applicable shift are required."))
        escalation = any(cint(r.get(ESCALATE)) or (cint(r.get("critical_test")) and cint(r.get("custom_ipqc_mandatory"))) for r in checkpoint["rows"])
        if escalation and not cstr(reference).strip():
            frappe.throw(_("Critical/plan-required escalation needs a supporting Quality Action reference."))
        return {"action": "Requested", "escalation_required": escalation}
    if not current or current["resolved"]:
        frappe.throw(_("An unresolved missed-sample request is required."))
    request = current["events"][0]
    approved = any(e["action"] == "Escalation Approved" for e in current["events"])
    if action == "Approve Escalation":
        if not request["escalation_required"] or approved:
            frappe.throw(_("This request is not awaiting escalation approval."))
        if request["by"] == frappe.session.user:
            frappe.throw(_("Escalation approval must be by a different authorized user."))
        return {"action": "Escalation Approved"}
    if request["escalation_required"] and not approved:
        frappe.throw(_("Required escalation approval must precede Quality closure."))
    return {"action": "Dispositioned"}


@frappe.whitelist()
def record_missed_sample(work_order, checkpoint_key, action="Record Disposition", reason="", remarks="", shift="", quality_action="", attachment=""):
    # Check authority before accessing any run information.
    qc._role(set(DEVIATION_APPROVER_ROLES) if action == "Approve Escalation" else {"Quality Manager"})
    qc._lock(work_order)
    wo = frappe.get_doc("Work Order", work_order)
    wo.check_permission("read")
    if not qc.is_parallel(wo) or cint(wo.docstatus) != 1 or wo.status in {"Closed", "Stopped", "Cancelled"}:
        frappe.throw(_("An active submitted parallel Work Order is required."))
    if frappe.db.exists("Stock Entry", {"work_order": work_order, "purpose": "Manufacture", "docstatus": 1}):
        frappe.throw(_("Missed-sample evidence cannot be changed after Manufacture."))
    plan = qc.frozen_plan(wo)
    events = events_for(wo)
    state = qc.aggregate(qc.get_obligations(wo, plan), qc.inspection_rows(work_order), events)
    checkpoint = next((r for r in state["checkpoints"] if r["key"] == checkpoint_key), None)
    if not checkpoint:
        frappe.throw(_("Checkpoint does not belong to this frozen run."))
    event = validate_action(wo, checkpoint, events, action, reason, remarks, shift, quality_action)
    if action == "Record Disposition":
        if quality_action:
            support = frappe.get_doc("Quality Action", quality_action)
            support.check_permission("read")
        if attachment:
            attached = frappe.get_doc("File", attachment)
            attached.check_permission("read")
            if attached.attached_to_doctype != "Work Order" or attached.attached_to_name != wo.name:
                frappe.throw(_("Supporting attachment must belong to this Work Order."))
        event.update(reason=cstr(reason).strip(), shift=cstr(shift).strip(), quality_action=quality_action,
                     attachment=attachment, trigger_basis=checkpoint["trigger_basis"], due_point=checkpoint["trigger_at"],
                     sampling_window=checkpoint["sampling_window"], overdue_after=checkpoint["overdue_after"],
                     job_card=plan["job_card"], batch=plan["batch"], fingerprint=wo.get(qc.FINGERPRINT))
    event.update(id=frappe.generate_hash(length=16), checkpoint=checkpoint_key,
                 remarks=cstr(remarks).strip(), by=frappe.session.user, on=str(now_datetime()), authority="Deviation Approver" if action == "Approve Escalation" else "Quality Manager")
    events.append(event)
    if event["action"] == "Requested" and not event["escalation_required"]:
        events.append({"id": frappe.generate_hash(length=16), "checkpoint": checkpoint_key,
                       "action": "Dispositioned", "by": frappe.session.user, "on": event["on"], "remarks": event["remarks"]})
    # Same server-only append pattern as existing QI action evidence; locked WO
    # serializes disposition, checkpoint creation, and Manufacture decisions.
    frappe.db.set_value("Work Order", wo.name, EVIDENCE, qc.encode(events))
    wo.add_comment("Info", frappe.utils.escape_html(f"IPQC {checkpoint_key}: {event['action']} [{event['id']}] — {event['remarks']}"))
    return {"reference": event["id"], "checkpoint": checkpoint_key}


def protect_history(doc, method=None):
    if doc.get(EVIDENCE):
        frappe.throw(_("Work Order contains permanent missed-sample disposition evidence and cannot be deleted."))
