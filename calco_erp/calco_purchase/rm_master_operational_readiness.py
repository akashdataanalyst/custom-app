"""One-time readiness foundation for established RM masters."""

import hashlib
import json
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime

from calco_erp.calco_purchase.legacy_rm_initialization import normalize_code


MANIFEST_REFERENCE = "RM-MASTER-OP-READY-v1.0"
MANIFEST_SHA256 = "BA458E2C2F7C02CD0ECBA387122B2B2B2C3E3E83195700079C049DDA77CA2741"
POPULATION_COUNT = 295
MANIFEST_PATH = Path(__file__).with_name("data") / "rm_master_operational_readiness_v1.json"
INITIALIZATION_REMARKS = (
    "Existing Raw Material master initialized as Operational Ready under the "
    "approved 2026-08-16 business decision. Future RM onboarding remains governed."
)
INITIALIZATION_EVIDENCE = f"{MANIFEST_REFERENCE} | Manifest SHA-256 {MANIFEST_SHA256}"
TECHNICAL_FIELDS = {
    "status": "custom_existing_rm_technical_status",
    "decision": "custom_existing_rm_technical_decision",
    "actor": "custom_existing_rm_technical_approved_by",
    "timestamp": "custom_existing_rm_technical_approved_on",
    "remarks": "custom_existing_rm_technical_remarks",
    "evidence": "custom_existing_rm_technical_evidence",
}


def _load_manifest():
    manifest_bytes = MANIFEST_PATH.read_bytes()
    actual_hash = hashlib.sha256(manifest_bytes).hexdigest().upper()
    if actual_hash != MANIFEST_SHA256:
        frappe.throw(
            _("RM readiness manifest hash mismatch. Expected {0}, found {1}.").format(
                MANIFEST_SHA256, actual_hash
            )
        )
    rows = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(rows, list) or len(rows) != POPULATION_COUNT:
        frappe.throw(_("RM readiness manifest must contain exactly {0} rows.").format(POPULATION_COUNT))
    by_code = {}
    for row in rows:
        code = normalize_code(row.get("ItemCode"))
        if not code or code in by_code:
            frappe.throw(_("RM readiness manifest has a blank or duplicate Item Code."))
        if row.get("ManifestReference") != MANIFEST_REFERENCE:
            frappe.throw(_("Invalid manifest reference for RM {0}.").format(code))
        if not cstr(row.get("InitializationBasis")).strip():
            frappe.throw(_("Initialization basis is missing for RM {0}.").format(code))
        by_code[code] = row
    return rows, by_code


def get_rm_operational_readiness_manifest():
    return _load_manifest()[1]


def is_rm_foundation_initialization(item):
    if not item:
        return False
    return (
        cstr(item.get(TECHNICAL_FIELDS["status"])).strip() == "Approved"
        and cstr(item.get(TECHNICAL_FIELDS["decision"])).strip() == "Approved"
        and cstr(item.get(TECHNICAL_FIELDS["evidence"])).strip() == INITIALIZATION_EVIDENCE
        and normalize_code(item.get("name")) in get_rm_operational_readiness_manifest()
    )


def _governed_item_codes():
    if not frappe.db.exists("DocType", "New RM Request"):
        return set()
    rows = frappe.get_all(
        "New RM Request", fields=["rm_code", "created_item"], limit_page_length=0
    )
    return {
        normalize_code(row.get("created_item") or row.get("rm_code"))
        for row in rows
        if row.get("created_item") or row.get("rm_code")
    }


def validate_live_manifest():
    rows, manifest = _load_manifest()
    codes = sorted(manifest)
    items = {
        normalize_code(row.name): row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", codes)},
            fields=["name", "item_group", "disabled", "is_stock_item", *TECHNICAL_FIELDS.values()],
            limit_page_length=0,
        )
    }
    governed = _governed_item_codes()
    conflicts = []
    exceptions = []
    for code in codes:
        item = items.get(code)
        if not item:
            conflicts.append({"item_code": code, "reason": "Item does not exist."})
            continue
        if cint(item.disabled) or not cint(item.is_stock_item) or item.item_group != "Raw Material":
            conflicts.append({"item_code": code, "reason": "Item is not an enabled Raw Material stock Item."})
        if code in governed:
            conflicts.append({"item_code": code, "reason": "Item is governed by New RM Request."})
        status = cstr(item.get(TECHNICAL_FIELDS["status"])).strip() or "Not Reviewed"
        if status in {"Rejected", "Revoked"}:
            exceptions.append({"item_code": code, "status": status})
    return {
        "manifest_reference": MANIFEST_REFERENCE,
        "manifest_sha256": MANIFEST_SHA256,
        "manifest_rows": len(rows),
        "unique_item_codes": len(manifest),
        "items_found": len(items),
        "conflicts": conflicts,
        "explicit_exceptions": exceptions,
    }


def initialize_existing_rm_operational_readiness():
    validation = validate_live_manifest()
    if validation["conflicts"]:
        frappe.throw(
            _("RM readiness initialization blocked by {0} manifest conflict(s).").format(
                len(validation["conflicts"])
            )
        )
    timestamp = now_datetime()
    actor = frappe.session.user if frappe.session.user not in {"", "Guest"} else "Administrator"
    result = {"initialized": [], "already_initialized": [], "preserved_approved": [], "explicit_exceptions": []}
    for code in sorted(get_rm_operational_readiness_manifest()):
        values = frappe.db.get_value(
            "Item", code, ["name", *TECHNICAL_FIELDS.values()], as_dict=True
        )
        status = cstr(values.get(TECHNICAL_FIELDS["status"])).strip() or "Not Reviewed"
        if status in {"Rejected", "Revoked"}:
            result["explicit_exceptions"].append({"item_code": code, "status": status})
        elif is_rm_foundation_initialization(values):
            result["already_initialized"].append(code)
        elif status == "Approved":
            result["preserved_approved"].append(code)
        elif status != "Not Reviewed":
            frappe.throw(_("RM {0} has unsupported status {1}.").format(code, status))
        else:
            frappe.db.set_value(
                "Item",
                code,
                {
                    TECHNICAL_FIELDS["status"]: "Approved",
                    TECHNICAL_FIELDS["decision"]: "Approved",
                    TECHNICAL_FIELDS["actor"]: actor,
                    TECHNICAL_FIELDS["timestamp"]: timestamp,
                    TECHNICAL_FIELDS["remarks"]: INITIALIZATION_REMARKS,
                    TECHNICAL_FIELDS["evidence"]: INITIALIZATION_EVIDENCE,
                },
                update_modified=True,
            )
            result["initialized"].append(code)
    result.update(
        {key: value for key, value in validation.items() if key != "explicit_exceptions"}
    )
    for key in ("initialized", "already_initialized", "preserved_approved", "explicit_exceptions"):
        result[f"{key}_count"] = len(result[key])
    result.update({"executed_by": actor, "executed_at": str(timestamp)})
    return result
