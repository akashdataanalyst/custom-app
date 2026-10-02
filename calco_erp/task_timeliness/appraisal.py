"""Opt-in Task Timeliness KRA bridge. Never adopts existing appraisals automatically.

Other KRAs retain native Goal Progress. The native per_weightage/goal_completion/
goal_score fields remain authoritative; only missing calculation evidence is new.
"""
from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import json
import frappe
from frappe.utils import getdate, get_datetime, now_datetime, flt
from calco_erp.task_timeliness import report as engine
from calco_erp.task_timeliness import access
from calco_erp.task_timeliness.completion import FIELDS, governed_kind

KRA = "Task Timeliness"
TABLE = "custom_task_timeliness_evidence"
POLICY = "task-timeliness-appraisal-v1-replace-work-not-done-on-time"
ADMIN_ROLES = {"HR Manager", "System Manager"}
_INTERNAL = ContextVar("calco_appraisal_calculation", default=False)
COUNTS = ("total_due", "on_time", "late", "overdue", "pending", "unknown_completion",
          "manual_other", "missing_due", "cancelled", "review_required")
EVIDENCE_FIELDS = ("period_start", "period_end", "period_source", "calculated_on", "calculated_by",
    "governed_user", "policy_version", "source_json", "fingerprint", "finalized_on", "finalized_by",
    "native_row_fingerprint") + COUNTS

@contextmanager
def internal():
    token = _INTERNAL.set(True)
    try: yield
    finally: _INTERNAL.reset(token)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

def admin():
    return frappe.session.user == "Administrator" or bool(ADMIN_ROLES.intersection(frappe.get_roles()))

def require_admin():
    if not admin(): frappe.throw("Task Timeliness administration requires HR Manager or System Manager.", frappe.PermissionError)
    access.require_desk_user()

def task_row(doc):
    rows = [r for r in doc.get("appraisal_kra", []) if r.kra == KRA]
    if len(rows) > 1: frappe.throw("Only one Task Timeliness KRA is permitted.")
    return rows[0] if rows else None

def managed(doc):
    return bool(task_row(doc) or doc.get(TABLE))

def evidence(doc):
    rows = doc.get(TABLE) or []
    if len(rows) > 1: frappe.throw("Only one current Task Timeliness evidence record is permitted.")
    return rows[0] if rows else None

def period(doc):
    start, end = doc.get("start_date"), doc.get("end_date")
    if bool(start) != bool(end): frappe.throw("Complete both individual Appraisal dates; a partial period is not authoritative.")
    source = "Appraisal"
    if not start:
        if not doc.appraisal_cycle: frappe.throw("Appraisal or linked Cycle period is required.")
        cycle = frappe.get_doc("Appraisal Cycle", doc.appraisal_cycle)
        start, end, source = cycle.start_date, cycle.end_date, "Appraisal Cycle"
    if not start or not end or getdate(start) > getdate(end): frappe.throw("A valid explicit appraisal period is required; current-quarter defaults are not used.")
    return str(getdate(start)), str(getdate(end)), source

def identity(doc):
    emp = frappe.get_doc("Employee", doc.employee)
    user = emp.user_id
    if emp.status != "Active" or not user: frappe.throw("Task Timeliness unavailable: an Active Employee with a governed User is required.")
    u = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
    if not u or not u.enabled or u.user_type != "System User" or user == "Guest":
        frappe.throw("Task Timeliness unavailable: an enabled System User is required; Website Users are not eligible.")
    matches = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, pluck="name", limit_page_length=0)
    if matches != [doc.employee]: frappe.throw("Task Timeliness unavailable: Employee/User mapping is ambiguous.")
    return user

def overlap(doc):
    goals = frappe.get_all("Goal", filters={"employee": doc.employee, "appraisal_cycle": doc.appraisal_cycle,
        "status": ["!=", "Archived"]}, fields=["name", "goal_name", "kra"], limit_page_length=0)
    blocked = [g.name for g in goals if g.goal_name == "Work Not Done On Time" or g.kra == KRA]
    if blocked: frappe.throw("Scoring overlap requires separately approved adoption. Preserve and retire the prior Goal from scoring before refreshing Task Timeliness: " + ", ".join(blocked))

def calculate(doc):
    user = identity(doc)
    start, end, source = period(doc)
    overlap(doc)
    rows, now, _, _ = access.population(user, {"from_date": start, "to_date": end})
    # Retain minimal exact source values and resolved authority, not free-text descriptions.
    records = []
    for r in rows:
        record = {k: r.get(k) for k in ("name", "allocated_to", "reference_type", "reference_name", "status", "date") + FIELDS}
        record["resolved_kind"] = r.get(FIELDS[2]) or governed_kind(r)
        records.append(record)
    payload = dict(employee=doc.employee, user=user, cycle=doc.appraisal_cycle, start=start, end=end,
        period_source=source, policy=POLICY, records=records)
    values = replay(payload, now)
    if values["on_time_percent"] is None:
        frappe.throw("Task Timeliness unavailable: no scored assignments in this period. Pending/unknown work is not assigned a zero or perfect score.")
    return payload, values, now

def replay(payload, calculated_on):
    rows = []
    for source in payload["records"]:
        row = dict(source)
        row[FIELDS[2]] = row.pop("resolved_kind")
        rows.append(row)
    result = engine.aggregate(rows, {payload["user"]: dict(enabled=1, user_type="System User")},
        [dict(name=payload["employee"], user_id=payload["user"], employee_name=payload["employee"])],
        get_datetime(calculated_on), include_administrator=True)
    if result: return result[0]
    return dict(**{k: 0 for k in COUNTS}, on_time_percent=None)

def verify_evidence(doc):
    ev = evidence(doc)
    if not ev: return None
    payload = json.loads(ev.source_json)
    if payload["employee"] != doc.employee or payload["cycle"] != doc.appraisal_cycle or ev.policy_version != POLICY or payload.get("policy") != POLICY:
        frappe.throw("Task Timeliness evidence lineage is invalid.")
    if (payload["start"], payload["end"], payload["period_source"]) != (str(getdate(ev.period_start)), str(getdate(ev.period_end)), ev.period_source):
        frappe.throw("Task Timeliness evidence period is invalid.")
    values = replay(payload, ev.calculated_on)
    canonical = dict(payload=payload, counts={k: values[k] for k in COUNTS}, rate=values["on_time_percent"])
    if digest(canonical) != ev.fingerprint or any(int(ev.get(k) or 0) != values[k] for k in COUNTS) or ev.governed_user != payload["user"]:
        frappe.throw("Task Timeliness evidence fingerprint is invalid.")
    return values

def ev_values(doc):
    return [{k: str(r.get(k)) if r.get(k) is not None else None for k in EVIDENCE_FIELDS + ("name", "parent", "parentfield", "parenttype")} for r in (doc.get(TABLE) or [])]

def native_fingerprint(row):
    return digest(dict(kra=row.kra, weight=flt(row.per_weightage), completion=flt(row.goal_completion), contribution=flt(row.goal_score)))

def protect(doc):
    old = doc.get_doc_before_save()
    if not old and not doc.is_new(): old = frappe.get_doc("Appraisal", doc.name)
    if not managed(doc) and not (old and managed(old)): return
    if doc.rate_goals_manually: frappe.throw("Task Timeliness integration requires native Automated Based on Goal Progress.")
    row, previous = task_row(doc), task_row(old) if old else None
    if not _INTERNAL.get():
        if ev_values(doc) != (ev_values(old) if old else []): frappe.throw("Task Timeliness evidence is system-owned; use Refresh Task Timeliness.", frappe.PermissionError)
        if (row is None) != (previous is None) or (row and previous and flt(row.per_weightage) != flt(previous.per_weightage)):
            require_admin()
        if row and ((previous and any(flt(row.get(k)) != flt(previous.get(k)) for k in ("goal_completion", "goal_score"))) or (not previous and (flt(row.goal_completion) or flt(row.goal_score)))):
            frappe.throw("Task Timeliness scores cannot be entered manually.", frappe.PermissionError)
        if old and evidence(old) and any(str(doc.get(k) or "") != str(old.get(k) or "") for k in ("employee", "appraisal_cycle", "start_date", "end_date")):
            frappe.throw("Appraisal identity/period is bound to Task Timeliness evidence; do not change it after calculation.")
    if old and old.docstatus == 1:
        if not row or not previous or native_fingerprint(row) != native_fingerprint(previous) or ev_values(doc) != ev_values(old):
            frappe.throw("Submitted Task Timeliness evidence and KRA contribution are immutable.")
    if evidence(doc) and not row: frappe.throw("Task Timeliness evidence cannot be detached from its KRA.")
    if row and not 0 <= flt(row.per_weightage) <= 100: frappe.throw("Task Timeliness weight must be between 0 and 100.")
    verify_evidence(doc)

def protect_kra_child(doc, method=None):
    # client.save / REST can save child documents directly using parent permission.
    # Native parent Save uses db_update for children, so its governed path is intact.
    previous = frappe.db.get_value("Appraisal KRA", doc.name, "kra") if doc.name else None
    if doc.kra == KRA or previous == KRA:
        frappe.throw("Edit Task Timeliness only through its governed Appraisal; standalone KRA writes are not permitted.", frappe.PermissionError)

class AppraisalTimelinessMixin:
    def validate(self):
        protect(self)
        super().validate()

    def set_goal_score(self, update=False):
        row = task_row(self)
        if row is None: return super().set_goal_score(update=update)
        values = verify_evidence(self)
        ev = evidence(self)
        # Native Goal saves can recalculate submitted Appraisals without validate().
        # Reject reintroduced legacy scoring as well as initial adoption overlap.
        if ev: overlap(self)
        frozen = ev.native_row_fingerprint if ev else None
        # Preserve HRMS Goal calculation for every other KRA. Defer its write until
        # the one system-owned KRA has been mapped into native percentage fields.
        result = super().set_goal_score(update=False)
        row.goal_completion = flt(values["on_time_percent"], row.precision("goal_completion")) if values else 0
        row.goal_score = flt(row.goal_completion * flt(row.per_weightage) / 100, row.precision("goal_score"))
        if self.docstatus in (1, 2) and frozen and native_fingerprint(row) != frozen:
            frappe.throw("Submitted Task Timeliness contribution cannot be recalculated differently.")
        self.calculate_total_score()
        if update:
            for kra in self.appraisal_kra: kra.db_update()
            self.calculate_final_score()
            self.db_update()
        return result

    def before_submit(self):
        parent = getattr(super(), "before_submit", None)
        if parent: parent()
        if not managed(self): return
        row, ev = task_row(self), evidence(self)
        if not row or not ev: frappe.throw("Refresh Task Timeliness before submitting this Appraisal.")
        payload, values, _ = calculate(self)
        expected = digest(dict(payload=payload, counts={k: values[k] for k in COUNTS}, rate=values["on_time_percent"]))
        if expected != ev.fingerprint: frappe.throw("Task Timeliness evidence has changed; refresh and review before submission.")
        # Native 100% check is deliberately retained and is stricter: save also blocks.
        self.validate_total_weightage("appraisal_kra", "KRAs")
        ev.finalized_on, ev.finalized_by = now_datetime(), frappe.session.user
        ev.native_row_fingerprint = native_fingerprint(row)

    def before_update_after_submit(self):
        parent = getattr(super(), "before_update_after_submit", None)
        if parent: parent()
        protect(self)

    def on_trash(self):
        if evidence(self): frappe.throw("Appraisal has retained Task Timeliness audit evidence and cannot be deleted.")
        parent = getattr(super(), "on_trash", None)
        if parent: parent()

@frappe.whitelist()
def refresh(appraisal):
    require_admin()
    frappe.db.sql("SELECT name FROM tabAppraisal WHERE name=%s FOR UPDATE", (appraisal,))
    doc = frappe.get_doc("Appraisal", appraisal)
    doc.check_permission("write")
    if doc.docstatus != 0: frappe.throw("Only a Draft Appraisal can refresh Task Timeliness.")
    row = task_row(doc)
    if not row: frappe.throw("Add the existing Task Timeliness KRA and explicitly rebalance weights first.")
    if doc.rate_goals_manually: frappe.throw("Automated Based on Goal Progress is required.")
    payload, values, now = calculate(doc)
    fp = digest(dict(payload=payload, counts={k: values[k] for k in COUNTS}, rate=values["on_time_percent"]))
    if evidence(doc) and evidence(doc).fingerprint == fp:
        verify_evidence(doc)
        return dict(appraisal=doc.name, changed=False, fingerprint=fp)
    with internal():
        doc.set(TABLE, [])
        doc.append(TABLE, dict(period_start=payload["start"], period_end=payload["end"], period_source=payload["period_source"],
            calculated_on=now, calculated_by=frappe.session.user, governed_user=payload["user"], policy_version=POLICY,
            source_json=json.dumps(payload, default=str, sort_keys=True), fingerprint=fp, **{k: values[k] for k in COUNTS}))
        doc.save()
    return dict(appraisal=doc.name, changed=True, fingerprint=fp)
