"""Repair the installed MPS dangling background hook, without a release path.

ERPNext 16.29 queues make_mrp although neither that method nor MRP Log exists.
The installed MRP is an on-demand Script Report, so no persistent background
artifact is needed. Preserve native submission validation and permissions.
Delegate automatically if upstream supplies the missing method later.
"""
import frappe
from frappe import _
from erpnext.manufacturing.doctype.master_production_schedule.master_production_schedule import MasterProductionSchedule
from calco_erp.planning_upgrade.mrp import method_fingerprint

# Filled from the reviewed installed implementation, not an ERPNext core patch.
REVIEWED_ENQUEUE = "db1c50242a033841619e05eeb5757fb3a093bb416d55051bb0d4dc4cb254ddfc"

class MPSSubmissionMixin:
    def enqueue_mrp_creation(self):
        if callable(getattr(super(), "make_mrp", None)):
            return super().enqueue_mrp_creation()
        if method_fingerprint(MasterProductionSchedule.enqueue_mrp_creation) != REVIEWED_ENQUEUE:
            frappe.throw(_("MPS upstream submission changed; compatibility review required."))
        frappe.msgprint(_("Schedule submitted. Open Material Requirements Planning Report to calculate requirements. Production release remains in Production Planning Center."), alert=True)
