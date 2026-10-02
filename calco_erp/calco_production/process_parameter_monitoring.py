from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime, nowdate

from calco_erp.calco_production.compounding_execution import (
    is_controlled_compounding_job_card,
    job_card_has_started,
)
from calco_erp.calco_production.mpds_master import CURRENT_STATUS, MPDS_DOCTYPE


MONITOR_DOCTYPE = "Process Parameter Monitor"
SNAPSHOT_DOCTYPE = "Process Parameter Snapshot"
OBSERVATION_DOCTYPE = "Process Observation"
VALUE_DOCTYPE = "Process Observation Value"
DOCUMENT_CODE = "F-PRD-01/01"
VALID_MONITOR_STATUSES = {"Draft", "Active", "Completed", "Superseded"}
FROZEN_MONITOR_STATUSES = {"Completed", "Superseded"}
ACKNOWLEDGEMENT_ROLES = {
    "Production Engineer",
    "Production Head",
    "Manufacturing Manager",
    "System Manager",
}
COMPLETION_ROLES = ACKNOWLEDGEMENT_ROLES
CORRECTION_ROLES = {"Production Head", "Manufacturing Manager", "System Manager"}


def _catalog(group: str, group_sequence: int, input_type: str, *rows, status_options: str = ""):
    return {
        key: {
            "parameter_key": key,
            "display_label": label,
            "parameter_group": group,
            "group_sequence": group_sequence,
            "input_type": input_type,
            "status_options": status_options,
        }
        for key, label in rows
    }


PROCESS_PARAMETER_CATALOG = {
    **_catalog(
        "Pre-checks", 1, "Text",
        ("screw_configuration", "Screw Configuration"),
        ("screen_pack", "Screen Pack"),
        ("die_hole", "Die Hole"),
    ),
    **_catalog(
        "Pre-checks", 1, "Status",
        ("air_knife_working", "Air Knife Working"),
        ("all_magnet_cleaning", "All Magnet Cleaning"),
        status_options="OK\nNot OK",
    ),
    **_catalog(
        "Venting & Devolatilizing Zone", 3, "Status",
        ("vent_condition", "Vent Condition"),
        status_options="Open\nClosed",
    ),
    **_catalog(
        "Melting & Mixing Zone", 2, "Numeric",
        *((f"temperature_zone_{number}", f"Temperature Zone {number}") for number in range(1, 5)),
    ),
    **_catalog(
        "Venting & Devolatilizing Zone", 3, "Numeric",
        *((f"temperature_zone_{number}", f"Temperature Zone {number}") for number in range(5, 9)),
        ("vacuum", "Vacuum"),
        ("vacuum_stuffer_temperature", "Vacuum Stuffer Temperature"),
    ),
    **_catalog(
        "Downstream / Process Zones", 4, "Numeric",
        *((f"temperature_zone_{number}", f"Temperature Zone {number}") for number in range(9, 12)),
        ("water_bath_temperature_1", "Water Bath Temperature 1"),
        ("water_bath_temperature_2", "Water Bath Temperature 2"),
        ("classifier_inlet_temperature", "Classifier Inlet Temperature"),
        ("classifier_outlet_temperature", "Classifier Outlet Temperature"),
    ),
    **_catalog(
        "Metering & Die Zone", 5, "Numeric",
        ("adapter_temperature", "Adapter Temperature"),
        ("screen_changer_temperature", "Screen Changer Temperature"),
        ("die_head_temperature", "Die Head Temperature"),
        ("melt_temperature", "Melt Temperature"),
        ("melt_pressure", "Melt Pressure"),
    ),
    **_catalog(
        "Running Checks", 6, "Numeric",
        ("throughput", "Throughput"),
        ("extruder_rpm", "Extruder RPM"),
        ("torque", "Torque"),
        ("current", "Current"),
        ("pelletizer_frequency", "Pelletizer Frequency"),
        ("specific_energy", "Specific Energy"),
        ("tcu_temperature", "TCU Temperature"),
        ("sf1_rpm", "SF1 RPM"),
        ("sf2_rpm", "SF2 RPM"),
        ("gear_box_oil_temperature", "Gear Box Oil Temperature"),
        ("granule_size", "Granule Size"),
    ),
}

CONFIRMED_EQUIVALENCES = {
    "die_hole": ("Die", "Die Hole"),
    "pelletizer_frequency": ("Palletizer Speed", "Pelletizer Frequency"),
    "sf1_rpm": ("SF1 RPM", "SF1 RPM"),
    "sf2_rpm": ("SF2 RPM", "SF2 RPM"),
    "gear_box_oil_temperature": ("Lube Oil Temp", "Gear Box Oil Temperature"),
}

PARENT_IDENTITY_FIELDS = (
    "controlled_document_code", "job_card", "work_order", "operation",
    "production_line", "machine", "fg_item", "fg_batch", "bom_no",
    "planned_job_qty", "company", "production_date", "shift",
    "recorded_by", "recorded_on", "mpds", "mpds_no", "mpds_revision",
    "mpds_revision_date", "mpds_effective_date", "mpds_payload_hash",
    "correction_of",
)

SNAPSHOT_FIELDS = (
    "sequence", "group_sequence", "parameter_group", "parameter_key",
    "display_label", "input_type", "status_options", "specification_text",
    "unit", "applicability", "mapping_status", "mapping_note",
    "exact_source_header", "source_cell", "source_value_type",
    "source_raw_value", "parser_status", "parsed_target", "parsed_minimum",
    "parsed_maximum", "parsed_tolerance",
)

OBSERVATION_IDENTITY_FIELDS = (
    "monitor", "job_card", "work_order", "fg_item", "fg_batch",
    "observation_sequence", "observation_number", "observation_time", "shift",
    "recorded_by", "recorded_on", "correction_of", "correction_reason",
)

VALUE_FIELDS = (
    "sequence", "group_sequence", "parameter_group", "parameter_key",
    "display_label", "input_type", "specification_text", "unit",
    "applicability", "actual_value", "actual_numeric", "comparison_result",
    "exception_reason", "exception_acknowledged", "acknowledged_by",
    "acknowledged_on",
)


def _job_card_status(job_card) -> str:
    return cstr(job_card.get("status")).strip().casefold()


def _get_controlled_context(job_card_name: str):
    job_card_name = cstr(job_card_name).strip()
    if not job_card_name or not frappe.db.exists("Job Card", job_card_name):
        frappe.throw(_("A valid standard Job Card is required."))
    job_card = frappe.get_doc("Job Card", job_card_name)
    if not is_controlled_compounding_job_card(job_card):
        frappe.throw(_("Process Parameters are available only for a controlled Compounding / Extrusion Job Card."))
    if not job_card.get("work_order") or not frappe.db.exists("Work Order", job_card.work_order):
        frappe.throw(_("The Job Card must reference a valid Work Order."))
    return job_card, frappe.get_doc("Work Order", job_card.work_order)


def _validate_creation_authority(job_card, work_order):
    if cint(job_card.get("docstatus")) == 2:
        frappe.throw(_("Process Parameter Monitor cannot be created for a cancelled Job Card."))
    if not job_card_has_started(job_card):
        frappe.throw(_("Start Compounding / Extrusion Job Card {0} before creating Process Parameter Monitor.").format(job_card.name))
    if _job_card_status(job_card) != "work in progress":
        frappe.throw(_("New Process Parameter Monitor requires Job Card {0} to be Work In Progress.").format(job_card.name))
    if cstr(job_card.work_order) != cstr(work_order.name):
        frappe.throw(_("Process Parameter Monitor Work Order lineage does not match its Job Card."))
    if not cstr(job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no")).strip():
        frappe.throw(_("FG Batch must be allocated before creating Process Parameter Monitor."))


def _validate_new_observation_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) != "work in progress":
        frappe.throw(_("New Process Observations require the Job Card to be Work In Progress."))


def _validate_review_authority(job_card):
    if cint(job_card.get("docstatus")) == 2 or _job_card_status(job_card) in {"completed", "closed", "cancelled"}:
        frappe.throw(_("Process Parameter exceptions cannot be acknowledged after the Job Card is closed or cancelled."))


def _set_context(doc, job_card, work_order):
    values = {
        "work_order": job_card.work_order,
        "operation": job_card.operation,
        "production_line": job_card.get("custom_production_line") or "",
        "machine": job_card.get("custom_machine") or job_card.get("workstation") or "",
        "fg_item": work_order.production_item,
        "fg_batch": job_card.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no") or "",
        "bom_no": job_card.get("bom_no") or work_order.bom_no,
        "planned_job_qty": job_card.get("for_quantity") or work_order.qty,
        "company": work_order.company,
        "production_date": job_card.get("posting_date") or work_order.get("planned_start_date") or nowdate(),
        "shift": job_card.get("custom_shift_type") or "",
    }
    for fieldname, value in values.items():
        doc.set(fieldname, value)


def _get_current_mpds(fg_item: str, production_line: str):
    name = frappe.db.get_value(
        MPDS_DOCTYPE,
        {"fg_item": fg_item, "production_line": production_line, "status": CURRENT_STATUS},
        "name",
    )
    if not name:
        frappe.throw(
            _("No Approved / Current MPDS exists for FG {0} on {1}. Process Parameter Monitor cannot be created.").format(
                fg_item, production_line
            )
        )
    return frappe.get_doc(MPDS_DOCTYPE, name)


def _snapshot_row(specification, sequence: int) -> dict[str, Any]:
    key = cstr(specification.get("process_parameter_key")).strip()
    catalog = PROCESS_PARAMETER_CATALOG.get(key)
    if not catalog:
        frappe.throw(_("MPDS process parameter {0} has no controlled F-PRD mapping.").format(key or specification.get("display_label")))
    mapping_status = cstr(specification.get("mapping_status"))
    mapping_note = cstr(specification.get("mapping_note"))
    if key in CONFIRMED_EQUIVALENCES:
        source, target = CONFIRMED_EQUIVALENCES[key]
        mapping_status = "Confirmed Equivalent"
        mapping_note = f"Business-confirmed equivalent: {source} -> {target}. Original source header preserved."
    return {
        **catalog,
        "sequence": sequence,
        "display_label": catalog["display_label"],
        "specification_text": cstr(specification.get("specification_text")),
        "unit": cstr(specification.get("unit")),
        "applicability": cstr(specification.get("applicability")) or "Blank",
        "mapping_status": mapping_status,
        "mapping_note": mapping_note,
        "exact_source_header": cstr(specification.get("exact_source_header")),
        "source_cell": cstr(specification.get("source_cell")),
        "source_value_type": cstr(specification.get("source_value_type")),
        "source_raw_value": cstr(specification.get("source_raw_value")),
        "parser_status": cstr(specification.get("parser_status")),
        "parsed_target": specification.get("parsed_target"),
        "parsed_minimum": specification.get("parsed_minimum"),
        "parsed_maximum": specification.get("parsed_maximum"),
        "parsed_tolerance": specification.get("parsed_tolerance"),
    }


def _set_mpds_snapshot(doc, mpds):
    specifications = [
        row for row in mpds.get("specifications") or []
        if cstr(row.get("parameter_group")) == DOCUMENT_CODE
        and cstr(row.get("process_parameter_key")) in PROCESS_PARAMETER_CATALOG
    ]
    by_key = {cstr(row.get("process_parameter_key")): row for row in specifications}
    missing = [key for key in PROCESS_PARAMETER_CATALOG if key not in by_key]
    if missing:
        frappe.throw(_("Approved / Current MPDS {0} is missing controlled F-PRD parameter mapping(s): {1}.").format(mpds.name, ", ".join(missing)))
    doc.mpds = mpds.name
    doc.mpds_no = cstr(mpds.get("mpds_no"))
    doc.mpds_revision = cstr(mpds.get("revision"))
    doc.mpds_revision_date = mpds.get("source_revision_date")
    doc.mpds_effective_date = mpds.get("effective_date")
    doc.mpds_payload_hash = cstr(mpds.get("imported_payload_hash"))
    doc.set("parameter_snapshot", [])
    ordered = sorted(
        by_key.values(),
        key=lambda row: (
            PROCESS_PARAMETER_CATALOG[cstr(row.get("process_parameter_key"))]["group_sequence"],
            cint(row.get("sequence")),
        ),
    )
    for sequence, specification in enumerate(ordered, start=1):
        doc.append("parameter_snapshot", _snapshot_row(specification, sequence))


def before_insert_process_parameter_monitor(doc):
    if not getattr(frappe.flags, "controlled_process_parameter_creation", False):
        frappe.throw(_("Create Process Parameter Monitor from a running Compounding / Extrusion Job Card cockpit."))
    if doc.get("correction_of"):
        _prepare_parent_correction(doc)
        return
    job_card, work_order = _get_controlled_context(doc.get("job_card"))
    _validate_creation_authority(job_card, work_order)
    _set_context(doc, job_card, work_order)
    _validate_current_monitor_identity(doc)
    from calco_erp.calco_production.mpds_start_snapshot import from_frozen_plan
    mpds = from_frozen_plan(work_order, doc.fg_item, doc.production_line)
    if mpds is None:
        mpds = _get_current_mpds(doc.fg_item, doc.production_line)
        from calco_erp.calco_production.manufacturing_master_sync import IDENTITY_PREFIX
        if cstr(mpds.get("import_identity_key")).startswith(IDENTITY_PREFIX):
            frappe.throw(_("This run has no frozen start-time authority for the new MPDS. Use controlled production review; a later master cannot be applied retroactively."))
    _set_mpds_snapshot(doc, mpds)
    doc.controlled_document_code = DOCUMENT_CODE
    doc.status = "Draft"
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()


def _prepare_parent_correction(doc):
    original = frappe.get_doc(MONITOR_DOCTYPE, doc.correction_of)
    if original.status != "Completed":
        frappe.throw(_("Only a Completed Process Parameter Monitor can be corrected."))
    for fieldname in PARENT_IDENTITY_FIELDS:
        if fieldname != "correction_of":
            doc.set(fieldname, original.get(fieldname))
    doc.controlled_document_code = DOCUMENT_CODE
    doc.status = "Draft"
    doc.recorded_by = frappe.session.user
    doc.recorded_on = now_datetime()
    doc.set("parameter_snapshot", [])
    for row in original.get("parameter_snapshot") or []:
        doc.append("parameter_snapshot", {field: row.get(field) for field in SNAPSHOT_FIELDS})


def _validate_current_monitor_identity(doc):
    if doc.get("correction_of") or doc.status == "Superseded":
        return
    existing = frappe.db.get_value(
        MONITOR_DOCTYPE,
        {
            "job_card": doc.job_card,
            "fg_batch": doc.fg_batch,
            "status": ("!=", "Superseded"),
            "name": ("!=", doc.name or ""),
        },
        "name",
    )
    if existing:
        frappe.throw(_("Process Parameter Monitor {0} already owns this Job Card and FG Batch context.").format(existing))


def validate_process_parameter_monitor(doc):
    if cstr(doc.get("status")) not in VALID_MONITOR_STATUSES:
        frappe.throw(_("Invalid Process Parameter Monitor status {0}.").format(doc.get("status")))
    _protect_parent_identity(doc)
    _protect_snapshot(doc)
    if not doc.is_new():
        _validate_current_monitor_identity(doc)
    if cstr(doc.get("controlled_document_code")) != DOCUMENT_CODE:
        frappe.throw(_("Controlled document identity cannot be changed."))
    if doc.status in FROZEN_MONITOR_STATUSES and not getattr(frappe.flags, "controlled_process_parameter_transition", False):
        before = doc.get_doc_before_save()
        if before and _monitor_payload(before) != _monitor_payload(doc):
            frappe.throw(_("Completed or Superseded Process Parameter Monitor evidence is immutable."))
    if doc.status == "Completed":
        _validate_monitor_completion(doc)


def _protect_parent_identity(doc):
    if doc.is_new() or not frappe.db.exists(MONITOR_DOCTYPE, doc.name):
        return
    before = doc.get_doc_before_save()
    if before and any(_canonical(before.get(field)) != _canonical(doc.get(field)) for field in PARENT_IDENTITY_FIELDS):
        frappe.throw(_("Process Parameter Monitor execution and MPDS context cannot be changed."))


def _snapshot_payload(rows):
    return [{field: _canonical(row.get(field)) for field in SNAPSHOT_FIELDS} for row in rows]


def _protect_snapshot(doc):
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if before and _snapshot_payload(before.get("parameter_snapshot") or []) != _snapshot_payload(doc.get("parameter_snapshot") or []):
        frappe.throw(_("Frozen MPDS Process Parameter snapshot cannot be changed."))


def _monitor_payload(doc):
    return {
        field: _canonical(doc.get(field))
        for field in (*PARENT_IDENTITY_FIELDS, "status", "completed_by", "completed_on", "superseded_by", "correction_reason")
    }


def prevent_process_parameter_monitor_delete(doc):
    if frappe.db.exists(OBSERVATION_DOCTYPE, {"monitor": doc.name}):
        frappe.throw(_("Process Parameter Monitor with controlled observations cannot be deleted."))
    if doc.status in FROZEN_MONITOR_STATUSES:
        frappe.throw(_("Completed or Superseded Process Parameter Monitor cannot be deleted."))


def before_insert_process_observation(doc):
    if not getattr(frappe.flags, "controlled_process_observation_creation", False):
        frappe.throw(_("Use Add Process Observation from Process Parameter Monitor."))


def validate_process_observation(doc):
    if doc.is_new():
        return
    before = doc.get_doc_before_save()
    if not before:
        return
    if not getattr(frappe.flags, "controlled_process_observation_update", False):
        if any(_canonical(before.get(field)) != _canonical(doc.get(field)) for field in OBSERVATION_IDENTITY_FIELDS):
            frappe.throw(_("Saved Process Observation identity is immutable."))
        if _value_payload(before.get("values") or []) != _value_payload(doc.get("values") or []):
            frappe.throw(_("Saved Process Observation evidence cannot be rewritten."))


def prevent_process_observation_delete(doc):
    frappe.throw(_("Saved Process Observations are immutable and cannot be deleted."))


def _value_payload(rows):
    return [{field: _canonical(row.get(field)) for field in VALUE_FIELDS} for row in rows]


def _canonical(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _coerce_values(values) -> dict[str, dict[str, Any]]:
    if isinstance(values, str):
        values = json.loads(values)
    if isinstance(values, dict):
        values = [{"parameter_key": key, "actual_value": value} for key, value in values.items()]
    result = {}
    for row in values or []:
        row = frappe._dict(row)
        key = cstr(row.get("parameter_key")).strip()
        if not key or key in result:
            frappe.throw(_("Process Observation contains a blank or duplicate parameter key."))
        result[key] = {
            "actual_value": row.get("actual_value"),
            "exception_reason": cstr(row.get("exception_reason")).strip(),
        }
    return result


NUMBER_TOKEN = r"[+-]?\d+(?:\.\d+)?"
PLUS_MINUS_PATTERN = re.compile(rf"^\s*({NUMBER_TOKEN})\s*±\s*({NUMBER_TOKEN})\s*$")
RANGE_PATTERN = re.compile(rf"^\s*({NUMBER_TOKEN})\s*(?:-|to|~)\s*({NUMBER_TOKEN})\s*$", re.I)
UPPER_PATTERN = re.compile(rf"^\s*(<=|≤)\s*({NUMBER_TOKEN})\s*$")
LOWER_PATTERN = re.compile(rf"^\s*(>=|≥)\s*({NUMBER_TOKEN})\s*$")
STRICT_UPPER_PATTERN = re.compile(rf"^\s*(<)\s*({NUMBER_TOKEN})\s*$")
STRICT_LOWER_PATTERN = re.compile(rf"^\s*(>)\s*({NUMBER_TOKEN})\s*$")
NUMBER_PATTERN = re.compile(rf"^\s*({NUMBER_TOKEN})\s*$")


def _decimal(value) -> Decimal | None:
    try:
        number = Decimal(cstr(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _comparison_rule(snapshot) -> dict[str, Decimal | str] | None:
    """Parse the frozen controlled specification without trusting nullable Float defaults."""
    text = cstr(snapshot.get("specification_text")).strip()
    if not text:
        return None

    match = PLUS_MINUS_PATTERN.match(text)
    if match:
        target, tolerance = (_decimal(value) for value in match.groups())
        if target is None or tolerance is None or tolerance < 0:
            return None
        return {"operator": "range", "minimum": target - tolerance, "maximum": target + tolerance}

    match = RANGE_PATTERN.match(text)
    if match:
        first, second = (_decimal(value) for value in match.groups())
        if first is None or second is None:
            return None
        return {"operator": "range", "minimum": min(first, second), "maximum": max(first, second)}

    for pattern, operator in (
        (UPPER_PATTERN, "lte"),
        (LOWER_PATTERN, "gte"),
        (STRICT_UPPER_PATTERN, "lt"),
        (STRICT_LOWER_PATTERN, "gt"),
    ):
        match = pattern.match(text)
        if match:
            limit = _decimal(match.group(2))
            return {"operator": operator, "limit": limit} if limit is not None else None

    match = NUMBER_PATTERN.match(text)
    if match:
        target = _decimal(match.group(1))
        return {"operator": "eq", "target": target} if target is not None else None
    return None


def _comparison_bounds(snapshot) -> tuple[Decimal | None, Decimal | None]:
    rule = _comparison_rule(snapshot)
    if not rule:
        return None, None
    operator = rule["operator"]
    if operator == "range":
        return rule["minimum"], rule["maximum"]
    if operator in {"lte", "lt"}:
        return None, rule["limit"]
    if operator in {"gte", "gt"}:
        return rule["limit"], None
    return rule["target"], rule["target"]


def _is_within_specification(actual: Decimal, rule: dict[str, Decimal | str]) -> bool:
    operator = rule["operator"]
    if operator == "range":
        return rule["minimum"] <= actual <= rule["maximum"]
    if operator == "lte":
        return actual <= rule["limit"]
    if operator == "gte":
        return actual >= rule["limit"]
    if operator == "lt":
        return actual < rule["limit"]
    if operator == "gt":
        return actual > rule["limit"]
    return actual == rule["target"]


def _evaluate_value(snapshot, supplied, require_exception_reason: bool = True) -> dict[str, Any]:
    applicability = cstr(snapshot.get("applicability"))
    input_type = cstr(snapshot.get("input_type"))
    value = supplied.get("actual_value") if supplied else None
    text = cstr(value).strip()
    if applicability == "Not Applicable":
        return {"actual_value": "", "actual_numeric": None, "comparison_result": "Not Evaluated", "exception_reason": ""}
    if not text:
        frappe.throw(_("Actual value is required for {0}.").format(snapshot.get("display_label")))
    numeric = None
    numeric_decimal = None
    if input_type == "Numeric":
        numeric_decimal = _decimal(text)
        if numeric_decimal is None:
            frappe.throw(_("{0} requires a numeric actual value.").format(snapshot.get("display_label")))
        numeric = float(numeric_decimal)
    elif input_type == "Status":
        options = {cstr(option).strip() for option in cstr(snapshot.get("status_options")).splitlines() if cstr(option).strip()}
        if text not in options:
            frappe.throw(_("{0} must be one of: {1}.").format(snapshot.get("display_label"), ", ".join(sorted(options))))
    comparison = "Not Evaluated"
    if numeric_decimal is not None and applicability == "Specified":
        rule = _comparison_rule(snapshot)
        if rule:
            comparison = "Within" if _is_within_specification(numeric_decimal, rule) else "Outside"
    reason = cstr((supplied or {}).get("exception_reason")).strip()
    if comparison == "Outside" and require_exception_reason and not reason:
        frappe.throw(_("Exception Reason is required for outside-specification parameter {0}.").format(snapshot.get("display_label")))
    return {"actual_value": text, "actual_numeric": numeric, "comparison_result": comparison, "exception_reason": reason}


def _build_observation_values(monitor, values, require_exception_reason: bool = True):
    supplied = _coerce_values(values)
    snapshots = list(monitor.get("parameter_snapshot") or [])
    expected = {cstr(row.parameter_key) for row in snapshots if cstr(row.applicability) != "Not Applicable"}
    extras = set(supplied) - {cstr(row.parameter_key) for row in snapshots}
    if extras:
        frappe.throw(_("Unexpected Process Parameter value(s): {0}.").format(", ".join(sorted(extras))))
    missing = expected - set(supplied)
    if missing:
        labels = [row.display_label for row in snapshots if row.parameter_key in missing]
        frappe.throw(_("Complete this observation for all applicable Process Parameters: {0}.").format(", ".join(labels)))
    rows = []
    for snapshot in snapshots:
        evaluated = _evaluate_value(
            snapshot,
            supplied.get(cstr(snapshot.parameter_key)),
            require_exception_reason=require_exception_reason,
        )
        rows.append({
            "sequence": snapshot.sequence,
            "group_sequence": snapshot.group_sequence,
            "parameter_group": snapshot.parameter_group,
            "parameter_key": snapshot.parameter_key,
            "display_label": snapshot.display_label,
            "input_type": snapshot.input_type,
            "specification_text": snapshot.specification_text,
            "unit": snapshot.unit,
            "applicability": snapshot.applicability,
            **evaluated,
        })
    return rows


def _lock_monitor(name: str):
    frappe.db.sql(f"select name from `tab{MONITOR_DOCTYPE}` where name=%s for update", (name,))


def _lock_job_card(name: str):
    frappe.db.sql("select name from `tabJob Card` where name=%s for update", (name,))


def _next_observation_sequence(monitor: str) -> int:
    rows = frappe.db.sql(
        f"select coalesce(max(observation_sequence), 0) from `tab{OBSERVATION_DOCTYPE}` where monitor=%s",
        (monitor,),
    )
    value = rows[0][0] if rows else 0
    return cint(value) + 1


def _insert_observation(doc):
    previous = getattr(frappe.flags, "controlled_process_observation_creation", False)
    frappe.flags.controlled_process_observation_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_process_observation_creation = previous


def _set_monitor_status(monitor, status: str):
    previous = getattr(frappe.flags, "controlled_process_parameter_transition", False)
    frappe.flags.controlled_process_parameter_transition = True
    try:
        monitor.status = status
        monitor.save()
    finally:
        frappe.flags.controlled_process_parameter_transition = previous


@frappe.whitelist()
def create_process_parameter_monitor(job_card: str):
    job_card_doc, work_order = _get_controlled_context(job_card)
    job_card_doc.check_permission("write")
    _validate_creation_authority(job_card_doc, work_order)
    _lock_job_card(job_card_doc.name)
    fg_batch = cstr(job_card_doc.get("custom_fg_batch_no") or work_order.get("custom_fg_batch_no"))
    existing = frappe.db.get_value(MONITOR_DOCTYPE, {"job_card": job_card, "fg_batch": fg_batch, "status": ("!=", "Superseded")}, "name")
    if existing:
        return frappe.get_doc(MONITOR_DOCTYPE, existing).as_dict()
    doc = frappe.new_doc(MONITOR_DOCTYPE)
    doc.job_card = job_card
    previous = getattr(frappe.flags, "controlled_process_parameter_creation", False)
    frappe.flags.controlled_process_parameter_creation = True
    try:
        doc.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_process_parameter_creation = previous
    return doc.as_dict()


@frappe.whitelist()
def preview_process_observation(name: str, values):
    monitor = frappe.get_doc(MONITOR_DOCTYPE, name)
    monitor.check_permission("read")
    rows = _build_observation_values(monitor, values, require_exception_reason=False)
    return {
        "outside": [
            {"parameter_key": row["parameter_key"], "display_label": row["display_label"], "actual_value": row["actual_value"], "specification_text": row["specification_text"]}
            for row in rows if row["comparison_result"] == "Outside"
        ]
    }


@frappe.whitelist()
def add_process_observation(name: str, values):
    monitor = frappe.get_doc(MONITOR_DOCTYPE, name)
    monitor.check_permission("write")
    if monitor.status not in {"Draft", "Active"}:
        frappe.throw(_("New Process Observations are allowed only on Draft or Active monitor."))
    job_card, _work_order = _get_controlled_context(monitor.job_card)
    _validate_new_observation_authority(job_card)
    rows = _build_observation_values(monitor, values)
    _lock_monitor(monitor.name)
    sequence = _next_observation_sequence(monitor.name)
    observation = frappe.new_doc(OBSERVATION_DOCTYPE)
    observation.update({
        "monitor": monitor.name,
        "job_card": monitor.job_card,
        "work_order": monitor.work_order,
        "fg_item": monitor.fg_item,
        "fg_batch": monitor.fg_batch,
        "observation_sequence": sequence,
        "observation_number": f"T{sequence}",
        "observation_time": now_datetime(),
        "shift": job_card.get("custom_shift_type") or "",
        "recorded_by": frappe.session.user,
        "recorded_on": now_datetime(),
        "status": "Complete",
    })
    for row in rows:
        observation.append("values", row)
    _insert_observation(observation)
    if monitor.status == "Draft":
        _set_monitor_status(monitor, "Active")
    return {"name": observation.name, "observation_number": observation.observation_number, "monitor": monitor.name}


def _check_roles(roles: set[str], message: str):
    if not roles.intersection(set(frappe.get_roles())):
        frappe.throw(_(message), frappe.PermissionError)


@frappe.whitelist()
def acknowledge_process_exception(observation: str, value: str):
    _check_roles(ACKNOWLEDGEMENT_ROLES, "Production Engineer authority is required to acknowledge Process Parameter exceptions.")
    doc = frappe.get_doc(OBSERVATION_DOCTYPE, observation)
    doc.check_permission("write")
    monitor = frappe.get_doc(MONITOR_DOCTYPE, doc.monitor)
    job_card, _work_order = _get_controlled_context(monitor.job_card)
    _validate_review_authority(job_card)
    row = next((item for item in doc.values if item.name == value), None)
    if not row or row.comparison_result != "Outside":
        frappe.throw(_("A valid outside-specification Process Parameter value is required."))
    if cint(row.exception_acknowledged):
        return {"observation": doc.name, "value": row.name, "acknowledged_by": row.acknowledged_by, "acknowledged_on": row.acknowledged_on}
    previous = getattr(frappe.flags, "controlled_process_observation_update", False)
    frappe.flags.controlled_process_observation_update = True
    try:
        row.exception_acknowledged = 1
        row.acknowledged_by = frappe.session.user
        row.acknowledged_on = now_datetime()
        doc.save()
    finally:
        frappe.flags.controlled_process_observation_update = previous
    return {"observation": doc.name, "value": row.name, "acknowledged_by": row.acknowledged_by, "acknowledged_on": row.acknowledged_on}


def _effective_observations(monitor: str):
    rows = frappe.get_all(
        OBSERVATION_DOCTYPE,
        filters={"monitor": monitor},
        fields=["name", "status", "observation_sequence", "observation_number", "observation_time", "shift", "recorded_by", "correction_of"],
        order_by="observation_sequence asc, creation asc",
    )
    corrected = {cstr(row.correction_of) for row in rows if row.correction_of}
    return [row for row in rows if row.name not in corrected and row.status != "Superseded"]


def _validate_monitor_completion(monitor):
    observations = _effective_observations(monitor.name)
    if not observations:
        frappe.throw(_("At least one complete Process Observation is required."))
    unresolved = []
    for observation in observations:
        values = frappe.get_all(
            VALUE_DOCTYPE,
            filters={"parent": observation.name, "parenttype": OBSERVATION_DOCTYPE},
            fields=["display_label", "comparison_result", "exception_reason", "exception_acknowledged"],
        )
        for row in values:
            if row.comparison_result == "Outside" and (not cstr(row.exception_reason).strip() or not cint(row.exception_acknowledged)):
                unresolved.append(f"{observation.observation_number}: {row.display_label}")
    if unresolved:
        frappe.throw(_("Acknowledge all Process Parameter exceptions before completion: {0}.").format(", ".join(unresolved)))


@frappe.whitelist()
def complete_process_parameter_monitor(name: str):
    _check_roles(COMPLETION_ROLES, "Production Engineer authority is required to complete Process Parameter Monitor.")
    monitor = frappe.get_doc(MONITOR_DOCTYPE, name)
    monitor.check_permission("write")
    if monitor.status not in {"Draft", "Active"}:
        frappe.throw(_("Only Draft or Active Process Parameter Monitor can be completed."))
    job_card, _work_order = _get_controlled_context(monitor.job_card)
    _validate_new_observation_authority(job_card)
    _validate_monitor_completion(monitor)
    monitor.completed_by = frappe.session.user
    monitor.completed_on = now_datetime()
    _set_monitor_status(monitor, "Completed")
    return {"name": monitor.name, "status": monitor.status, "completed_by": monitor.completed_by, "completed_on": monitor.completed_on}


@frappe.whitelist()
def correct_process_observation(observation: str, reason: str, values):
    _check_roles(ACKNOWLEDGEMENT_ROLES, "Production Engineer authority is required to correct Process Observations.")
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(OBSERVATION_DOCTYPE, observation)
    original.check_permission("write")
    monitor = frappe.get_doc(MONITOR_DOCTYPE, original.monitor)
    if monitor.status != "Active":
        frappe.throw(_("Observation correction is available only while Process Parameter Monitor is Active."))
    job_card, _work_order = _get_controlled_context(monitor.job_card)
    _validate_new_observation_authority(job_card)
    _lock_monitor(monitor.name)
    if frappe.db.exists(OBSERVATION_DOCTYPE, {"correction_of": original.name, "status": ("!=", "Superseded")}):
        frappe.throw(_("Correct the latest effective Process Observation instead of historical evidence."))
    rows = _build_observation_values(monitor, values)
    replacement = frappe.new_doc(OBSERVATION_DOCTYPE)
    replacement.update({
        "monitor": monitor.name, "job_card": monitor.job_card, "work_order": monitor.work_order,
        "fg_item": monitor.fg_item, "fg_batch": monitor.fg_batch,
        "observation_sequence": original.observation_sequence,
        "observation_number": original.observation_number,
        "observation_time": now_datetime(), "shift": job_card.get("custom_shift_type") or "",
        "recorded_by": frappe.session.user, "recorded_on": now_datetime(), "status": "Complete",
        "correction_of": original.name, "correction_reason": reason,
    })
    for row in rows:
        replacement.append("values", row)
    _insert_observation(replacement)
    previous = getattr(frappe.flags, "controlled_process_observation_update", False)
    frappe.flags.controlled_process_observation_update = True
    try:
        original.status = "Superseded"
        original.superseded_by = replacement.name
        original.save()
    finally:
        frappe.flags.controlled_process_observation_update = previous
    return {"name": replacement.name, "correction_of": original.name, "observation_number": replacement.observation_number}


@frappe.whitelist()
def make_process_parameter_correction(name: str, reason: str):
    _check_roles(CORRECTION_ROLES, "Production Head or Manufacturing Manager authority is required for correction.")
    reason = cstr(reason).strip()
    if not reason:
        frappe.throw(_("Correction Reason is mandatory."))
    original = frappe.get_doc(MONITOR_DOCTYPE, name)
    original.check_permission("write")
    if original.status != "Completed":
        frappe.throw(_("Only a Completed Process Parameter Monitor can be corrected."))
    job_card, work_order = _get_controlled_context(original.job_card)
    _validate_creation_authority(job_card, work_order)
    correction = frappe.new_doc(MONITOR_DOCTYPE)
    correction.job_card = original.job_card
    correction.correction_of = original.name
    correction.correction_reason = reason
    previous = getattr(frappe.flags, "controlled_process_parameter_creation", False)
    frappe.flags.controlled_process_parameter_creation = True
    try:
        correction.insert(ignore_permissions=True)
    finally:
        frappe.flags.controlled_process_parameter_creation = previous
    original.superseded_by = correction.name
    _set_monitor_status(original, "Superseded")
    return {"name": correction.name, "correction_of": original.name}


@frappe.whitelist()
def get_process_parameter_monitor_view(name: str):
    monitor = frappe.get_doc(MONITOR_DOCTYPE, name)
    monitor.check_permission("read")
    observations = frappe.get_all(
        OBSERVATION_DOCTYPE,
        filters={"monitor": name},
        fields=["name", "status", "observation_sequence", "observation_number", "observation_time", "shift", "recorded_by", "recorded_on", "correction_of", "correction_reason", "superseded_by"],
        order_by="observation_sequence asc, creation asc",
    )
    for observation in observations:
        observation["values"] = frappe.get_all(
            VALUE_DOCTYPE,
            filters={"parent": observation.name, "parenttype": OBSERVATION_DOCTYPE},
            fields=["name", *VALUE_FIELDS],
            order_by="group_sequence asc, sequence asc",
        )
    return {"monitor": monitor.as_dict(), "observations": observations}


def _mpds_blocker(job_card: str) -> str:
    try:
        job_card_doc, work_order = _get_controlled_context(job_card)
        fg_item = work_order.production_item
        production_line = job_card_doc.get("custom_production_line") or ""
        if not production_line:
            return "Production Line is required before Process Parameter Monitor creation."
        if not frappe.db.exists(MPDS_DOCTYPE, {"fg_item": fg_item, "production_line": production_line, "status": CURRENT_STATUS}):
            return f"No Approved / Current MPDS exists for FG {fg_item} on {production_line}."
    except Exception as exc:
        return cstr(exc)
    return ""


def get_process_parameter_module(job_card: str, creation_allowed: bool, current_shift: str = "") -> dict[str, Any]:
    monitors = frappe.get_all(
        MONITOR_DOCTYPE,
        filters={"job_card": job_card},
        fields=["name", "status", "modified", "fg_batch", "mpds", "mpds_no", "mpds_revision"],
        order_by="modified desc",
    )
    counts = defaultdict(int)
    for monitor in monitors:
        counts[monitor.status] += 1
    current = next((row for row in monitors if row.status != "Superseded"), None)
    observation_count = frappe.db.count(OBSERVATION_DOCTYPE, {"monitor": current.name}) if current else 0
    blocker = "" if current else _mpds_blocker(job_card)
    return {
        "key": "process_parameters",
        "label": "Process Parameters",
        "document_code": DOCUMENT_CODE,
        "revision": current.mpds_revision if current else "",
        "status": current.status if current else "Optional",
        "record_count": len(monitors),
        "observation_count": observation_count,
        "latest_record": current.name if current else "",
        "mpds": current.mpds if current else "",
        "mpds_no": current.mpds_no if current else "",
        "last_event": current.modified if current else "",
        "open_issue": blocker,
        "action_enabled": bool(creation_allowed and not current and not blocker),
        "view_enabled": True,
        "execution_available": bool(creation_allowed),
        "current_shift": current_shift or "",
        "optional": True,
    }


def _unresolved_monitor_detail(row):
    return f"{row.name} ({row.status}: Process Parameter Monitor completion pending)"


def validate_process_parameter_completion(doc):
    monitors = frappe.get_all(
        MONITOR_DOCTYPE,
        filters={"job_card": doc.name},
        fields=["name", "status", "superseded_by"],
        order_by="creation asc",
    )
    unresolved = [row for row in monitors if row.status in {"Draft", "Active"}]
    broken = [row for row in monitors if row.status == "Superseded" and not row.superseded_by]
    problems = [_unresolved_monitor_detail(row) for row in unresolved]
    problems.extend(f"{row.name} (Superseded without resolved correction)" for row in broken)
    if problems:
        frappe.throw(_("Resolve Process Parameter Monitor evidence before completing this Job Card: {0}.").format(", ".join(problems)))
