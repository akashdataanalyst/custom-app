"""One-time ERP go-live readiness compatibility for approved legacy raw materials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import frappe
from frappe import _


MANIFEST_REFERENCE = "ERP-GL-LRM-INIT-v2.0"
MANIFEST_SHA256 = "E4D71339139C5754F4185A8EF2752AA8881D59CE2084EA898330AF1A9E4B7EFC"
MAPPING_SHA256 = "BD5946E6572542EF2257391BB9399A16902070161F17C20BFE58272D98C4951E"
DRY_RUN_SHA256 = "26A3DA5E2F8D0B552805431151B3B128B78485FE0398D1E5724463E15EA401F4"
POPULATION_COUNT = 162
CONFIG_KEY = "calco_legacy_rm_initialization"
MANIFEST_PATH = Path(__file__).with_name("data") / "erp_go_live_legacy_rm_initialization_v2.json"


def normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


def _load_manifest() -> tuple[list[dict], dict[str, dict]]:
    manifest_bytes = MANIFEST_PATH.read_bytes()
    actual_hash = hashlib.sha256(manifest_bytes).hexdigest().upper()
    if actual_hash != MANIFEST_SHA256:
        frappe.throw(
            _("Legacy RM manifest hash mismatch. Expected {0}, found {1}.").format(
                MANIFEST_SHA256, actual_hash
            )
        )

    rows = json.loads(manifest_bytes.decode("utf-8-sig"))
    if not isinstance(rows, list) or len(rows) != POPULATION_COUNT:
        frappe.throw(_("Legacy RM manifest must contain exactly {0} rows.").format(POPULATION_COUNT))

    by_code: dict[str, dict] = {}
    for row in rows:
        code = normalize_code(row.get("RMCode"))
        if not code:
            frappe.throw(_("Legacy RM manifest contains a blank canonical RM code."))
        if code in by_code:
            frappe.throw(_("Duplicate canonical RM code {0} in legacy manifest.").format(code))
        if row.get("ManifestReference") != MANIFEST_REFERENCE:
            frappe.throw(_("Invalid manifest reference for RM {0}.").format(code))
        if not str(row.get("InitializationBasis") or "").strip():
            frappe.throw(_("Initialization basis is missing for RM {0}.").format(code))
        by_code[code] = row

    if "P00014" in by_code:
        frappe.throw(_("P00014 is excluded and must not be present in the legacy manifest."))
    return rows, by_code


def get_approved_legacy_rm_manifest() -> dict[str, dict]:
    """Return the approved eligibility boundary without activating legacy readiness."""
    _rows, by_code = _load_manifest()
    return by_code


def _activation_config() -> dict | None:
    config = frappe.conf.get(CONFIG_KEY)
    if not config:
        return None
    if isinstance(config, str):
        config = frappe.parse_json(config)
    if not isinstance(config, dict):
        frappe.throw(_("Legacy RM initialization configuration is invalid."))
    return config


def _validate_activation(config: dict) -> None:
    expected = {
        "manifest_reference": MANIFEST_REFERENCE,
        "manifest_sha256": MANIFEST_SHA256,
        "mapping_sha256": MAPPING_SHA256,
        "dry_run_sha256": DRY_RUN_SHA256,
        "population_count": POPULATION_COUNT,
    }
    for key, value in expected.items():
        if config.get(key) != value:
            frappe.throw(_("Legacy RM activation value {0} does not match the approved release.").format(key))
    for key in ("signoff_reference", "executed_at", "executed_by"):
        if not str(config.get(key) or "").strip():
            frappe.throw(_("Legacy RM activation value {0} is required.").format(key))


def validate_live_manifest() -> dict:
    """Validate the packaged manifest against live Recovery master data without writing."""
    rows, by_code = _load_manifest()
    codes = sorted(by_code)
    items = {
        normalize_code(row.name): row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", codes)},
            fields=["name", "item_group", "disabled", "is_stock_item"],
            limit_page_length=0,
        )
    }
    planning = {
        normalize_code(row.item_code): row
        for row in frappe.get_all(
            "RM Planning Parameter",
            filters={"item_code": ("in", codes), "is_active": 1},
            fields=["name", "item_code", "is_active"],
            limit_page_length=0,
        )
    }
    exceptions = []
    for code in codes:
        item = items.get(code)
        if not item:
            exceptions.append({"rm_code": code, "reason": "Item missing"})
            continue
        if item.disabled or not item.is_stock_item or item.item_group != "Raw Material":
            exceptions.append({"rm_code": code, "reason": "Item is not an enabled Raw Material stock item"})
        if code not in planning:
            exceptions.append({"rm_code": code, "reason": "Active RM Planning Parameter missing"})

    return {
        "manifest_reference": MANIFEST_REFERENCE,
        "manifest_sha256": MANIFEST_SHA256,
        "manifest_rows": len(rows),
        "unique_canonical_rm_codes": len(by_code),
        "items_found": len(items),
        "eligible_items": sum(
            1
            for item in items.values()
            if not item.disabled and item.is_stock_item and item.item_group == "Raw Material"
        ),
        "active_rm_planning_parameters": len(planning),
        "p00014_excluded": "P00014" not in by_code,
        "conflicts": len(exceptions),
        "exceptions": exceptions,
    }


def get_legacy_initialized_readiness(item_codes: list[str] | set[str]) -> dict[str, dict[str, object]]:
    """Return approved one-time readiness results, or fail closed when inactive/invalid."""
    config = _activation_config()
    if not config:
        return {}

    try:
        _validate_activation(config)
        _rows, by_code = _load_manifest()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Legacy RM Initialization Validation Failed")
        return {}

    requested = {
        original: normalize_code(original)
        for original in item_codes
        if normalize_code(original) in by_code
    }
    if not requested:
        return {}

    canonical_codes = sorted(set(requested.values()))
    eligible = {
        normalize_code(code)
        for code in frappe.get_all(
            "Item",
            filters={
                "name": ("in", canonical_codes),
                "item_group": "Raw Material",
                "disabled": 0,
                "is_stock_item": 1,
            },
            pluck="name",
            limit_page_length=0,
        )
    }
    active_planning = {
        normalize_code(code)
        for code in frappe.get_all(
            "RM Planning Parameter",
            filters={"item_code": ("in", canonical_codes), "is_active": 1},
            pluck="item_code",
            limit_page_length=0,
        )
    }

    result = {}
    for original, canonical in requested.items():
        if canonical not in eligible or canonical not in active_planning:
            continue
        row = by_code[canonical]
        result[original] = {
            "ready": True,
            "overall": "Operational Ready",
            "percent": 100,
            "source": "ERP Go-Live Legacy RM Initialization",
            "source_rm_code": row.get("SourceRMCode") or canonical,
            "canonical_rm_code": canonical,
            "initialization_basis": row.get("InitializationBasis"),
            "manifest_reference": MANIFEST_REFERENCE,
            "manifest_sha256": MANIFEST_SHA256,
            "signoff_reference": config.get("signoff_reference"),
            "initialized_at": config.get("executed_at"),
            "initialized_by": config.get("executed_by"),
        }
    return result
