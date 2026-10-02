"""Serialized Stop invariant and audited, zero-quantity legacy execution closure."""
import copy
import json

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime, time_diff_in_hours

AUDIT_FIELD = "custom_stopped_execution_closure"
ROLES = {"Production Head", "Manufacturing Manager"}
_TOKEN = object()  # Cannot be supplied through document JSON or RPC flags.
RUNNING = {"Work In Progress", "Work in Progress", "In Process", "Running"}


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({"Job Card": [{"fieldname": AUDIT_FIELD,
        "label": "Stopped Execution Closure Audit", "fieldtype": "Long Text",
        "insert_after": "remarks", "read_only": 1, "no_copy": 1,
        "description": "Administrative timer closure; elapsed time is not verified productive runtime."}]}, update=True)


def lock_work_order(name):
    rows = frappe.db.sql("select name, docstatus, status from `tabWork Order` where name=%s for update", name, as_dict=True)
    if not rows:
        frappe.throw("Work Order does not exist.")
    return rows[0]


def has_active_execution(card):
    return ((not cint(card.get("is_paused")) and card.get("status") in RUNNING) or
            any(r.get("from_time") and not r.get("to_time") for r in card.get("time_logs") or []))


def assert_stop_allowed(work_order):
    lock_work_order(work_order)
    cards = frappe.db.sql("select name, status, is_paused, operation from `tabJob Card` where work_order=%s and docstatus<2 order by name for update", work_order, as_dict=True)
    blockers = []
    for card in cards:
        card.time_logs = frappe.db.sql("select from_time,to_time from `tabJob Card Time Log` where parent=%s and parenttype='Job Card' and parentfield='time_logs' for update", card.name, as_dict=True)
        if has_active_execution(card):
            blockers.append(f"{card.name} ({card.operation})")
    if blockers:
        frappe.throw("Cannot Stop Work Order. Pause/resolve active execution first. Blocking Job Cards: " + ", ".join(blockers))


def lock_execution(card):
    if not card.get("work_order"):
        return
    wo = lock_work_order(card.work_order)
    if cint(wo.docstatus) != 1 or wo.status in {"Stopped", "Closed", "Cancelled"}:
        frappe.throw("Cannot Start/Resume execution: parent Work Order is not active and submitted.")
    lock_machine(card)
    rows = frappe.db.sql("select name, modified from `tabJob Card` where name=%s for update", card.name, as_dict=True)
    if not rows or get_datetime(rows[0].modified) != get_datetime(card.modified):
        frappe.throw("Job Card changed after it was opened. Reload before Start/Resume.", frappe.TimestampMismatchError)
    if card.get(AUDIT_FIELD):
        frappe.throw("Administratively closed execution cannot be restarted. Preserve its closure evidence.")


def lock_machine(card):
    if card.get("workstation"):
        frappe.db.sql("select name from `tabWorkstation` where name=%s for update", card.workstation)


def is_closing(card):
    return card.flags.get("stopped_execution_token") is _TOKEN


def _canonical(value):
    if callable(getattr(value, "as_dict", None)):
        value = value.as_dict()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items() if k not in {"modified", "modified_by", "__onload", "__unsaved"}}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if callable(getattr(value, "isoformat", None)):
        return str(value)
    return value


def assert_permitted_changes(before, after, audit):
    a, b = _canonical(before), _canonical(after)
    affected = {r["name"]: r for r in audit["time_logs"]}
    if b.get("docstatus") != 0 or b.get("status") != "On Hold" or not b.get("is_paused") or flt(b.get("total_completed_qty")):
        frappe.throw("Closure must remain Draft / On Hold with zero completed quantity.")
    for rows in (a.get("time_logs", []), b.get("time_logs", [])):
        for row in rows:
            if row["name"] in affected:
                if rows is b.get("time_logs") and str(row.get("to_time")) != audit["closed_at"]:
                    frappe.throw("Unexpected closure timestamp.")
                for key in ("to_time", "time_in_mins"):
                    row.pop(key, None)
    for key in ("status", "is_paused", "total_time_in_mins", AUDIT_FIELD):
        a.pop(key, None); b.pop(key, None)
    if a != b:
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        frappe.throw("Unexpected fields changed during execution closure: " + ", ".join(changed))
    expected = sum(flt(r.time_in_mins) for r in after.time_logs)
    if abs(flt(after.total_time_in_mins) - expected) > 0.00001:
        frappe.throw("Unexpected timer total during closure.")
    if json.loads(after.get(AUDIT_FIELD)) != audit:
        frappe.throw("Closure audit was modified.")


def prevent_closure_deletion(doc, method=None):
    if doc.get(AUDIT_FIELD):
        frappe.throw("Administratively closed execution must be retained as historical evidence.")


def protect_audit(doc, method=None):
    previous = doc.get_doc_before_save()
    if not is_closing(doc) and (doc.get(AUDIT_FIELD) or (previous and previous.get(AUDIT_FIELD))):
        if not previous or doc.get(AUDIT_FIELD) != previous.get(AUDIT_FIELD):
            frappe.throw("Stopped execution closure evidence is server-controlled and immutable.")
        if _canonical(doc.time_logs) != _canonical(previous.time_logs) or doc.is_paused != previous.is_paused or doc.total_completed_qty != previous.total_completed_qty:
            frappe.throw("Administratively closed execution evidence cannot be changed.")
    if is_closing(doc):
        assert_permitted_changes(doc.flags.closure_before, doc, doc.flags.closure_audit)


class StopInvariantWorkOrderMixin:
    def update_status(self, status=None):
        if status == "Stopped":
            assert_stop_allowed(self.name)
        return super().update_status(status)

    def validate(self):
        if self.status == "Stopped":
            assert_stop_allowed(self.name)
        return super().validate()


class StoppedExecutionJobCardMixin:
    def set_employees(self):
        if not is_closing(self):
            return super().set_employees()

    def set_expected_and_actual_time(self):
        if not is_closing(self):
            return super().set_expected_and_actual_time()

    def before_validate(self):
        if self.get("work_order"):
            lock_work_order(self.work_order)
        return super().before_validate()

    def add_time_log(self, args):
        lock_execution(self)
        return super().add_time_log(args)

    def validate_time_logs(self, save=False):
        if not is_closing(self):
            return super().validate_time_logs(save=save)
        # Only identified open intervals were closed by the server. Preserve all
        # original intervals and quantities; audit marks runtime as unverified.
        self.total_time_in_mins = sum(flt(r.time_in_mins) for r in self.time_logs)

    def validate_work_order(self):
        if not is_closing(self):
            return super().validate_work_order()
        wo = lock_work_order(self.work_order)
        if wo.docstatus != 1 or wo.status != "Stopped":
            frappe.throw("Administrative closure requires a submitted Stopped Work Order.")


@frappe.whitelist()
def close_stopped_execution(job_card, reason):
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw("Production Head or Manufacturing Manager authority is required.", frappe.PermissionError)
    reason = (reason or "").strip()
    if not reason:
        frappe.throw("Abandonment / closure reason is required.")
    # A savepoint also protects callers that catch an exception without rolling
    # back their outer transaction. No commit occurs inside this action.
    frappe.db.savepoint("close_stopped_execution")
    try:
        card = frappe.get_doc("Job Card", job_card)
        card.check_permission("write")
        wo = lock_work_order(card.work_order)
        lock_machine(card)
        frappe.db.sql("select name from `tabJob Card` where name=%s for update", card.name)
        card.reload()
        if wo.docstatus != 1 or wo.status != "Stopped" or card.docstatus != 0 or not has_active_execution(card):
            frappe.throw("Requires a submitted Stopped Work Order and a Draft Job Card with running/open execution.")
        if card.get(AUDIT_FIELD) or flt(card.total_completed_qty) or any(flt(r.completed_qty) for r in card.time_logs):
            frappe.throw("This closure supports only unreconciled zero-completion execution without prior closure evidence.")
        lock_machine(card)
        before = copy.deepcopy(card.as_dict())
        closed_at = now_datetime()
        affected, anomalies = [], []
        for row in card.time_logs:
            if row.from_time and not row.to_time:
                if get_datetime(row.from_time) > closed_at:
                    frappe.throw("Open time log begins in the future; investigate before closure.")
                candidates = card.get_open_job_cards(row.employee, workstation=card.workstation) if row.employee else []
                affected.append({"name": row.name, "employee": row.employee, "from_time": str(row.from_time),
                                 "previous_time_in_mins": flt(row.time_in_mins), "closed_at": str(closed_at),
                                 "overlap_candidates": sorted({r if isinstance(r, str) else r.name for r in candidates})})
                row.to_time = closed_at
                row.time_in_mins = time_diff_in_hours(closed_at, row.from_time) * 60
        # Run standard overlap checks on a detached copy. Never normalize or save
        # those results; the original validation exception is retained verbatim.
        probe = frappe.get_doc(copy.deepcopy(card.as_dict()))
        try:
            probe.validate_time_logs()
        except Exception as exc:
            from erpnext.manufacturing.doctype.job_card.job_card import OverlapError
            if not isinstance(exc, OverlapError):
                raise
            anomalies.append({"type": type(exc).__name__, "message": str(exc)})
        audit = {"version": "stopped-execution-closure-v1", "job_card": card.name,
                 "work_order": card.work_order, "actor": frappe.session.user, "closed_at": str(closed_at),
                 "reason": reason, "previous_status": card.status, "previous_is_paused": card.is_paused,
                 "previous_total_time_in_mins": flt(card.total_time_in_mins),
                 "time_logs": affected, "anomalies": anomalies,
                 "runtime_verified": False, "runtime_interpretation": "Administrative elapsed interval; not verified productive runtime.",
                 "completed_qty": flt(card.total_completed_qty)}
        card.flags.stopped_execution_token = _TOKEN
        card.flags.closure_before = before
        card.flags.closure_audit = audit
        card.set(AUDIT_FIELD, json.dumps(audit, sort_keys=True))
        card.is_paused = 1
        card.status = "On Hold"
        card.total_time_in_mins = sum(flt(r.time_in_mins) for r in card.time_logs)
        card.save()
        assert_permitted_changes(before, card, audit)
        from calco_erp.calco_production.production_readiness import _sync_controlled_workstation_status, _derive_controlled_workstation_state
        _sync_controlled_workstation_status(card)
        saved = frappe.get_doc("Job Card", card.name)
        assert_permitted_changes(before, saved, audit)
        return {"job_card": card.name, "status": saved.status, "closed_at": str(closed_at),
                "closed_logs": [r["name"] for r in affected], "anomalies": anomalies,
                "machine": _derive_controlled_workstation_state(saved)}
    except Exception:
        frappe.db.rollback(save_point="close_stopped_execution")
        raise
