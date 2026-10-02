from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

SUPPLIER_ONBOARDING_MESSAGE = "Proposed suppliers must be created through the Supplier Approval Request workflow."

from calco_erp.calco_purchase.master_data_governance import (
    RESOLUTION_MANUAL_REVIEW,
    SUPPLIER_SOURCE_EXISTING,
    SUPPLIER_SOURCE_PROPOSED,
    create_or_update_planning_parameter,
    create_or_update_supplier_matrix_row,
    get_supplier_duplicate_candidates,
    get_supplier_request_resolution_context,
    get_supplier_source,
    normalize_identity_value,
    normalize_request_code,
)
from calco_erp.calco_purchase.supplier_document_governance import (
    initialize_governance_version,
    refresh_document_checklist,
    validate_complete_document_package,
    validate_purchase_document_gate,
    validate_quality_document_gate,
    validate_supplier_rm_for_item,
)


class NewSupplierRequest(Document):
    CLOSED_DUPLICATE_CHECK_STATUSES = {"Completed", "Rejected", "Cancelled"}

    QUALITY_REVIEW_REQUIRED_FIELDS = [
        ("Certificates Checked", "certificates_checked"),
        ("Quality Audit Required?", "quality_audit_required"),
        ("Quality Remarks", "supplier_quality_remarks"),
        ("Quality Decision", "supplier_quality_decision"),
    ]

    PURCHASE_REVIEW_REQUIRED_FIELDS = [
        ("Lead Time", "supplier_purchase_lead_time"),
        ("MOQ", "supplier_purchase_moq"),
        ("Payment Terms", "supplier_purchase_payment_terms"),
        ("Commercial Terms", "commercial_terms"),
        ("Purchase Remarks", "supplier_purchase_remarks"),
        ("Purchase Decision", "supplier_purchase_decision"),
    ]

    MANAGEMENT_REVIEW_REQUIRED_FIELDS = [
        ("Strategic Supplier?", "strategic_supplier"),
        ("Risk Remarks", "risk_remarks"),
        ("Final Approval Decision", "final_approval_decision"),
    ]

    def before_validate(self):
        self.flags.pop("duplicate_cancellation_rows", None)
        from calco_erp.calco_purchase.supplier_request_duplicate import validate_cancellation
        if validate_cancellation(self):
            return
        initialize_governance_version(self)

    def validate(self):
        if self.flags.get("duplicate_cancellation_rows"):
            return
        self.normalize_text_fields(
            "supplier_name",
            "proposed_supplier_name",
            "source_rm_request",
            "source_rm_code",
            "source_rm_name",
            "source_category",
            "source_stock_uom",
            "commercial_remarks",
            "certificates_checked",
            "supplier_quality_remarks",
            "supplier_purchase_payment_terms",
            "commercial_terms",
            "supplier_purchase_remarks",
            "risk_remarks",
        )
        self.default_numeric_fields(
            "expected_purchase_rate",
            "expected_lead_time_days",
            "expected_moq",
            "expected_purchase_pack_size",
        )
        if not self.status:
            self.status = "Draft"
        self.validate_supplier_identity()
        self.validate_request_items()
        refresh_document_checklist(self)
        self.validate_duplicate_supplier_rm_requests()
        self.validate_stage_reviews()

    def normalize_text_fields(self, *fieldnames: str):
        for fieldname in fieldnames:
            if self.meta.has_field(fieldname) or self.get(fieldname) is not None:
                self.set(fieldname, (self.get(fieldname) or "").strip())

    def default_numeric_fields(self, *fieldnames: str):
        for fieldname in fieldnames:
            if self.meta.has_field(fieldname) or self.get(fieldname) is not None:
                self.set(fieldname, self.get(fieldname) or 0)

    def on_update(self):
        if self.status == "Cancelled":
            from calco_erp.calco_purchase.supplier_request_duplicate import cancellation_audit
            cancellation_audit(self)
            return
        self.link_to_source_rm_request()
        if self.status == "ERP Creation" and not frappe.utils.cint(self.erp_creation_completed):
            self.run_erp_creation()

    def link_to_source_rm_request(self):
        if not self.source_rm_request:
            return
        if not frappe.db.exists("New RM Request", self.source_rm_request):
            return
        rm_meta = frappe.get_meta("New RM Request")
        if not rm_meta.has_field("supplier_request"):
            return

        if not frappe.db.get_value("New RM Request", self.source_rm_request, "supplier_request"):
            frappe.db.set_value(
                "New RM Request",
                self.source_rm_request,
                "supplier_request",
                self.name,
            )

    def validate_supplier_identity(self):
        source = get_supplier_source(self)
        if self.meta.has_field("supplier_source") and (self.supplier_source or self.is_new()):
            self.supplier_source = source

        if source == SUPPLIER_SOURCE_EXISTING:
            if not self.supplier_name:
                frappe.throw(_("Existing Supplier is required when Supplier Source is Existing Supplier."))
            if self.proposed_supplier_name:
                frappe.throw(_("Proposed Supplier Name must be empty when Supplier Source is Existing Supplier."))
            self.normalize_supplier_link()
            return

        if not self.proposed_supplier_name:
            frappe.throw(_("Proposed Supplier Name is required when Supplier Source is Proposed Supplier."))
        if self.supplier_name:
            if self.status not in {"ERP Creation", "Completed"} and not self.created_supplier:
                frappe.throw(
                    _("Existing / Resolved Supplier can only be set during Governance Outputs for a Proposed Supplier.")
                )
            self.normalize_supplier_link()
            return

        candidates = get_supplier_duplicate_candidates(self)
        if len(candidates) == 1 and self.status not in {"ERP Creation", "Completed"}:
            frappe.throw(
                _("Supplier {0} already exists. Select Existing Supplier instead of creating a Proposed Supplier.").format(
                    candidates[0]
                )
            )

    def normalize_supplier_link(self):
        if not self.supplier_name:
            return

        if frappe.db.exists("Supplier", self.supplier_name):
            return

        supplier = frappe.db.get_value("Supplier", {"supplier_name": self.supplier_name}, "name")
        if supplier:
            self.supplier_name = supplier
            return

        frappe.throw(_(SUPPLIER_ONBOARDING_MESSAGE))

    def validate_request_items(self):
        if not self.supplier_request_items:
            frappe.throw(_("At least one existing or proposed RM is required."))
        seen_items = set()
        for row in self.supplier_request_items:
            row.proposed_rm_code = normalize_request_code(row.get("proposed_rm_code"))
            item_code = (row.item_code or "").strip()
            proposed_rm_code = row.proposed_rm_code
            linked_rm_request = (row.get("linked_rm_request") or "").strip()

            if not item_code and not proposed_rm_code:
                frappe.throw(_("Each Supplier Request row must specify an existing Item or a Proposed RM Code."))
            if bool(proposed_rm_code) != bool(linked_rm_request):
                frappe.throw(
                    _("Proposed RM Code and Linked RM Request must both be provided for a proposed RM.")
                )

            identity = item_code or proposed_rm_code
            identity_key = normalize_identity_value(identity)
            if identity_key in seen_items:
                frappe.throw(
                    _("RM {0} is already included in this Supplier Request. Duplicate rows are not allowed.").format(
                        identity
                    )
                )
            seen_items.add(identity_key)

            if item_code:
                if not frappe.db.exists("Item", item_code):
                    frappe.throw(_("Item {0} does not exist in ERP.").format(item_code))
                item_group = frappe.db.get_value("Item", item_code, "item_group")
                if item_group != "Raw Material":
                    frappe.throw(
                        _("Supplier Request items must be Raw Material items. {0} is currently in Item Group {1}.").format(
                            item_code,
                            item_group or "-",
                        )
                    )

            if proposed_rm_code:
                if not frappe.db.exists("New RM Request", linked_rm_request):
                    frappe.throw(_("Linked RM Request {0} does not exist.").format(linked_rm_request))
                rm_request = frappe.db.get_value(
                    "New RM Request",
                    linked_rm_request,
                    ["rm_code", "created_item"],
                    as_dict=True,
                )
                if normalize_request_code(rm_request.rm_code) != proposed_rm_code:
                    frappe.throw(
                        _("Proposed RM Code {0} does not match Linked RM Request {1} ({2}).").format(
                            proposed_rm_code,
                            linked_rm_request,
                            rm_request.rm_code,
                        )
                    )
                if item_code and rm_request.created_item != item_code:
                    frappe.throw(
                        _("Resolved Item {0} does not match the Item created by Linked RM Request {1}.").format(
                            item_code,
                            linked_rm_request,
                        )
                    )

    def validate_duplicate_supplier_rm_requests(self):
        if self.status in self.CLOSED_DUPLICATE_CHECK_STATUSES:
            return

        from calco_erp.calco_purchase.supplier_request_duplicate import validate_registered_combinations
        validate_registered_combinations(self)
        supplier = self.supplier_name
        for row in self.supplier_request_items or []:
            item_identity = row.item_code or row.get("proposed_rm_code")
            open_request = self.get_open_supplier_request_for_item(
                row.item_code,
                row.get("proposed_rm_code"),
            )
            if open_request:
                frappe.throw(
                    _("Supplier {0} already has an open onboarding request {1} for RM {2}. Duplicate onboarding request is not allowed.").format(
                        supplier or self.proposed_supplier_name,
                        open_request,
                        item_identity,
                    )
                )

    def get_open_supplier_request_for_item(
        self,
        item_code: str | None,
        proposed_rm_code: str | None,
    ) -> str | None:
        if not item_code and not proposed_rm_code:
            return None

        supplier_source = get_supplier_source(self)
        if supplier_source == SUPPLIER_SOURCE_EXISTING:
            supplier_condition = "parent.supplier_name = %(supplier)s"
            supplier_value = self.supplier_name
        else:
            supplier_condition = "lower(trim(ifnull(parent.proposed_supplier_name, ''))) = %(proposed_supplier)s"
            supplier_value = normalize_identity_value(self.proposed_supplier_name)

        item_conditions = []
        values = {
            "closed_statuses": tuple(self.CLOSED_DUPLICATE_CHECK_STATUSES),
        }
        if supplier_source == SUPPLIER_SOURCE_EXISTING:
            values["supplier"] = supplier_value
        else:
            values["proposed_supplier"] = supplier_value
        if item_code:
            item_conditions.append("child.item_code = %(item_code)s")
            values["item_code"] = item_code
        if proposed_rm_code:
            item_conditions.append("lower(trim(ifnull(child.proposed_rm_code, ''))) = %(proposed_rm_code)s")
            values["proposed_rm_code"] = normalize_identity_value(proposed_rm_code)

        conditions = (
            f"{supplier_condition} and ({' or '.join(item_conditions)}) "
            "and ifnull(parent.status, '') not in %(closed_statuses)s"
        )
        if not self.is_new():
            conditions += " and parent.name != %(current_name)s"
            values["current_name"] = self.name

        rows = frappe.db.sql(
            f"""
            select parent.name
            from `tabNew Supplier Request` parent
            inner join `tabSupplier Request Item` child on child.parent = parent.name
            where {conditions}
            order by parent.modified desc
            limit 1
            """,
            values,
            as_dict=True,
        )
        return rows[0].name if rows else None

    def run_erp_creation(self):
        self.validate_erp_creation_prerequisites()
        resolution = get_supplier_request_resolution_context(self)
        if resolution["status"] == RESOLUTION_MANUAL_REVIEW and not self.supplier_name:
            return

        resolved_items = self.resolve_request_items()
        if not resolved_items:
            return

        supplier_name, supplier_action = self.resolve_supplier_for_output(resolution)
        if not supplier_name:
            return

        created_matrix_rows = []
        touched_planning = []
        matrix_by_item = {}
        planning_by_item = {}

        for row, item_code in resolved_items:
            validate_supplier_rm_for_item(self, item_code)
            matrix_name = create_or_update_supplier_matrix_row(
                item_code=item_code,
                supplier=supplier_name,
                supplier_type=self.supplier_type,
                approval_status=row.approval_status or "Approved",
                supplier_rating=row.supplier_rating or self.get("supplier_rating"),
                lead_time=row.lead_time or self.get("lead_time_days"),
                payment_terms=row.payment_terms or self.get("payment_terms"),
                effective_date=row.effective_date or self.get("effective_date"),
                expiry_date=row.expiry_date or self.get("expiry_date"),
            )
            created_matrix_rows.append(matrix_name)
            matrix_by_item[item_code] = matrix_name
            planning_name = create_or_update_planning_parameter(
                item_code=item_code,
                preferred_supplier=supplier_name,
            )
            touched_planning.append(planning_name)
            planning_by_item[item_code] = planning_name

        for row, item_code in resolved_items:
            if row.item_code != item_code:
                row.item_code = item_code
                row.db_update()

        self.db_set(
            {
                "supplier_name": supplier_name,
                "created_supplier": supplier_name,
                "created_matrix_rows": "\n".join(created_matrix_rows),
                "created_planning_parameters": "\n".join([name for name in touched_planning if name]),
                "erp_creation_completed": 1,
                "creation_log": self.build_creation_log(
                    supplier_name,
                    supplier_action,
                    created_matrix_rows,
                    touched_planning,
                ),
                "status": "Completed",
            },
            update_modified=False,
        )
        self.complete_linked_rm_requests(
            supplier_name,
            resolved_items,
            matrix_by_item,
            planning_by_item,
        )

    def resolve_request_items(self) -> list[tuple[object, str]]:
        prepared_rows = []
        linked_requests = {}

        for row in self.supplier_request_items:
            if row.item_code and frappe.db.exists("Item", row.item_code):
                prepared_rows.append((row, row.item_code))
                continue

            if not row.proposed_rm_code or not row.linked_rm_request:
                return []
            rm_request = frappe.get_doc("New RM Request", row.linked_rm_request)
            if rm_request.status not in {"ERP Creation", "Completed"}:
                return []
            if rm_request.status == "ERP Creation":
                rm_request.validate_erp_creation_prerequisites()
            linked_requests[row.name] = rm_request

        resolved_rows = []
        for row in self.supplier_request_items:
            if row.item_code and frappe.db.exists("Item", row.item_code):
                resolved_rows.append((row, row.item_code))
                continue

            rm_request = linked_requests[row.name]
            item_code = rm_request.created_item or ""
            if item_code and frappe.db.exists("Item", item_code):
                resolved_rows.append((row, item_code))
                continue
            if frappe.db.exists("Item", rm_request.rm_code):
                frappe.throw(
                    _("Item {0} already exists but is not resolved to New RM Request {1}. Purchase Master Data review is required.").format(
                        rm_request.rm_code,
                        rm_request.name,
                    )
                )
            item = rm_request.create_item()
            resolved_rows.append((row, item.name))

        return resolved_rows

    def resolve_supplier_for_output(self, resolution: dict[str, object]) -> tuple[str, str]:
        if self.supplier_name and frappe.db.exists("Supplier", self.supplier_name):
            action = (
                "Resolved Proposed Supplier"
                if get_supplier_source(self) == SUPPLIER_SOURCE_PROPOSED
                else "Linked Existing Supplier"
            )
            return self.supplier_name, action

        candidates = resolution.get("supplier_candidates") or get_supplier_duplicate_candidates(self)
        if len(candidates) > 1:
            return "", "Manual Review"
        if len(candidates) == 1:
            return candidates[0], "Resolved Proposed Supplier"

        return self.create_supplier(), "Created Proposed Supplier"

    def create_supplier(self) -> str:
        validate_complete_document_package(self)
        supplier_name = (
            self.proposed_supplier_name
            if get_supplier_source(self) == SUPPLIER_SOURCE_PROPOSED
            else self.supplier_name
        )
        if frappe.db.exists("Supplier", supplier_name):
            return supplier_name

        supplier = frappe.new_doc("Supplier")
        supplier.supplier_name = supplier_name
        supplier.supplier_type = "Company"
        if frappe.db.exists("Supplier Group", "All Supplier Groups"):
            supplier.supplier_group = "All Supplier Groups"
        frappe.flags.allow_supplier_creation_from_supplier_request = True
        try:
            supplier.insert(ignore_permissions=True)
        finally:
            frappe.flags.allow_supplier_creation_from_supplier_request = False
        self.create_supplier_master_links(supplier)
        return supplier.name

    def create_supplier_master_links(self, supplier) -> None:
        if self.get("default_currency") and supplier.meta.has_field("default_currency"):
            supplier.db_set("default_currency", self.default_currency, update_modified=False)
        if (
            self.get("supplier_purchase_payment_terms")
            and supplier.meta.has_field("payment_terms")
            and frappe.db.exists("Payment Terms Template", self.supplier_purchase_payment_terms)
        ):
            supplier.db_set(
                "payment_terms",
                self.supplier_purchase_payment_terms,
                update_modified=False,
            )

        address_name = self.get_or_create_supplier_address(supplier.name)
        contact_name = self.get_or_create_supplier_contact(supplier.name, address_name)
        bank_account_name = self.get_or_create_supplier_bank_account(supplier.name)
        updates = {}
        if address_name and supplier.meta.has_field("supplier_primary_address"):
            updates["supplier_primary_address"] = address_name
        if contact_name and supplier.meta.has_field("supplier_primary_contact"):
            updates["supplier_primary_contact"] = contact_name
        if bank_account_name and supplier.meta.has_field("default_bank_account"):
            updates["default_bank_account"] = bank_account_name
        if updates:
            supplier.db_set(updates, update_modified=False)

    def get_linked_party_record(self, parenttype: str, supplier_name: str) -> str | None:
        return frappe.db.get_value(
            "Dynamic Link",
            {
                "parenttype": parenttype,
                "link_doctype": "Supplier",
                "link_name": supplier_name,
            },
            "parent",
        )

    def get_or_create_supplier_address(self, supplier_name: str) -> str:
        existing = self.get_linked_party_record("Address", supplier_name)
        if existing:
            return existing
        address = frappe.new_doc("Address")
        address.address_title = self.proposed_supplier_name or supplier_name
        address.address_type = "Billing"
        address.address_line1 = self.proposed_address_line1
        address.address_line2 = self.proposed_address_line2
        address.city = self.proposed_city
        address.state = self.proposed_state
        address.country = self.proposed_country
        address.pincode = self.proposed_pincode
        address.email_id = self.primary_contact_email
        address.phone = self.primary_contact_mobile
        address.is_primary_address = 1
        address.append("links", {"link_doctype": "Supplier", "link_name": supplier_name})
        address.insert(ignore_permissions=True)
        return address.name

    def get_or_create_supplier_contact(self, supplier_name: str, address_name: str) -> str:
        existing = self.get_linked_party_record("Contact", supplier_name)
        if existing:
            return existing
        contact = frappe.new_doc("Contact")
        contact.first_name = self.primary_contact_name
        contact.email_id = self.primary_contact_email
        contact.mobile_no = self.primary_contact_mobile
        contact.address = address_name
        contact.is_primary_contact = 1
        contact.append("links", {"link_doctype": "Supplier", "link_name": supplier_name})
        contact.insert(ignore_permissions=True)
        return contact.name

    def get_or_create_supplier_bank_account(self, supplier_name: str) -> str:
        existing = frappe.db.get_value(
            "Bank Account",
            {
                "party_type": "Supplier",
                "party": supplier_name,
                "bank_account_no": self.bank_account_no,
            },
            "name",
        )
        if existing:
            return existing
        account = frappe.new_doc("Bank Account")
        account.account_name = self.bank_account_name
        account.bank = self.bank
        account.bank_account_no = self.bank_account_no
        account.branch_code = self.bank_branch_code
        account.party_type = "Supplier"
        account.party = supplier_name
        account.is_default = 1
        account.insert(ignore_permissions=True)
        return account.name

    def complete_linked_rm_requests(
        self,
        supplier_name: str,
        resolved_items: list[tuple[object, str]],
        matrix_by_item: dict[str, str],
        planning_by_item: dict[str, str],
    ):
        for row, item_code in resolved_items:
            if not row.linked_rm_request:
                continue
            rm_request = frappe.get_doc("New RM Request", row.linked_rm_request)
            planning_name = planning_by_item.get(item_code) or ""
            matrix_name = matrix_by_item.get(item_code) or ""
            rm_request.db_set(
                {
                    "created_item": item_code,
                    "created_planning_parameter": planning_name,
                    "created_supplier_matrix": matrix_name,
                    "erp_creation_completed": 1,
                    "creation_log": rm_request.build_creation_log(
                        item_code,
                        planning_name,
                        matrix_name,
                    ),
                    "preferred_supplier": supplier_name,
                    "status": "Completed",
                },
                update_modified=False,
            )

    def build_creation_log(
        self,
        supplier_name: str,
        supplier_action: str,
        matrix_rows: list[str],
        planning_rows: list[str],
    ) -> str:
        parts = [f"{supplier_action}: {supplier_name}"]
        if matrix_rows:
            parts.append("Created Supplier Approval Matrix Rows: " + ", ".join(matrix_rows))
        if planning_rows:
            parts.append("Touched RM Planning Parameters: " + ", ".join([name for name in planning_rows if name]))
        return "\n".join(parts)

    def validate_stage_reviews(self):
        before = self.get_doc_before_save()
        previous_status = (before.status or "").strip() if before else ""
        current_status = (self.status or "").strip()

        if previous_status == "Quality Review" and current_status in {"Purchase Review", "Rejected"}:
            self.require_quality_review(current_status)
            if current_status != "Rejected":
                validate_quality_document_gate(self)
        if previous_status == "Purchase Review" and current_status in {"Management Review", "Rejected"}:
            self.require_purchase_review(current_status)
            if current_status != "Rejected":
                validate_purchase_document_gate(self)
        if previous_status == "Management Review" and current_status in {"ERP Creation", "Rejected"}:
            self.require_management_review(current_status)
            if current_status != "Rejected":
                validate_complete_document_package(self)

    def validate_erp_creation_prerequisites(self):
        self.require_quality_review("Purchase Review")
        self.require_purchase_review("Management Review")
        self.require_management_review("ERP Creation")
        validate_complete_document_package(self)

    def require_quality_review(self, target_status: str):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.QUALITY_REVIEW_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Quality Review")
        expected_decision = "Rejected" if target_status == "Rejected" else "Approved"
        if self.supplier_quality_decision != expected_decision:
            frappe.throw(_("Quality Decision must be {0} before moving to {1}.").format(expected_decision, target_status))

    def require_purchase_review(self, target_status: str):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.PURCHASE_REVIEW_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Purchase Review")
        expected_decision = "Rejected" if target_status == "Rejected" else "Approved"
        if self.supplier_purchase_decision != expected_decision:
            frappe.throw(_("Purchase Decision must be {0} before moving to {1}.").format(expected_decision, target_status))

    def require_management_review(self, target_status: str):
        required = {
            fieldname_label: getattr(self, fieldname)
            for fieldname_label, fieldname in self.MANAGEMENT_REVIEW_REQUIRED_FIELDS
        }
        self.throw_if_missing(required, "Management Review")
        expected_decision = "Rejected" if target_status == "Rejected" else "Approved"
        if self.final_approval_decision != expected_decision:
            frappe.throw(_("Final Approval Decision must be {0} before moving to {1}.").format(expected_decision, target_status))

    def throw_if_missing(self, required: dict[str, object], stage_label: str):
        missing = [label for label, value in required.items() if value in (None, "", [])]
        if missing:
            frappe.throw(_("{0} cannot be approved/rejected until these fields are completed: {1}").format(stage_label, ", ".join(missing)))
