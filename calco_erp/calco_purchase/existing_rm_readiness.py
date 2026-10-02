from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe import _
from frappe.utils import cint, cstr, getdate, now_datetime

from calco_erp.calco_purchase.legacy_rm_initialization import normalize_code
from calco_erp.calco_purchase.rm_master_operational_readiness import (
    get_rm_operational_readiness_manifest,
    is_rm_foundation_initialization,
)


TECHNICAL_ROLE = "Technical User"
NOT_REVIEWED = "Not Reviewed"
APPROVED = "Approved"
REJECTED = "Rejected"
REVOKED = "Revoked"

FIELDS = {
    "status": "custom_existing_rm_technical_status",
    "decision": "custom_existing_rm_technical_decision",
    "actor": "custom_existing_rm_technical_approved_by",
    "timestamp": "custom_existing_rm_technical_approved_on",
    "remarks": "custom_existing_rm_technical_remarks",
    "evidence": "custom_existing_rm_technical_evidence",
}
CONTROLLED_FIELDS = tuple(FIELDS.values())


def ensure_existing_rm_technical_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "Item": [
                {
                    "fieldname": "custom_existing_rm_technical_section",
                    "label": "Existing RM Technical Certification",
                    "fieldtype": "Section Break",
                    "insert_after": "quality_inspection_template",
                    "collapsible": 1,
                },
                {
                    "fieldname": FIELDS["status"],
                    "label": "Technical Certification Status",
                    "fieldtype": "Select",
                    "options": f"{NOT_REVIEWED}\n{APPROVED}\n{REJECTED}\n{REVOKED}",
                    "default": NOT_REVIEWED,
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": "custom_existing_rm_technical_section",
                },
                {
                    "fieldname": FIELDS["decision"],
                    "label": "Latest Technical Decision",
                    "fieldtype": "Select",
                    "options": f"\n{APPROVED}\n{REJECTED}\n{REVOKED}",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": FIELDS["status"],
                },
                {
                    "fieldname": FIELDS["actor"],
                    "label": "Technical Decision By",
                    "fieldtype": "Link",
                    "options": "User",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": FIELDS["decision"],
                },
                {
                    "fieldname": FIELDS["timestamp"],
                    "label": "Technical Decision On",
                    "fieldtype": "Datetime",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": FIELDS["actor"],
                },
                {
                    "fieldname": FIELDS["remarks"],
                    "label": "Technical Decision Remarks",
                    "fieldtype": "Small Text",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": FIELDS["timestamp"],
                },
                {
                    "fieldname": FIELDS["evidence"],
                    "label": "Technical Evidence / Reference",
                    "fieldtype": "Small Text",
                    "read_only": 1,
                    "no_copy": 1,
                    "insert_after": FIELDS["remarks"],
                },
            ]
        },
        update=True,
    )
    frappe.clear_cache(doctype="Item")


def is_eligible_existing_rm(item_code: str) -> bool:
    return normalize_code(item_code) in get_rm_operational_readiness_manifest()


def validate_existing_rm_technical_fields(doc, method=None):
    """Prevent normal Item saves from changing server-controlled certification fields."""
    if getattr(doc.flags, "existing_rm_technical_action", False):
        return

    previous = doc.get_doc_before_save()
    if not previous:
        status = cstr(doc.get(FIELDS["status"])).strip()
        other_values = [
            cstr(doc.get(fieldname)).strip() for fieldname in CONTROLLED_FIELDS[1:]
        ]
        if status in {"", NOT_REVIEWED} and not any(other_values):
            return
        frappe.throw(
            _("Existing RM Technical Certification can only be changed through its controlled actions.")
        )

    changed = [
        fieldname
        for fieldname in CONTROLLED_FIELDS
        if cstr(doc.get(fieldname)) != cstr(previous.get(fieldname))
    ]
    if changed:
        frappe.throw(
            _("Existing RM Technical Certification can only be changed through its controlled actions.")
        )


def _component(key, label, ready, status, blockers=None, evidence=None):
    return {
        "key": key,
        "label": label,
        "ready": bool(ready),
        "status": status,
        "blockers": blockers or [],
        "evidence": evidence or [],
    }


def _purchase_component(item, matrices, enabled_suppliers, today):
    blockers = []
    if not item:
        blockers.append("Item does not exist.")
    else:
        if cint(item.get("disabled")):
            blockers.append("Item is disabled.")
        if not cint(item.get("is_stock_item")):
            blockers.append("Item is not a stock Item.")
        if item.get("item_group") != "Raw Material":
            blockers.append("Item Group is not Raw Material.")

    valid = []
    for row in matrices:
        supplier = row.get("supplier")
        effective = getdate(row.get("effective_date")) if row.get("effective_date") else None
        expiry = getdate(row.get("expiry_date")) if row.get("expiry_date") else None
        if (
            row.get("approval_status") == APPROVED
            and supplier in enabled_suppliers
            and effective
            and effective <= today
            and (not expiry or expiry >= today)
        ):
            valid.append(row)
    if not valid:
        blockers.append(
            "No currently valid approved Supplier Approval Matrix with an enabled Supplier."
        )
    return _component(
        "purchase_ready",
        "Purchase Ready",
        not blockers,
        "Ready" if not blockers else "Not Ready",
        blockers,
        [
            {
                "doctype": "Supplier Approval Matrix",
                "name": row.get("name"),
                "supplier": row.get("supplier"),
            }
            for row in valid
        ],
    )


def _quality_component(item, standards):
    template = cstr((item or {}).get("quality_inspection_template")).strip()
    matching = [
        row
        for row in standards
        if cint(row.get("is_active"))
        and row.get("quality_inspection_template") == template
    ]
    blockers = []
    if not template:
        blockers.append("Item has no Quality Inspection Template.")
    if template and not matching:
        blockers.append(
            "No active RM Testing Standard matches the Item and Quality Inspection Template."
        )
    evidence = []
    if template:
        evidence.append({"doctype": "Quality Inspection Template", "name": template})
    evidence.extend(
        {"doctype": "RM Testing Standard", "name": row.get("name")}
        for row in matching
    )
    return _component(
        "quality_ready",
        "Quality Ready",
        not blockers,
        "Ready" if not blockers else "Not Ready",
        blockers,
        evidence,
    )


def _planning_component(rows):
    active = [row for row in rows if cint(row.get("is_active"))]
    blockers = [] if active else ["No active RM Planning Parameter exists for the exact Item."]
    return _component(
        "planning_ready",
        "Planning Ready",
        bool(active),
        "Ready" if active else "Not Ready",
        blockers,
        [
            {"doctype": "RM Planning Parameter", "name": row.get("name")}
            for row in active
        ],
    )


def _technical_component(item):
    status = cstr((item or {}).get(FIELDS["status"])).strip() or NOT_REVIEWED
    required = [
        FIELDS["actor"],
        FIELDS["timestamp"],
        FIELDS["remarks"],
        FIELDS["evidence"],
    ]
    complete = status == APPROVED and all(
        cstr((item or {}).get(fieldname)).strip() for fieldname in required
    )
    blockers = []
    if not complete:
        blockers.append(f"Existing RM Technical Certification is {status}.")
    evidence = []
    if complete:
        evidence.append(
            {
                "doctype": "Item",
                "name": item.get("name"),
                "approved_by": item.get(FIELDS["actor"]),
                "approved_on": str(item.get(FIELDS["timestamp"]) or ""),
                "reference": item.get(FIELDS["evidence"]),
            }
        )
    return _component(
        "production_ready",
        "Production Ready",
        complete,
        "Ready" if complete else status,
        blockers,
        evidence,
    )


def _apply_foundation_initialization(components, item):
    if not is_rm_foundation_initialization(item):
        return False
    evidence = {
        "doctype": "Item",
        "name": item.get("name"),
        "reference": item.get(FIELDS["evidence"]),
        "approved_by": item.get(FIELDS["actor"]),
        "approved_on": str(item.get(FIELDS["timestamp"]) or ""),
    }
    for component in components:
        if component["ready"]:
            continue
        component.update(
            {
                "ready": True,
                "status": "Initialized for Existing RM",
                "blockers": [],
                "evidence": [evidence],
            }
        )
    return True


def evaluate_existing_rm_readiness(
    item_codes: Iterable[str],
) -> dict[str, dict[str, object]]:
    original_to_canonical = {
        item_code: normalize_code(item_code)
        for item_code in item_codes
        if cstr(item_code).strip()
    }
    if not original_to_canonical:
        return {}

    manifest = get_rm_operational_readiness_manifest()
    codes = sorted(set(original_to_canonical.values()))
    item_fields = [
        "name",
        "disabled",
        "is_stock_item",
        "item_group",
        "quality_inspection_template",
        *CONTROLLED_FIELDS,
    ]
    items = {
        normalize_code(row.name): row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", codes)},
            fields=item_fields,
            limit_page_length=0,
        )
    }
    matrices_by_item = {code: [] for code in codes}
    matrices = frappe.get_all(
        "Supplier Approval Matrix",
        filters={"item_code": ("in", codes), "approval_status": APPROVED},
        fields=[
            "name",
            "item_code",
            "supplier",
            "approval_status",
            "effective_date",
            "expiry_date",
        ],
        limit_page_length=0,
    )
    for row in matrices:
        matrices_by_item.setdefault(normalize_code(row.item_code), []).append(row)
    suppliers = {row.supplier for row in matrices if row.get("supplier")}
    enabled_suppliers = (
        set(
            frappe.get_all(
                "Supplier",
                filters={"name": ("in", sorted(suppliers)), "disabled": 0},
                pluck="name",
                limit_page_length=0,
            )
        )
        if suppliers
        else set()
    )

    standards_by_item = {code: [] for code in codes}
    standards = frappe.get_all(
        "RM Testing Standard",
        filters={"rm_item": ("in", codes), "is_active": 1},
        fields=["name", "rm_item", "quality_inspection_template", "is_active"],
        limit_page_length=0,
    )
    for row in standards:
        standards_by_item.setdefault(normalize_code(row.rm_item), []).append(row)

    planning_by_item = {code: [] for code in codes}
    planning = frappe.get_all(
        "RM Planning Parameter",
        filters={"item_code": ("in", codes), "is_active": 1},
        fields=["name", "item_code", "is_active"],
        limit_page_length=0,
    )
    for row in planning:
        planning_by_item.setdefault(normalize_code(row.item_code), []).append(row)

    today = getdate()
    result = {}
    for original, code in original_to_canonical.items():
        item = items.get(code)
        if code not in manifest:
            result[original] = {
                "ready": False,
                "overall": "Not Ready",
                "percent": 0,
                "source": "Existing RM Evidence",
                "components": [],
                "blockers": [
                    "Item is outside the approved Existing RM certification eligibility manifest."
                ],
                "evidence": [],
            }
            continue
        components = [
            _purchase_component(
                item, matrices_by_item.get(code, []), enabled_suppliers, today
            ),
            _quality_component(item, standards_by_item.get(code, [])),
            _planning_component(planning_by_item.get(code, [])),
            _technical_component(item),
        ]
        foundation_initialized = _apply_foundation_initialization(components, item)
        ready_count = sum(1 for component in components if component["ready"])
        blockers = [
            blocker for component in components for blocker in component["blockers"]
        ]
        evidence = [
            entry for component in components for entry in component["evidence"]
        ]
        result[original] = {
            "ready": ready_count == 4,
            "overall": "Operational Ready" if ready_count == 4 else "Not Ready",
            "percent": ready_count * 25,
            "source": (
                "RM Master Operational Readiness Foundation"
                if foundation_initialized
                else "Existing RM Evidence"
            ),
            "components": components,
            "blockers": blockers,
            "evidence": evidence,
            "canonical_rm_code": code,
        }
    return result


def _require_technical_user():
    if TECHNICAL_ROLE not in frappe.get_roles(frappe.session.user):
        frappe.throw(
            _("Only a user with the Technical User role may make this decision."),
            frappe.PermissionError,
        )


def _validate_certifiable_item(doc):
    if not is_eligible_existing_rm(doc.name):
        frappe.throw(
            _(
                "Item {0} is outside the approved Existing RM certification eligibility manifest."
            ).format(doc.name)
        )
    if cint(doc.disabled) or not cint(doc.is_stock_item) or doc.item_group != "Raw Material":
        frappe.throw(
            _("Only an enabled Raw Material stock Item can be technically certified.")
        )


def _audit_comment(previous_status, new_status, remarks, evidence, timestamp):
    values = [
        "Existing RM Technical Certification",
        f"Previous Status: {previous_status}",
        f"New Status: {new_status}",
        f"Decision By: {frappe.session.user}",
        f"Decision On: {timestamp}",
        f"Remarks: {remarks}",
    ]
    if evidence:
        values.append(f"Evidence / Reference: {evidence}")
    return "<br>".join(
        frappe.utils.escape_html(cstr(value)) for value in values
    )


@frappe.whitelist()
def apply_existing_rm_technical_decision(
    item_code, decision, remarks, evidence=None
):
    _require_technical_user()
    decision = cstr(decision).strip().title()
    remarks = cstr(remarks).strip()
    evidence = cstr(evidence).strip()
    if decision not in {APPROVED, REJECTED, REVOKED}:
        frappe.throw(_("Decision must be Approved, Rejected, or Revoked."))
    if not remarks:
        frappe.throw(_("Technical decision remarks are mandatory."))
    if decision == APPROVED and not evidence:
        frappe.throw(_("Technical evidence or reference is mandatory for approval."))

    doc = frappe.get_doc("Item", item_code)
    _validate_certifiable_item(doc)
    previous_status = cstr(doc.get(FIELDS["status"])).strip() or NOT_REVIEWED
    if decision == REVOKED and previous_status != APPROVED:
        frappe.throw(
            _("Only an Approved Existing RM Technical Certification can be revoked.")
        )

    timestamp = now_datetime()
    doc.set(FIELDS["status"], decision)
    doc.set(FIELDS["decision"], decision)
    doc.set(FIELDS["actor"], frappe.session.user)
    doc.set(FIELDS["timestamp"], timestamp)
    doc.set(FIELDS["remarks"], remarks)
    if decision == APPROVED:
        doc.set(FIELDS["evidence"], evidence)
    doc.flags.existing_rm_technical_action = True
    doc.save(ignore_permissions=True)
    doc.add_comment(
        "Comment",
        _audit_comment(previous_status, decision, remarks, evidence, timestamp),
    )
    return get_existing_rm_technical_context(doc.name)


@frappe.whitelist()
def get_existing_rm_technical_context(item_code):
    item_code = cstr(item_code).strip()
    result = evaluate_existing_rm_readiness([item_code]).get(item_code) or {}
    status = (
        frappe.db.get_value("Item", item_code, FIELDS["status"]) or NOT_REVIEWED
    )
    return {
        "eligible": is_eligible_existing_rm(item_code),
        "status": status,
        "can_decide": TECHNICAL_ROLE in frappe.get_roles(frappe.session.user),
        "readiness": result,
    }
