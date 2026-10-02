from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, today


GOVERNANCE_VERSION = 1
EXPIRING_SOON_DAYS = 30
SUPPLIER_TABLE = "supplier_documents"
SUPPLIER_RM_TABLE = "supplier_rm_documents"
EDITABLE_DOCUMENT_FIELDS = (
    "attachment",
    "document_number",
    "issue_date",
    "expiry_date",
    "verification_status",
    "verification_remarks",
)
VERIFIED_STATUSES = {"Verified", "Expiring Soon"}
NUMBER_REQUIRED_DOCUMENTS = {"GST Registration", "PAN", "MSME / Udyam"}


@dataclass(frozen=True)
class DocumentRule:
    key: str
    document_type: str
    category: str
    scope: str
    requirement: str
    reason: str
    applicable: bool = True
    rm_reference: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "document_key": self.key,
            "document_type": self.document_type,
            "category": self.category,
            "document_scope": self.scope,
            "rm_reference": self.rm_reference,
            "requirement": self.requirement,
            "applicable": 1 if self.applicable else 0,
            "rule_reason": self.reason,
        }


SUPPLIER_RULE_CONFIG = (
    ("REGISTRATION", "Supplier Registration Form", "Onboarding", "Required"),
    ("GST", "GST Registration", "Statutory", "Conditional"),
    ("PAN", "PAN", "Statutory", "Required"),
    ("MSME", "MSME / Udyam", "Statutory", "Conditional"),
    ("AGREEMENT", "Supplier Agreement", "Commercial", "Required"),
    ("BANK_PROOF", "Bank Proof / Cancelled Cheque", "Banking", "Required"),
    ("TAX_TDS", "Tax / TDS Declaration", "Statutory", "Conditional"),
    ("NDA", "NDA", "Compliance", "Conditional"),
    ("CODE_OF_CONDUCT", "Code of Conduct", "Compliance", "Conditional"),
)

RM_RULE_CONFIG = (
    ("TDS", "Technical Data Sheet (TDS)", "Technical", "Required"),
    ("MSDS", "MSDS", "Technical", "Conditional"),
    ("COA", "COA", "Technical", "Required"),
    ("ROHS", "RoHS", "Compliance", "Conditional"),
    ("REACH", "REACH", "Compliance", "Conditional"),
    ("UL", "UL", "Compliance", "Conditional"),
    ("TRIAL", "Trial / Sample Approval", "Trial / Sample", "Conditional"),
)


def governance_enabled(doc) -> bool:
    return cint(doc.get("document_governance_version")) >= GOVERNANCE_VERSION


def initialize_governance_version(doc) -> None:
    if doc.is_new() and not doc.get("document_governance_version"):
        doc.document_governance_version = GOVERNANCE_VERSION


def ensure_document_checklist(doc) -> None:
    """Activate and generate the checklist for an explicitly opened active request."""
    if not governance_enabled(doc):
        doc.document_governance_version = GOVERNANCE_VERSION
    refresh_document_checklist(doc)


def refresh_document_checklist(doc) -> None:
    if not governance_enabled(doc):
        return

    _sync_rules(doc, SUPPLIER_TABLE, build_supplier_rules(doc))
    _sync_rules(doc, SUPPLIER_RM_TABLE, build_supplier_rm_rules(doc))
    _normalize_verification(doc, SUPPLIER_TABLE)
    _normalize_verification(doc, SUPPLIER_RM_TABLE)


def build_supplier_rules(doc) -> list[DocumentRule]:
    if (doc.get("supplier_source") or "") != "Proposed Supplier":
        return []

    supplier_type = (doc.get("supplier_type") or "").strip()
    domestic = (
        (doc.get("supplier_origin") or "Domestic") == "Domestic"
        and supplier_type != "Overseas"
    )
    applicability = {
        "REGISTRATION": True,
        "GST": domestic,
        "PAN": domestic,
        "MSME": domestic and (doc.get("msme_status") or "") == "Yes",
        "AGREEMENT": True,
        "BANK_PROOF": True,
        "TAX_TDS": cint(doc.get("tax_tds_declaration_required")) == 1,
        "NDA": cint(doc.get("nda_required")) == 1,
        "CODE_OF_CONDUCT": cint(doc.get("code_of_conduct_required")) == 1,
    }
    reasons = {
        "REGISTRATION": _("Required for every proposed supplier."),
        "GST": _("Applicable to domestic suppliers."),
        "PAN": _("Required for domestic suppliers."),
        "MSME": _("Applicable when MSME Status is Yes."),
        "AGREEMENT": _("Required before proposed supplier creation."),
        "BANK_PROOF": _("Required before proposed supplier creation."),
        "TAX_TDS": _("Applicable when Tax / TDS Declaration Required is selected."),
        "NDA": _("Applicable when NDA Required is selected."),
        "CODE_OF_CONDUCT": _("Applicable when Code of Conduct Required is selected."),
    }
    return [
        DocumentRule(
            key=f"SUPPLIER:{key}",
            document_type=document_type,
            category=category,
            scope="Supplier",
            requirement=requirement,
            applicable=applicability[key],
            reason=reasons[key],
        )
        for key, document_type, category, requirement in SUPPLIER_RULE_CONFIG
    ]


def build_supplier_rm_rules(doc) -> list[DocumentRule]:
    rules: list[DocumentRule] = []
    for item in doc.get("supplier_request_items") or []:
        rm_reference = (item.get("item_code") or item.get("proposed_rm_code") or "").strip()
        if not rm_reference:
            continue
        category = _resolve_material_category(doc, item)
        applicability = {
            "TDS": True,
            "MSDS": cint(item.get("msds_required")) == 1 or category.lower() != "packing",
            "COA": True,
            "ROHS": cint(item.get("rohs_required")) == 1,
            "REACH": cint(item.get("reach_required")) == 1,
            "UL": cint(item.get("ul_required")) == 1,
            "TRIAL": cint(item.get("trial_approval_required")) == 1,
        }
        reasons = {
            "TDS": _("Required technical evidence for each supplier-RM combination."),
            "MSDS": _("Required for non-packing material unless explicitly classified otherwise."),
            "COA": _("Required quality evidence for each supplier-RM combination."),
            "ROHS": _("Applicable when RoHS Required is selected for this RM."),
            "REACH": _("Applicable when REACH Required is selected for this RM."),
            "UL": _("Applicable when UL Required is selected for this RM."),
            "TRIAL": _("Applicable when Trial Approval Required is selected for this RM."),
        }
        for key, document_type, rule_category, requirement in RM_RULE_CONFIG:
            rules.append(
                DocumentRule(
                    key=f"RM:{rm_reference}:{key}",
                    document_type=document_type,
                    category=rule_category,
                    scope="Supplier-RM",
                    requirement=requirement,
                    applicable=applicability[key],
                    reason=f"{reasons[key]} {_('Material Category')}: {category}.",
                    rm_reference=rm_reference,
                )
            )
    return rules


def _resolve_material_category(doc, item) -> str:
    category = (item.get("material_category") or "").strip()
    if not category and item.get("linked_rm_request"):
        rm_context = frappe.db.get_value(
            "New RM Request",
            item.linked_rm_request,
            ["category", "recommended_material_type"],
            as_dict=True,
        ) or {}
        category = (
            rm_context.get("category")
            or rm_context.get("recommended_material_type")
            or ""
        )
    if not category and doc.get("source_rm_code") in {item.get("item_code"), item.get("proposed_rm_code")}:
        category = (doc.get("source_category") or "").strip()
    category = category or "Raw Material"
    if not item.get("material_category"):
        item.material_category = category
    return category


def _sync_rules(doc, table_field: str, rules: list[DocumentRule]) -> None:
    expected = {rule.key: rule for rule in rules}
    existing: dict[str, object] = {}
    for row in list(doc.get(table_field) or []):
        key = (row.get("document_key") or "").strip()
        if not key or key not in expected or key in existing:
            doc.remove(row)
            continue
        existing[key] = row

    for rule in rules:
        row = existing.get(rule.key)
        if not row:
            row = doc.append(table_field, {})
        for fieldname, value in rule.as_dict().items():
            row.set(fieldname, value)


def _evidence_signature(row) -> tuple[object, ...]:
    return tuple(
        str(row.get(fieldname) or "")
        for fieldname in ("attachment", "document_number", "issue_date", "expiry_date")
    )


def _normalize_verification(doc, table_field: str) -> None:
    before = doc.get_doc_before_save()
    previous = {
        (row.get("document_key") or ""): row
        for row in ((before.get(table_field) or []) if before else [])
    }
    today_date = getdate(today())
    expiring_cutoff = getdate(add_days(today_date, EXPIRING_SOON_DAYS))

    for row in doc.get(table_field) or []:
        row.available = 1 if row.get("attachment") else 0
        status = (row.get("verification_status") or "Pending").strip()
        old_row = previous.get(row.document_key)

        if not row.attachment:
            status = "Pending"
        elif (
            old_row
            and old_row.get("verification_status") in VERIFIED_STATUSES | {"Expired"}
            and _evidence_signature(old_row) != _evidence_signature(row)
            and status in VERIFIED_STATUSES | {"Expired"}
        ):
            status = "Pending"
        elif status in {"Verified", "Expiring Soon", "Expired"}:
            expiry = getdate(row.expiry_date) if row.expiry_date else None
            if expiry and expiry < today_date:
                status = "Expired"
            elif expiry and expiry <= expiring_cutoff:
                status = "Expiring Soon"
            else:
                status = "Verified"

        if status == "Rejected" and not (row.get("verification_remarks") or "").strip():
            frappe.throw(
                _("Verification Remarks are required when {0} is rejected.").format(row.document_type)
            )

        if status in VERIFIED_STATUSES | {"Rejected", "Expired"}:
            if not old_row or old_row.get("verification_status") != status or not row.get("verified_by"):
                row.verified_by = frappe.session.user
                row.verified_on = now_datetime()
        elif status == "Pending":
            row.verified_by = ""
            row.verified_on = None

        row.verification_status = status


def apply_document_updates(doc, table_field: str, rows: object) -> None:
    if table_field not in {SUPPLIER_TABLE, SUPPLIER_RM_TABLE}:
        frappe.throw(_("Unsupported supplier governance document table."))
    incoming = frappe.parse_json(rows) if isinstance(rows, str) else (rows or [])
    existing = {
        (row.get("document_key") or ""): row
        for row in (doc.get(table_field) or [])
    }
    seen: set[str] = set()
    for values in incoming:
        key = (values.get("document_key") or "").strip()
        if not key or key not in existing:
            frappe.throw(_("Supplier governance document rows are system generated and cannot be added manually."))
        if key in seen:
            frappe.throw(_("Duplicate supplier governance document row: {0}.").format(key))
        seen.add(key)
        row = existing[key]
        for fieldname in EDITABLE_DOCUMENT_FIELDS:
            row.set(fieldname, values.get(fieldname))


def validate_quality_document_gate(doc) -> None:
    if not governance_enabled(doc):
        return
    refresh_document_checklist(doc)
    _validate_table(doc, SUPPLIER_RM_TABLE, _("Supplier-RM Documents"))


def validate_purchase_document_gate(doc) -> None:
    if not governance_enabled(doc):
        return
    refresh_document_checklist(doc)
    validate_quality_document_gate(doc)
    if (doc.get("supplier_source") or "") == "Proposed Supplier":
        _validate_supplier_master_data(doc)
        _validate_table(doc, SUPPLIER_TABLE, _("Supplier Documents"))


def validate_complete_document_package(doc) -> None:
    validate_purchase_document_gate(doc)


def validate_supplier_rm_for_item(doc, rm_reference: str) -> None:
    if not governance_enabled(doc):
        return
    refresh_document_checklist(doc)
    _validate_table(doc, SUPPLIER_RM_TABLE, _("Supplier-RM Documents"), rm_reference=rm_reference)


def _validate_table(doc, table_field: str, label: str, rm_reference: str | None = None) -> None:
    failures = []
    for row in doc.get(table_field) or []:
        if rm_reference and row.get("rm_reference") != rm_reference:
            continue
        if not cint(row.get("applicable")) or row.get("requirement") not in {"Required", "Conditional"}:
            continue
        if not row.get("attachment"):
            failures.append(_("{0}: attachment is required").format(row.document_type))
            continue
        if row.document_type in NUMBER_REQUIRED_DOCUMENTS and not (row.get("document_number") or "").strip():
            failures.append(_("{0}: document number is required").format(row.document_type))
        if row.get("verification_status") not in VERIFIED_STATUSES:
            failures.append(
                _("{0}: status must be Verified or Expiring Soon (current: {1})").format(
                    row.document_type,
                    row.get("verification_status") or "Pending",
                )
            )
    if failures:
        frappe.throw(_("{0} are incomplete:<br>{1}").format(label, "<br>".join(failures)))


def _validate_supplier_master_data(doc) -> None:
    required_fields = {
        _("Supplier Origin"): doc.get("supplier_origin"),
        _("MSME Status"): doc.get("msme_status"),
        _("Lead Time"): doc.get("supplier_purchase_lead_time"),
        _("MOQ"): doc.get("supplier_purchase_moq"),
        _("Pack Size"): doc.get("supplier_purchase_pack_size"),
        _("Payment Terms"): doc.get("supplier_purchase_payment_terms"),
        _("Currency"): doc.get("default_currency"),
        _("Freight / Delivery Term"): doc.get("incoterm"),
        _("Address Line 1"): doc.get("proposed_address_line1"),
        _("City / Town"): doc.get("proposed_city"),
        _("Country"): doc.get("proposed_country"),
        _("Primary Contact Name"): doc.get("primary_contact_name"),
        _("Primary Contact Email"): doc.get("primary_contact_email"),
        _("Primary Contact Mobile"): doc.get("primary_contact_mobile"),
        _("Bank Account Name"): doc.get("bank_account_name"),
        _("Bank"): doc.get("bank"),
        _("Bank Account Number"): doc.get("bank_account_no"),
    }
    missing = [label for label, value in required_fields.items() if value in (None, "", [])]
    if missing:
        frappe.throw(
            _("Purchase Review cannot proceed until supplier master data is complete: {0}").format(
                ", ".join(missing)
            )
        )


def get_document_summary(doc, table_field: str) -> str:
    rows = doc.get(table_field) or []
    applicable = [
        row
        for row in rows
        if cint(row.get("applicable")) and row.get("requirement") in {"Required", "Conditional"}
    ]
    verified = [row for row in applicable if row.get("verification_status") in VERIFIED_STATUSES]
    if not rows:
        return _("Legacy request - no structured document checklist")
    return _("{0}/{1} applicable documents verified").format(len(verified), len(applicable))
