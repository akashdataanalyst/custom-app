from __future__ import annotations

import json
from datetime import date, datetime

import frappe
from frappe import _

from calco_erp.calco_purchase.master_data_governance import (
    RESOLUTION_MANUAL_REVIEW,
    get_supplier_request_resolution_context,
    get_supplier_source,
)
from calco_erp.calco_purchase.existing_rm_readiness import (
    FIELDS as EXISTING_RM_TECHNICAL_FIELDS,
    evaluate_existing_rm_readiness,
)
from calco_erp.calco_purchase.legacy_rm_initialization import normalize_code
from calco_erp.calco_purchase.rm_master_operational_readiness import (
    get_rm_operational_readiness_manifest,
)

RM_DOCTYPE = "New RM Request"
SUPPLIER_REQUEST_DOCTYPE = "New Supplier Request"
MATRIX_DOCTYPE = "Supplier Approval Matrix"
PLANNING_DOCTYPE = "RM Planning Parameter"
QUALITY_TEMPLATE_DOCTYPE = "Quality Inspection Template"

OPEN_STATUSES = {"Draft", "Technical Review", "Document & Sample Readiness", "Quality Review", "Purchase Review", "Management Review", "ERP Creation"}
CLOSED_STATUSES = {"Completed", "Rejected", "Cancelled"}
RM_TECHNICAL_COMPLETE_FROM = {"Document & Sample Readiness", "Quality Review", "Purchase Review", "ERP Creation", "Completed"}
RM_QUALITY_COMPLETE_FROM = {"Purchase Review", "ERP Creation", "Completed"}
RM_COMMERCIAL_COMPLETE_FROM = {"ERP Creation", "Completed"}
SUPPLIER_QUALITY_COMPLETE_FROM = {"Purchase Review", "Management Review", "ERP Creation", "Completed"}
SUPPLIER_COMMERCIAL_COMPLETE_FROM = {"Management Review", "ERP Creation", "Completed"}
SUPPLIER_MANAGEMENT_COMPLETE_FROM = {"ERP Creation", "Completed"}

CARD_DEFINITIONS = [
    ("total_governance_items", "Total Governance Items"),
    ("average_operational_readiness", "Operational Readiness Average"),
    ("pending_technical_qualification", "Pending Technical Qualification"),
    ("pending_quality_readiness", "Pending Quality Readiness"),
    ("pending_commercial_review", "Pending Commercial Review"),
    ("pending_management_approval", "Pending Management Approval"),
    ("waiting_supplier_approval_matrix", "Waiting for Supplier Approval Matrix"),
    ("waiting_rm_quality_template", "Waiting for RM Quality Template"),
    ("waiting_rm_planning", "Waiting for RM Planning"),
    ("completed", "Completed"),
    ("rejected", "Rejected"),
]


def _doctype_exists(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def _meta(doctype: str):
    if not _doctype_exists(doctype):
        return None
    return frappe.get_meta(doctype)


def _has_field(doctype: str, fieldname: str) -> bool:
    meta = _meta(doctype)
    return bool(meta and meta.has_field(fieldname))


def _existing_fields(doctype: str, fields: list[str]) -> list[str]:
    meta = _meta(doctype)
    if not meta:
        return []
    standard_fields = {"name", "owner", "creation", "modified", "docstatus"}
    return [field for field in fields if field in standard_fields or meta.has_field(field)]


def _get_value(doctype: str, name_or_filters, fieldname: str):
    if not _doctype_exists(doctype) or not _has_field(doctype, fieldname):
        return None
    return frappe.db.get_value(doctype, name_or_filters, fieldname)


def _safe_doc_value(doc: dict, fieldname: str, default=None):
    value = doc.get(fieldname)
    return default if value is None else value


def _parse_filters(filters: dict) -> dict:
    if filters.get("filters_json"):
        try:
            parsed = json.loads(filters.get("filters_json") or "{}")
            if isinstance(parsed, dict):
                filters.update(parsed)
        except Exception:
            pass
    return filters


@frappe.whitelist()
def get_dashboard_data(*args, **filters) -> dict[str, object]:
    filters = _parse_filters(filters or {})
    rows = build_dashboard_rows()
    rows = apply_filters(rows, filters)
    return {
        "cards": build_cards(rows),
        "rows": rows,
        "filters": build_filter_options(rows),
        "generated_on": frappe.utils.now(),
    }


def build_dashboard_rows() -> list[dict[str, object]]:
    rows = build_request_dashboard_rows()
    governed_rm_codes = {
        normalize_code(row.get("rm_code"))
        for row in rows
        if row.get("doctype") == RM_DOCTYPE and row.get("rm_code")
    }
    rows.extend(build_existing_rm_rows(governed_rm_codes))
    rows.sort(key=lambda row: row.get("modified") or row.get("creation") or "", reverse=True)
    return rows


def build_request_dashboard_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_keys: set[str] = set()

    for rm in get_rm_requests():
        row = build_rm_row(rm)
        key = str(row.get("row_key") or row.get("request_id"))
        if key not in seen_keys:
            rows.append(row)
            seen_keys.add(key)

    linked_supplier_requests = {
        name
        for row in rows
        for name in row.get("linked_supplier_request_names", [])
        if name
    }

    for supplier_request in get_supplier_requests():
        if supplier_request.get("name") in linked_supplier_requests:
            continue
        source_rm_request = supplier_request.get("source_rm_request") if _has_field(SUPPLIER_REQUEST_DOCTYPE, "source_rm_request") else ""
        if source_rm_request and frappe.db.exists(RM_DOCTYPE, source_rm_request):
            continue
        row = build_supplier_request_row(supplier_request)
        key = str(row.get("row_key") or row.get("request_id"))
        if key not in seen_keys:
            rows.append(row)
            seen_keys.add(key)

    return rows


def build_existing_rm_rows(
    governed_rm_codes: set[str] | None = None,
) -> list[dict[str, object]]:
    governed_rm_codes = {
        normalize_code(item_code) for item_code in (governed_rm_codes or set())
    }
    manifest = get_rm_operational_readiness_manifest()
    item_codes = sorted(set(manifest) - governed_rm_codes)
    if not item_codes:
        return []

    item_fields = [
        "name",
        "item_name",
        "owner",
        "creation",
        "modified",
        *EXISTING_RM_TECHNICAL_FIELDS.values(),
    ]
    items = {
        normalize_code(row.name): row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", item_codes)},
            fields=item_fields,
            limit_page_length=0,
        )
    }
    readiness = evaluate_existing_rm_readiness(item_codes)
    return [
        build_existing_rm_row(items[item_code], readiness[item_code])
        for item_code in item_codes
        if item_code in items and item_code in readiness
    ]


def _readiness_components_by_key(readiness: dict) -> dict[str, dict]:
    return {
        component.get("key"): component
        for component in readiness.get("components") or []
    }


def _existing_rm_owner(component: dict, is_ready: bool) -> str:
    if is_ready:
        return "None"
    return {
        "purchase_ready": "Purchase Master Data",
        "quality_ready": "QA/QC",
        "planning_ready": "Planning / Purchase",
        "production_ready": "R&D / Technical",
    }.get(component.get("key"), "Master Data Governance")


def build_existing_rm_row(item: dict, readiness: dict) -> dict[str, object]:
    components = _readiness_components_by_key(readiness)
    purchase = components.get("purchase_ready") or {}
    quality = components.get("quality_ready") or {}
    planning = components.get("planning_ready") or {}
    technical = components.get("production_ready") or {}
    is_ready = bool(readiness.get("ready"))
    overall = "Operational Ready" if is_ready else "In Progress"
    pending_component = next(
        (
            component
            for component in (purchase, quality, planning, technical)
            if component and not component.get("ready")
        ),
        {},
    )
    next_action = "Ready" if is_ready else (
        readiness.get("blockers") or ["Review readiness evidence."]
    )[0]
    suppliers = dedupe(
        [
            evidence.get("supplier")
            for evidence in purchase.get("evidence") or []
            if evidence.get("supplier")
        ]
    )
    has_quality_template = any(
        evidence.get("doctype") == QUALITY_TEMPLATE_DOCTYPE
        for evidence in quality.get("evidence") or []
    )
    item_code = item.get("name") or ""
    row = {
        "row_key": f"existing-rm::{item_code}",
        "governance_type": "Existing RM",
        "request_id": item_code,
        "doctype": "Item",
        "rm_code": item_code,
        "rm_name": item.get("item_name") or "",
        "supplier": ", ".join(suppliers),
        "supplier_selection": "Existing Approved Supplier" if purchase.get("ready") else "Pending",
        "resolution_status": "Resolved",
        "current_stage": "Operational Ready" if is_ready else pending_component.get("label") or "Readiness Review",
        "status": overall,
        "technical_status": "Approved / Ready" if technical.get("ready") else technical.get("status") or "Not Reviewed",
        "quality_readiness_status": quality.get("status") or "Not Ready",
        "commercial_status": "Not Applicable",
        "management_status": "Not Applicable",
        "supplier_approval_matrix_status": "Approved" if purchase.get("ready") else "Pending",
        "rm_quality_template_status": "Ready" if has_quality_template else "Pending",
        "rm_planning_status": "Ready" if planning.get("ready") else "Pending",
        "current_owner": _existing_rm_owner(pending_component, is_ready),
        "owner_user": item.get("owner"),
        "age": get_age_label(item.get("creation")),
        "age_days": get_age_days(item.get("creation")),
        "priority": "Normal" if is_ready else infer_priority(item.get("creation"), "Draft"),
        "creation": str(item.get("creation") or ""),
        "modified": str(item.get("modified") or ""),
        "linked_supplier_request_names": [],
        "actions": [{"label": "Open Item", "doctype": "Item", "name": item_code}],
        "next_action": next_action,
        "readiness_blockers": readiness.get("blockers") or [],
        "readiness_evidence": readiness.get("evidence") or [],
        "technical_decision_by": item.get(EXISTING_RM_TECHNICAL_FIELDS["actor"]),
        "technical_decision_on": str(
            item.get(EXISTING_RM_TECHNICAL_FIELDS["timestamp"]) or ""
        ),
        "technical_evidence": item.get(EXISTING_RM_TECHNICAL_FIELDS["evidence"]) or "",
        "is_open": not is_ready,
        "is_completed": is_ready,
        "is_rejected": False,
        "waiting_supplier_approval_matrix": not bool(purchase.get("ready")),
        "waiting_rm_quality_template": not bool(quality.get("ready")),
        "waiting_rm_planning": not bool(planning.get("ready")),
        "pending_technical_qualification": not bool(technical.get("ready")),
        "pending_quality_readiness": not bool(quality.get("ready")),
        "pending_commercial_review": False,
        "pending_management_approval": False,
        "operational_readiness_percent": frappe.utils.cint(readiness.get("percent")),
        "operational_readiness_overall": overall,
        "operational_readiness_components": readiness.get("components") or [],
    }
    row["journey"] = build_existing_rm_journey(row)
    return row


def build_existing_rm_journey(row: dict) -> list[dict[str, str]]:
    component_owners = {
        "purchase_ready": "Purchase Master Data",
        "quality_ready": "QA/QC",
        "planning_ready": "Planning / Purchase",
        "production_ready": "R&D / Technical",
    }
    journey = [
        {
            "label": component.get("label") or "Readiness",
            "status": component.get("status") or "Not Ready",
            "owner": component_owners.get(
                component.get("key"), "Master Data Governance"
            ),
        }
        for component in row.get("operational_readiness_components") or []
    ]
    journey.append(
        {
            "label": "Operational Readiness",
            "status": (
                f"{row.get('operational_readiness_percent', 0)}% - "
                f"{row.get('operational_readiness_overall')}"
            ),
            "owner": "System",
        }
    )
    return journey


def get_operational_readiness_by_item(item_codes: list[str]) -> dict[str, dict[str, object]]:
    """Expose the existing governance readiness result to recommendation services."""
    requested = {item_code for item_code in item_codes if item_code}
    readiness = {
        item_code: {"ready": False, "overall": "Missing", "percent": 0}
        for item_code in requested
    }
    if not requested:
        return readiness

    governed_items = set()
    for row in build_request_dashboard_rows():
        item_code = row.get("rm_code") or ""
        if item_code not in requested or row.get("doctype") != RM_DOCTYPE:
            continue
        governed_items.add(item_code)
        percentage = frappe.utils.cint(row.get("operational_readiness_percent"))
        overall = row.get("operational_readiness_overall") or "In Progress"
        current = readiness[item_code]
        if overall == "Operational Ready":
            readiness[item_code] = {"ready": True, "overall": overall, "percent": 100}
        elif not current["ready"] and percentage >= frappe.utils.cint(current["percent"]):
            readiness[item_code] = {"ready": False, "overall": overall, "percent": percentage}

    readiness.update(evaluate_existing_rm_readiness(requested - governed_items))
    return readiness


def get_rm_requests() -> list[dict]:
    fields = _existing_fields(
        RM_DOCTYPE,
        [
            "name", "status", "owner", "creation", "modified", "rm_code", "rm_name", "preferred_supplier",
            "created_item", "created_planning_parameter", "created_supplier_matrix", "supplier_request",
            "technical_decision", "document_readiness_decision", "quality_decision", "purchase_decision",
        ],
    )
    if not fields:
        return []
    return frappe.get_all(RM_DOCTYPE, fields=fields, order_by="modified desc", limit_page_length=500)


def get_supplier_requests() -> list[dict]:
    fields = _existing_fields(
        SUPPLIER_REQUEST_DOCTYPE,
        [
            "name", "status", "owner", "creation", "modified", "supplier_name", "source_rm_request",
            "supplier_source", "proposed_supplier_name",
            "source_rm_code", "source_rm_name", "created_supplier", "created_matrix_rows",
            "created_planning_parameters", "supplier_quality_decision", "supplier_purchase_decision",
            "final_approval_decision", "creation_log",
        ],
    )
    if not fields:
        return []
    return frappe.get_all(SUPPLIER_REQUEST_DOCTYPE, fields=fields, order_by="modified desc", limit_page_length=500)


def build_rm_row(rm: dict) -> dict[str, object]:
    linked_requests = get_linked_supplier_requests_for_rm(rm)
    primary_supplier_request = linked_requests[0] if linked_requests else None
    rm_code = _safe_doc_value(rm, "rm_code", "") or ""
    item_code = resolve_item_code(rm_code, _safe_doc_value(rm, "created_item", ""))
    supplier = resolve_rm_supplier(rm, primary_supplier_request)
    matrix_rows = get_matrix_rows(supplier, [item_code, rm_code])
    planning_rows = get_planning_rows([item_code, rm_code])
    quality_template = get_quality_template(item_code or rm_code)
    supplier_selection = infer_supplier_selection(rm, primary_supplier_request, matrix_rows)
    resolution = (
        get_supplier_request_resolution_context(primary_supplier_request)
        if primary_supplier_request
        else {"status": "Resolved" if item_code and frappe.db.exists("Item", item_code) else "Pending"}
    )
    status = _safe_doc_value(rm, "status", "Draft") or "Draft"
    current_stage = get_current_stage(status, primary_supplier_request)

    row = {
        "row_key": f"rm::{rm.get('name')}",
        "governance_type": "Material Governance Request",
        "request_id": rm.get("name"),
        "doctype": RM_DOCTYPE,
        "rm_code": item_code or rm_code,
        "rm_name": _safe_doc_value(rm, "rm_name", "") or _get_value("Item", item_code, "item_name") or "",
        "supplier": supplier or "",
        "supplier_selection": supplier_selection,
        "resolution_status": resolution["status"],
        "current_stage": current_stage,
        "status": status,
        "technical_status": get_rm_technical_status(status),
        "quality_readiness_status": get_rm_quality_status(status, quality_template),
        "commercial_status": get_rm_commercial_status(status),
        "management_status": get_supplier_management_status(primary_supplier_request),
        "supplier_approval_matrix_status": get_matrix_status(matrix_rows, supplier, item_code or rm_code),
        "rm_quality_template_status": "Ready" if quality_template else "Pending",
        "rm_planning_status": "Ready" if planning_rows else "Pending",
        "current_owner": "Purchase Master Data" if resolution["status"] == RESOLUTION_MANUAL_REVIEW else get_stage_owner(status, primary_supplier_request),
        "owner_user": rm.get("owner"),
        "age": get_age_label(rm.get("creation")),
        "age_days": get_age_days(rm.get("creation")),
        "priority": infer_priority(rm.get("creation"), status),
        "creation": str(rm.get("creation") or ""),
        "modified": str(rm.get("modified") or ""),
        "linked_supplier_request_names": [req.get("name") for req in linked_requests if req.get("name")],
        "actions": build_actions(rm.get("name"), RM_DOCTYPE, supplier, linked_requests, matrix_rows, quality_template, planning_rows),
    }
    row.update(build_wait_flags(row))
    row.update(build_operational_readiness(row))
    row["journey"] = build_journey(row)
    return row


def build_supplier_request_row(doc: dict) -> dict[str, object]:
    requested_items = get_supplier_request_item_codes(doc.get("name"))
    rm_code = doc.get("source_rm_code") or (requested_items[0] if requested_items else "")
    item_code = resolve_item_code(rm_code, "")
    supplier = doc.get("created_supplier") or doc.get("supplier_name") or doc.get("proposed_supplier_name") or ""
    resolved_supplier = doc.get("created_supplier") or doc.get("supplier_name") or ""
    resolution = get_supplier_request_resolution_context(doc)
    matrix_rows = get_matrix_rows(resolved_supplier, requested_items or [item_code, rm_code])
    planning_rows = get_planning_rows(requested_items or [item_code, rm_code])
    quality_template = get_quality_template(item_code or rm_code)
    status = doc.get("status") or "Draft"
    supplier_selection = infer_supplier_selection({}, doc, matrix_rows)
    row = {
        "row_key": f"supplier-request::{doc.get('name')}",
        "governance_type": "Supplier Approval Request",
        "request_id": doc.get("name"),
        "doctype": SUPPLIER_REQUEST_DOCTYPE,
        "rm_code": item_code or rm_code,
        "rm_name": doc.get("source_rm_name") or _get_value("Item", item_code, "item_name") or "",
        "supplier": supplier,
        "supplier_selection": supplier_selection,
        "resolution_status": resolution["status"],
        "current_stage": get_supplier_request_stage(status),
        "status": status,
        "technical_status": "Not Applicable",
        "quality_readiness_status": get_supplier_quality_status(status),
        "commercial_status": get_supplier_commercial_status(status),
        "management_status": get_supplier_management_status(doc),
        "supplier_approval_matrix_status": get_matrix_status(matrix_rows, resolved_supplier, item_code or rm_code),
        "rm_quality_template_status": "Ready" if quality_template else "Pending",
        "rm_planning_status": "Ready" if planning_rows else "Pending",
        "current_owner": "Purchase Master Data" if resolution["status"] == RESOLUTION_MANUAL_REVIEW else get_stage_owner(status, doc),
        "owner_user": doc.get("owner"),
        "age": get_age_label(doc.get("creation")),
        "age_days": get_age_days(doc.get("creation")),
        "priority": infer_priority(doc.get("creation"), status),
        "creation": str(doc.get("creation") or ""),
        "modified": str(doc.get("modified") or ""),
        "linked_supplier_request_names": [doc.get("name")] if doc.get("name") else [],
        "actions": build_actions(doc.get("name"), SUPPLIER_REQUEST_DOCTYPE, resolved_supplier, [doc], matrix_rows, quality_template, planning_rows),
    }
    row.update(build_wait_flags(row))
    row.update(build_operational_readiness(row))
    row["journey"] = build_journey(row)
    return row


def get_linked_supplier_requests_for_rm(rm: dict) -> list[dict]:
    names: list[str] = []
    direct = rm.get("supplier_request") if _has_field(RM_DOCTYPE, "supplier_request") else ""
    if direct:
        names.append(direct)
    if _has_field(SUPPLIER_REQUEST_DOCTYPE, "source_rm_request"):
        reverse_names = frappe.get_all(
            SUPPLIER_REQUEST_DOCTYPE,
            filters={"source_rm_request": rm.get("name")},
            pluck="name",
            order_by="modified desc",
            limit_page_length=20,
        )
        names.extend(reverse_names)

    deduped = []
    seen = set()
    fields = _existing_fields(
        SUPPLIER_REQUEST_DOCTYPE,
        [
            "name", "status", "owner", "creation", "modified", "supplier_name", "source_rm_request",
            "supplier_source", "proposed_supplier_name",
            "source_rm_code", "source_rm_name", "created_supplier", "created_matrix_rows",
            "created_planning_parameters", "supplier_quality_decision", "supplier_purchase_decision",
            "final_approval_decision", "creation_log",
        ],
    )
    for name in names:
        if not name or name in seen or not frappe.db.exists(SUPPLIER_REQUEST_DOCTYPE, name):
            continue
        seen.add(name)
        values = frappe.db.get_value(SUPPLIER_REQUEST_DOCTYPE, name, fields, as_dict=True)
        if values:
            deduped.append(values)
    return deduped


def resolve_rm_supplier(rm: dict, supplier_request: dict | None) -> str:
    for value in (rm.get("preferred_supplier"), supplier_request and supplier_request.get("created_supplier"), supplier_request and supplier_request.get("supplier_name")):
        if value:
            return value
    return ""


def resolve_item_code(rm_code: str, created_item: str | None) -> str:
    if created_item and frappe.db.exists("Item", created_item):
        return created_item
    if rm_code and frappe.db.exists("Item", rm_code):
        return rm_code
    return rm_code or ""


def get_supplier_request_item_codes(name: str | None) -> list[str]:
    if not name or not _doctype_exists("Supplier Request Item"):
        return []
    fields = ["item_code"]
    if _has_field("Supplier Request Item", "proposed_rm_code"):
        fields.append("proposed_rm_code")
    rows = frappe.get_all("Supplier Request Item", filters={"parent": name}, fields=fields, order_by="idx asc")
    return dedupe(
        [
            row.get("item_code") or row.get("proposed_rm_code")
            for row in rows
            if row.get("item_code") or row.get("proposed_rm_code")
        ]
    )


def get_matrix_rows(supplier: str | None, item_codes: list[str]) -> list[dict]:
    supplier = supplier or ""
    items = dedupe([item for item in item_codes if item])
    if not supplier or not items or not _doctype_exists(MATRIX_DOCTYPE):
        return []
    rows = frappe.get_all(
        MATRIX_DOCTYPE,
        filters={"supplier": supplier, "item_code": ["in", items]},
        fields=["name", "item_code", "supplier", "approval_status"],
        order_by="modified desc",
        limit_page_length=50,
    )
    return rows


def get_planning_rows(item_codes: list[str]) -> list[dict]:
    items = dedupe([item for item in item_codes if item])
    if not items or not _doctype_exists(PLANNING_DOCTYPE):
        return []
    fields = _existing_fields(PLANNING_DOCTYPE, ["name", "item_code", "preferred_supplier", "is_active"])
    return frappe.get_all(
        PLANNING_DOCTYPE,
        filters={"item_code": ["in", items]},
        fields=fields,
        order_by="modified desc",
        limit_page_length=20,
    )


def get_quality_template(item_code: str | None) -> str:
    if not item_code or not frappe.db.exists("Item", item_code):
        return ""
    template = _get_value("Item", item_code, "quality_inspection_template") or ""
    if template and _doctype_exists(QUALITY_TEMPLATE_DOCTYPE) and frappe.db.exists(QUALITY_TEMPLATE_DOCTYPE, template):
        return template
    return template or ""


def infer_supplier_selection(rm: dict, supplier_request: dict | None, matrix_rows: list[dict]) -> str:
    if any((row.get("approval_status") or "") == "Approved" for row in matrix_rows) and not supplier_request:
        return "Existing Approved Supplier"
    if supplier_request:
        if get_supplier_source(supplier_request) == "Proposed Supplier":
            return "Proposed Supplier Approval"
        if supplier_request.get("supplier_name") or rm.get("preferred_supplier"):
            return "Existing Supplier - New RM Approval"
    if any((row.get("approval_status") or "") == "Approved" for row in matrix_rows):
        return "Existing Approved Supplier"
    return "Legacy / Not Inferred"


def get_current_stage(status: str, supplier_request: dict | None) -> str:
    if supplier_request and (supplier_request.get("status") or "") not in CLOSED_STATUSES:
        return "Supplier Approval Request"
    return status or "Draft"


def get_supplier_request_stage(status: str) -> str:
    return status or "Draft"


def get_rm_technical_status(status: str) -> str:
    if status in RM_TECHNICAL_COMPLETE_FROM:
        return "Completed"
    if status == "Technical Review":
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_rm_quality_status(status: str, quality_template: str) -> str:
    if quality_template:
        return "Ready"
    if status in RM_QUALITY_COMPLETE_FROM:
        return "Completed"
    if status in {"Document & Sample Readiness", "Quality Review"}:
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_rm_commercial_status(status: str) -> str:
    if status in RM_COMMERCIAL_COMPLETE_FROM:
        return "Completed"
    if status == "Purchase Review":
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_supplier_quality_status(status: str) -> str:
    if status in SUPPLIER_QUALITY_COMPLETE_FROM:
        return "Completed"
    if status == "Quality Review":
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_supplier_commercial_status(status: str) -> str:
    if status in SUPPLIER_COMMERCIAL_COMPLETE_FROM:
        return "Completed"
    if status == "Purchase Review":
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_supplier_management_status(supplier_request: dict | None) -> str:
    if not supplier_request:
        return "Not Applicable"
    status = supplier_request.get("status") or "Draft"
    if status in SUPPLIER_MANAGEMENT_COMPLETE_FROM:
        return "Completed"
    if status == "Management Review":
        return "Pending"
    if status == "Rejected":
        return "Rejected"
    return "Not Started"


def get_matrix_status(matrix_rows: list[dict], supplier: str | None, item_code: str | None) -> str:
    if not supplier or not item_code:
        return "Not Linked"
    if any((row.get("approval_status") or "") == "Approved" for row in matrix_rows):
        return "Approved"
    if matrix_rows:
        statuses = dedupe([row.get("approval_status") or "Pending" for row in matrix_rows])
        return ", ".join(statuses)
    return "Pending"


def get_stage_owner(status: str, supplier_request: dict | None) -> str:
    if supplier_request and (supplier_request.get("status") or "") not in CLOSED_STATUSES:
        status = supplier_request.get("status") or status
    mapping = {
        "Draft": "Requester",
        "Technical Review": "Technical",
        "Document & Sample Readiness": "Quality",
        "Quality Review": "Quality",
        "Purchase Review": "Purchase",
        "Management Review": "Management",
        "ERP Creation": "Purchase Master Data",
        "Completed": "Completed",
        "Rejected": "Rejected",
        "Cancelled": "Cancelled",
    }
    return mapping.get(status or "", "Requester")


def build_wait_flags(row: dict) -> dict[str, bool]:
    status = row.get("status") or ""
    is_terminal = status in CLOSED_STATUSES
    return {
        "is_open": not is_terminal,
        "is_completed": status == "Completed",
        "is_rejected": status in {"Rejected", "Cancelled"},
        "waiting_supplier_approval_matrix": not is_terminal and row.get("supplier_approval_matrix_status") not in {"Approved"},
        "waiting_rm_quality_template": not is_terminal and row.get("rm_quality_template_status") != "Ready",
        "waiting_rm_planning": not is_terminal and row.get("rm_planning_status") != "Ready",
        "pending_technical_qualification": row.get("technical_status") == "Pending",
        "pending_quality_readiness": row.get("quality_readiness_status") == "Pending",
        "pending_commercial_review": row.get("commercial_status") == "Pending",
        "pending_management_approval": row.get("management_status") == "Pending",
    }



def build_operational_readiness(row: dict) -> dict[str, object]:
    components = [
        {
            "key": "purchase_ready",
            "label": "Purchase Ready",
            "ready": row.get("supplier_approval_matrix_status") == "Approved",
            "status": row.get("supplier_approval_matrix_status") or "Pending",
        },
        {
            "key": "quality_ready",
            "label": "Quality Ready",
            "ready": row.get("quality_readiness_status") in {"Ready", "Completed"} and row.get("rm_quality_template_status") == "Ready",
            "status": f"{row.get('quality_readiness_status') or 'Pending'} / Template {row.get('rm_quality_template_status') or 'Pending'}",
        },
        {
            "key": "planning_ready",
            "label": "Planning Ready",
            "ready": row.get("rm_planning_status") == "Ready",
            "status": row.get("rm_planning_status") or "Pending",
        },
        {
            "key": "production_ready",
            "label": "Production Ready",
            "ready": row.get("technical_status") in {"Ready", "Completed"} and row.get("quality_readiness_status") in {"Ready", "Completed"},
            "status": "Ready" if row.get("technical_status") in {"Ready", "Completed"} and row.get("quality_readiness_status") in {"Ready", "Completed"} else "Pending",
        },
    ]
    ready_count = sum(1 for component in components if component["ready"])
    percentage = round((ready_count / len(components)) * 100) if components else 0
    if row.get("is_rejected"):
        overall = "Blocked"
    elif percentage == 100:
        overall = "Operational Ready"
    elif percentage == 0:
        overall = "Not Started"
    else:
        overall = "In Progress"
    return {
        "operational_readiness_percent": percentage,
        "operational_readiness_overall": overall,
        "operational_readiness_components": components,
    }


def build_journey(row: dict) -> list[dict[str, str]]:
    stages = [
        ("Request Created", row.get("status") or "Draft", "Requester"),
        ("Supplier / Procurement Strategy", row.get("supplier_selection") or "Not Selected", "Purchase"),
        ("Technical Qualification", row.get("technical_status") or "Not Started", "R&D / Technical"),
        ("Quality Readiness", row.get("quality_readiness_status") or "Not Started", "QA/QC"),
        ("Commercial Review", row.get("commercial_status") or "Not Started", "Purchase"),
        ("Management Approval", row.get("management_status") or "Not Started", "COO / Management"),
        ("Governance Outputs", row.get("resolution_status") or "Pending", "Purchase Master Data / System"),
        ("Supplier Approval Matrix", row.get("supplier_approval_matrix_status") or "Pending", "Purchase Master Data / System"),
        ("RM Quality Inspection Template", row.get("rm_quality_template_status") or "Pending", "QA/QC / System"),
        ("RM Planning Readiness", row.get("rm_planning_status") or "Pending", "Planning / Purchase"),
        ("Operational Readiness", f"{row.get('operational_readiness_percent', 0)}% - {row.get('operational_readiness_overall') or 'In Progress'}", "System"),
    ]
    journey = []
    seen = set()
    for label, status, owner in stages:
        if label in seen:
            continue
        seen.add(label)
        journey.append({"label": label, "status": status, "owner": owner})
    return journey
def build_actions(request_name, request_doctype, supplier, supplier_requests, matrix_rows, quality_template, planning_rows):
    actions = [{"label": "Open / Continue Request", "doctype": request_doctype, "name": request_name}]
    for supplier_request in supplier_requests or []:
        if supplier_request.get("name"):
            actions.append({"label": "Open Supplier Approval Request", "doctype": SUPPLIER_REQUEST_DOCTYPE, "name": supplier_request.get("name")})
    if supplier and frappe.db.exists("Supplier", supplier):
        actions.append({"label": "Open Supplier", "doctype": "Supplier", "name": supplier})
    for row in matrix_rows or []:
        actions.append({"label": "Open Supplier Approval Matrix", "doctype": MATRIX_DOCTYPE, "name": row.get("name")})
    if quality_template:
        actions.append({"label": "Open RM Quality Inspection Template", "doctype": QUALITY_TEMPLATE_DOCTYPE, "name": quality_template})
    for row in planning_rows or []:
        actions.append({"label": "Open RM Planning Parameter", "doctype": PLANNING_DOCTYPE, "name": row.get("name")})
    return dedupe_actions(actions)


def build_cards(rows: list[dict]) -> list[dict[str, object]]:
    return [
        {"key": key, "label": label, "value": get_card_value(key, rows)}
        for key, label in CARD_DEFINITIONS
    ]


def get_card_value(key: str, rows: list[dict]):
    if key == "total_governance_items":
        return len(rows)
    if key == "average_operational_readiness":
        if not rows:
            return "0%"
        average = round(sum(frappe.utils.cint(row.get("operational_readiness_percent")) for row in rows) / len(rows))
        return f"{average}%"
    if key == "completed":
        return sum(1 for row in rows if row.get("is_completed"))
    if key == "rejected":
        return sum(1 for row in rows if row.get("is_rejected"))
    return sum(1 for row in rows if row.get(key))


def build_filter_options(rows: list[dict]) -> dict[str, list[str]]:
    return {
        "governance_type": sorted({row.get("governance_type") for row in rows if row.get("governance_type")}),
        "supplier_selection": sorted({row.get("supplier_selection") for row in rows if row.get("supplier_selection")}),
        "current_stage": sorted({row.get("current_stage") for row in rows if row.get("current_stage")}),
        "status": sorted({row.get("status") for row in rows if row.get("status")}),
        "owner": sorted({row.get("current_owner") for row in rows if row.get("current_owner")}),
        "priority": ["High", "Medium", "Normal"],
    }


def apply_filters(rows: list[dict], filters: dict) -> list[dict]:
    search = (filters.get("search") or filters.get("search_text") or "").strip().lower()
    governance_type = (filters.get("governance_type") or "").strip()
    supplier_selection = (filters.get("supplier_selection") or "").strip()
    current_stage = (filters.get("current_stage") or "").strip()
    status = (filters.get("status") or "").strip()
    owner = (filters.get("owner") or "").strip()
    priority = (filters.get("priority") or "").strip()
    only_my_pending = frappe.utils.cint(filters.get("only_my_pending_actions"))
    user = frappe.session.user

    card_key = (filters.get("card_key") or "").strip()

    filtered = []
    for row in rows:
        if card_key and not row_matches_card(row, card_key):
            continue
        if search and search not in " ".join(str(row.get(field) or "").lower() for field in ["request_id", "rm_code", "rm_name", "supplier", "supplier_selection", "current_stage"]):
            continue
        if governance_type and row.get("governance_type") != governance_type:
            continue
        if supplier_selection and row.get("supplier_selection") != supplier_selection:
            continue
        if current_stage and row.get("current_stage") != current_stage:
            continue
        if status and row.get("status") != status:
            continue
        if owner and row.get("current_owner") != owner:
            continue
        if priority and row.get("priority") != priority:
            continue
        if only_my_pending and not (row.get("owner_user") == user and row.get("is_open")):
            continue
        filtered.append(row)
    return filtered



def row_matches_card(row: dict, card_key: str) -> bool:
    if card_key in {"", "total_governance_items"}:
        return True
    if card_key == "average_operational_readiness":
        return frappe.utils.cint(row.get("operational_readiness_percent")) < 100
    if card_key == "completed":
        return bool(row.get("is_completed"))
    if card_key == "rejected":
        return bool(row.get("is_rejected"))
    return bool(row.get(card_key))
def get_age_days(value) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        value = frappe.utils.get_datetime(value)
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return 0
    return max((frappe.utils.getdate(frappe.utils.today()) - value).days, 0)


def get_age_label(value) -> str:
    days = get_age_days(value)
    return f"{days}d"


def infer_priority(value, status: str) -> str:
    if status in CLOSED_STATUSES:
        return "Normal"
    days = get_age_days(value)
    if days >= 30:
        return "High"
    if days >= 14:
        return "Medium"
    return "Normal"


def dedupe(values: list) -> list:
    result = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def dedupe_actions(actions: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for action in actions:
        key = (action.get("label"), action.get("doctype"), action.get("name"))
        if not action.get("name") or key in seen:
            continue
        result.append(action)
        seen.add(key)
    return result
