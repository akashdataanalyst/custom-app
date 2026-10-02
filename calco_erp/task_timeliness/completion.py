"""Observe existing closure authorities; never close a ToDo here.

First completion is immutable. Reopened evidence is retained but unscored pending
review. A context variable binds native ToDo.save to the exact source lifecycle.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import frappe
from frappe.utils import now_datetime, get_datetime

_CONTEXT = ContextVar("calco_assignment_completion", default=None)
FIELDS = ("custom_calco_completed_on", "custom_calco_completed_by",
          "custom_calco_completion_authority", "custom_calco_completion_reopened")

@contextmanager
def observing(authority, doctype, name, cancelled=False):
    token = _CONTEXT.set((authority, doctype, name, bool(cancelled)))
    try:
        yield
    finally:
        _CONTEXT.reset(token)

def governed_kind(doc):
    # Exact service marker, not a loose prefix match or arbitrary description.
    from calco_erp.planning_upgrade.purchase_assignments import STAGES, marker
    dt, name = doc.get("reference_type"), doc.get("reference_name")
    if dt in STAGES and name and marker(STAGES[dt], dt, name) in (doc.get("description") or ""):
        return "Purchase Service"
    if dt == "Task" and name:
        return "Native Task"
    return "Manual / Other"

def _same(field, left, right):
    if field == FIELDS[0]:
        return (get_datetime(left) if left else None) == (get_datetime(right) if right else None)
    if field == FIELDS[3]:
        return int(left or 0) == int(right or 0)
    return (left or "") == (right or "")

def protect_and_stamp(doc, method=None):
    # Targeted schema must be installed before activating these hooks.
    if not doc.meta.has_field(FIELDS[0]):
        return
    old = None
    if not doc.is_new():
        old = frappe.db.get_value("ToDo", doc.name, ["status", "date", "allocated_to", "reference_type", "reference_name", *FIELDS], as_dict=True, for_update=True)
    for field in FIELDS:
        if not _same(field, doc.get(field), old.get(field) if old else None):
            frappe.throw("Completion evidence is server-owned and cannot be edited or copied.")
    if not old:
        return  # No fabricated completion on insert/import of historical closed rows.
    if old.get(FIELDS[0]) and ((old.status != "Open" and doc.status == "Open") or any(
        str(doc.get(k) or "") != str(old.get(k) or "") for k in ("date", "allocated_to", "reference_type", "reference_name"))):
        doc.set(FIELDS[3], 1)
    ctx = _CONTEXT.get()
    if old.status != "Open" or doc.status != "Closed" or old.get(FIELDS[0]) or not ctx:
        return
    authority, dt, name, cancelled = ctx
    if (doc.reference_type, doc.reference_name) != (dt, name) or governed_kind(doc) != authority:
        return
    doc.set(FIELDS[0], now_datetime())
    doc.set(FIELDS[1], frappe.session.user)
    doc.set(FIELDS[2], authority + (" / Cancelled source" if cancelled else ""))

class TaskCompletionMixin:
    # ERPNext closes linked ToDos in BOTH methods. validate_status runs before
    # Task's new status is persisted, so a ToDo hook querying Task alone is unsafe.
    def validate_status(self):
        with observing("Native Task", "Task", self.name) if self.status == "Completed" else observing("None", "Task", self.name):
            return super().validate_status()

    def unassign_todo(self):
        with observing("Native Task", "Task", self.name) if self.status == "Completed" else observing("None", "Task", self.name):
            return super().unassign_todo()
