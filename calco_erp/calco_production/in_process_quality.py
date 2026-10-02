from __future__ import annotations

import hashlib
import json
import math

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime

MODEL = "parallel-ipqc-v1"
STAGE = "In-Process QC"
MODEL_FIELD = "custom_ipqc_model"
SNAPSHOT = "custom_ipqc_plan_snapshot"
FINGERPRINT = "custom_ipqc_plan_fingerprint"
STARTED = "custom_ipqc_started_on"
CHECKPOINTS = {"Startup": "custom_ipqc_startup", "Stabilization": "custom_ipqc_stabilization", "End-of-Batch": "custom_ipqc_end_of_batch"}
PLAN_FIELDS = ["name", "custom_inspection_scope", "parameter", "version", "minimum_value", "maximum_value", "target_value", "unit", "test_type", "critical_test", "custom_ipqc_mandatory", "custom_ipqc_samples", *CHECKPOINTS.values(), "custom_ipqc_period_basis", "custom_ipqc_period_interval", "custom_ipqc_sampling_window", "custom_ipqc_missed_escalation", "custom_ipqc_quantity_basis"]
QI_IDENTITY = ["inspection_type", "batch_no", "company", "custom_work_order", "custom_work_order_qc_stage", "reference_type", "reference_name", "item_code", "custom_ipqc_batch", FINGERPRINT, "custom_ipqc_checkpoint", "custom_ipqc_sequence", "custom_ipqc_key", "custom_ipqc_specs", "custom_ipqc_sampled_on", "custom_ipqc_sampled_by", "custom_ipqc_shift", "custom_ipqc_follow_up_of"]
AUDIT = "custom_ipqc_actions"
QUALITY_ROLES = {"Quality User", "Quality Manager"}
PRODUCTION_ROLES = {"Production Engineer", "Production Head", "Manufacturing Manager"}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def is_parallel(wo):
    return bool(wo and wo.get(MODEL_FIELD) == MODEL)


def _lock(work_order):
    frappe.db.sql("select name from `tabWork Order` where name=%s for update", (work_order,))


def _role(roles):
    if frappe.session.user != "Administrator" and not roles.intersection(frappe.get_roles()):
        frappe.throw(_("This action requires the designated Quality or Production authority."), frappe.PermissionError)


def initialize_work_order(doc, method=None):
    # No defaults or backfill: only newly inserted, plan-linked Work Orders opt in.
    for field in (MODEL_FIELD, SNAPSHOT, FINGERPRINT, STARTED, "custom_ipqc_missed_events"):
        doc.set(field, None)
    from calco_erp.calco_production.fg_planning_authority import is_dashboard
    if is_dashboard(doc) or (doc.get("production_plan") and doc.get("production_plan_item")):
        doc.set(MODEL_FIELD, MODEL)


def protect_work_order(doc, method=None):
    if doc.is_new():
        return
    old = frappe.db.get_value("Work Order", doc.name, [MODEL_FIELD, SNAPSHOT, FINGERPRINT, STARTED, "custom_ipqc_missed_events"], as_dict=True)
    if not old:
        return
    for field in (MODEL_FIELD, SNAPSHOT, FINGERPRINT, STARTED, "custom_ipqc_missed_events"):
        if cstr(doc.get(field)) != cstr(old.get(field)):
            frappe.throw(_("The run QC model and frozen plan cannot be edited."))
    if old.get(SNAPSHOT):
        plan = json.loads(old[SNAPSHOT])
        if doc.production_item != plan["item_code"] or cstr(doc.get("custom_fg_batch_no")) != plan["batch"]:
            frappe.throw(_("Frozen QC run item and batch lineage cannot be changed."))


def validate_plan_row(doc):
    if doc.get("custom_inspection_scope") != STAGE:
        return
    if not any(cint(doc.get(field)) for field in CHECKPOINTS.values()) and not doc.get("custom_ipqc_period_basis"):
        frappe.throw(_("Select at least one In-Process checkpoint or a periodic trigger."))
    if cint(doc.get("custom_ipqc_samples")) <= 0:
        frappe.throw(_("In-Process samples per parameter must be positive."))
    if flt(doc.get("custom_ipqc_sampling_window")) < 0:
        frappe.throw(_("Sampling window cannot be negative."))
    basis = doc.get("custom_ipqc_period_basis")
    if doc.get("custom_ipqc_quantity_basis") not in {None, "", "Cumulative FG Qty", "Unique Physical Process Output"}:
        frappe.throw(_("Invalid quantity trigger basis."))
    if basis and (basis not in {"Elapsed Minutes", "Produced Quantity"} or flt(doc.get("custom_ipqc_period_interval")) <= 0):
        frappe.throw(_("Periodic sampling requires an explicit positive time or quantity interval."))
    if not basis and flt(doc.get("custom_ipqc_period_interval")):
        frappe.throw(_("Select the periodic trigger basis."))


def prepare_start(job_card):
    if frappe.db.get_value("Work Order", job_card.work_order, MODEL_FIELD) != MODEL:
        return None
    wo = frappe.get_doc("Work Order", job_card.work_order)
    if not is_parallel(wo):
        return None
    _lock(wo.name)
    wo.reload()
    if wo.get(SNAPSHOT):
        assert_no_hold(wo.name)
        return None
    from calco_erp.calco_production.compounding_execution import job_card_has_started
    if job_card_has_started(job_card):
        frappe.throw(_("A started parallel QC run is missing its frozen plan; Quality must investigate."))
    rows = frappe.get_all("FG Control Plan", filters={"fg_item_code": wo.production_item, "is_active": 1, "applicable": 1, "custom_inspection_scope": STAGE}, fields=PLAN_FIELDS, order_by="name asc", limit_page_length=0)
    from calco_erp.calco_quality.manufacturing_master_authority import guard_and_bind
    master_state = guard_and_bind(wo.production_item, job_card.get("custom_production_line") or job_card.get("workstation"), rows)
    if not rows:
        frappe.throw(_("Configure the applicable In-Process FG Control Plan before first Compounding Start."))
    for row in rows:
        validate_plan_row(row)
    plan = {"model": MODEL, "work_order": wo.name, "job_card": job_card.name, "item_code": wo.production_item, "quantity_authority": "shift-output-v1", "quantity_uom": frappe.db.get_value("Item", wo.production_item, "stock_uom"), "rows": [dict(row) for row in rows]}
    from calco_erp.calco_production.mpds_start_snapshot import prepare as prepare_mpds
    mpds = master_state["mpds"] if master_state else prepare_mpds(wo.production_item, job_card.get("custom_production_line") or job_card.get("workstation"))
    if mpds:
        plan["mpds_snapshot"] = mpds
    from calco_erp.calco_quality.quality_master_versions import prepare_for_start as prepare_quality_masters
    quality = master_state["quality_snapshot"] if master_state else prepare_quality_masters(wo.production_item, job_card.get("custom_production_line") or job_card.get("workstation"))
    if quality:
        plan["quality_master_snapshot"] = quality
    return plan


def freeze_after_start(job_card, plan):
    if not plan:
        return
    if not any(row.get("from_time") for row in job_card.get("time_logs") or []):
        frappe.throw(_("Standard Job Card Start must record a start time before QC activation."))
    # Called after standard Start and batch allocation in the same transaction.
    plan["batch"] = cstr(frappe.db.get_value("Work Order", job_card.work_order, "custom_fg_batch_no"))
    if not plan["batch"]:
        frappe.throw(_("A production batch is required to freeze the In-Process QC plan."))
    frappe.db.set_value("Work Order", job_card.work_order, {SNAPSHOT: encode(plan), FINGERPRINT: fingerprint(plan), STARTED: now_datetime()}, update_modified=False)
    from calco_erp.calco_production.shift_output_snapshot import activate_at_first_start
    activate_at_first_start(job_card, plan)


def frozen_plan(wo):
    if not wo.get(SNAPSHOT):
        frappe.throw(_("In-Process QC becomes Available after successful Compounding Start."))
    plan = json.loads(wo.get(SNAPSHOT))
    if fingerprint(plan) != wo.get(FINGERPRINT):
        frappe.throw(_("Frozen QC plan fingerprint is invalid."))
    return plan


def checkpoint_obligations(plan, elapsed_minutes=0, produced_qty=0, ended=False, fg_qty=None):
    """Recompute obligations from frozen rules and standard execution, never latest QI."""
    result = []
    for label, field in CHECKPOINTS.items():
        rows = [row for row in plan["rows"] if cint(row.get(field))]
        if rows:
            result.append({"key": label, "checkpoint": label, "sequence": 1, "rows": rows, "mandatory": any(cint(r.get("custom_ipqc_mandatory")) for r in rows), "available": label != "End-of-Batch" or ended})
    groups = {}
    for row in plan["rows"]:
        basis = row.get("custom_ipqc_period_basis")
        if basis:
            quantity_basis = row.get("custom_ipqc_quantity_basis") or "" if basis == "Produced Quantity" else ""
            groups.setdefault((basis, flt(row.get("custom_ipqc_period_interval")), quantity_basis), []).append(row)
    for (basis, interval, quantity_basis), rows in sorted(groups.items()):
        if interval <= 0:
            frappe.throw(_("Invalid frozen periodic interval."))
        progress = elapsed_minutes if basis == "Elapsed Minutes" else (fg_qty if quantity_basis == "Cumulative FG Qty" else produced_qty)
        if progress is None:frappe.throw(_("FG quantity evidence is required for the frozen FG trigger basis."))
        identity_basis = basis + (":" + quantity_basis if quantity_basis else "")
        for sequence in range(1, math.floor((max(progress, 0) + 1e-9) / interval) + 1):
            result.append({"key": f"Periodic:{identity_basis}:{interval:g}:{sequence}", "checkpoint": "Periodic", "sequence": sequence, "rows": rows, "mandatory": any(cint(r.get("custom_ipqc_mandatory")) for r in rows), "available": True, "trigger_basis": basis, "quantity_basis": quantity_basis, "trigger_at": interval * sequence, "sampling_window": min(flt(r.get("custom_ipqc_sampling_window")) for r in rows), "overdue_after": interval * sequence + min(flt(r.get("custom_ipqc_sampling_window")) for r in rows), "overdue": progress > interval * sequence + min(flt(r.get("custom_ipqc_sampling_window")) for r in rows) + 1e-9})
    return result


def active_elapsed_minutes(time_logs, current=None):
    # Overlapping employee logs describe one physical run; pauses contribute no time.
    current = get_datetime(current or now_datetime())
    intervals = sorted((get_datetime(r.get("from_time")), min(get_datetime(r.get("to_time") or current), current)) for r in time_logs if r.get("from_time"))
    merged = []
    for start, end in intervals:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum((end - start).total_seconds() for start, end in merged) / 60


def get_obligations(wo, plan):
    from calco_erp.calco_production.compounding_execution import job_card_is_complete
    card = frappe.get_doc("Job Card", plan["job_card"])
    # Pausing also closes logs and sets actual_end_date. Only submitted
    # completion proves that the existing controlled completion gates passed.
    from calco_erp.calco_production.physical_completion import ended as physically_ended
    ended = job_card_is_complete(card) or physically_ended(card)
    if plan.get("quantity_authority") == "shift-output-v1":
        from calco_erp.calco_production.shift_production_output import run_state
        output = run_state(card, plan)
        # Retain crossed identities after an audited correction; window state uses
        # the corrected current cumulative output, not Job Card completion quantity.
        from calco_erp.calco_production.partial_fg_lots import fg_progress
        fg = fg_progress(card) if any(r.get("custom_ipqc_quantity_basis") == "Cumulative FG Qty" for r in plan["rows"]) else None
        obligations = checkpoint_obligations(plan, active_elapsed_minutes(card.get("time_logs") or []), output["high_water"], ended, fg_qty=fg["high_water"] if fg else None)
        for obligation in obligations:
            if obligation.get("trigger_basis") == "Produced Quantity":
                current = fg["current"] if obligation.get("quantity_basis") == "Cumulative FG Qty" else output["current_cumulative"]
                obligation["overdue"] = current > obligation["overdue_after"] + 1e-9
        return _physical_end_identities(card, obligations)
    return checkpoint_obligations(plan, active_elapsed_minutes(card.get("time_logs") or []), flt(card.get("total_completed_qty")), ended)


def inspection_rows(work_order):
    return frappe.get_all("Quality Inspection", filters={"custom_work_order": work_order, "custom_work_order_qc_stage": STAGE}, fields=["name", "docstatus", "status", "custom_ipqc_key", "custom_ipqc_follow_up_of", AUDIT, "custom_ipqc_sampled_on", "custom_ipqc_shift"], order_by="creation asc", limit_page_length=0)


def audit_events(row):
    return json.loads(row.get(AUDIT) or "[]")


def active_hold(rows):
    held = set()
    for row in rows:
        for event in audit_events(row):
            if event["action"] == "Hold":
                held.add(row["name"])
            elif event["action"] == "Release Hold":
                held.discard(row["name"])
    return bool(held)


def aggregate(obligations, rows, missed_events=None):
    from calco_erp.calco_production.missed_in_process_quality import apply_resolution
    children = {}
    for row in rows:
        if cint(row.get("docstatus")) != 2 and row.get("custom_ipqc_follow_up_of"):
            children.setdefault(row["custom_ipqc_follow_up_of"], []).append(row)

    def resolved(row, visited=None):
        visited = set(visited or ())
        if row["name"] in visited or cint(row.get("docstatus")) == 2:
            return False
        visited.add(row["name"])
        if cint(row.get("docstatus")) == 1 and row.get("status") == "Accepted":
            return True
        if cint(row.get("docstatus")) == 1 and row.get("status") == "Review Required" and any(e["action"] == "Review Accepted" for e in audit_events(row)):
            return True
        return any(resolved(child, visited) for child in children.get(row["name"], []))

    blockers = []
    for obligation in obligations:
        roots = [r for r in rows if r.get("custom_ipqc_key") == obligation["key"] and not r.get("custom_ipqc_follow_up_of") and cint(r.get("docstatus")) != 2]
        obligation["inspections"] = [r["name"] for r in rows if r.get("custom_ipqc_key") == obligation["key"]]
        obligation["can_create"] = not roots
        obligation["satisfied"] = bool(roots) and all(resolved(r) for r in roots)
        apply_resolution(obligation, missed_events or [])
        if obligation["mandatory"] and not (obligation["satisfied"] or obligation["dispositioned"]):
            blockers.append(obligation["key"])
    unresolved = [r["name"] for r in rows if cint(r.get("docstatus")) != 2 and r.get("status") in {"Rejected", "Review Required"} and not resolved(r)]
    # A required follow-up cannot be hidden by a later passing unrelated sample.
    pending_followups = [r["name"] for r in rows if cint(r.get("docstatus")) != 2 and r.get("custom_ipqc_follow_up_of") and not resolved(r)]
    hold = active_hold(rows)
    ready = not (blockers or unresolved or pending_followups or hold)
    status = "Hold" if hold else "Review Required" if unresolved else "Completed" if ready else "In Progress" if any(cint(r.get("docstatus")) != 2 for r in rows) else "Available"
    return {"status": status, "ready": ready, "hold": hold, "blockers": blockers + unresolved + pending_followups, "checkpoints": obligations, "inspections": [{**{k: v for k, v in r.items() if k != AUDIT}, "actions": audit_events(r)} for r in rows]}


def state_for_work_order(wo):
    if isinstance(wo, str):
        wo = frappe.get_doc("Work Order", wo)
    if not is_parallel(wo):
        return {}
    if not wo.get(SNAPSHOT):
        return {"status": "Not Started", "ready": False, "hold": False, "blockers": ["Compounding Start required"], "checkpoints": [], "inspections": []}
    plan = frozen_plan(wo)
    state = aggregate(get_obligations(wo, plan), inspection_rows(wo.name), frappe.parse_json(wo.get("custom_ipqc_missed_events") or "[]"))
    state["fingerprint"] = wo.get(FINGERPRINT)
    state["checkpoints"] = [{k: v for k, v in o.items() if k != "rows"} for o in state["checkpoints"]]
    return state


@frappe.whitelist()
def get_in_process_qc(work_order):
    wo = frappe.get_doc("Work Order", work_order)
    if QUALITY_ROLES.intersection(frappe.get_roles()) and frappe.has_permission("Quality Inspection", "read"):
        return state_for_work_order(wo)
    wo.check_permission("read")
    return state_for_work_order(wo)


def assert_manufacture_allowed(work_order):
    if frappe.db.get_value("Work Order", work_order, MODEL_FIELD) != MODEL:
        return False
    _lock(work_order)
    wo = frappe.get_doc("Work Order", work_order)
    if not is_parallel(wo):
        return False
    state = state_for_work_order(wo)
    if not state["ready"]:
        frappe.throw(_("Manufacture is blocked by In-Process QC: {0}. Quality disposition is required.").format(", ".join(state["blockers"]) or state["status"]))
    return True


def assert_no_hold(work_order):
    from calco_erp.calco_production.fg_planning_authority import assert_execution_allowed
    assert_execution_allowed(work_order)
    if frappe.db.get_value("Work Order", work_order, MODEL_FIELD) != MODEL:
        return
    wo = frappe.get_doc("Work Order", work_order)
    if not is_parallel(wo):
        return
    _lock(work_order)
    if active_hold(inspection_rows(work_order)):
        frappe.throw(_("Quality Hold is active. Only Quality authority can release it before Production continues."))


def _validate_follow_up(parent, plan):
    if parent.get("custom_work_order") != plan["work_order"] or parent.get("reference_name") != plan["job_card"] or parent.get("custom_work_order_qc_stage") != STAGE or cint(parent.docstatus) == 2 or parent.status not in {"Rejected", "Review Required"}:
        frappe.throw(_("Follow-up requires a failed/review In-Process QI from this run."))
    events = audit_events(parent)
    recommendation = next((e for e in reversed(events) if e["action"] == "Recommendation"), None)
    adjustment = next((e for e in reversed(events) if e["action"] == "Adjustment"), None)
    if not recommendation or not adjustment or get_datetime(adjustment["on"]) < get_datetime(recommendation["on"]):
        frappe.throw(_("Record Quality recommendation, then Production adjustment/Process Observation, before follow-up QI."))
    observation = frappe.get_doc("Process Observation", adjustment["process_observation"])
    if observation.status != "Complete" or observation.work_order != plan["work_order"] or observation.job_card != plan["job_card"]:
        frappe.throw(_("Follow-up adjustment evidence must remain effective for this run."))


@frappe.whitelist()
def make_checkpoint_qi(work_order, checkpoint_key="", follow_up_of="", shift_report=""):
    _role(QUALITY_ROLES)
    _lock(work_order)
    wo = frappe.get_doc("Work Order", work_order)
    frappe.has_permission("Quality Inspection", "create", throw=True)
    if not is_parallel(wo) or cint(wo.docstatus) != 1 or wo.status in {"Stopped", "Closed", "Cancelled"}:
        frappe.throw(_("An active parallel-model Work Order is required."))
    plan = frozen_plan(wo)
    if follow_up_of:
        parent = frappe.get_doc("Quality Inspection", follow_up_of)
        parent.check_permission("read")
        _validate_follow_up(parent, plan)
        checkpoint_key = parent.get("custom_ipqc_key")
    obligations = get_obligations(wo, plan)
    obligation = next((o for o in obligations if o["key"] == checkpoint_key), None)
    if not obligation or not obligation["available"]:
        frappe.throw(_("This checkpoint is not yet due under the frozen FG Control Plan."))
    existing = frappe.db.get_value("Quality Inspection", {"custom_work_order": work_order, "custom_work_order_qc_stage": STAGE, "custom_ipqc_key": checkpoint_key, "custom_ipqc_follow_up_of": follow_up_of or "", "docstatus": ("<", 2)}, "name")
    if existing:
        existing_qi = frappe.get_doc("Quality Inspection", existing)
        if cint(existing_qi.docstatus) == 0 and any(not row.get("status") for row in existing_qi.get("readings") or []):
            # Reuse an incomplete draft through normal validation, never duplicate it.
            existing_qi.save()
        return {"name": existing}
    from calco_erp.calco_production.missed_in_process_quality import events_for, disposition_for
    if disposition_for(events_for(wo), checkpoint_key):
        frappe.throw(_("This checkpoint has missed-sample disposition evidence; do not fabricate a historical QI."))
    card = frappe.get_doc("Job Card", plan["job_card"])
    if shift_report:
        report = frappe.get_doc("Shift Report", shift_report)
        report.check_permission("read")
        if report.job_card != card.name or report.work_order != wo.name:
            frappe.throw(_("Shift Report must belong to this production run."))
    qi = frappe.new_doc("Quality Inspection")
    qi.update({"inspection_type": "In Process", "batch_no": plan["batch"] if frappe.db.exists("Batch", plan["batch"]) else "", "reference_type": "Job Card", "reference_name": card.name, "item_code": plan["item_code"], "company": wo.company, "inspected_by": frappe.session.user, "status": "Pending", "custom_work_order": work_order, "custom_work_order_qc_stage": STAGE, "custom_ipqc_batch": plan["batch"], FINGERPRINT: wo.get(FINGERPRINT), "custom_ipqc_checkpoint": obligation["checkpoint"], "custom_ipqc_sequence": obligation["sequence"], "custom_ipqc_key": checkpoint_key, "custom_ipqc_specs": encode(obligation["rows"]), "custom_ipqc_sampled_on": now_datetime(), "custom_ipqc_sampled_by": frappe.session.user, "custom_ipqc_shift": card.get("custom_shift_type") or "", "custom_ipqc_shift_report": shift_report, "custom_ipqc_follow_up_of": follow_up_of or "", AUDIT: "[]"})
    qi.flags.ipqc_creation = True
    qi.insert()
    return {"name": qi.name}


def apply_frozen_readings(doc):
    if doc.get("custom_work_order_qc_stage") != STAGE:
        return False
    validate_qi_identity(doc)
    from calco_erp.calco_quality import fg_quality_setup as fg
    specs = json.loads(doc.get("custom_ipqc_specs"))
    payloads = []
    for row in specs:
        payload = fg.build_fg_control_plan_payload(frappe._dict(row), 1)
        payload["custom_required_tests"] = cint(row["custom_ipqc_samples"])
        payloads.append(payload)
    key_map = {p["custom_parameter_key"]: p for p in payloads}
    spec_map = {p["specification"]: p for p in payloads}
    preserved = fg.get_fg_preserved_sample_entries(doc.get("parameter_samples") or [], doc.get("readings") or [], key_map, spec_map)
    doc.set("readings", [])
    for payload in payloads:
        reading = fg.build_fg_reading_row(payload)
        reading["custom_ipqc_spec_version"] = payload["version"]
        doc.append("readings", reading)
    doc.set("parameter_samples", [])
    for sample in fg.build_fg_parameter_sample_rows(payloads, preserved):
        doc.append("parameter_samples", sample)
    doc.sample_size = max(p["custom_required_tests"] for p in payloads)
    fg.evaluate_fg_inspection_state(doc)
    for reading in doc.readings:
        if not reading.get("status"):
            # ERPNext initializes this required controller field to Accepted.
            # Empty custom_parameter_result and parent Pending still mean unmeasured.
            reading.status = "Accepted"
    # All OOS remains failed evidence; non-critical OOS cannot be disguised as acceptance.
    results = [r.get("custom_parameter_result") for r in doc.readings]
    if "Rejected" in results:
        doc.status = "Rejected"
    elif "Review Required" in results:
        doc.status = "Review Required"
    elif all(r == "Accepted" for r in results):
        doc.status = "Accepted"
    else:
        doc.status = "Pending"
    return True


def validate_qi_identity(doc, method=None):
    previous = doc.get_doc_before_save() if not doc.is_new() else None
    stage = doc.get("custom_work_order_qc_stage")
    if previous and previous.get("custom_work_order_qc_stage") == STAGE and stage != STAGE:
        frappe.throw(_("In-Process inspection identity cannot be removed."))
    if stage != STAGE:
        return
    _role(QUALITY_ROLES)
    _lock(doc.get("custom_work_order"))
    wo = frappe.get_doc("Work Order", doc.get("custom_work_order"))
    plan = frozen_plan(wo)
    if not is_parallel(wo) or doc.inspection_type != "In Process" or (doc.get("batch_no") and doc.batch_no != plan["batch"]) or doc.reference_type != "Job Card" or doc.reference_name != plan["job_card"] or doc.item_code != plan["item_code"] or doc.get("custom_ipqc_batch") != plan["batch"] or doc.get(FINGERPRINT) != wo.get(FINGERPRINT):
        frappe.throw(_("QC lineage must match the frozen Work Order, Job Card, item and batch."))
    if doc.is_new() and not doc.flags.get("ipqc_creation"):
        frappe.throw(_("Create checkpoint QIs through the In-Process QC action."))
    if previous and previous.status == "Rejected":
        current_samples = {(r.get("parameter_key"), cint(r.get("sample_no"))): cstr(r.get("reading")) for r in doc.get("parameter_samples") or []}
        for sample in previous.get("parameter_samples") or []:
            recorded = cstr(sample.get("reading"))
            if recorded and current_samples.get((sample.get("parameter_key"), cint(sample.get("sample_no")))) != recorded:
                frappe.throw(_("Recorded failed sample measurements must be preserved. Create a linked follow-up QI."))
    if previous and cint(previous.docstatus) == 1:
        for field in ("status", "readings", "parameter_samples"):
            if _measurement_identity(doc.get(field)) != _measurement_identity(previous.get(field)):
                frappe.throw(_("Submitted checkpoint measurements and results are immutable."))
    if previous:
        for field in [*QI_IDENTITY, AUDIT, "custom_ipqc_shift_report"]:
            if cstr(doc.get(field)) != cstr(previous.get(field)):
                frappe.throw(_("QC identity and audit evidence cannot be edited directly."))
    if doc.get("custom_ipqc_follow_up_of"):
        _validate_follow_up(frappe.get_doc("Quality Inspection", doc.get("custom_ipqc_follow_up_of")), plan)


def before_submit_qi(doc, method=None):
    if doc.get("custom_work_order_qc_stage") != STAGE:
        return
    apply_frozen_readings(doc)
    if doc.status not in {"Accepted", "Rejected", "Review Required"}:
        frappe.throw(_("Complete all checkpoint measurements before submitting the QI."))
    from calco_erp.calco_quality.fg_quality_setup import fg_parameter_sample_has_reading
    if not doc.get("parameter_samples") or any(not fg_parameter_sample_has_reading(r) for r in doc.parameter_samples):
        frappe.throw(_("Complete all checkpoint sample readings before submission."))


def protect_qi_history(doc, method=None):
    if doc.get("custom_work_order_qc_stage") != STAGE:
        return
    _role(QUALITY_ROLES)
    _lock(doc.get("custom_work_order"))
    if doc.status in {"Rejected", "Review Required"} or audit_events(doc):
        frappe.throw(_("Failed/review QC and action evidence must be preserved; create a linked follow-up QI."))
    if frappe.db.exists("Stock Entry", {"work_order": doc.get("custom_work_order"), "purpose": "Manufacture", "docstatus": 1}):
        frappe.throw(_("Checkpoint evidence cannot be removed after Manufacture."))
    if frappe.db.exists("Quality Inspection", {"custom_ipqc_follow_up_of": doc.name, "docstatus": ("<", 2)}):
        frappe.throw(_("Inspection has follow-up evidence and cannot be removed."))


@frappe.whitelist()
def record_quality_action(quality_inspection, action, remarks):
    _role(QUALITY_ROLES)
    if action not in {"Recommendation", "Hold", "Release Hold", "Review Accepted"} or not cstr(remarks).strip():
        frappe.throw(_("Select a Quality action and enter its reason/recommendation."))
    qi = frappe.get_doc("Quality Inspection", quality_inspection)
    qi.check_permission("read")
    if qi.get("custom_work_order_qc_stage") != STAGE or cint(qi.docstatus) == 2:
        frappe.throw(_("An active In-Process QI is required."))
    _lock(qi.get("custom_work_order"))
    qi.reload()
    if action == "Review Accepted":
        _role({"Quality Manager"})
        if cint(qi.docstatus) != 1 or qi.status != "Review Required" or any(r.get("custom_parameter_result") == "Rejected" for r in qi.get("readings") or []):
            frappe.throw(_("Only a submitted review without failed measurements can receive Quality Manager review acceptance."))
    if action == "Recommendation" and qi.status not in {"Rejected", "Review Required"}:
        frappe.throw(_("Recommendations require failed or review-required QC evidence."))
    _append_action(qi, {"action": action, "remarks": cstr(remarks).strip()})
    return {"name": qi.name}


@frappe.whitelist()
def record_production_adjustment(quality_inspection, process_observation, remarks):
    _role(PRODUCTION_ROLES)
    qi = frappe.get_doc("Quality Inspection", quality_inspection)
    _lock(qi.get("custom_work_order"))
    qi.reload()
    if qi.get("custom_work_order_qc_stage") != STAGE or cint(qi.docstatus) == 2 or qi.status not in {"Rejected", "Review Required"}:
        frappe.throw(_("Adjustment must address a failed/review In-Process QI."))
    recommendation = next((e for e in reversed(audit_events(qi)) if e["action"] == "Recommendation"), None)
    observation = frappe.get_doc("Process Observation", process_observation)
    observation.check_permission("read")
    if not recommendation or not cstr(remarks).strip() or observation.status != "Complete" or observation.job_card != qi.reference_name or observation.work_order != qi.get("custom_work_order") or get_datetime(observation.get("recorded_on") or observation.creation) < get_datetime(recommendation["on"]):
        frappe.throw(_("Link an effective Process Observation from this run recorded after the Quality recommendation, with adjustment remarks."))
    _append_action(qi, {"action": "Adjustment", "remarks": cstr(remarks).strip(), "process_observation": observation.name})
    return {"name": qi.name}


def _append_action(qi, event):
    event.update({"by": frappe.session.user, "on": str(now_datetime())})
    events = audit_events(qi)
    events.append(event)
    frappe.db.set_value("Quality Inspection", qi.name, AUDIT, encode(events))
    qi.add_comment("Info", frappe.utils.escape_html(f"In-Process QC {event['action']}: {event['remarks']}"))


def validate_job_card_execution(doc, method=None):
    from calco_erp.calco_production.shift_output_snapshot import protect_activation
    protect_activation(doc)
    from calco_erp.calco_production.execution_policy import pausing
    if pausing(doc):
        return
    previous_doc = doc.get_doc_before_save()
    if previous_doc and previous_doc.get("work_order") and previous_doc.get("work_order") != doc.get("work_order"):
        previous_wo = frappe.get_doc("Work Order", previous_doc.work_order)
        if is_parallel(previous_wo) and previous_wo.get(SNAPSHOT) and frozen_plan(previous_wo)["job_card"] == doc.name:
            frappe.throw(_("Frozen production Job Card lineage cannot be changed."))
    if not doc.get("work_order"):
        return
    wo = frappe.get_doc("Work Order", doc.work_order)
    if not is_parallel(wo):
        return
    previous = doc.get_doc_before_save()
    if not wo.get(SNAPSHOT) and doc.get("operation") == "Compounding / Extrusion" and doc.get("time_logs") and not doc.flags.get("ipqc_starting"):
        frappe.throw(_("Use standard Compounding Start to activate the frozen In-Process QC plan."))
    changed = not previous or any(encode(doc.get(f)) != encode(previous.get(f)) for f in ("time_logs", "total_completed_qty", "is_paused"))
    pausing = bool(doc.get("is_paused")) and previous and flt(doc.get("total_completed_qty")) == flt(previous.get("total_completed_qty")) and [r.get("from_time") for r in doc.get("time_logs") or []] == [r.get("from_time") for r in previous.get("time_logs") or []]
    if changed and not pausing:
        assert_no_hold(wo.name)
    if wo.get(SNAPSHOT):
        plan = frozen_plan(wo)
        if doc.name == plan["job_card"] and (doc.get("operation") != "Compounding / Extrusion" or doc.get("work_order") != plan["work_order"]):
            frappe.throw(_("Frozen production Job Card lineage cannot be changed."))


def _measurement_identity(value):
    if isinstance(value, list):
        return encode([{k: v for k, v in (r.as_dict() if hasattr(r, "as_dict") else r).items() if k not in {"modified", "modified_by", "docstatus"}} for r in value])
    return encode(value)




def _physical_end_identities(card, obligations):
    # A re-ended run requires a fresh EOB; no previous lot/end acceptance is inherited.
    from calco_erp.calco_production.physical_completion import events
    count=sum(e['action']=='Physical End' for e in events(card))
    if count>1:
        for row in obligations:
            if row['checkpoint']=='End-of-Batch':row['key'] += ':Physical End:'+str(count)
    return obligations
