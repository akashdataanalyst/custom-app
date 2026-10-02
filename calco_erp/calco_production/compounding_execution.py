from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cstr, flt, get_datetime, now_datetime

from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION
from calco_erp.calco_production.wip_consumption import get_execution_wip_context, is_controlled_work_order


CONTROLLED_DOCUMENTS = (
    {"key": "premix", "group": "Material & Feeding", "label": "Premix", "document_code": "F-PMS-01", "revision": "REV-03", "source": "Premix Sheet"},
    {"key": "blending", "group": "Material & Feeding", "label": "Blending", "document_code": "F-BR-01", "revision": "REV-01", "source": "Blending Record"},
    {"key": "silo_control", "group": "Material & Feeding", "label": "Silo Control", "document_code": "F-SCS-01", "revision": "REV-02", "source": "Silo Control Sheet"},
    {"key": "feeder_run", "group": "Material & Feeding", "label": "Feeder Run", "document_code": "F-FRS-01", "revision": "REV-03", "source": "Feeder HMI"},
    {"key": "bulk_density", "group": "Material & Feeding", "label": "Bulk Density", "document_code": "F-BDMS-01", "revision": "REV-01", "source": "Manual laboratory reading"},
    {"key": "process_parameters", "group": "Process Control", "label": "Process Parameters", "document_code": "F-PRD-01/01", "revision": "", "source": "Controlled process observations"},
    {"key": "shift_reporting", "group": "Shift", "label": "Shift Reporting", "document_code": "F-PRD-01", "revision": "REV-01", "source": "Shift Reporting Register / Extruder HMI"},
)

PERMISSION_CONTRACT = {
    "operator_capture": ["Manufacturing User"],
    "engineer_review": ["Production Engineer", "Manufacturing Manager"],
    "head_approval_and_backdating": ["Production Head", "Manufacturing Manager"],
    "quality_authority": ["Quality User", "Quality Manager"],
    "system_administration": ["System Manager"],
}

TIMESTAMP_CONTRACT = {
    "actual_timestamp": "Server current time by default",
    "entry_timestamp": "Server creation timestamp",
    "recorded_by": "Authenticated session user",
    "shift": "Independent shift context linked to the same Job Card",
    "back_entry": {"enabled": False, "required_evidence": ["actual_timestamp", "entry_timestamp", "reason", "authorized_by"]},
}

CORRECTION_CONTRACT = {
    "states": ["Draft", "Finalized", "Superseded"],
    "correction_requires": ["reason", "requested_by", "requested_on", "authorized_by"],
    "history_rule": "Finalized evidence is superseded or amended; it is never silently overwritten.",
}

PRINT_CONTRACT = {"required_identity": ["controlled_document_code", "revision", "job_card", "work_order", "fg_item", "fg_batch", "date_or_shift", "prepared_by", "approved_by"]}

BULK_DENSITY_CONTRACT = {
    "calculation_mode": "Manual",
    "must_not_calculate_from_w1_w2": True,
    "fields": ["server_datetime", "fg_item_from_job_card", "fg_batch_from_job_card", "rm_item_from_work_order_wip", "rm_batch_from_work_order_wip", "feeder_no", "w1_manual", "w2_manual", "bulk_density_manual", "recorded_by", "recorded_on"],
}

# Future typed modules register validators here. Phase 1 adds no new gates.
START_VALIDATORS: tuple[str, ...] = ()
COMPLETION_VALIDATORS: tuple[str, ...] = (
    "calco_erp.calco_production.premix_execution.validate_no_open_premix_runs",
    "calco_erp.calco_production.blending_execution.validate_blending_completion",
    "calco_erp.calco_production.silo_control.validate_silo_completion",
    "calco_erp.calco_production.feeder_run.validate_feeder_run_completion",
    "calco_erp.calco_production.bulk_density_monitoring.validate_bulk_density_completion",
    "calco_erp.calco_production.process_parameter_monitoring.validate_process_parameter_completion",
    "calco_erp.calco_production.shift_reporting.validate_shift_reporting_completion",
)


def is_controlled_compounding_job_card(doc) -> bool:
    return bool(
        cstr(doc.get("operation")).strip() == COMPOUNDING_OPERATION
        and cstr(doc.get("work_order")).strip()
        and is_controlled_work_order(doc.get("work_order"))
    )


def build_compounding_cockpit(doc, wo, grade_change: dict[str, Any], initial_qc: dict[str, Any]) -> dict[str, Any]:
    if not is_controlled_compounding_job_card(doc):
        return {}

    started = job_card_has_started(doc)
    complete = job_card_is_complete(doc)
    paused = bool(doc.get("is_paused") or cstr(doc.get("status")).strip() == "On Hold")
    blending_creation_allowed = bool(
        started
        and not paused
        and not complete
        and cstr(doc.get("status")).strip().casefold() == "work in progress"
    )
    planned_qty = flt(doc.get("for_quantity") or (wo.get("qty") if wo else 0))
    completed_qty = flt(doc.get("total_completed_qty"))
    wip_context = get_execution_wip_context(doc.get("work_order"), include_history=True)

    return {
        "state": "Completed" if complete else "On Hold" if paused else "In Progress" if started else "Not Started",
        "context": {
            "work_order": doc.get("work_order") or "",
            "job_card": doc.get("name") or "",
            "operation": doc.get("operation") or "",
            "production_line": doc.get("custom_production_line") or "",
            "machine": doc.get("custom_machine") or doc.get("workstation") or "",
            "fg_item": (wo.get("production_item") if wo else "") or "",
            "fg_batch": doc.get("custom_fg_batch_no") or (wo.get("custom_fg_batch_no") if wo else "") or "",
            "bom": doc.get("bom_no") or (wo.get("bom_no") if wo else "") or "",
            "planned_qty": planned_qty,
            "completed_qty": completed_qty,
            "job_card_status": doc.get("status") or "",
            "is_paused": paused,
            "execution_start": doc.get("actual_start_date"),
            "execution_end": doc.get("actual_end_date"),
            "elapsed_seconds": get_elapsed_seconds(doc),
            "current_shift": doc.get("custom_shift_type") or "",
            "operator": doc.get("custom_operator") or "",
            "current_user": get_current_user(),
            "grade_change_clearance": grade_change.get("clearance") or "",
            "grade_change_status": grade_change.get("status") or "",
            "initial_qc_status": initial_qc.get("status") or "Pending",
            "initial_qc": initial_qc.get("name") or "",
        },
        "wip_context": wip_context,
        "module_groups": build_module_groups(
            doc.get("name"),
            started,
            doc.get("custom_shift_type"),
            blending_creation_allowed=blending_creation_allowed,
        ),
        "contracts": {
            "timestamp_and_audit": TIMESTAMP_CONTRACT,
            "multi_shift": {"one_job_card_many_shifts": True, "shift_change_creates_job_card": False, "future_shift_record_link": "Job Card"},
            "permissions": PERMISSION_CONTRACT,
            "correction_and_revision": CORRECTION_CONTRACT,
            "print": PRINT_CONTRACT,
            "bulk_density": BULK_DENSITY_CONTRACT,
            "feeder_sources": {"feeder_run": "Feeder HMI", "shift_reporting": "Extruder HMI", "shared_dataset": False},
        },
    }


def build_module_groups(
    job_card: str,
    execution_started: bool,
    current_shift: str | None = None,
    blending_creation_allowed: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for definition in CONTROLLED_DOCUMENTS:
        if definition["key"] == "premix":
            from calco_erp.calco_production.premix_execution import get_premix_module

            module = get_premix_module(job_card, execution_started, current_shift or "")
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "blending":
            from calco_erp.calco_production.blending_execution import get_blending_module

            module = get_blending_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "silo_control":
            from calco_erp.calco_production.silo_control import get_silo_module

            module = get_silo_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "feeder_run":
            from calco_erp.calco_production.feeder_run import get_feeder_run_module

            module = get_feeder_run_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "bulk_density":
            from calco_erp.calco_production.bulk_density_monitoring import (
                get_bulk_density_module,
            )

            module = get_bulk_density_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "process_parameters":
            from calco_erp.calco_production.process_parameter_monitoring import (
                get_process_parameter_module,
            )

            module = get_process_parameter_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        if definition["key"] == "shift_reporting":
            from calco_erp.calco_production.shift_reporting import (
                get_shift_reporting_module,
            )

            module = get_shift_reporting_module(
                job_card, blending_creation_allowed, current_shift or ""
            )
            groups.setdefault(definition["group"], []).append(module)
            continue
        module = dict(definition)
        module.update(
            {
                "status": "Not Implemented",
                "record_count": 0,
                "last_event": "",
                "open_issue": "Future phase",
                "action": "",
                "action_enabled": False,
                "execution_available": bool(execution_started),
                "current_shift": current_shift or "",
            }
        )
        groups.setdefault(module.pop("group"), []).append(module)
    return [{"label": label, "modules": modules} for label, modules in groups.items()]


def get_elapsed_seconds(doc) -> int:
    elapsed = 0.0
    current = None
    for row in doc.get("time_logs") or []:
        if row.get("time_in_mins"):
            elapsed += flt(row.get("time_in_mins")) * 60
            continue
        if row.get("from_time"):
            start = get_datetime(row.get("from_time"))
            if row.get("to_time"):
                end = get_datetime(row.get("to_time"))
            else:
                current = current or now_datetime()
                end = current
            elapsed += max((end - start).total_seconds(), 0)
    return int(round(elapsed))


def get_current_user() -> str:
    try:
        return cstr(frappe.session.user)
    except RuntimeError:
        return ""


def job_card_has_started(doc) -> bool:
    return bool(
        doc.get("actual_start_date")
        or doc.get("time_logs")
        or flt(doc.get("total_completed_qty"))
        or cstr(doc.get("status")).strip() in {"Work in Progress", "In Process"}
    )


def job_card_is_complete(doc) -> bool:
    return bool(doc.get("docstatus") == 1 and cstr(doc.get("status")).strip() in {"Completed", "Closed"})


def validate_compounding_start_extension(doc):
    if is_controlled_compounding_job_card(doc):
        run_extension_validators(START_VALIDATORS, doc)


def validate_compounding_completion_extension(doc, method=None):
    if is_controlled_compounding_job_card(doc):
        run_extension_validators(COMPLETION_VALIDATORS, doc)


def run_extension_validators(validators: tuple[str, ...], doc):
    for validator in validators:
        frappe.get_attr(validator)(doc)
