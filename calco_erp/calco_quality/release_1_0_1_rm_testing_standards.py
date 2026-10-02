from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import frappe


DATASET_PATH = Path(__file__).with_name("data") / "release_1_0_1_rm_testing_standards.csv"
EXPECTED_CREATED = 19
EXPECTED_UPDATED = 87
EXPECTED_TOTAL = 106

FIELD_COLUMNS = {
    "acceptable_min": ("BaselineMin", "RecoveryMin"),
    "acceptable_max": ("BaselineMax", "RecoveryMax"),
    "approval_rule": ("BaselineEvaluation", "RecoveryEvaluation"),
    "unit": ("BaselineUnit", "RecoveryUnit"),
}
CREATE_FIELDS = (*FIELD_COLUMNS, "is_active")
ALLOWED_UPDATE_FIELDS = frozenset(FIELD_COLUMNS)


@dataclass(frozen=True)
class ReleaseRow:
    action: str
    rm_item: str
    testing_type: str
    approved_fields: tuple[str, ...]
    release_values: dict[str, object]
    baseline_values: dict[str, object]

    @property
    def key(self) -> tuple[str, str]:
        return self.rm_item, self.testing_type


def load_release_rows() -> list[ReleaseRow]:
    with DATASET_PATH.open(encoding="utf-8-sig", newline="") as source:
        rows = [_parse_row(row) for row in csv.DictReader(source)]

    _validate_release_rows(rows)
    return rows


def get_release_summary() -> dict[str, int]:
    rows = load_release_rows()
    return {
        "total": len(rows),
        "create": sum(row.action == "create" for row in rows),
        "update": sum(row.action == "update" for row in rows),
    }


def preview_release() -> dict[str, int]:
    rows = load_release_rows()
    existing_by_key = _preflight(rows)
    summary = {"create": 0, "update": 0, "unchanged": 0}

    for row in rows:
        existing_name = existing_by_key.get(row.key)
        if not existing_name:
            summary["create"] += 1
        elif _changed_values(existing_name, row):
            summary["update"] += 1
        else:
            summary["unchanged"] += 1

    return summary


def apply_release() -> dict[str, int]:
    rows = load_release_rows()
    existing_by_key = _preflight(rows)
    summary = {"created": 0, "updated": 0, "unchanged": 0}

    for row in rows:
        existing_name = existing_by_key.get(row.key)
        if existing_name:
            changed_values = _changed_values(existing_name, row)
            if changed_values:
                frappe.db.set_value(
                    "RM Testing Standard",
                    existing_name,
                    changed_values,
                    update_modified=False,
                )
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
            continue

        doc = frappe.get_doc(
            {
                "doctype": "RM Testing Standard",
                "rm_item": row.rm_item,
                "rm_code": row.rm_item,
                "testing_type": row.testing_type,
                **row.release_values,
            }
        )
        doc.insert(ignore_permissions=True)
        summary["created"] += 1

    validation = validate_current_state(throw=True)
    summary["validated"] = validation["matched"]
    frappe.clear_cache(doctype="RM Testing Standard")
    return summary


def validate_current_state(*, throw: bool = False) -> dict[str, object]:
    rows = load_release_rows()
    mismatches: list[str] = []
    matched = 0

    for row in rows:
        names = frappe.get_all(
            "RM Testing Standard",
            filters={"rm_item": row.rm_item, "testing_type": row.testing_type},
            pluck="name",
        )
        if len(names) != 1:
            mismatches.append(
                f"{row.rm_item} / {row.testing_type}: expected one row, found {len(names)}"
            )
            continue

        current = frappe.db.get_value(
            "RM Testing Standard",
            names[0],
            list(row.approved_fields),
            as_dict=True,
        ) or {}
        field_mismatches = [
            fieldname
            for fieldname in row.approved_fields
            if _normalize_value(current.get(fieldname), fieldname)
            != _normalize_value(row.release_values[fieldname], fieldname)
        ]
        if field_mismatches:
            mismatches.append(
                f"{row.rm_item} / {row.testing_type}: {', '.join(field_mismatches)}"
            )
        else:
            matched += 1

    result = {
        "expected": EXPECTED_TOTAL,
        "matched": matched,
        "mismatches": mismatches,
    }
    if throw and mismatches:
        frappe.throw(
            "Release 1.0.1 RM Testing Standard validation failed:\n"
            + "\n".join(mismatches[:20])
        )
    return result


def _parse_row(source: dict[str, str]) -> ReleaseRow:
    change_type = (source.get("ChangeType") or "").strip()
    if change_type == "Added in Recovery":
        action = "create"
        approved_fields = CREATE_FIELDS
    elif change_type == "Changed in Recovery":
        action = "update"
        approved_fields = tuple(
            fieldname.strip()
            for fieldname in (source.get("ChangedFields") or "").split(",")
            if fieldname.strip()
        )
    else:
        raise ValueError(f"Unsupported ChangeType: {change_type}")

    release_values = {
        fieldname: source.get(columns[1], "")
        for fieldname, columns in FIELD_COLUMNS.items()
        if fieldname in approved_fields
    }
    baseline_values = {
        fieldname: source.get(columns[0], "")
        for fieldname, columns in FIELD_COLUMNS.items()
        if fieldname in approved_fields
    }
    if "is_active" in approved_fields:
        release_values["is_active"] = int(source.get("Active") or 0)

    return ReleaseRow(
        action=action,
        rm_item=(source.get("RMItem") or "").strip(),
        testing_type=(source.get("Parameter") or "").strip(),
        approved_fields=approved_fields,
        release_values=release_values,
        baseline_values=baseline_values,
    )


def _validate_release_rows(rows: list[ReleaseRow]) -> None:
    create_count = sum(row.action == "create" for row in rows)
    update_count = sum(row.action == "update" for row in rows)
    if (len(rows), create_count, update_count) != (
        EXPECTED_TOTAL,
        EXPECTED_CREATED,
        EXPECTED_UPDATED,
    ):
        raise ValueError(
            "Release dataset count mismatch: "
            f"expected {EXPECTED_TOTAL}/{EXPECTED_CREATED}/{EXPECTED_UPDATED}, "
            f"found {len(rows)}/{create_count}/{update_count}"
        )

    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not all(row.key):
            raise ValueError(f"Release row has an incomplete key: {row.key}")
        if row.key in seen:
            raise ValueError(f"Duplicate release row: {row.key}")
        seen.add(row.key)

        if row.action == "update":
            unsupported = set(row.approved_fields) - ALLOWED_UPDATE_FIELDS
            if not row.approved_fields or unsupported:
                raise ValueError(
                    f"Invalid approved fields for {row.key}: {row.approved_fields}"
                )
        elif tuple(row.approved_fields) != CREATE_FIELDS:
            raise ValueError(f"Invalid create fields for {row.key}")


def _preflight(rows: list[ReleaseRow]) -> dict[tuple[str, str], str]:
    if not frappe.db.exists("DocType", "RM Testing Standard"):
        frappe.throw("RM Testing Standard DocType is not installed.")

    missing_items = sorted(
        {row.rm_item for row in rows if not frappe.db.exists("Item", row.rm_item)}
    )
    missing_parameters = sorted(
        {
            row.testing_type
            for row in rows
            if not frappe.db.exists("Quality Inspection Parameter", row.testing_type)
        }
    )
    if missing_items or missing_parameters:
        messages = []
        if missing_items:
            messages.append("Missing Items: " + ", ".join(missing_items))
        if missing_parameters:
            messages.append(
                "Missing Quality Inspection Parameters: "
                + ", ".join(missing_parameters)
            )
        frappe.throw("\n".join(messages))

    existing_by_key: dict[tuple[str, str], str] = {}
    missing_updates: list[str] = []
    duplicate_keys: list[str] = []
    for row in rows:
        names = frappe.get_all(
            "RM Testing Standard",
            filters={"rm_item": row.rm_item, "testing_type": row.testing_type},
            pluck="name",
        )
        if len(names) > 1:
            duplicate_keys.append(f"{row.rm_item} / {row.testing_type}")
        elif names:
            existing_by_key[row.key] = names[0]
        elif row.action == "update":
            missing_updates.append(f"{row.rm_item} / {row.testing_type}")

    if duplicate_keys:
        frappe.throw(
            "Duplicate RM Testing Standard keys block Release 1.0.1:\n"
            + "\n".join(duplicate_keys)
        )
    if missing_updates:
        frappe.throw(
            "Expected RM Testing Standard rows are missing:\n"
            + "\n".join(missing_updates)
        )
    return existing_by_key


def _changed_values(existing_name: str, row: ReleaseRow) -> dict[str, object]:
    current = frappe.db.get_value(
        "RM Testing Standard",
        existing_name,
        list(row.approved_fields),
        as_dict=True,
    ) or {}
    return {
        fieldname: row.release_values[fieldname]
        for fieldname in row.approved_fields
        if _normalize_value(current.get(fieldname), fieldname)
        != _normalize_value(row.release_values[fieldname], fieldname)
    }


def _normalize_value(value, fieldname: str):
    if fieldname == "is_active":
        return int(value or 0)
    return str(value or "").strip()
