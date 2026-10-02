from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from calco_erp.calco_purchase.supplier_document_governance import (
    SUPPLIER_RM_TABLE,
    SUPPLIER_TABLE,
    apply_document_updates,
    ensure_document_checklist,
    get_document_summary,
    governance_enabled,
)

from calco_erp.calco_purchase.master_data_governance import (
    RESOLUTION_MANUAL_REVIEW,
    RESOLUTION_RESOLVED,
    get_supplier_request_resolution_context,
)

STAGE_COLORS = {
    "Not Started": "grey",
    "In Progress": "blue",
    "Completed": "green",
    "Rejected": "red",
    "Skipped": "grey",
    "Stopped": "grey",
}

RM_STAGE_KEYS = [
    "request_created",
    "technical_review",
    "document_sample_readiness",
    "quality_review",
    "purchase_review",
    "erp_item_creation",
    "rm_planning_parameter",
    "supplier_approval_matrix",
    "completed",
]

SUPPLIER_STAGE_KEYS = [
    "request_created",
    "quality_review",
    "purchase_review",
    "management_review",
    "supplier_master_creation",
    "supplier_approval_matrix",
    "rm_planning_link",
    "completed",
]


@dataclass
class StageDefinition:
    key: str
    label: str
    owner_role: str


RM_STAGES = [
    StageDefinition("request_created", "Request Created", "Requester"),
    StageDefinition("technical_review", "Technical Review", "Technical User"),
    StageDefinition("document_sample_readiness", "Document & Sample Readiness", "Technical User"),
    StageDefinition("quality_review", "Quality Review", "Quality Manager"),
    StageDefinition("purchase_review", "Purchase Review", "Purchase Manager"),
    StageDefinition("erp_item_creation", "ERP Item Creation", "System Manager"),
    StageDefinition("rm_planning_parameter", "RM Planning Parameter", "System Manager"),
    StageDefinition("supplier_approval_matrix", "Supplier Approval Matrix", "System Manager"),
    StageDefinition("completed", "Completed", "System Manager"),
]

SUPPLIER_STAGES = [
    StageDefinition("request_created", "Request Created", "Requester"),
    StageDefinition("quality_review", "Quality Review", "Quality Manager"),
    StageDefinition("purchase_review", "Purchase Review", "Purchase Manager"),
    StageDefinition("management_review", "Management Review", "Management Reviewer"),
    StageDefinition("supplier_master_creation", "Governance Outputs", "Purchase Master Data"),
    StageDefinition("supplier_approval_matrix", "Supplier Approval Matrix", "System Manager"),
    StageDefinition("rm_planning_link", "RM Planning Link", "System Manager"),
    StageDefinition("completed", "Completed", "System Manager"),
]

RM_STATUS_TO_STAGE = {
    "Draft": "request_created",
    "Technical Review": "technical_review",
    "Document & Sample Readiness": "document_sample_readiness",
    "Quality Review": "quality_review",
    "Purchase Review": "purchase_review",
    "ERP Creation": "erp_item_creation",
    "Completed": "completed",
}

SUPPLIER_STATUS_TO_STAGE = {
    "Draft": "request_created",
    "Quality Review": "quality_review",
    "Purchase Review": "purchase_review",
    "Management Review": "management_review",
    "ERP Creation": "supplier_master_creation",
    "Completed": "completed",
}

RM_REJECT_ROLE_TO_STAGE = {
    "Technical User": "technical_review",
    "Quality Manager": "quality_review",
    "Purchase Manager": "purchase_review",
}

SUPPLIER_REJECT_ROLE_TO_STAGE = {
    "Quality Manager": "quality_review",
    "Purchase Manager": "purchase_review",
    "Management Reviewer": "management_review",
}

RM_WORKFLOW_STATE_TO_STAGE = {
    "Technical Review": "technical_review",
    "Document & Sample Readiness": "document_sample_readiness",
    "Quality Review": "quality_review",
    "Purchase Review": "purchase_review",
}

SUPPLIER_WORKFLOW_STATE_TO_STAGE = {
    "Quality Review": "quality_review",
    "Purchase Review": "purchase_review",
    "Management Review": "management_review",
}

RM_STAGE_SCROLL_FIELDS = {
    "request_created": "requester_section",
    "technical_review": "technical_review_section",
    "document_sample_readiness": "document_readiness_section",
    "quality_review": "quality_review_section",
    "purchase_review": "purchase_review_section",
    "erp_item_creation": "erp_section",
}

SUPPLIER_STAGE_SCROLL_FIELDS = {
    "request_created": "supplier_section",
    "quality_review": "quality_review_section",
    "purchase_review": "purchase_review_section",
    "management_review": "management_review_section",
    "supplier_master_creation": "erp_section",
}

RM_REVIEW_FIELDS = {
    "technical_review": [
        ("Technical Review Remarks", "technical_review_remarks"),
        ("Existing Alternative Available?", "existing_alternative_available"),
        ("Recommended Material Type", "recommended_material_type"),
        ("Application Suitability", "application_suitability"),
        ("Technical Approval Attachment", "technical_approval_attachment"),
        ("Technical Decision", "technical_decision"),
    ],
    "document_sample_readiness": [
        ("TDS Attachment", "tds_attachment"),
        ("MSDS Attachment", "msds_attachment"),
        ("TC / COA Attachment", "tc_coa_attachment"),
        ("Sample Available?", "sample_available"),
        ("Sample Required?", "sample_required"),
        ("Sample Quantity Kg", "sample_quantity_kg"),
        ("Sample Received By Quality?", "sample_received_by_quality"),
        ("Sample Received Date", "sample_received_date"),
        ("Document Readiness Remarks", "document_readiness_remarks"),
        ("Document Readiness Decision", "document_readiness_decision"),
    ],
    "quality_review": [
        ("MSDS Available?", "msds_available"),
        ("TDS Available?", "tds_available"),
        ("COA Available?", "coa_available"),
        ("Additional Test Instructions", "required_incoming_tests"),
        ("Quality Review Remarks", "quality_review_remarks"),
        ("Quality Approval Attachment", "quality_approval_attachment"),
        ("Quality Decision", "quality_decision"),
    ],
    "purchase_review": [
        ("Expected Lead Time", "purchase_lead_time_days"),
        ("Expected MOQ", "purchase_moq"),
        ("Purchase Pack Size", "purchase_pack_size"),
        ("Commercial Remarks", "commercial_remarks"),
        ("Purchase Decision", "purchase_decision"),
    ],
}

SUPPLIER_REVIEW_FIELDS = {
    "quality_review": [
        ("Certificates Checked", "certificates_checked"),
        ("Quality Audit Required?", "quality_audit_required"),
        ("Quality Remarks", "supplier_quality_remarks"),
        ("Quality Decision", "supplier_quality_decision"),
    ],
    "purchase_review": [
        ("Lead Time", "supplier_purchase_lead_time"),
        ("MOQ", "supplier_purchase_moq"),
        ("Payment Terms", "supplier_purchase_payment_terms"),
        ("Commercial Terms", "commercial_terms"),
        ("Purchase Remarks", "supplier_purchase_remarks"),
        ("Purchase Decision", "supplier_purchase_decision"),
    ],
    "management_review": [
        ("Strategic Supplier?", "strategic_supplier"),
        ("Risk Remarks", "risk_remarks"),
        ("Final Approval Decision", "final_approval_decision"),
    ],
}

STAGE_EDITABLE_FIELDS = {
    "New RM Request": {
        "Technical Review": [fieldname for _, fieldname in RM_REVIEW_FIELDS["technical_review"]],
        "Document & Sample Readiness": [fieldname for _, fieldname in RM_REVIEW_FIELDS["document_sample_readiness"]],
        "Quality Review": [fieldname for _, fieldname in RM_REVIEW_FIELDS["quality_review"]]
        + ["no_incoming_inspection_required", "incoming_inspection_parameters"],
        "Purchase Review": [fieldname for _, fieldname in RM_REVIEW_FIELDS["purchase_review"]],
    },
    "New Supplier Request": {
        "Quality Review": [fieldname for _, fieldname in SUPPLIER_REVIEW_FIELDS["quality_review"]]
        + [SUPPLIER_RM_TABLE],
        "Purchase Review": [fieldname for _, fieldname in SUPPLIER_REVIEW_FIELDS["purchase_review"]]
        + [
            "supplier_purchase_pack_size",
            "default_currency",
            "incoterm",
            "freight_delivery_terms",
            "supplier_origin",
            "msme_status",
            "tax_tds_declaration_required",
            "nda_required",
            "code_of_conduct_required",
            "proposed_address_line1",
            "proposed_address_line2",
            "proposed_city",
            "proposed_state",
            "proposed_country",
            "proposed_pincode",
            "primary_contact_name",
            "primary_contact_email",
            "primary_contact_mobile",
            "bank_account_name",
            "bank",
            "bank_account_no",
            "bank_branch_code",
            SUPPLIER_TABLE,
        ],
        "Management Review": [fieldname for _, fieldname in SUPPLIER_REVIEW_FIELDS["management_review"]],
    },
}

QUALITY_PARAMETER_FIELDS = (
    "specification",
    "value",
    "numeric",
    "min_value",
    "max_value",
    "formula_based_criteria",
    "acceptance_formula",
)

RM_REVIEW_REQUIRED_FIELDS = {
    "Technical Review": [
        "technical_review_remarks",
        "existing_alternative_available",
        "recommended_material_type",
        "application_suitability",
        "technical_decision",
    ],
    "Document & Sample Readiness": [
        "tds_attachment",
        "msds_attachment",
        "tc_coa_attachment",
        "sample_available",
        "sample_required",
        "document_readiness_remarks",
        "document_readiness_decision",
    ],
    "Quality Review": [
        "msds_available",
        "tds_available",
        "coa_available",
        "quality_review_remarks",
        "quality_approval_attachment",
        "quality_decision",
    ],
    "Purchase Review": [
        "purchase_lead_time_days",
        "purchase_moq",
        "purchase_pack_size",
        "commercial_remarks",
        "purchase_decision",
    ],
}

SUPPLIER_REVIEW_REQUIRED_FIELDS = {
    "Quality Review": [
        "certificates_checked",
        "quality_audit_required",
        "supplier_quality_remarks",
        "supplier_quality_decision",
    ],
    "Purchase Review": [
        "supplier_purchase_lead_time",
        "supplier_purchase_moq",
        "supplier_purchase_payment_terms",
        "commercial_terms",
        "supplier_purchase_remarks",
        "supplier_purchase_decision",
    ],
    "Management Review": [
        "strategic_supplier",
        "risk_remarks",
        "final_approval_decision",
    ],
}


def _is_unsaved_doc_name(name: str | None) -> bool:
    return bool(name and str(name).startswith("new-"))


def _unsaved_tracker_payload(doctype: str, name: str | None) -> dict[str, object]:
    stage_order_source = [
        _("Request Created"),
        _("Quality Review"),
        _("Purchase Review"),
        _("Management Review"),
        _("Governance Outputs"),
        _("Supplier Approval Matrix"),
        _("RM Planning Link"),
        _("Completed"),
    ]
    if doctype == "New RM Request":
        stage_order_source = [
            _("Request Created"),
            _("Technical Review"),
            _("Document & Sample Readiness"),
            _("Quality Review"),
            _("Purchase Review"),
            _("ERP Item Creation"),
            _("RM Planning Parameter"),
            _("Supplier Approval Matrix"),
            _("Completed"),
        ]
    return {
        "doctype": doctype,
        "name": name,
        "title": name,
        "overall_status": "Draft",
        "stage_order_source": stage_order_source,
        "stages": [],
    }


@frappe.whitelist()
def get_new_rm_request_tracker(name: str | None = None, *args, **kwargs) -> dict[str, object]:
    name = name or kwargs.get("name")
    if not name:
        frappe.throw(_("New RM Request name is required to load the journey tracker."))
    if _is_unsaved_doc_name(name):
        return _unsaved_tracker_payload("New RM Request", name)
    doc = frappe.get_doc("New RM Request", name)
    return build_new_rm_request_tracker(doc)


@frappe.whitelist()
def get_new_supplier_request_tracker(name: str | None = None, *args, **kwargs) -> dict[str, object]:
    name = name or kwargs.get("name")
    if not name:
        frappe.throw(_("New Supplier Request name is required to load the journey tracker."))
    if _is_unsaved_doc_name(name):
        return _unsaved_tracker_payload("New Supplier Request", name)
    doc = frappe.get_doc("New Supplier Request", name)
    return build_new_supplier_request_tracker(doc)


@frappe.whitelist()
def ensure_supplier_document_checklist(name: str) -> dict[str, object]:
    if not name:
        frappe.throw(_("New Supplier Request name is required."))

    doc = frappe.get_doc("New Supplier Request", name)
    doc.check_permission("write")
    if (doc.status or "") != "Purchase Review":
        frappe.throw(_("Supplier document checklist can only be initialized in Purchase Review."))

    proposed_supplier = (doc.get("supplier_source") or "") == "Proposed Supplier"
    requires_initialization = (
        not governance_enabled(doc)
        or not (doc.get(SUPPLIER_RM_TABLE) or [])
        or (proposed_supplier and not (doc.get(SUPPLIER_TABLE) or []))
    )
    if requires_initialization:
        ensure_document_checklist(doc)
        doc.save()
        doc.reload()

    return {
        "name": doc.name,
        "initialized": requires_initialization,
        "supplier_document_count": len(doc.get(SUPPLIER_TABLE) or []),
        "supplier_rm_document_count": len(doc.get(SUPPLIER_RM_TABLE) or []),
    }


def get_linked_supplier_request_name(doc) -> str:
    if doc.meta.has_field("supplier_request") and doc.get("supplier_request"):
        return doc.get("supplier_request")

    if not doc.name or _is_unsaved_doc_name(doc.name):
        return ""

    linked_requests = frappe.get_all(
        "New Supplier Request",
        filters={"source_rm_request": doc.name},
        pluck="name",
        order_by="modified desc",
        limit_page_length=1,
    )
    return linked_requests[0] if linked_requests else ""


def get_rm_purchase_decision(doc) -> str:
    if doc.meta.has_field("commercial_feasibility_decision"):
        return (doc.get("commercial_feasibility_decision") or "").strip()
    if doc.meta.has_field("purchase_decision"):
        return (doc.get("purchase_decision") or "").strip()
    return ""


def normalize_incoming_inspection_parameters(rows: object) -> list[dict[str, object]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        frappe.throw(_("Incoming Inspection Parameters must be provided as a list."))

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            frappe.throw(_("Each Incoming Inspection Parameter must be a valid row."))

        normalized_row = {fieldname: row.get(fieldname) for fieldname in QUALITY_PARAMETER_FIELDS}
        normalized_row["numeric"] = frappe.utils.cint(normalized_row.get("numeric"))
        normalized_row["formula_based_criteria"] = frappe.utils.cint(
            normalized_row.get("formula_based_criteria")
        )
        normalized.append(normalized_row)
    return normalized


def set_incoming_inspection_parameters(doc, rows: object) -> int:
    normalized_rows = normalize_incoming_inspection_parameters(rows)
    doc.set("incoming_inspection_parameters", [])
    for row in normalized_rows:
        doc.append("incoming_inspection_parameters", row)
    return len(normalized_rows)

@frappe.whitelist()
def save_stage_review(
    doctype: str,
    name: str,
    values: dict[str, object] | str | None = None,
    action: str | None = None,
) -> dict[str, object]:
    if not doctype or not name:
        frappe.throw(_("Both doctype and document name are required."))

    doc = frappe.get_doc(doctype, name)
    values = frappe.parse_json(values) if values else {}
    allowed_fields = set(STAGE_EDITABLE_FIELDS.get(doctype, {}).get(doc.status or "", []))

    invalid_fields = sorted(set(values or {}).difference(allowed_fields))
    if invalid_fields:
        frappe.throw(
            _("These fields cannot be updated while the document is in {0}: {1}").format(
                doc.status or _("this stage"),
                ", ".join(invalid_fields),
            )
        )

    expected_parameter_count = None
    expected_document_counts: dict[str, int] = {}
    for fieldname in allowed_fields:
        if fieldname in values:
            if fieldname == "incoming_inspection_parameters":
                if doctype != "New RM Request":
                    frappe.throw(_("Incoming Inspection Parameters are only supported for New RM Request."))
                expected_parameter_count = set_incoming_inspection_parameters(doc, values[fieldname])
            elif fieldname in {SUPPLIER_TABLE, SUPPLIER_RM_TABLE}:
                apply_document_updates(doc, fieldname, values[fieldname])
                expected_document_counts[fieldname] = len(doc.get(fieldname) or [])
            else:
                doc.set(fieldname, values[fieldname])

    doc.save()
    doc.reload()

    if expected_parameter_count is not None:
        persisted_parameter_count = len(doc.get("incoming_inspection_parameters") or [])
        if persisted_parameter_count != expected_parameter_count:
            frappe.throw(
                _("Incoming Inspection Parameters could not be saved. Expected {0}, found {1}.").format(
                    expected_parameter_count,
                    persisted_parameter_count,
                )
            )

    for fieldname, expected_count in expected_document_counts.items():
        persisted_count = len(doc.get(fieldname) or [])
        if persisted_count != expected_count:
            frappe.throw(
                _("Supplier governance documents could not be saved. Expected {0}, found {1}.").format(
                    expected_count,
                    persisted_count,
                )
            )

    if action:
        doc = apply_workflow(doc, action)
        doc.reload()

    return {
        "name": doc.name,
        "status": doc.status,
        "incoming_inspection_parameter_count": len(
            doc.get("incoming_inspection_parameters") or []
        )
        if doctype == "New RM Request"
        else 0,
        "supplier_document_count": len(doc.get(SUPPLIER_TABLE) or [])
        if doctype == "New Supplier Request"
        else 0,
        "supplier_rm_document_count": len(doc.get(SUPPLIER_RM_TABLE) or [])
        if doctype == "New Supplier Request"
        else 0,
    }


def build_new_rm_request_tracker(doc) -> dict[str, object]:
    current_status = (doc.status or "Draft").strip() or "Draft"
    current_stage_key = RM_STATUS_TO_STAGE.get(current_status, "request_created")
    rejected_stage_key = infer_rejected_stage_key(doc, RM_REJECT_ROLE_TO_STAGE, RM_WORKFLOW_STATE_TO_STAGE) if current_status == "Rejected" else ""

    item_name = doc.created_item or frappe.db.exists("Item", doc.rm_code)
    planning_name = doc.created_planning_parameter or (item_name and frappe.db.exists("RM Planning Parameter", item_name))
    supplier_request_name = get_linked_supplier_request_name(doc)
    matrix_name = ""

    if supplier_request_name:
        requested_supplier = frappe.db.get_value(
            "New Supplier Request",
            supplier_request_name,
            "created_supplier",
        )
        if requested_supplier:
            matrix_rows = get_supplier_request_matrix_rows(requested_supplier, [item_name or doc.rm_code])
            matrix_name = ", ".join(row["name"] for row in matrix_rows)
    elif doc.preferred_supplier:
        matrix_name = doc.created_supplier_matrix or frappe.db.get_value(
            "Supplier Approval Matrix",
            {"item_code": item_name or doc.rm_code, "supplier": doc.preferred_supplier},
            "name",
        )

    stages = [
        make_request_stage(
            doc=doc,
            definition=RM_STAGES[0],
            status=resolve_review_stage_status(RM_STAGE_KEYS, "request_created", current_stage_key, current_status, rejected_stage_key),
            summary="Request is saved and awaiting workflow progression." if current_status == "Draft" else "Request has been created.",
        ),
        build_review_stage(
            doc=doc,
            definition=RM_STAGES[1],
            status=resolve_review_stage_status(RM_STAGE_KEYS, "technical_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=RM_REVIEW_FIELDS["technical_review"],
            required_fields=RM_REVIEW_REQUIRED_FIELDS["Technical Review"],
            pending_summary="Complete the Technical Review section before approving or rejecting this request.",
        ),
        build_review_stage(
            doc=doc,
            definition=RM_STAGES[2],
            status=resolve_review_stage_status(RM_STAGE_KEYS, "document_sample_readiness", current_stage_key, current_status, rejected_stage_key),
            review_fields=RM_REVIEW_FIELDS["document_sample_readiness"],
            required_fields=RM_REVIEW_REQUIRED_FIELDS["Document & Sample Readiness"],
            pending_summary="Upload the required documents and confirm sample readiness before Quality Review can start.",
        ),
        build_review_stage(
            doc=doc,
            definition=RM_STAGES[3],
            status=resolve_review_stage_status(RM_STAGE_KEYS, "quality_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=RM_REVIEW_FIELDS["quality_review"],
            required_fields=RM_REVIEW_REQUIRED_FIELDS["Quality Review"],
            pending_summary="Complete the Quality Review section before approving or rejecting this request.",
        ),
        build_review_stage(
            doc=doc,
            definition=RM_STAGES[4],
            status=resolve_review_stage_status(RM_STAGE_KEYS, "purchase_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=RM_REVIEW_FIELDS["purchase_review"],
            required_fields=RM_REVIEW_REQUIRED_FIELDS["Purchase Review"],
            pending_summary=(
                "Supplier Request is linked. Continue supplier onboarding in its workflow before ERP creation."
                if supplier_request_name
                else (
                    "Supplier Development Required: create or open a Supplier Request before moving to ERP Creation."
                    if get_rm_purchase_decision(doc) == "Supplier Development Required"
                    else "Complete the Purchase Review section before approving or rejecting this request."
                )
            ),
        ),
        build_rm_item_stage(doc, item_name, current_status),
        build_rm_planning_stage(doc, item_name, planning_name, current_status),
        build_rm_matrix_stage(doc, item_name, matrix_name, current_status),
        build_rm_completed_stage(doc, item_name, planning_name, matrix_name, current_status),
    ]

    return {
        "doctype": doc.doctype,
        "name": doc.name,
        "title": doc.rm_name or doc.rm_code or doc.name,
        "overall_status": current_status,
        "stage_order_source": [stage.label for stage in RM_STAGES],
        "stages": stages,
    }


def build_new_supplier_request_tracker(doc) -> dict[str, object]:
    current_status = (doc.status or "Draft").strip() or "Draft"
    current_stage_key = SUPPLIER_STATUS_TO_STAGE.get(current_status, "request_created")
    rejected_stage_key = infer_rejected_stage_key(doc, SUPPLIER_REJECT_ROLE_TO_STAGE, SUPPLIER_WORKFLOW_STATE_TO_STAGE) if current_status == "Rejected" else ""

    supplier_name = resolve_supplier_request_supplier(doc)
    requested_items = [
        row.item_code or row.get("proposed_rm_code")
        for row in (doc.supplier_request_items or [])
        if row.item_code or row.get("proposed_rm_code")
    ]
    resolution = get_supplier_request_resolution_context(doc)
    matrix_rows = get_supplier_request_matrix_rows(supplier_name or doc.supplier_name, requested_items)
    planning_links = get_supplier_request_planning_links(supplier_name or doc.supplier_name, requested_items)

    stages = [
        make_request_stage(
            doc=doc,
            definition=SUPPLIER_STAGES[0],
            status=resolve_review_stage_status(SUPPLIER_STAGE_KEYS, "request_created", current_stage_key, current_status, rejected_stage_key),
            summary="Supplier request is saved and awaiting workflow progression." if current_status == "Draft" else "Supplier request has been created.",
        ),
        build_review_stage(
            doc=doc,
            definition=SUPPLIER_STAGES[1],
            status=resolve_review_stage_status(SUPPLIER_STAGE_KEYS, "quality_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=SUPPLIER_REVIEW_FIELDS["quality_review"],
            required_fields=SUPPLIER_REVIEW_REQUIRED_FIELDS["Quality Review"],
            pending_summary="Complete the Quality Review section before approving or rejecting this supplier request.",
        ),
        build_review_stage(
            doc=doc,
            definition=SUPPLIER_STAGES[2],
            status=resolve_review_stage_status(SUPPLIER_STAGE_KEYS, "purchase_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=SUPPLIER_REVIEW_FIELDS["purchase_review"],
            required_fields=SUPPLIER_REVIEW_REQUIRED_FIELDS["Purchase Review"],
            pending_summary="Complete the Purchase Review section before approving or rejecting this supplier request.",
        ),
        build_review_stage(
            doc=doc,
            definition=SUPPLIER_STAGES[3],
            status=resolve_review_stage_status(SUPPLIER_STAGE_KEYS, "management_review", current_stage_key, current_status, rejected_stage_key),
            review_fields=SUPPLIER_REVIEW_FIELDS["management_review"],
            required_fields=SUPPLIER_REVIEW_REQUIRED_FIELDS["Management Review"],
            pending_summary="Complete the Management Review section before approving or rejecting this supplier request.",
        ),
        build_governance_outputs_stage(doc, supplier_name, requested_items, current_status, resolution),
        build_supplier_matrix_rows_stage(doc, supplier_name, requested_items, matrix_rows, current_status),
        build_supplier_planning_link_stage(doc, supplier_name, requested_items, planning_links, current_status),
        build_supplier_completed_stage(doc, supplier_name, requested_items, matrix_rows, planning_links, current_status),
    ]

    return {
        "doctype": doc.doctype,
        "name": doc.name,
        "title": doc.supplier_name or doc.get("proposed_supplier_name") or doc.name,
        "overall_status": current_status,
        "resolution_status": resolution["status"],
        "stage_order_source": [stage.label for stage in SUPPLIER_STAGES],
        "stages": stages,
    }


def resolve_supplier_request_supplier(doc) -> str | None:
    supplier = doc.created_supplier or doc.supplier_name
    if supplier and frappe.db.exists("Supplier", supplier):
        return supplier
    if supplier:
        return frappe.db.get_value("Supplier", {"supplier_name": supplier}, "name")
    return None


def make_request_stage(*, doc, definition: StageDefinition, status: str, summary: str) -> dict[str, object]:
    scroll_field = get_stage_scroll_field(doc.doctype, definition.key)
    return make_stage(
        key=definition.key,
        label=definition.label,
        owner_role=definition.owner_role,
        status=status,
        summary=summary if status != "Rejected" else "Request was rejected at this review stage.",
        documents=[make_document(doc.doctype, doc.name, doc.status or "", doc.get("creation_log") or "")],
        action=make_open_action(
            doc.doctype,
            [doc.name],
            scroll_to_field=scroll_field,
            guide_message=guidance_message_for_stage(definition.label),
            label="Go to Section" if scroll_field else None,
        ),
        message=guidance_message_for_stage(definition.label),
    )


def build_review_stage(
    *,
    doc,
    definition: StageDefinition,
    status: str,
    review_fields: list[tuple[str, str]],
    required_fields: list[str],
    pending_summary: str,
) -> dict[str, object]:
    scroll_field = get_stage_scroll_field(doc.doctype, definition.key)
    audit = build_review_audit(doc, definition, review_fields)
    summary = build_review_summary(status, pending_summary, audit, definition.owner_role)
    return make_stage(
        key=definition.key,
        label=definition.label,
        owner_role=definition.owner_role,
        status=status,
        summary=summary,
        documents=[make_document(doc.doctype, doc.name, doc.status or "", doc.get("creation_log") or "")],
        action=make_open_action(
            doc.doctype,
            [doc.name],
            scroll_to_field=scroll_field,
            guide_message=guidance_message_for_stage(definition.label),
            label="Go to Section" if scroll_field else None,
        ),
        message=guidance_message_for_stage(definition.label),
        audit=audit,
        required_fields=list(required_fields or []),
    )


def build_rm_item_stage(doc, item_name: str | None, current_status: str) -> dict[str, object]:
    if item_name:
        return make_stage(
            key="erp_item_creation",
            label="ERP Item Creation",
            owner_role="System Manager",
            status="Completed",
            summary=f"ERP Item {item_name} has been created.",
            documents=[make_document("Item", item_name, "Created", doc.rm_name or "")],
            action=make_open_action("Item", [item_name]),
            message="Open the created Item record.",
        )
    status = "In Progress" if current_status in {"ERP Creation", "Completed"} else ("Stopped" if current_status == "Rejected" else "Not Started")
    return make_stage(
        key="erp_item_creation",
        label="ERP Item Creation",
        owner_role="System Manager",
        status=status,
        summary="ERP Item will be created automatically after approvals." if status == "Not Started" else "ERP item creation is pending automation.",
        action=make_open_action(doc.doctype, [doc.name], scroll_to_field="erp_section", guide_message="Use Actions to continue ERP creation for this request.") if status in {"In Progress", "Stopped"} else None,
        message="Use Actions to continue ERP creation for this request." if status == "In Progress" else "This stage has not started yet.",
    )


def build_rm_planning_stage(doc, item_name: str | None, planning_name: str | None, current_status: str) -> dict[str, object]:
    if planning_name:
        return make_stage(
            key="rm_planning_parameter",
            label="RM Planning Parameter",
            owner_role="System Manager",
            status="Completed",
            summary=f"RM Planning Parameter {planning_name} exists for this RM.",
            documents=[make_document("RM Planning Parameter", planning_name, "Created", item_name or doc.rm_code)],
            action=make_open_action("RM Planning Parameter", [planning_name]),
            message="Open the linked planning parameter.",
        )
    status = "In Progress" if current_status in {"ERP Creation", "Completed"} and item_name else ("Stopped" if current_status == "Rejected" else "Not Started")
    return make_stage(
        key="rm_planning_parameter",
        label="RM Planning Parameter",
        owner_role="System Manager",
        status=status,
        summary="RM Planning Parameter will be created automatically after ERP item creation." if status != "Stopped" else "Downstream ERP planning step stopped after rejection.",
        action=make_open_action(doc.doctype, [doc.name]) if status in {"In Progress", "Stopped"} else None,
        message="Open the request for planning creation details." if status == "In Progress" else "This stage has not started yet.",
    )


def build_rm_matrix_stage(doc, item_name: str | None, matrix_name: str | None, current_status: str) -> dict[str, object]:
    supplier_request_name = get_linked_supplier_request_name(doc) or ""
    if not doc.preferred_supplier and not supplier_request_name:
        return make_stage(
            key="supplier_approval_matrix",
            label="Supplier Approval Matrix",
            owner_role="System Manager",
            status="Skipped",
            summary="Skipped / Not Applicable because no supplier source was provided.",
            action=make_info_action(
                _("Supplier Approval Matrix is handled from Supplier Requests and is not auto-created from this request."),
            ),
            message="Supplier onboarding now runs as a separate request workflow.",
        )
    if matrix_name:
        matrix_docs = [row_name for row_name in (matrix_name or "").split(", ") if row_name]
        return make_stage(
            key="supplier_approval_matrix",
            label="Supplier Approval Matrix",
            owner_role="System Manager",
            status="Completed",
            summary=f"Supplier Approval Matrix row(s) available: {matrix_name}.",
            documents=[
                make_document("Supplier Approval Matrix", row_name, "Created", doc.preferred_supplier or supplier_request_name)
                for row_name in matrix_docs
            ],
            action=make_open_action("Supplier Approval Matrix", matrix_docs),
            message="Open the linked Supplier Approval Matrix row.",
        )
    if supplier_request_name:
        request_status = frappe.db.get_value("New Supplier Request", supplier_request_name, "status") or ""
        if request_status in {"Technical Review", "Document & Sample Readiness", "Quality Review", "Purchase Review", "Management Review", "ERP Creation", "Draft"}:
            return make_stage(
                key="supplier_approval_matrix",
                label="Supplier Approval Matrix",
                owner_role="System Manager",
                status="In Progress",
                summary="Supplier Request is active. Matrix rows will be generated when that request is completed.",
                action=make_open_action("New Supplier Request", [supplier_request_name], label=_("Open Supplier Request")),
                message="Supplier matrix rows are created by the Supplier Request workflow.",
            )
        if request_status == "Completed":
            if matrix_name:
                matrix_docs = [row_name for row_name in (matrix_name or "").split(", ") if row_name]
                return make_stage(
                    key="supplier_approval_matrix",
                    label="Supplier Approval Matrix",
                    owner_role="System Manager",
                    status="Completed",
                    summary=f"Supplier Request completed. Matrix rows available: {matrix_name}.",
                    documents=[
                        make_document("Supplier Approval Matrix", row_name, "Created", supplier_request_name)
                        for row_name in matrix_docs
                    ],
                    action=make_open_action("Supplier Approval Matrix", matrix_docs),
                    message="Open linked Supplier Approval Matrix rows.",
                )
            return make_stage(
                key="supplier_approval_matrix",
                label="Supplier Approval Matrix",
                owner_role="System Manager",
                status="Completed",
                summary="Supplier Request workflow completed and no separate matrix rows were required for RM completion.",
                action=make_open_action("New Supplier Request", [supplier_request_name], label=_("Open Supplier Request")),
                message="Supplier Request has completed; RM journey can continue."
            )
        if request_status == "Rejected":
            return make_stage(
                key="supplier_approval_matrix",
                label="Supplier Approval Matrix",
                owner_role="System Manager",
                status="Stopped",
                summary="Supplier Request was rejected. Downstream supplier governance did not complete.",
                action=make_open_action("New Supplier Request", [supplier_request_name], label=_("Open Supplier Request")),
                message="Supplier governance stopped after Supplier Request was rejected.",
            )

    status = "In Progress" if current_status in {"ERP Creation", "Completed"} and item_name else ("Stopped" if current_status == "Rejected" else "Not Started")
    return make_stage(
        key="supplier_approval_matrix",
        label="Supplier Approval Matrix",
        owner_role="System Manager",
        status=status,
        summary="Supplier Approval Matrix is not auto-created from RM Request. Use the linked Supplier Request workflow when needed."
        if status != "Stopped"
        else "Downstream supplier matrix step stopped after rejection.",
        action=make_open_action(doc.doctype, [doc.name]) if status in {"In Progress", "Stopped"} else None,
        message="Open the request for supplier governance details." if status == "In Progress" else "This stage has not started yet.",
    )


def build_rm_completed_stage(doc, item_name: str | None, planning_name: str | None, matrix_name: str | None, current_status: str) -> dict[str, object]:
    if current_status == "Completed" and item_name and planning_name:
        return make_stage(
            key="completed",
            label="Completed",
            owner_role="System Manager",
            status="Completed",
            summary="RM Request journey is fully completed after ERP creation.",
            documents=[
                make_document("Item", item_name, "Created", doc.rm_name or ""),
                make_document(doc.doctype, doc.name, "Completed", doc.creation_log or ""),
            ],
            action=make_open_action("Item", [item_name]),
            message="Open the created Item.",
        )
    status = "Stopped" if current_status == "Rejected" else ("In Progress" if current_status == "Completed" else "Not Started")
    summary = "Request rejected. Downstream stages are stopped." if status == "Stopped" else "Completion will follow after all ERP creation stages finish."
    return make_stage(
        key="completed",
        label="Completed",
        owner_role="System Manager",
        status=status,
        summary=summary,
        action=make_open_action(doc.doctype, [doc.name]) if status != "Not Started" else None,
        message="Open the request to review final status." if status != "Not Started" else "This stage has not started yet.",
    )


def build_governance_outputs_stage(
    doc,
    supplier_name: str | None,
    requested_items: list[str],
    current_status: str,
    resolution: dict[str, object],
) -> dict[str, object]:
    resolution_status = resolution["status"]
    if current_status == "Rejected":
        status = "Stopped"
    elif resolution_status == RESOLUTION_RESOLVED:
        status = "Completed"
    elif current_status in {"ERP Creation", "Completed"}:
        status = "In Progress"
    else:
        status = "Not Started"

    if resolution_status == RESOLUTION_MANUAL_REVIEW:
        candidates = ", ".join(resolution.get("supplier_candidates") or [])
        summary = (
            "Resolution Status: Manual Review. Purchase Master Data must resolve the ambiguous Supplier"
            + (f" candidates: {candidates}." if candidates else ".")
        )
    elif resolution_status == RESOLUTION_RESOLVED:
        summary = "Resolution Status: Resolved. Item and Supplier masters are linked."
    else:
        summary = "Resolution Status: Pending. Item and Supplier will resolve together after governance approvals."

    documents = [
        make_document("Item", item_code, "Resolved", "")
        for item_code in requested_items
        if frappe.db.exists("Item", item_code)
    ]
    if supplier_name:
        documents.append(make_document("Supplier", supplier_name, "Resolved", doc.supplier_type or ""))

    return make_stage(
        key="supplier_master_creation",
        label="Governance Outputs",
        owner_role="Purchase Master Data",
        status=status,
        summary=summary,
        documents=documents,
        action=make_open_action(doc.doctype, [doc.name], scroll_to_field="erp_section", guide_message="Review Item and Supplier resolution outputs.") if status in {"In Progress", "Stopped"} else None,
        message="Purchase Master Data must resolve the Supplier." if resolution_status == RESOLUTION_MANUAL_REVIEW else "Review Item and Supplier governance outputs.",
    )


def build_supplier_matrix_rows_stage(doc, supplier_name: str | None, requested_items: list[str], matrix_rows: list[dict[str, str]], current_status: str) -> dict[str, object]:
    if matrix_rows and len(matrix_rows) >= len(set(requested_items or [])):
        return make_stage(
            key="supplier_approval_matrix",
            label="Supplier Approval Matrix",
            owner_role="System Manager",
            status="Completed",
            summary=f"{len(matrix_rows)} Supplier Approval Matrix row(s) created for the requested RM items.",
            documents=[make_document("Supplier Approval Matrix", row["name"], row.get("approval_status") or "", row.get("item_code") or "") for row in matrix_rows],
            action=make_open_action("Supplier Approval Matrix", [row["name"] for row in matrix_rows]),
            message="Open the created Supplier Approval Matrix row(s).",
        )
    status = "In Progress" if current_status in {"ERP Creation", "Completed"} and supplier_name else ("Stopped" if current_status == "Rejected" else "Not Started")
    existing_count = len(matrix_rows)
    summary = (
        f"{existing_count}/{len(set(requested_items or []))} Supplier Approval Matrix row(s) created."
        if existing_count and status == "In Progress"
        else ("Supplier Approval Matrix rows will be created after Item and Supplier resolution." if status != "Stopped" else "Downstream matrix creation stopped after rejection.")
    )
    return make_stage(
        key="supplier_approval_matrix",
        label="Supplier Approval Matrix",
        owner_role="System Manager",
        status=status,
        summary=summary,
        documents=[make_document("Supplier Approval Matrix", row["name"], row.get("approval_status") or "", row.get("item_code") or "") for row in matrix_rows],
        action=make_open_action("Supplier Approval Matrix", [row["name"] for row in matrix_rows]) if matrix_rows else (make_open_action(doc.doctype, [doc.name]) if status in {"In Progress", "Stopped"} else None),
        message="Open the matrix rows or request." if status != "Not Started" else "This stage has not started yet.",
    )


def build_supplier_planning_link_stage(doc, supplier_name: str | None, requested_items: list[str], planning_links: list[dict[str, str]], current_status: str) -> dict[str, object]:
    expected_items = len(set(requested_items or []))
    if planning_links and len(planning_links) >= expected_items:
        return make_stage(
            key="rm_planning_link",
            label="RM Planning Link",
            owner_role="System Manager",
            status="Completed",
            summary=f"Preferred supplier linked to {len(planning_links)} RM Planning Parameter record(s).",
            documents=[make_document("RM Planning Parameter", row["name"], "Linked", row.get("item_code") or "") for row in planning_links],
            action=make_open_action("RM Planning Parameter", [row["name"] for row in planning_links]),
            message="Open the linked RM Planning Parameter record(s).",
        )
    status = "In Progress" if current_status in {"ERP Creation", "Completed"} and supplier_name else ("Stopped" if current_status == "Rejected" else "Not Started")
    summary = (
        f"{len(planning_links)}/{expected_items} RM Planning Parameter link(s) updated."
        if planning_links and status == "In Progress"
        else ("RM Planning Parameter preferred supplier links will be updated automatically." if status != "Stopped" else "Downstream planning link step stopped after rejection.")
    )
    return make_stage(
        key="rm_planning_link",
        label="RM Planning Link",
        owner_role="System Manager",
        status=status,
        summary=summary,
        documents=[make_document("RM Planning Parameter", row["name"], "Linked", row.get("item_code") or "") for row in planning_links],
        action=make_open_action("RM Planning Parameter", [row["name"] for row in planning_links]) if planning_links else (make_open_action(doc.doctype, [doc.name]) if status in {"In Progress", "Stopped"} else None),
        message="Open the linked planning parameter(s) or request." if status != "Not Started" else "This stage has not started yet.",
    )


def build_supplier_completed_stage(doc, supplier_name: str | None, requested_items: list[str], matrix_rows: list[dict[str, str]], planning_links: list[dict[str, str]], current_status: str) -> dict[str, object]:
    expected_items = len(set(requested_items or []))
    if current_status == "Completed" and supplier_name and len(matrix_rows) >= expected_items and len(planning_links) >= expected_items:
        return make_stage(
            key="completed",
            label="Completed",
            owner_role="System Manager",
            status="Completed",
            summary="Supplier Request journey is fully completed.",
            documents=[
                make_document("Supplier", supplier_name, "Created", doc.supplier_type or ""),
                make_document(doc.doctype, doc.name, "Completed", doc.creation_log or ""),
            ],
            action=make_open_action("Supplier", [supplier_name]),
            message="Open the linked Supplier.",
        )
    status = "Stopped" if current_status == "Rejected" else ("In Progress" if current_status == "Completed" else "Not Started")
    summary = "Request rejected. Downstream stages are stopped." if status == "Stopped" else "Completion will follow after Governance Outputs, Matrix, and Planning links are ready."
    return make_stage(
        key="completed",
        label="Completed",
        owner_role="System Manager",
        status=status,
        summary=summary,
        action=make_open_action(doc.doctype, [doc.name]) if status != "Not Started" else None,
        message="Open the request to review final status." if status != "Not Started" else "This stage has not started yet.",
    )


def resolve_review_stage_status(stage_order: list[str], stage_key: str, current_stage_key: str, current_status: str, rejected_stage_key: str) -> str:
    if current_status == "Rejected":
        reject_index = stage_order.index(rejected_stage_key) if rejected_stage_key in stage_order else 1
        current_index = stage_order.index(stage_key)
        if current_index < reject_index:
            return "Completed"
        if current_index == reject_index:
            return "Rejected"
        return "Stopped"

    current_index = stage_order.index(current_stage_key) if current_stage_key in stage_order else 0
    stage_index = stage_order.index(stage_key)
    if stage_index < current_index:
        return "Completed"
    if stage_index == current_index:
        return "In Progress"
    return "Not Started"


def get_incoming_inspection_summary(doc) -> str:
    if doc.doctype != "New RM Request" or not doc.meta.has_field("incoming_inspection_parameters"):
        return ""
    if doc.meta.has_field("no_incoming_inspection_required") and frappe.utils.cint(
        doc.get("no_incoming_inspection_required")
    ):
        return _("No incoming inspection required")

    parameter_count = len(doc.get("incoming_inspection_parameters") or [])
    if parameter_count:
        if parameter_count == 1:
            return _("1 inspection parameter defined")
        return _("{0} inspection parameters defined").format(parameter_count)
    if (doc.get("quality_decision") or "").strip() == "Approved":
        return _("Legacy quality approval - no structured parameters recorded")
    return _("No inspection parameters defined")


def build_review_audit(doc, definition: StageDefinition, review_fields: list[tuple[str, str]]) -> dict[str, object]:
    workflow_action = get_latest_stage_workflow_action(doc, definition.label)
    items = []
    for label, fieldname in review_fields:
        value = format_audit_value(doc.get(fieldname))
        if value:
            items.append({"label": label, "value": value})

    if doc.doctype == "New RM Request" and definition.label == "Quality Review":
        parameter_summary = get_incoming_inspection_summary(doc)
        if parameter_summary:
            items.append({"label": _("Incoming Inspection"), "value": parameter_summary})
    if doc.doctype == "New Supplier Request" and definition.label == "Quality Review":
        items.append({"label": _("Supplier-RM Documents"), "value": get_document_summary(doc, SUPPLIER_RM_TABLE)})
    if doc.doctype == "New Supplier Request" and definition.label == "Purchase Review":
        items.append({"label": _("Supplier Documents"), "value": get_document_summary(doc, SUPPLIER_TABLE)})
        items.append({"label": _("Supplier-RM Documents"), "value": get_document_summary(doc, SUPPLIER_RM_TABLE)})

    audit: dict[str, object] = {
        "items": items,
        "reviewed_by": (workflow_action or {}).get("completed_by") or "",
        "reviewed_on": format_audit_timestamp((workflow_action or {}).get("modified") or (workflow_action or {}).get("creation")),
        "decision": next((item["value"] for item in items if "Decision" in item["label"]), ""),
    }
    if workflow_action and workflow_action.get("completed_by_role"):
        audit["reviewed_role"] = workflow_action.get("completed_by_role")
    return audit


def build_review_summary(status: str, pending_summary: str, audit: dict[str, object], owner_role: str) -> str:
    if status == "Rejected":
        decision = audit.get("decision") or "Rejected"
        reviewer = audit.get("reviewed_by") or owner_role
        reviewed_on = audit.get("reviewed_on")
        when_text = f" on {reviewed_on}" if reviewed_on else ""
        return f"{decision} by {reviewer}{when_text}. Review data is captured in the stage details."

    if status == "Stopped":
        return "This downstream review did not proceed because the request was rejected earlier in the flow."

    if audit.get("items"):
        decision = audit.get("decision") or "Recorded"
        reviewer = audit.get("reviewed_by") or owner_role
        reviewed_on = audit.get("reviewed_on")
        when_text = f" on {reviewed_on}" if reviewed_on else ""
        return f"{decision} by {reviewer}{when_text}. Open Details to review the captured checklist."

    return pending_summary


def get_latest_stage_workflow_action(doc, workflow_state: str) -> dict[str, object] | None:
    rows = frappe.db.sql(
        """
        select completed_by, completed_by_role, workflow_state, creation, modified
        from `tabWorkflow Action`
        where reference_doctype = %(doctype)s
          and reference_name = %(name)s
          and ifnull(status, '') = 'Completed'
          and ifnull(workflow_state, '') = %(workflow_state)s
        order by modified desc, creation desc
        limit 1
        """,
        {"doctype": doc.doctype, "name": doc.name, "workflow_state": workflow_state},
        as_dict=True,
    )
    return rows[0] if rows else None


def format_audit_value(value: object) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


def format_audit_timestamp(value: object) -> str:
    if not value:
        return ""
    return frappe.utils.format_datetime(value, "dd-MM-yyyy HH:mm")


def infer_rejected_stage_key(
    doc,
    role_map: dict[str, str],
    workflow_state_map: dict[str, str] | None = None,
) -> str:
    row = frappe.db.sql(
        """
        select completed_by_role, workflow_state
        from `tabWorkflow Action`
        where reference_doctype = %(doctype)s
          and reference_name = %(name)s
          and ifnull(status, '') = 'Completed'
        order by modified desc, creation desc
        limit 1
        """,
        {"doctype": doc.doctype, "name": doc.name},
        as_dict=True,
    )
    if row:
        row = row[0]
        workflow_state = (row.get("workflow_state") or "").strip()
        if workflow_state_map and workflow_state in workflow_state_map:
            return workflow_state_map[workflow_state]
        stage_key = role_map.get((row.get("completed_by_role") or "").strip())
        if stage_key:
            return stage_key
        for key in role_map.values():
            if workflow_state and workflow_state.lower().startswith(key.replace("_", " ").split()[0]):
                return key
    return next(iter(role_map.values()))


def get_supplier_request_matrix_rows(supplier_name: str, item_codes: list[str]) -> list[dict[str, str]]:
    if not supplier_name or not item_codes:
        return []
    return frappe.get_all(
        "Supplier Approval Matrix",
        filters={"supplier": supplier_name, "item_code": ("in", list(set(item_codes)))},
        fields=["name", "item_code", "approval_status"],
        order_by="item_code asc",
    )


def get_supplier_request_planning_links(supplier_name: str, item_codes: list[str]) -> list[dict[str, str]]:
    if not supplier_name or not item_codes:
        return []
    return frappe.get_all(
        "RM Planning Parameter",
        filters={"item_code": ("in", list(set(item_codes))), "preferred_supplier": supplier_name},
        fields=["name", "item_code"],
        order_by="item_code asc",
    )


def make_stage(
    *,
    key: str,
    label: str,
    owner_role: str,
    status: str,
    summary: str,
    documents: list[dict[str, str]] | None = None,
    action: dict[str, object] | None = None,
    message: str = "",
    audit: dict[str, object] | None = None,
    required_fields: list[str] | None = None,
) -> dict[str, object]:
    documents = documents or []
    return {
        "key": key,
        "label": label,
        "owner_role": owner_role,
        "status": status,
        "summary": summary,
        "color": STAGE_COLORS.get(status, "grey"),
        "documents": documents,
        "action": action,
        "message": message or summary,
        "audit": audit or {},
        "required_fields": required_fields or [],
    }


def make_document(doctype: str, name: str, status: str = "", detail: str = "") -> dict[str, str]:
    return {
        "doctype": doctype,
        "name": name,
        "status": status,
        "detail": detail,
    }


def make_open_action(
    doctype: str,
    docnames: list[str],
    scroll_to_field: str | None = None,
    guide_message: str | None = None,
    label: str | None = None,
) -> dict[str, object] | None:
    docnames = [name for name in docnames if name and not str(name).startswith("new-")]
    if not docnames:
        return None
    payload = {
        "action_type": "open_existing",
        "doctype": doctype,
        "docnames": docnames,
    }
    if scroll_to_field:
        payload["scroll_to_field"] = scroll_to_field
    if guide_message:
        payload["guide_message"] = guide_message
    if label:
        payload["label"] = label
    return payload


def make_info_action(message: str) -> dict[str, object]:
    return {
        "action_type": "info",
        "message": message,
    }


def get_stage_scroll_field(doctype: str, stage_key: str) -> str:
    if doctype == "New RM Request":
        return RM_STAGE_SCROLL_FIELDS.get(stage_key, "")
    if doctype == "New Supplier Request":
        return SUPPLIER_STAGE_SCROLL_FIELDS.get(stage_key, "")
    return ""


def guidance_message_for_stage(stage_label: str) -> str:
    messages = {
        "Technical Review": _("Scroll to the Technical Review section and complete all review fields before approving or rejecting."),
        "Document & Sample Readiness": _("Upload the required documents, confirm sample readiness, and complete this stage before Quality Review can begin."),
        "Quality Review": _("Scroll to the Quality Review section and complete all review fields before approving or rejecting."),
        "Purchase Review": _(
            "Scroll to the Purchase Review section, capture commercial feasibility, and create/open a Supplier Request only when supplier development is needed."
        ),
        "Management Review": _("Scroll to the Management Review section and complete all review fields before approving or rejecting."),
        "ERP Creation": _("Scroll to the ERP Creation section to review the generated master-data records."),
    }
    return messages.get(stage_label, _("Open the relevant section for this stage."))
