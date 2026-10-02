import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from calco_erp.calco_quality.purchase_receipt_qc import (
    get_purchase_receipt_row_map,
    sync_purchase_receipt_qc_statuses,
)
from calco_erp.calco_quality.rm_purchase_flow_setup import get_stock_setting_value
from calco_erp.calco_quality.rm_warehouse_flow import handle_rm_release_note_submit


class RMReleaseNote(Document):
    def validate(self):
        self.sync_source_details()
        self.apply_default_release_warehouse()

        if not self.status:
            self.status = "Released"

    def before_submit(self):
        self.sync_source_details()
        self.apply_default_release_warehouse()
        self.validate_release_source()
        self.validate_duplicate_submitted_release()

        if self.status != "Released":
            frappe.throw(_("RM Release Note can only be submitted with status Released."))

        if not self.release_qty or self.release_qty <= 0:
            frappe.throw(_("Release Qty must be greater than zero."))

    def on_submit(self):
        stock_entry = handle_rm_release_note_submit(self)
        if stock_entry:
            self.custom_generated_stock_entry = stock_entry
            self.db_set("custom_generated_stock_entry", stock_entry, update_modified=False)
        purchase_receipt = self.get_linked_purchase_receipt()
        if purchase_receipt:
            sync_purchase_receipt_qc_statuses(purchase_receipt)

    def on_cancel(self):
        purchase_receipt = self.get_linked_purchase_receipt()
        if purchase_receipt:
            sync_purchase_receipt_qc_statuses(purchase_receipt)

    def sync_source_details(self):
        if self.get("custom_rm_deviation_approval"):
            self.sync_from_deviation()
            return

        if self.get("custom_quality_inspection"):
            self.sync_from_quality_inspection()
            return

        if self.rm_qc_decision:
            self.sync_from_decision()
            return

        frappe.throw(_("Select an accepted Quality Inspection or an approved RM Deviation Approval before creating RM Release Note."))

    def sync_from_quality_inspection(self):
        inspection = frappe.get_doc("Quality Inspection", self.get("custom_quality_inspection"))
        self.custom_purchase_receipt = inspection.reference_name
        self.custom_supplier = frappe.db.get_value("Purchase Receipt", inspection.reference_name, "supplier") or self.get("custom_supplier")
        self.item_code = inspection.item_code
        self.batch_no = inspection.batch_no
        self.custom_purchase_receipt_item = (
            self.get("custom_purchase_receipt_item") or inspection.get("child_row_reference") or ""
        )
        source_row = get_matching_purchase_receipt_row(
            inspection.reference_name,
            inspection.item_code,
            inspection.batch_no,
            purchase_receipt_item=self.custom_purchase_receipt_item,
        )
        if source_row:
            self.custom_item_name = source_row.get("item_name") or self.get("custom_item_name")
            if not self.release_qty:
                accepted_qty = flt(source_row.get("custom_accepted_qty") or source_row.get("received_qty") or source_row.get("qty") or 0)
                self.release_qty = accepted_qty

    def sync_from_decision(self):
        decision = frappe.get_doc("RM QC Decision", self.rm_qc_decision)
        self.custom_purchase_receipt = decision.purchase_receipt
        self.custom_quality_inspection = decision.quality_inspection
        self.custom_purchase_receipt_item = (
            self.get("custom_purchase_receipt_item") or decision.get("purchase_receipt_item") or ""
        )
        self.custom_supplier = decision.get("custom_supplier") or self.get("custom_supplier")
        self.item_code = decision.item_code
        self.batch_no = decision.batch_no
        self.custom_item_name = decision.get("custom_item_name") or self.get("custom_item_name")
        if not self.release_qty:
            self.release_qty = decision.sample_qty

    def sync_from_deviation(self):
        deviation = frappe.get_doc("RM Deviation Approval", self.get("custom_rm_deviation_approval"))
        self.rm_qc_decision = deviation.rm_qc_decision or self.rm_qc_decision
        self.custom_purchase_receipt = deviation.purchase_receipt
        self.custom_quality_inspection = deviation.quality_inspection
        self.custom_purchase_receipt_item = deviation.purchase_receipt_item
        self.custom_supplier = deviation.supplier
        self.item_code = deviation.item_code
        self.batch_no = deviation.batch_no
        self.custom_item_name = deviation.item_name
        if not self.release_qty:
            self.release_qty = deviation.approved_qty

    def apply_default_release_warehouse(self):
        if self.release_warehouse:
            return

        release_warehouse = get_stock_setting_value("custom_rm_released_warehouse")
        if release_warehouse:
            self.release_warehouse = release_warehouse

    def validate_release_source(self):
        if self.get("custom_rm_deviation_approval"):
            self.validate_deviation_approval_for_release()
            return

        if self.get("custom_quality_inspection"):
            inspection = frappe.get_doc("Quality Inspection", self.get("custom_quality_inspection"))
            if inspection.docstatus != 1:
                frappe.throw(_("Quality Inspection must be submitted before RM Release Note can be submitted."))
            if normalize_overall_result(inspection.get("custom_overall_result") or inspection.status) != "ACCEPTED":
                frappe.throw(
                    _("RM QC Decision is required before RM Release Note for non-accepted Incoming Quality Inspection.")
                )
            if inspection.inspection_type != "Incoming" or inspection.reference_type != "Purchase Receipt":
                frappe.throw(_("RM Release Note requires an Incoming Quality Inspection linked to Purchase Receipt."))
            allowed_qty = self.get_allowed_release_qty_from_pr(
                inspection.reference_name,
                inspection.item_code,
                inspection.batch_no,
                purchase_receipt_item=self.get("custom_purchase_receipt_item"),
            )
            if self.release_qty - allowed_qty > 1e-9:
                frappe.throw(_("Release quantity cannot exceed the accepted quantity ({0}) from the Purchase Receipt row.").format(allowed_qty))
            return

        if self.rm_qc_decision:
            self.validate_deviation_approval_for_release()
            return

        frappe.throw(_("RM Release Note requires an accepted Incoming Quality Inspection or an approved RM Deviation Approval."))

    def validate_deviation_approval_for_release(self):
        deviation_name = (self.get("custom_rm_deviation_approval") or "").strip()
        if not deviation_name:
            frappe.throw(_("Approved RM Deviation Approval is required before releasing non-accepted RM material."))

        deviation = frappe.get_doc("RM Deviation Approval", deviation_name)
        if deviation.docstatus != 1 or deviation.approval_status != "Approved":
            frappe.throw(_("RM Deviation Approval must be submitted and approved before release under deviation."))
        if deviation.item_code != self.item_code or (deviation.batch_no or "") != (self.batch_no or ""):
            frappe.throw(_("RM Deviation Approval must match the same item and batch as the release note."))
        if self.rm_qc_decision and deviation.rm_qc_decision and deviation.rm_qc_decision != self.rm_qc_decision:
            frappe.throw(_("RM Deviation Approval must link the same RM QC Decision as the release note."))
        if self.release_qty - flt(deviation.approved_qty or 0) > 1e-9:
            frappe.throw(_("Release quantity cannot exceed the approved deviation quantity."))

    def get_allowed_release_qty_from_pr(
        self,
        purchase_receipt,
        item_code,
        batch_no,
        purchase_receipt_item=None,
    ):
        source_row = get_matching_purchase_receipt_row(
            purchase_receipt,
            item_code,
            batch_no,
            purchase_receipt_item=purchase_receipt_item,
        )
        if not source_row:
            return 0
        accepted_qty = flt(source_row.get("custom_accepted_qty") or 0)
        if accepted_qty > 0:
            return accepted_qty
        return flt(source_row.get("received_qty") or source_row.get("qty") or 0)

    def get_linked_purchase_receipt(self):
        return (
            (self.get("custom_purchase_receipt") or "").strip()
            or frappe.db.get_value("RM QC Decision", self.rm_qc_decision, "purchase_receipt")
            or ""
        )

    def validate_duplicate_submitted_release(self):
        duplicates = find_matching_rm_release_notes(
            purchase_receipt=self.get("custom_purchase_receipt"),
            quality_inspection=self.get("custom_quality_inspection"),
            item_code=self.item_code,
            batch_no=self.batch_no,
            exclude_name=self.name,
            docstatus=1,
        )
        if not duplicates:
            return

        duplicate_names = ", ".join(row.get("name") for row in duplicates if row.get("name"))
        frappe.throw(
            _(
                "RM Release Note {0} is already submitted for this Purchase Receipt, Quality Inspection, item, and batch. "
                "Open the existing release note instead of creating another one."
            ).format(duplicate_names)
        )


def get_matching_purchase_receipt_row(
    purchase_receipt,
    item_code,
    batch_no,
    purchase_receipt_item=None,
):
    rows = get_purchase_receipt_row_map(purchase_receipt).values()
    if purchase_receipt_item:
        row = get_purchase_receipt_row_map(purchase_receipt).get(purchase_receipt_item)
        if not row:
            frappe.throw(_("Purchase Receipt Item Row {0} was not found.").format(purchase_receipt_item))
        if row.get("item_code") != item_code or (row.get("batch_no") or "") != (batch_no or ""):
            frappe.throw(
                _("Purchase Receipt Item Row {0} does not match Item {1} and Batch {2}.").format(
                    purchase_receipt_item,
                    item_code,
                    batch_no or "",
                )
            )
        return row
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


def find_matching_rm_release_notes(
    purchase_receipt=None,
    quality_inspection=None,
    item_code=None,
    batch_no=None,
    exclude_name=None,
    docstatus=None,
):
    filters = {
        "custom_purchase_receipt": purchase_receipt or "",
        "custom_quality_inspection": quality_inspection or "",
        "item_code": item_code or "",
        "batch_no": batch_no or "",
    }
    if docstatus is not None:
        filters["docstatus"] = docstatus

    rows = frappe.get_all(
        "RM Release Note",
        filters=filters,
        fields=["name", "docstatus", "status", "custom_purchase_receipt", "custom_quality_inspection", "item_code", "batch_no", "release_qty", "creation", "modified"],
        order_by="creation asc",
    )
    if exclude_name:
        rows = [row for row in rows if row.get("name") != exclude_name]
    return rows


def normalize_overall_result(value):
    mapping = {
        "PASS": "ACCEPTED",
        "FAIL": "REJECTED",
        "PENDING MANUAL REVIEW": "REVIEW REQUIRED",
    }
    return mapping.get((value or "").strip().upper(), (value or "").strip().upper())
