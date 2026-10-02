from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from calco_erp.calco_quality.purchase_receipt_qc import (
    ACCEPTED_UNDER_DEVIATION_STATUS,
    PR_ITEM_ACCEPTED_QTY_FIELD,
    PR_ITEM_DEVIATION_LINK_FIELD,
    PR_ITEM_QC_STATUS_FIELD,
    PR_ITEM_QI_LINK_FIELD,
    PR_ITEM_REJECTED_QTY_FIELD,
    REJECTED_STATUS,
    enforce_deviation_request_roles,
    enforce_deviation_submit_roles,
    get_purchase_receipt_row_map,
    sync_purchase_receipt_qc_statuses,
)
from calco_erp.calco_quality.doctype.rm_qc_decision.rm_qc_decision import is_deviation_required_decision


class RMDeviationApproval(Document):
    def validate(self):
        self.sync_source_details()
        self.validate_source_alignment()
        self.validate_duplicate_active_request()
        self.validate_quantities()
        self.validate_approval_transition()

        if not self.approval_status:
            self.approval_status = "Draft"

    def before_submit(self):
        enforce_deviation_request_roles()
        self.sync_source_details()
        self.validate_source_alignment()
        self.validate_duplicate_active_request()
        self.validate_required_submission_fields()
        self.approval_status = "Pending Operations Approval"

    def on_submit(self):
        sync_purchase_receipt_qc_statuses(self.purchase_receipt)

    def on_cancel(self):
        sync_purchase_receipt_qc_statuses(self.purchase_receipt)

    def sync_source_details(self):
        source_row = self.get_source_row()
        pr_doc = frappe.get_doc("Purchase Receipt", self.purchase_receipt)
        self.purchase_order = source_row.get("purchase_order") or self.purchase_order
        if self.meta.has_field("custom_material_request"):
            self.custom_material_request = source_row.get("material_request") or self.get("custom_material_request")
        if self.meta.has_field("custom_material_request_item"):
            self.custom_material_request_item = source_row.get("material_request_item") or self.get("custom_material_request_item")
        self.supplier = pr_doc.get("supplier") or self.supplier
        self.item_code = source_row.get("item_code")
        self.item_name = source_row.get("item_name")
        self.batch_no = source_row.get("batch_no")
        self.warehouse = source_row.get("warehouse")
        self.qc_status = source_row.get(PR_ITEM_QC_STATUS_FIELD) or self.qc_status
        self.received_qty = flt(source_row.get("received_qty") or source_row.get("qty") or 0)
        self.accepted_qty = flt(source_row.get(PR_ITEM_ACCEPTED_QTY_FIELD) or 0)
        self.rejected_qty = flt(source_row.get(PR_ITEM_REJECTED_QTY_FIELD) or source_row.get("rejected_qty") or 0)
        self.rate = flt(source_row.get("rate") or 0)
        self.amount = flt(source_row.get("amount") or (self.rate * self.received_qty))

        linked_qi = source_row.get("quality_inspection") or source_row.get(PR_ITEM_QI_LINK_FIELD) or ""
        if linked_qi and not self.quality_inspection:
            self.quality_inspection = linked_qi

        linked_deviation = source_row.get(PR_ITEM_DEVIATION_LINK_FIELD) or ""
        if linked_deviation and linked_deviation != self.name:
            existing_status = frappe.db.get_value("RM Deviation Approval", linked_deviation, "approval_status")
            if existing_status == "Approved":
                self.approval_status = self.approval_status or "Approved"

        if not self.operations_head:
            self.operations_head = self.get_default_operations_head()

        if self.rm_qc_decision and frappe.db.exists("RM QC Decision", self.rm_qc_decision):
            decision = frappe.get_doc("RM QC Decision", self.rm_qc_decision)
            if decision.quality_inspection and not self.quality_inspection:
                self.quality_inspection = decision.quality_inspection
            if decision.get("custom_deviation_attachment") and not self.deviation_attachment:
                self.deviation_attachment = decision.get("custom_deviation_attachment")
            if decision.get("custom_decision_reason") and not self.deviation_reason:
                self.deviation_reason = decision.get("custom_decision_reason")
            if decision.get("custom_decision_reason") and not self.justification_for_acceptance:
                self.justification_for_acceptance = decision.get("custom_decision_reason")
            if decision.remarks and not self.rejection_reason_from_qc:
                self.rejection_reason_from_qc = decision.remarks
            if decision.get("custom_failed_parameters") and not self.failed_parameters:
                self.failed_parameters = decision.get("custom_failed_parameters")

        self.sync_quality_inspection_context()

    def sync_quality_inspection_context(self):
        if not self.quality_inspection or not frappe.db.exists("Quality Inspection", self.quality_inspection):
            return

        inspection = frappe.get_doc("Quality Inspection", self.quality_inspection)
        if not self.rejection_reason_from_qc:
            for fieldname in (
                "remarks",
                "report",
                "custom_rejection_reason",
                "custom_rejection_remarks",
                "custom_remarks",
            ):
                value = (inspection.get(fieldname) or "").strip()
                if value:
                    self.rejection_reason_from_qc = value
                    break

        failed_lines = []
        for row in inspection.get("readings") or []:
            status = (row.get("status") or "").strip().lower()
            if status not in ("rejected", "fail", "failed"):
                continue
            label = row.get("specification") or row.get("parameter") or row.get("parameter_group") or _("Parameter")
            reading = row.get("reading_value") or row.get("numeric") or row.get("value") or ""
            failed_lines.append(f"{label}: {reading}".strip(": "))
        if failed_lines and not self.failed_parameters:
            self.failed_parameters = "\n".join(failed_lines)

    def get_source_row(self):
        if not self.purchase_receipt or not self.purchase_receipt_item:
            frappe.throw(_("Purchase Receipt and rejected item reference are required for deviation approval."))

        source_rows = get_purchase_receipt_row_map(self.purchase_receipt)
        source_row = source_rows.get(self.purchase_receipt_item)
        if not source_row:
            frappe.throw(_("The linked Purchase Receipt row could not be found."))
        return source_row

    def validate_source_alignment(self):
        source_row = self.get_source_row()
        row_status = (source_row.get(PR_ITEM_QC_STATUS_FIELD) or "").strip()
        if self.docstatus == 0 and row_status not in (REJECTED_STATUS, ACCEPTED_UNDER_DEVIATION_STATUS):
            frappe.throw(_("Deviation approval is only allowed for rejected QC material."))

        if not self.rm_qc_decision:
            frappe.throw(_("Linked RM QC Decision is required before deviation approval."))

        decision = frappe.get_doc("RM QC Decision", self.rm_qc_decision)
        if decision.docstatus != 1:
            frappe.throw(_("RM QC Decision must be submitted before deviation approval."))
        if not is_deviation_required_decision(decision.decision):
            frappe.throw(_("RM Deviation Approval is only allowed when RM QC Decision is Deviation Required."))

        if not self.quality_inspection:
            frappe.throw(_("Link the rejected Quality Inspection before continuing."))

        inspection = frappe.get_doc("Quality Inspection", self.quality_inspection)
        if inspection.docstatus != 1:
            frappe.throw(_("Quality Inspection must be submitted before deviation approval."))
        if inspection.reference_type != "Purchase Receipt" or inspection.reference_name != self.purchase_receipt:
            frappe.throw(_("Deviation approval must reference the same Purchase Receipt as the inspection."))
        if inspection.item_code != self.item_code or (inspection.batch_no or "") != (self.batch_no or ""):
            frappe.throw(_("Deviation approval must match the same item and batch as the linked inspection."))

    def validate_duplicate_active_request(self):
        rows = frappe.get_all(
            "RM Deviation Approval",
            filters={
                "purchase_receipt_item": self.purchase_receipt_item,
                "quality_inspection": self.quality_inspection,
                "docstatus": ("<", 2),
                "name": ("!=", self.name or ""),
            },
            fields=["name", "approval_status"],
            limit_page_length=1,
        )
        if rows:
            frappe.throw(
                _("RM Deviation Approval {0} already exists for this Purchase Receipt row and Quality Inspection.").format(
                    rows[0].name
                )
            )

    def validate_quantities(self):
        rejected_qty = flt(self.rejected_qty)
        approved_qty = flt(self.approved_qty or self.rejected_qty)
        if rejected_qty <= 0:
            frappe.throw(_("Rejected quantity must be greater than zero before deviation approval can be created."))
        if approved_qty <= 0:
            frappe.throw(_("Approved quantity must be greater than zero."))
        if approved_qty - rejected_qty > 1e-9:
            frappe.throw(_("Approved quantity cannot exceed the rejected quantity."))
        self.approved_qty = approved_qty

    def validate_required_submission_fields(self):
        missing = []
        for fieldname, label in (
            ("deviation_reason", _("Deviation Reason")),
            ("justification_for_acceptance", _("Justification for Acceptance")),
            ("risk_assessment", _("Risk Assessment")),
            ("deviation_attachment", _("Deviation Attachment")),
        ):
            if not self.get(fieldname):
                missing.append(label)
        if missing:
            frappe.throw(_("Complete the following before submitting for approval: {0}").format(", ".join(missing)))

    def validate_approval_transition(self):
        if self.docstatus != 1:
            return
        if self.approval_status not in ("Draft", "Pending Operations Approval", "Approved", "Rejected"):
            frappe.throw(_("Submitted RM Deviation Approval must be Pending Operations Approval, Approved, or Rejected."))

    def get_default_operations_head(self):
        for role in ("Operations Head", "System Manager"):
            rows = frappe.get_all(
                "Has Role",
                filters={"role": role, "parenttype": "User"},
                fields=["parent"],
                order_by="modified desc",
                limit_page_length=0,
            )
            users = [row.parent for row in rows if row.parent]
            if not users:
                continue
            active = frappe.get_all(
                "User",
                filters={"name": ("in", users), "enabled": 1, "user_type": "System User"},
                pluck="name",
                limit_page_length=1,
            )
            if active:
                return active[0]
            if users:
                return users[0]
        return ""

    def approve(self, approval_remarks: str | None = None):
        self.ensure_pending_approval()
        enforce_deviation_submit_roles()
        values = {
            "approval_status": "Approved",
            "approval_remarks": approval_remarks or self.approval_remarks or "",
            "approval_date": now_datetime(),
            "operations_head": frappe.session.user,
        }
        frappe.db.set_value(
            self.doctype,
            self.name,
            values,
            update_modified=True,
        )
        self.reload()
        sync_purchase_receipt_qc_statuses(self.purchase_receipt)
        from calco_erp.calco_quality.automatic_rm_release import (
            process_approved_deviation,
        )

        process_approved_deviation(self)

    def reject(self, approval_remarks: str | None = None):
        self.ensure_pending_approval()
        enforce_deviation_submit_roles()
        if not (approval_remarks or self.approval_remarks):
            frappe.throw(_("Approval remarks are mandatory when rejecting RM Deviation Approval."))

        values = {
            "approval_status": "Rejected",
            "approval_remarks": approval_remarks or self.approval_remarks,
            "approval_date": now_datetime(),
            "operations_head": frappe.session.user,
        }
        frappe.db.set_value(
            self.doctype,
            self.name,
            values,
            update_modified=True,
        )
        self.reload()
        capa_name = create_supplier_capa_request_for_deviation(self)
        if capa_name and hasattr(self, "supplier_capa_request"):
            frappe.db.set_value(self.doctype, self.name, "supplier_capa_request", capa_name, update_modified=False)
        sync_purchase_receipt_qc_statuses(self.purchase_receipt)

    def ensure_pending_approval(self):
        if self.docstatus != 1:
            frappe.throw(_("Submit the deviation request before approval action."))
        if self.approval_status != "Pending Operations Approval":
            frappe.throw(_("Only a Pending Operations Approval deviation request can be approved or rejected."))


@frappe.whitelist()
def approve_deviation(name, approval_remarks=None):
    doc = frappe.get_doc("RM Deviation Approval", name)
    doc.approve(approval_remarks=approval_remarks)
    return doc.name


@frappe.whitelist()
def reject_deviation(name, approval_remarks=None):
    doc = frappe.get_doc("RM Deviation Approval", name)
    doc.reject(approval_remarks=approval_remarks)
    return doc.name


@frappe.whitelist()
def request_supplier_capa(name):
    doc = frappe.get_doc("RM Deviation Approval", name)
    capa_name = create_supplier_capa_request_for_deviation(doc)
    if capa_name and hasattr(doc, "supplier_capa_request"):
        doc.db_set("supplier_capa_request", capa_name, update_modified=False)
    return capa_name


def create_supplier_capa_request_for_deviation(doc):
    if not frappe.db.exists("DocType", "Supplier CAPA Request"):
        return ""

    existing = frappe.get_all(
        "Supplier CAPA Request",
        filters={
            "purchase_receipt": doc.purchase_receipt,
            "quality_inspection": doc.quality_inspection,
            "item_code": doc.item_code,
            "batch_no": doc.batch_no or "",
            "docstatus": ("<", 2),
        },
        fields=["name"],
        limit_page_length=1,
    )
    if existing:
        return existing[0].name

    capa = frappe.get_doc(
        {
            "doctype": "Supplier CAPA Request",
            "supplier": doc.supplier,
            "purchase_receipt": doc.purchase_receipt,
            "quality_inspection": doc.quality_inspection,
            "item_code": doc.item_code,
            "batch_no": doc.batch_no,
            "rejection_reason": doc.rejection_reason_from_qc or doc.deviation_reason,
            "required_response_date": now_datetime(),
            "attachment": doc.deviation_attachment,
        }
    )
    capa.insert(ignore_permissions=True)
    return capa.name


def get_existing_deviation_for_decision(rm_qc_decision):
    if not rm_qc_decision:
        return ""
    rows = frappe.get_all(
        "RM Deviation Approval",
        filters={"rm_qc_decision": rm_qc_decision, "docstatus": ("<", 2)},
        fields=["name"],
        order_by="modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0].name if rows else ""
