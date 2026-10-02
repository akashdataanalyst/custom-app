from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

SUPPLIER_ONBOARDING_MESSAGE = "Proposed suppliers must be created through the Supplier Approval Request workflow."

from calco_erp.calco_purchase.master_data_governance import (
    RM_QUALITY_TEMPLATE,
    create_or_update_planning_parameter,
    get_default_supplier_type_for_supplier,
    normalize_request_code,
)


class NewRMRequest(Document):
    TECHNICAL_REVIEW_REQUIRED_FIELDS = [
        ("Technical Review Remarks", "technical_review_remarks"),
        ("Existing Alternative Available?", "existing_alternative_available"),
        ("Recommended Material Type", "recommended_material_type"),
        ("Application Suitability", "application_suitability"),
        ("Technical Decision", "technical_decision"),
    ]

    QUALITY_REVIEW_REQUIRED_FIELDS = [
        ("MSDS Available?", "msds_available"),
        ("TDS Available?", "tds_available"),
        ("COA Available?", "coa_available"),
        ("Quality Review Remarks", "quality_review_remarks"),
        ("Quality Approval Attachment", "quality_approval_attachment"),
        ("Quality Decision", "quality_decision"),
    ]

    DOCUMENT_READINESS_REQUIRED_FIELDS = [
        ("TDS Attachment", "tds_attachment"),
        ("MSDS Attachment", "msds_attachment"),
        ("TC / COA Attachment", "tc_coa_attachment"),
        ("Sample Available?", "sample_available"),
        ("Sample Required?", "sample_required"),
        ("Document Readiness Remarks", "document_readiness_remarks"),
        ("Document Readiness Decision", "document_readiness_decision"),
    ]

    PURCHASE_REVIEW_REQUIRED_FIELDS = [
        ("Expected Lead Time", "purchase_lead_time_days"),
        ("Expected MOQ", "purchase_moq"),
        ("Purchase Pack Size", "purchase_pack_size"),
        ("Commercial Remarks", "commercial_remarks"),
        ("Purchase Decision", "purchase_decision"),
    ]

    def validate(self):
        self.rm_code = normalize_request_code(self.rm_code)
        self.rm_name = (self.rm_name or "").strip()
        self.description = (self.description or "").strip()
        self.recommended_material_type = (self.recommended_material_type or "").strip()
        self.application_suitability = (self.application_suitability or "").strip()
        self.technical_review_remarks = (self.technical_review_remarks or "").strip()
        self.document_readiness_remarks = (self.document_readiness_remarks or "").strip()
        self.required_incoming_tests = (self.required_incoming_tests or "").strip()
        self.quality_review_remarks = (self.quality_review_remarks or "").strip()
        if self.meta.has_field("commercial_feasibility_decision"):
            self.set("commercial_feasibility_decision", (self.get("commercial_feasibility_decision") or "").strip())
        if self.meta.has_field("purchase_target_rate"):
            self.set("purchase_target_rate", self.get("purchase_target_rate") or 0)
        self.purchase_lead_time_days = self.purchase_lead_time_days or 0
        self.purchase_moq = self.purchase_moq or 0
        self.purchase_pack_size = self.purchase_pack_size or 0
        self.commercial_remarks = (self.commercial_remarks or "").strip()
        if self.meta.has_field("supplier_request"):
            self.set("supplier_request", (self.get("supplier_request") or "").strip())
        if not self.status:
            self.status = "Draft"
        self.validate_preferred_supplier()
        self.validate_duplicate_rm()
        self.validate_stage_reviews()

    def on_update(self):
        if self.status == "ERP Creation" and not frappe.utils.cint(self.erp_creation_completed):
            supplier_request_name = (
                self.get("supplier_request")
                if self.meta.has_field("supplier_request")
                else frappe.db.get_value(
                    "New Supplier Request",
                    {"source_rm_request": self.name},
                    "name",
                    order_by="modified desc",
                )
            )
            if supplier_request_name and frappe.db.exists(
                "New Supplier Request",
                supplier_request_name,
            ):
                supplier_request = frappe.get_doc("New Supplier Request", supplier_request_name)
                if (
                    supplier_request.status == "ERP Creation"
                    and not frappe.utils.cint(supplier_request.erp_creation_completed)
                ):
                    supplier_request.run_erp_creation()
                return
            self.run_erp_creation()

    def validate_preferred_supplier(self):
        if not self.preferred_supplier:
            return

        if frappe.db.exists("Supplier", self.preferred_supplier):
            return

        supplier = frappe.db.get_value("Supplier", {"supplier_name": self.preferred_supplier}, "name")
        if supplier:
            self.preferred_supplier = supplier
            return

        frappe.throw(_(SUPPLIER_ONBOARDING_MESSAGE))

    def validate_duplicate_rm(self):
        existing = frappe.db.get_value("Item", self.rm_code, ["name", "item_group"], as_dict=True)
        if existing and existing.name != (self.created_item or ""):
            frappe.throw(
                _("Item {0} already exists in ERP under Item Group {1}. Use the existing Item instead of a new RM Request.").format(
                    existing.name,
                    existing.item_group,
                )
            )

    def run_erp_creation(self):
        self.validate_erp_creation_prerequisites()
        item = self.create_item()
        planning_parameter = create_or_update_planning_parameter(
            item_code=item.name,
            preferred_supplier=self.preferred_supplier,
            current_season=self.current_season,
            manual_lead_time_days=self.manual_lead_time_days,
            safety_days=self.safety_days,
            review_period_days=self.review_period_days,
            minimum_order_qty=self.minimum_order_qty,
            purchase_pack_size=self.purchase_pack_size,
        )

        self.db_set(
            {
                "created_item": item.name,
                "created_planning_parameter": planning_parameter,
                "created_supplier_matrix": "",
                "erp_creation_completed": 1,
                "creation_log": self.build_creation_log(item.name, planning_parameter, ""),
                "preferred_supplier": self.preferred_supplier,
                "status": "Completed",
            },
            update_modified=False,
        )

    def create_item(self):
        item = frappe.new_doc("Item")
        item.item_code = self.rm_code
        item.item_name = self.rm_name
        item.item_group = "Raw Material"
        item.stock_uom = self.stock_uom or "Kg"
        item.is_stock_item = 1
        item.include_item_in_manufacturing = 1
        item.valuation_method = "Moving Average"
        item.has_batch_no = 1
        item.inspection_required_before_purchase = 1
        item.quality_inspection_template = RM_QUALITY_TEMPLATE
        item.custom_enable_rm_qc = 1
        item.allow_alternative_item = 0
        item.disabled = 0
        item.description = self.description or ""
        item.insert(ignore_permissions=True)
        return item

    def build_creation_log(self, item_name: str, planning_parameter: str, matrix_name: str) -> str:
        parts = [f"Created Item: {item_name}"]
        if planning_parameter:
            parts.append(f"Created RM Planning Parameter: {planning_parameter}")
        if matrix_name:
            parts.append(f"Created Supplier Approval Matrix: {matrix_name}")
        return "\n".join(parts)

    def validate_stage_reviews(self):
        before = self.get_doc_before_save()
        previous_status = (before.status or "").strip() if before else ""
        current_status = (self.status or "").strip()

        if previous_status == "Technical Review" and current_status in {"Quality Review", "Rejected"}:
            self.require_technical_review(current_status)
        if previous_status == "Technical Review" and current_status in {"Document & Sample Readiness"}:
            self.require_technical_review("Document & Sample Readiness")
        if previous_status == "Document & Sample Readiness" and current_status in {"Quality Review", "Rejected"}:
            self.require_document_readiness(current_status)
        if previous_status == "Quality Review" and current_status in {"Purchase Review", "Rejected"}:
            self.require_quality_review(
                current_status,
                validate_inspection_parameters=current_status == "Purchase Review",
            )
        if previous_status == "Purchase Review" and current_status in {"ERP Creation", "Rejected"}:
            self.require_purchase_review(current_status)

    def validate_erp_creation_prerequisites(self):
        self.require_technical_review("Document & Sample Readiness")
        self.require_document_readiness("Quality Review")
        self.require_quality_review("Purchase Review")
        self.require_purchase_review("ERP Creation")

    def require_technical_review(self, target_status: str):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.TECHNICAL_REVIEW_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Technical Review")
        expected_decision = "Rejected" if target_status == "Rejected" else "Approved"
        if self.technical_decision != expected_decision:
            frappe.throw(_("Technical Decision must be {0} before moving to {1}.").format(expected_decision, target_status))

    def require_quality_review(self, target_status: str, *, validate_inspection_parameters: bool = False):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.QUALITY_REVIEW_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Quality Review")
        expected_decision = "Rejected" if target_status == "Rejected" else "Approved"
        if self.quality_decision != expected_decision:
            frappe.throw(_("Quality Decision must be {0} before moving to {1}.").format(expected_decision, target_status))
        if validate_inspection_parameters:
            self.validate_incoming_inspection_parameters()

    def validate_incoming_inspection_parameters(self):
        rows = self.get("incoming_inspection_parameters") if self.meta.has_field("incoming_inspection_parameters") else []
        rows = rows or []
        no_inspection_required = (
            frappe.utils.cint(self.get("no_incoming_inspection_required"))
            if self.meta.has_field("no_incoming_inspection_required")
            else 0
        )

        if no_inspection_required and rows:
            frappe.throw(
                _("No Incoming Inspection Required cannot be selected when inspection parameters are defined.")
            )
        if not rows and not no_inspection_required:
            frappe.throw(
                _("At least one incoming inspection parameter is required before approving Quality Review, unless No Incoming Inspection Required is selected.")
            )

        for row_number, row in enumerate(rows, start=1):
            parameter = (row.get("specification") or "").strip()
            if not parameter:
                frappe.throw(_("Incoming Inspection Parameter row {0}: Parameter is required.").format(row_number))

            if frappe.utils.cint(row.get("formula_based_criteria")):
                if not (row.get("acceptance_formula") or "").strip():
                    frappe.throw(
                        _("Incoming Inspection Parameter row {0}: Acceptance Formula is required for formula-based criteria.").format(
                            row_number
                        )
                    )
                continue

            if frappe.utils.cint(row.get("numeric")):
                minimum = row.get("min_value")
                maximum = row.get("max_value")
                if minimum in (None, "") or maximum in (None, ""):
                    frappe.throw(
                        _("Incoming Inspection Parameter row {0}: Minimum Value and Maximum Value are required for numeric criteria.").format(
                            row_number
                        )
                    )
                if frappe.utils.flt(minimum) > frappe.utils.flt(maximum):
                    frappe.throw(
                        _("Incoming Inspection Parameter row {0}: Minimum Value cannot exceed Maximum Value.").format(
                            row_number
                        )
                    )
                continue

            if not (row.get("value") or "").strip():
                frappe.throw(
                    _("Incoming Inspection Parameter row {0}: Acceptance Criteria is required for non-numeric criteria.").format(
                        row_number
                    )
                )

    def require_document_readiness(self, target_status: str):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.DOCUMENT_READINESS_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Document & Sample Readiness")

        if self.sample_required == "Yes":
            if not self.sample_quantity_kg or frappe.utils.flt(self.sample_quantity_kg) <= 0:
                frappe.throw(_("Document & Sample Readiness requires Sample Quantity Kg greater than 0 when Sample Required is Yes."))
            if self.sample_received_by_quality != "Yes":
                frappe.throw(_("Document & Sample Readiness requires Sample Received By Quality to be Yes when Sample Required is Yes."))
            if not self.sample_received_date:
                frappe.throw(_("Document & Sample Readiness requires Sample Received Date when Sample Required is Yes."))

        expected_decision = "Incomplete" if target_status == "Rejected" else "Complete"
        if self.document_readiness_decision != expected_decision:
            frappe.throw(
                _("Document Readiness Decision must be {0} before moving to {1}.").format(
                    expected_decision, target_status
                )
            )

    def require_purchase_review(self, target_status: str):
        required = {
            fieldname_label: self.get(fieldname)
            for fieldname_label, fieldname in self.PURCHASE_REVIEW_REQUIRED_FIELDS
            if self.meta.has_field(fieldname)
        }
        self.throw_if_missing(required, "Purchase Review")

        purchase_decision = (self.get("purchase_decision") or "").strip()
        if purchase_decision and purchase_decision not in {"Approved", "Rejected"}:
            frappe.throw(_("Purchase Decision must be Approved or Rejected before moving to {0}.").format(target_status))

        if self.meta.has_field("commercial_feasibility_decision"):
            feasibility_decision = (self.get("commercial_feasibility_decision") or "").strip()
            decision_by_feasibility = {
                "Commercially Feasible": "Approved",
                "Not Feasible": "Rejected",
            }
            expected_decision = decision_by_feasibility.get(feasibility_decision)
            if expected_decision and purchase_decision != expected_decision:
                frappe.throw(
                    _("Purchase Decision must be {0} for commercial feasibility '{1}' before moving to {2}.").format(
                        expected_decision,
                        feasibility_decision,
                        target_status,
                    )
                )

        if target_status == "Rejected" and purchase_decision != "Rejected":
            frappe.throw(_("Purchase Decision must be Rejected before moving to Rejected."))

        if target_status == "ERP Creation" and purchase_decision != "Approved":
            frappe.throw(_("Purchase Decision must be Approved before moving to ERP Creation."))

    def throw_if_missing(self, required: dict[str, object], stage_label: str):
        missing = [label for label, value in required.items() if value in (None, "", [])]
        if missing:
            frappe.throw(_("{0} cannot be approved/rejected until these fields are completed: {1}").format(stage_label, ", ".join(missing)))
