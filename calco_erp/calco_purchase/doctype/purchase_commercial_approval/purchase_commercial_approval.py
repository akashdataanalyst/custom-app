from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, today


TERMINAL_STATUSES = {"Approved", "Rejected"}
REOPEN_ROLES = {"System Manager", "Commercial Admin"}
LOCKED_FIELDS = {
    "benchmark_rate",
    "quoted_rate",
    "variance_amount",
    "variance_percent",
    "reason_for_higher_price",
    "decision",
    "approved_by",
    "approval_date",
}


class PurchaseCommercialApproval(Document):
    def validate(self):
        self._previous_doc = self.get_doc_before_save() if not self.is_new() else None
        self._prevent_terminal_edits()
        self._sync_rates()
        self._sync_approval_state()
        self._capture_timeline_transition()

    def on_update(self):
        self._add_timeline_comment()

    def _sync_rates(self):
        self.variance_amount = flt(self.quoted_rate) - flt(self.benchmark_rate or 0)
        self.variance_percent = ((flt(self.quoted_rate) - flt(self.benchmark_rate)) / flt(self.benchmark_rate) * 100) if flt(self.benchmark_rate) else 0

    def _sync_approval_state(self):
        decision = (self.decision or "").strip()
        if decision == "Approved":
            if not (self.reason_for_higher_price or "").strip():
                frappe.throw(_("Reason for Higher Price is mandatory before approval."))
            self.approval_status = "Approved"
            self.approved_by = frappe.session.user
            self.approval_date = today()
            return

        if decision == "Rejected":
            self.approval_status = "Rejected"
            self.approved_by = None
            self.approval_date = None
            return

        if self.approval_status == "Reopened":
            self.decision = ""
            self.approved_by = None
            self.approval_date = None
            return

        self.approval_status = "Draft"
        self.decision = ""
        self.approved_by = None
        self.approval_date = None

    def _prevent_terminal_edits(self):
        previous = getattr(self, "_previous_doc", None)
        if not previous or previous.approval_status not in TERMINAL_STATUSES:
            return

        if getattr(self.flags, "allow_reopen_edit", False):
            return

        changed_locked_fields = [
            fieldname
            for fieldname in LOCKED_FIELDS.union({"approval_status", "reopened_by", "reopened_date", "reopen_reason"})
            if self.has_value_changed(fieldname)
        ]
        if changed_locked_fields:
            frappe.throw(
                _("Approved or Rejected Commercial Approval documents are locked. Use Reopen Approval if you need to change them.")
            )

    def _capture_timeline_transition(self):
        previous = getattr(self, "_previous_doc", None)
        previous_status = (previous.approval_status or "Draft") if previous else None
        current_status = self.approval_status or "Draft"
        if previous_status == current_status:
            self.flags.timeline_comment = None
            return

        if current_status == "Approved":
            self.flags.timeline_comment = _(
                "Commercial Approval approved by {0} on {1}."
            ).format(frappe.session.user, self.approval_date or today())
            return

        if current_status == "Rejected":
            self.flags.timeline_comment = _(
                "Commercial Approval rejected by {0} on {1}."
            ).format(frappe.session.user, today())
            return

        if current_status == "Reopened":
            self.flags.timeline_comment = _(
                "Commercial Approval reopened by {0} on {1}. Reason: {2}"
            ).format(
                self.reopened_by or frappe.session.user,
                self.reopened_date or now_datetime(),
                self.reopen_reason or _("No reason provided"),
            )
            return

        self.flags.timeline_comment = None

    def _add_timeline_comment(self):
        comment = getattr(self.flags, "timeline_comment", None)
        if not comment:
            return
        self.add_comment("Comment", comment)


@frappe.whitelist()
def reopen_purchase_commercial_approval(name: str, reopen_reason: str):
    if not name:
        frappe.throw(_("Purchase Commercial Approval is required."))
    if not (reopen_reason or "").strip():
        frappe.throw(_("Reopen Reason is mandatory."))

    if not user_can_reopen():
        frappe.throw(_("Only System Manager or Commercial Admin can reopen Commercial Approval."))

    doc = frappe.get_doc("Purchase Commercial Approval", name)
    if doc.approval_status not in TERMINAL_STATUSES:
        frappe.throw(_("Only Approved or Rejected Commercial Approval documents can be reopened."))

    doc.flags.allow_reopen_edit = True
    doc.approval_status = "Reopened"
    doc.decision = ""
    doc.approved_by = None
    doc.approval_date = None
    doc.reopened_by = frappe.session.user
    doc.reopened_date = now_datetime()
    doc.reopen_reason = reopen_reason
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "approval_status": doc.approval_status}


def user_can_reopen() -> bool:
    return bool(REOPEN_ROLES.intersection(set(frappe.get_roles())))


def ensure_purchase_commercial_approval_setup():
    if not frappe.db.exists("Role", "Commercial Admin"):
        frappe.get_doc({"doctype": "Role", "role_name": "Commercial Admin"}).insert(ignore_permissions=True)


def check_locked_edit_as_user(name: str, user: str = "Guest"):
    original_user = frappe.session.user
    try:
        frappe.set_user(user)
        doc = frappe.get_doc("Purchase Commercial Approval", name)
        doc.reason_for_higher_price = f"Edit attempt by {user}"
        doc.save(ignore_permissions=True)
        return {"result": "unexpected-pass", "user": user}
    except Exception as exc:
        return {"result": "blocked", "user": user, "message": str(exc)}
    finally:
        frappe.set_user(original_user)


def check_reopen_as_user(name: str, reopen_reason: str, user: str):
    original_user = frappe.session.user
    try:
        frappe.set_user(user)
        result = reopen_purchase_commercial_approval(name, reopen_reason)
        return {"result": "allowed", "user": user, "payload": result}
    except Exception as exc:
        return {"result": "blocked", "user": user, "message": str(exc)}
    finally:
        frappe.set_user(original_user)


def apply_decision_as_user(name: str, decision: str, user: str = "Administrator", reason: str | None = None):
    original_user = frappe.session.user
    try:
        frappe.set_user(user)
        doc = frappe.get_doc("Purchase Commercial Approval", name)
        doc.decision = decision
        if reason is not None:
            doc.reason_for_higher_price = reason
        doc.save(ignore_permissions=True)
        return {
            "result": "saved",
            "user": user,
            "name": doc.name,
            "approval_status": doc.approval_status,
            "decision": doc.decision,
        }
    finally:
        frappe.set_user(original_user)
