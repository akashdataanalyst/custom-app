from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import cstr


RM_QI_TEMPLATE_PREFIX = "Calco RM QC - "
MANUAL_APPROVAL_RULES = {"Manual Review"}
REFERENCE_ONLY_APPROVAL_RULE = "Reference Only"
NUMERIC_RECORDING_APPROVAL_RULE = "Observation (Numeric Recording)"
PASS_FAIL_APPROVAL_RULE = "Pass / Fail"
NUMERIC_APPROVAL_RULES = {
    "Numeric Range",
    "Minimum Only",
    "Maximum Only",
    NUMERIC_RECORDING_APPROVAL_RULE,
}
OBSERVATION_APPROVAL_RULE = "Observation"
ACCEPTED_OBSERVATIONS = {
    "0",
    "none",
    "nil",
    "no",
    "no metal",
    "no contamination",
    "absent",
    "not present",
    "pass",
    "ok",
}
REJECTED_OBSERVATIONS = {
    "yes",
    "metal present",
    "contamination found",
    "present",
    "detected",
    "fail",
}

def normalize_text(value) -> str:
    return re.sub(r"\s+", " ", cstr(value or "")).strip()


def normalize_observation(value) -> str:
    raw_value = "" if value is None else cstr(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", raw_value.casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def evaluate_observation(value) -> str:
    normalized = normalize_observation(value)
    if not normalized:
        return ""

    if normalized in ACCEPTED_OBSERVATIONS:
        return "Accepted"
    if normalized in REJECTED_OBSERVATIONS:
        return "Rejected"

    tokens = set(normalized.split())
    subject_tokens = {"metal", "contamination"}
    negative_tokens = {"no", "without", "absent"}
    positive_tokens = {"present", "found", "detected"}

    if "not present" in normalized or "not detected" in normalized:
        return "Accepted"
    if tokens.intersection(negative_tokens) and tokens.intersection(subject_tokens):
        return "Accepted"
    if tokens.intersection(subject_tokens) and tokens.intersection(positive_tokens):
        return "Rejected"

    return ""


def evaluate_pass_fail(value) -> str:
    normalized = normalize_observation(value)
    if normalized == "ok":
        return "Accepted"
    if normalized == "not ok":
        return "Rejected"
    return ""


def parse_decimal(value) -> Decimal | None:
    cleaned = normalize_text(value)
    if not cleaned:
        return None

    cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_float(value) -> float | None:
    parsed = parse_decimal(value)
    return float(parsed) if parsed is not None else None


def derive_target_value(acceptable_min=None, acceptable_max=None) -> str:
    min_value = normalize_text(acceptable_min)
    max_value = normalize_text(acceptable_max)

    if min_value and max_value and min_value == max_value:
        return min_value

    for candidate in (max_value, min_value):
        if candidate and parse_decimal(candidate) is None:
            return candidate

    return ""


def derive_approval_rule(acceptable_min=None, acceptable_max=None, target_value=None) -> str:
    min_value = normalize_text(acceptable_min)
    max_value = normalize_text(acceptable_max)
    target = normalize_text(target_value)

    numeric_min = parse_decimal(min_value) if min_value else None
    numeric_max = parse_decimal(max_value) if max_value else None

    if numeric_min is not None and numeric_max is not None:
        return "Numeric Range"
    if numeric_min is not None and not max_value:
        return "Minimum Only"
    if numeric_max is not None and not min_value:
        return "Maximum Only"

    reference_text = " ".join(filter(None, [min_value.lower(), max_value.lower(), target.lower()]))
    if "refer" in reference_text:
        return "Reference Only"

    if target or min_value or max_value:
        return "Manual Review"

    return "Manual Review"


def infer_result_type_from_rules(rules: list[str]) -> str:
    normalized = set(rules)
    if normalized.intersection(NUMERIC_APPROVAL_RULES):
        return "Numeric"
    if normalized.intersection(MANUAL_APPROVAL_RULES):
        return "Manual Review"
    return "Qualitative"


def build_rm_template_name(item_code: str) -> str:
    cleaned = normalize_text(item_code) or "RM"
    return (RM_QI_TEMPLATE_PREFIX + cleaned)[:140]


def resolve_item_by_code_or_name(item_reference: str) -> str | None:
    cleaned = normalize_text(item_reference)
    if not cleaned:
        return None

    if frappe.db.exists("Item", cleaned):
        return cleaned

    for filters in ({"item_code": cleaned}, {"item_name": cleaned}):
        match = frappe.db.get_value("Item", filters, "name")
        if match:
            return match

    return None

