import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from calco_erp.calco_quality.rm_quality_flow_queries import (
    get_pending_rm_quality_inspection_validation_message,
    is_pending_rm_quality_inspection,
)
from calco_erp.calco_quality.purchase_receipt_qc import (
    get_purchase_receipt_row_map,
    get_quality_inspection_context,
    sync_purchase_receipt_qc_statuses,
)
from calco_erp.calco_quality.rm_warehouse_flow import handle_rm_qc_decision_submit

DECISION_DEVIATION_REQUIRED = "Deviation Required"
DECISION_RETURN_TO_SUPPLIER = "Return to Supplier"
DECISION_HOLD_FOR_REVIEW = "Hold for Review"


class RMQCDecision(Document):
    def validate(self):
        self.sync_source_details()
        self.validate_pending_quality_inspection()
        self.decision = canonical_rm_qc_decision(self.decision)

        if not self.status:
            self.status = self.decision or "Pending"

    def before_submit(self):
        self.validate_pending_quality_inspection()
        self.decision = canonical_rm_qc_decision(self.decision)

        if self.decision not in (
            DECISION_DEVIATION_REQUIRED,
            DECISION_RETURN_TO_SUPPLIER,
            DECISION_HOLD_FOR_REVIEW,
        ):
            frappe.throw(_("RM QC Decision must be Deviation Required, Return to Supplier, or Hold for Review before submit."))

        if self.quality_inspection:
            inspection = frappe.get_doc("Quality Inspection", self.quality_inspection)
            if inspection.docstatus != 1:
                frappe.throw(_("Quality Inspection must be submitted before RM QC Decision can be submitted."))
            overall_result = normalize_overall_result(
                inspection.get("custom_overall_result")
                or ("ACCEPTED" if inspection.status == "Accepted" else inspection.status)
            )
            if overall_result == "ACCEPTED":
                frappe.throw(
                    _("RM QC Decision is only required for non-accepted Incoming Quality Inspection. Create RM Release Note directly for accepted QC.")
                )

        self.validate_decision_submission_requirements()
        self.status = self.decision

    def on_submit(self):
        handle_rm_qc_decision_submit(self)
        if self.purchase_receipt:
            sync_purchase_receipt_qc_statuses(self.purchase_receipt)

        if not self.inward_validation or not has_rm_inward_validation_doctype():
            return

        if not frappe.db.exists("RM Inward Validation", self.inward_validation):
            return

        inward = frappe.get_doc("RM Inward Validation", self.inward_validation)
        inward.status = self.status
        inward.save(ignore_permissions=True)

    def on_cancel(self):
        if self.purchase_receipt:
            sync_purchase_receipt_qc_statuses(self.purchase_receipt)

    def sync_source_details(self):
        if self.inward_validation and has_rm_inward_validation_doctype():
            inward = frappe.get_doc("RM Inward Validation", self.inward_validation)
            self.purchase_receipt = inward.purchase_receipt
            self.item_code = inward.item_code
            self.batch_no = inward.batch_no
            self.sample_qty = self.sample_qty or inward.received_qty

        if not self.quality_inspection:
            if not self.inward_validation:
                frappe.throw(_("Select a pending RM Quality Inspection before creating RM QC Decision."))
            return

        inspection = frappe.get_doc("Quality Inspection", self.quality_inspection)
        if inspection.inspection_type != "Incoming" or inspection.reference_type != "Purchase Receipt":
            frappe.throw(_("RM QC Decision only supports incoming Quality Inspection linked to Purchase Receipt."))

        self.purchase_receipt = inspection.reference_name
        self.item_code = inspection.item_code
        self.batch_no = inspection.batch_no
        source_row = get_matching_purchase_receipt_row(self.purchase_receipt, self.item_code, self.batch_no)
        if source_row:
            set_optional_field(self, "custom_material_request", source_row.get("material_request") or "")
            set_optional_field(self, "custom_material_request_item", source_row.get("material_request_item") or "")
            self.custom_supplier = frappe.db.get_value("Purchase Receipt", self.purchase_receipt, "supplier") or self.get("custom_supplier")
            self.custom_item_name = source_row.get("item_name") or self.get("custom_item_name")
            self.custom_received_qty = flt(source_row.get("received_qty") or source_row.get("qty") or 0)
            self.custom_accepted_qty = flt(source_row.get("custom_accepted_qty") or 0)
            self.custom_rejected_or_hold_qty = flt(
                source_row.get("custom_rejected_qty") or source_row.get("rejected_qty") or source_row.get("qty") or 0
            )
            if not self.sample_qty:
                self.sample_qty = self.custom_rejected_or_hold_qty or self.custom_received_qty
        elif not self.sample_qty:
            self.sample_qty = get_purchase_receipt_sample_qty(
                self.purchase_receipt,
                self.item_code,
                self.batch_no,
            )

        context = get_quality_inspection_context(self.quality_inspection)
        if context.get("failed_parameters") and not self.get("custom_failed_parameters"):
            self.custom_failed_parameters = context.get("failed_parameters")
        if context.get("rejection_reason_from_qc") and not self.remarks:
            self.remarks = context.get("rejection_reason_from_qc")

    def validate_pending_quality_inspection(self):
        if not self.quality_inspection:
            return

        if not is_pending_rm_quality_inspection(
            self.quality_inspection,
            exclude_rm_qc_decision=self.name if self.name else None,
        ):
            frappe.throw(get_pending_rm_quality_inspection_validation_message())

        inspection = frappe.get_doc("Quality Inspection", self.quality_inspection)
        if inspection.item_code != self.item_code or (inspection.batch_no or "") != (self.batch_no or ""):
            frappe.throw(_("Quality Inspection must match the RM item and batch."))

    def validate_decision_submission_requirements(self):
        if not self.get("custom_decision_reason"):
            frappe.throw(_("Decision Reason / Justification is mandatory before submitting RM QC Decision."))

        if self.decision == DECISION_DEVIATION_REQUIRED and not self.get("custom_deviation_attachment"):
            frappe.throw(_("Deviation Attachment is mandatory when RM QC Decision is Deviation Required."))


@frappe.whitelist()
def create_rm_deviation_from_decision(name):
    decision = frappe.get_doc("RM QC Decision", name)
    if decision.docstatus != 1:
        frappe.throw(_("Submit RM QC Decision before creating RM Deviation Approval."))
    if canonical_rm_qc_decision(decision.decision) != DECISION_DEVIATION_REQUIRED:
        frappe.throw(_("RM Deviation Approval can only be created when decision is Deviation Required."))

    from calco_erp.calco_quality.doctype.rm_deviation_approval.rm_deviation_approval import (
        get_existing_deviation_for_decision,
    )

    existing = get_existing_deviation_for_decision(decision.name)
    if existing:
        return existing

    source_row = get_matching_purchase_receipt_row(decision.purchase_receipt, decision.item_code, decision.batch_no)
    if not source_row:
        frappe.throw(_("The linked Purchase Receipt row could not be found for RM QC Decision {0}.").format(decision.name))

    approval = frappe.get_doc(
        {
            "doctype": "RM Deviation Approval",
            "purchase_receipt": decision.purchase_receipt,
            "purchase_receipt_item": source_row.get("name"),
            "purchase_order": source_row.get("purchase_order"),
            "custom_material_request": source_row.get("material_request") or "",
            "custom_material_request_item": source_row.get("material_request_item") or "",
            "supplier": decision.get("custom_supplier") or frappe.db.get_value("Purchase Receipt", decision.purchase_receipt, "supplier"),
            "item_code": decision.item_code,
            "item_name": decision.get("custom_item_name") or source_row.get("item_name"),
            "batch_no": decision.batch_no,
            "warehouse": source_row.get("warehouse"),
            "quality_inspection": decision.quality_inspection,
            "rm_qc_decision": decision.name,
            "qc_status": source_row.get("custom_qc_status"),
            "received_qty": flt(decision.get("custom_received_qty") or source_row.get("received_qty") or source_row.get("qty") or 0),
            "accepted_qty": flt(decision.get("custom_accepted_qty") or 0),
            "rejected_qty": flt(decision.get("custom_rejected_or_hold_qty") or source_row.get("custom_rejected_qty") or source_row.get("qty") or 0),
            "approved_qty": flt(decision.get("custom_rejected_or_hold_qty") or source_row.get("custom_rejected_qty") or source_row.get("qty") or 0),
            "rate": flt(source_row.get("rate") or 0),
            "amount": flt(source_row.get("amount") or 0),
            "rejection_reason_from_qc": decision.remarks or "",
            "failed_parameters": decision.get("custom_failed_parameters") or "",
            "deviation_reason": decision.get("custom_decision_reason") or "",
            "justification_for_acceptance": decision.get("custom_decision_reason") or "",
            "deviation_attachment": decision.get("custom_deviation_attachment") or "",
            "approval_status": "Draft",
        }
    )
    approval.insert(ignore_permissions=True)
    return approval.name


@frappe.whitelist()
def create_rm_qc_decision_from_inspection(name):
    inspection = frappe.get_doc("Quality Inspection", name)
    if inspection.docstatus != 1:
        frappe.throw(_("Submit Incoming Quality Inspection before creating RM QC Decision."))
    if inspection.inspection_type != "Incoming" or inspection.reference_type != "Purchase Receipt":
        frappe.throw(_("RM QC Decision can only be created from incoming Quality Inspection linked to Purchase Receipt."))

    overall_result = normalize_overall_result(
        inspection.get("custom_overall_result") or ("ACCEPTED" if inspection.status == "Accepted" else inspection.status)
    )
    if overall_result == "ACCEPTED":
        frappe.throw(_("RM QC Decision is required only for non-accepted Incoming Quality Inspection."))

    existing = get_existing_active_decision_for_quality_inspection(inspection.name)
    if existing:
        return existing

    doc = frappe.get_doc(
        {
            "doctype": "RM QC Decision",
            "quality_inspection": inspection.name,
            "purchase_receipt": inspection.reference_name,
            "item_code": inspection.item_code,
            "batch_no": inspection.batch_no or "",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def has_rm_inward_validation_doctype():
    return bool(frappe.db.exists("DocType", "RM Inward Validation"))


def get_purchase_receipt_sample_qty(purchase_receipt, item_code, batch_no):
    if not purchase_receipt or not item_code:
        return None

    filters = {
        "parent": purchase_receipt,
        "parenttype": "Purchase Receipt",
        "item_code": item_code,
    }
    if batch_no:
        filters["batch_no"] = batch_no

    row = frappe.get_all(
        "Purchase Receipt Item",
        filters=filters,
        fields=["qty", "received_qty"],
        limit_page_length=1,
        order_by="idx asc",
    )

    if not row and batch_no:
        row = frappe.get_all(
            "Purchase Receipt Item",
            filters={
                "parent": purchase_receipt,
                "parenttype": "Purchase Receipt",
                "item_code": item_code,
            },
            fields=["qty", "received_qty"],
            limit_page_length=1,
            order_by="idx asc",
        )

    if not row:
        return None

    return row[0].get("received_qty") or row[0].get("qty")


def normalize_overall_result(value: str | None) -> str:
    mapping = {
        "PASS": "ACCEPTED",
        "FAIL": "REJECTED",
        "PENDING MANUAL REVIEW": "REVIEW REQUIRED",
        "REVIEW REQUIRED": "REVIEW REQUIRED",
        "HOLD": "HOLD",
    }
    return mapping.get((value or "").strip().upper(), (value or "").strip().upper())


def canonical_rm_qc_decision(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"deviation required", "deviation", "qc deviation", "deviation approval"}:
        return DECISION_DEVIATION_REQUIRED
    if normalized in {"return to supplier", "return", "purchase return"}:
        return DECISION_RETURN_TO_SUPPLIER
    if normalized in {"hold for review", "hold", "review required"}:
        return DECISION_HOLD_FOR_REVIEW
    return (value or "").strip()


def is_deviation_required_decision(value: str | None) -> bool:
    return canonical_rm_qc_decision(value) == DECISION_DEVIATION_REQUIRED


def set_optional_field(doc, fieldname, value):
    if doc.meta.has_field(fieldname):
        doc.set(fieldname, value)


def get_matching_purchase_receipt_row(purchase_receipt, item_code, batch_no):
    rows = get_purchase_receipt_row_map(purchase_receipt).values()
    for row in rows:
        if row.get("item_code") != item_code:
            continue
        if (row.get("batch_no") or "") != (batch_no or ""):
            continue
        return row
    for row in rows:
        if row.get("item_code") == item_code:
            return row
    return None


def get_existing_active_decision_for_quality_inspection(quality_inspection):
    rows = frappe.get_all(
        "RM QC Decision",
        filters={"quality_inspection": quality_inspection, "docstatus": ("<", 2)},
        fields=["name"],
        order_by="modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0].name if rows else ""
