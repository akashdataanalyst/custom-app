from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _
from frappe.utils import cstr, flt

from calco_erp.calco_production import work_order_lifecycle as lifecycle
from calco_erp.calco_production.in_process_quality import state_for_work_order


STATUS_NOT_STARTED = "Not Started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_COMPLETED = "Completed"
STATUS_BLOCKED = "Blocked"
STATUS_DRAFT_RESERVATION = "Draft Reservation"

COLOR_BY_STATUS = {
    STATUS_NOT_STARTED: "grey",
    STATUS_IN_PROGRESS: "blue",
    STATUS_COMPLETED: "green",
    STATUS_BLOCKED: "red",
    STATUS_DRAFT_RESERVATION: "orange",
    "Pending Evaluation": "orange",
    "Required - Checklist Pending": "orange",
    "Reset Approval Required": "orange",
    "Pending Approval": "orange",
    "Completed - Not Required": "green",
    "Rejected": "red",
}

STAGE_ORDER = [
    "work_order_created",
    "material_reservation",
    "material_transfer",
    "grade_change_checklist",
    "premix_preparation",
    "rm_loading",
    "compounding",
    "pelletizing",
    "initial_qc",
    "fg_receipt",
    "final_qc",
    "completed",
    "fg_release",
]

CONTROLLED_STAGE_ORDER = [
    "work_order_created",
    "production_readiness",
    "material_reservation",
    "material_transfer",
    "production_execution",
    "final_qc",
    "fg_release",
    "completed",
]

STAGE_TO_KEY = {
    lifecycle.STAGE_MATERIAL_TRANSFER_PENDING: "work_order_created",
    lifecycle.STAGE_MATERIAL_TRANSFERRED: "material_transfer",
    lifecycle.STAGE_GRADE_CHANGE_CHECKLIST: "grade_change_checklist",
    lifecycle.STAGE_PREMIX_PREPARATION: "premix_preparation",
    lifecycle.STAGE_RM_LOADING: "rm_loading",
    lifecycle.STAGE_COMPOUNDING: "compounding",
    lifecycle.STAGE_PELLETIZING: "pelletizing",
    lifecycle.STAGE_INITIAL_QC_PENDING: "initial_qc",
    lifecycle.STAGE_INITIAL_QC_FAILED: "initial_qc",
    lifecycle.STAGE_INITIAL_QC_PASSED: "initial_qc",
    lifecycle.STAGE_PRODUCTION_RUNNING: "fg_receipt",
    lifecycle.STAGE_FINAL_QC_PENDING: "final_qc",
    lifecycle.STAGE_COMPLETED: "completed",
}


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    responsible_role: str


STAGE_SPECS = {
    "work_order_created": StageSpec("work_order_created", "Work Order", "Production Manager"),
    "production_readiness": StageSpec("production_readiness", "Production Readiness", "Production Head"),
    "material_reservation": StageSpec("material_reservation", "Material Reservation", "Production Head"),
    "material_transfer": StageSpec("material_transfer", "Material Transfer", "Stores User"),
    "grade_change_checklist": StageSpec("grade_change_checklist", "Grade Change Checklist", "Production Engineer"),
    "premix_preparation": StageSpec("premix_preparation", "Premix Preparation", "Production Engineer"),
    "rm_loading": StageSpec("rm_loading", "RM Loading", "Production Operator"),
    "compounding": StageSpec("compounding", "Compounding", "Production Operator"),
    "pelletizing": StageSpec("pelletizing", "Pelletizing", "Production Operator"),
    "initial_qc": StageSpec("initial_qc", "Initial QC", "Quality User"),
    "fg_receipt": StageSpec("fg_receipt", "FG Receipt", "Stores User"),
    "final_qc": StageSpec("final_qc", "Final QC", "Quality User"),
    "completed": StageSpec("completed", "Completed", "Production Manager"),
    "fg_release": StageSpec("fg_release", "FG Release", "Quality User"),
    "production_execution": StageSpec("production_execution", "Production Execution", "Production Operator"),
}

FIELD_STAGE_KEYS = {
    "grade_change_checklist",
    "premix_preparation",
    "rm_loading",
    "compounding",
    "pelletizing",
}


def _clean_kwargs(kwargs: dict[str, Any] | None) -> dict[str, Any]:
    cleaned = dict(kwargs or {})
    cleaned.pop("cmd", None)
    cleaned.pop("method", None)
    return cleaned


@frappe.whitelist()
def get_work_order_journey(work_order: str | None = None, *args, **kwargs) -> dict[str, Any]:
    kwargs = _clean_kwargs(kwargs)
    work_order = work_order or kwargs.get("work_order") or kwargs.get("docname")
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))

    wo = frappe.get_doc("Work Order", work_order)
    from calco_erp.calco_production.material_reservation_transfer import (
        is_controlled_calco_work_order,
    )

    controlled = is_controlled_calco_work_order(wo.name)
    stock_entries = get_stock_entries(wo.name)
    material_transfer = first_stock_entry(stock_entries, "Material Transfer for Manufacture")
    manufacture_entry = first_stock_entry(stock_entries, "Manufacture")
    execution = get_production_execution_state(wo, manufacture_entry) if controlled else {}
    compounding_job_card = execution.get("compounding_job_card") or get_compounding_job_card(wo.name)
    readiness = get_production_readiness_state(wo) if controlled else None

    final_qc = get_linked_qi(
        wo, lifecycle.WORK_ORDER_FINAL_QI_FIELD, lifecycle.QI_STAGE_FINAL
    )
    linked_docs = {
        "material_reservation": get_material_reservation_journey_state(wo.name) if controlled else None,
        "material_transfer": material_transfer,
        "compounding_job_card": compounding_job_card,
        "initial_qc": get_linked_qi(wo, lifecycle.WORK_ORDER_INITIAL_QI_FIELD, lifecycle.QI_STAGE_INITIAL),
        "initial_qc_retest": get_linked_qi(wo, lifecycle.WORK_ORDER_RETEST_QI_FIELD, lifecycle.QI_STAGE_RETEST),
        "fg_receipt": manufacture_entry,
        "final_qc": final_qc,
        "fg_release": get_linked_final_qc_release(final_qc),
        "production_readiness": readiness,
        "production_execution": execution,
        "controlled": controlled,
    }

    current_stage = wo.get(lifecycle.WORK_ORDER_STAGE_FIELD) or lifecycle.STAGE_MATERIAL_TRANSFER_PENDING
    current_key = STAGE_TO_KEY.get(current_stage, "work_order_created")
    stage_order = CONTROLLED_STAGE_ORDER if controlled else [
        key for key in STAGE_ORDER if key != "material_reservation"
    ]
    if compounding_job_card:
        stage_order = [key for key in stage_order if key != "premix_preparation"]
    stages = [build_stage(wo, key, current_key, linked_docs) for key in stage_order]
    apply_completed_display(wo, stages)

    return {
        "summary": {
            "work_order": wo.name,
            "fg_item": wo.production_item or "",
            "fg_item_name": frappe.db.get_value("Item", wo.production_item, "item_name") if wo.production_item else "",
            "planned_qty": round(flt(wo.qty), 3),
            "produced_qty": round(flt(wo.produced_qty), 3),
            "fg_batch_no": wo.get("custom_fg_batch_no") or "",
            "current_production_stage": current_stage,
            "status": wo.status or "",
            "controlled_material_flow": controlled,
            "production_completed_display": wo.docstatus == 1 and wo.status == STATUS_COMPLETED,
        },
        "stages": stages,
        "readiness": readiness,
        "in_process_qc": state_for_work_order(wo),
    }


def apply_completed_display(wo, stages):
    """Presentation only: current RM availability cannot reopen completed execution.

    Original readiness payload and all QC/release states remain authoritative.
    """
    if wo.docstatus != 1 or wo.status != STATUS_COMPLETED:
        return
    for stage in stages:
        if stage['key'] == 'production_readiness':
            stage.update(status='Not Applicable',color='grey',blocked_reason='',historical=True,
                summary=_('Production completed. Current availability is not a new production prerequisite.'))


def build_stage(wo, key: str, current_key: str, linked_docs: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    spec = STAGE_SPECS[key]
    status = derive_stage_status(wo, key, current_key, linked_docs)
    blocked_reason = get_blocked_reason(wo, key, status, linked_docs)
    action = get_stage_action(wo, key, status, linked_docs, blocked_reason)
    linked = get_stage_linked_doc(key, linked_docs)

    stage = {
        "key": key,
        "label": spec.label,
        "status": status,
        "color": COLOR_BY_STATUS.get(status, "grey"),
        "responsible_role": get_responsible_role(spec, key, linked_docs),
        "action_label": action.get("label", ""),
        "action_type": action.get("type", ""),
        "action_method_or_route": action.get("target", ""),
        "action_args": action.get("args", {}),
        "action_disabled": bool(action.get("disabled")),
        "linked_doctype": linked.get("doctype", "") if linked else "",
        "linked_docname": linked.get("name", "") if linked else "",
        "linked_status": linked.get("status", "") if linked else "",
        "blocked_reason": blocked_reason,
        "summary": get_stage_summary(
            wo, key, status, linked, blocked_reason, linked_docs=linked_docs
        ),
    }
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        active = execution.get("active_job_card") or {}
        stage["execution_details"] = {
            "active_job_card": active.get("name") or "",
            "operation": active.get("operation") or "",
            "execution_stage": execution.get("execution_stage") or "",
            "machine": active.get("custom_machine")
            or active.get("custom_production_line")
            or active.get("workstation")
            or "",
            "operator": active.get("custom_operator") or "",
            "shift": active.get("custom_shift_type") or "",
            "fg_batch_no": active.get("custom_fg_batch_no")
            or wo.get("custom_fg_batch_no")
            or "",
            "produced_qty": round(flt(wo.get("produced_qty")), 3),
            "planned_qty": round(flt(wo.get("qty")), 3),
            "operations": execution.get("operation_groups") or [],
        }
    return stage


def derive_stage_status(wo, key: str, current_key: str, linked_docs: dict[str, dict[str, Any] | None]) -> str:
    stage = wo.get(lifecycle.WORK_ORDER_STAGE_FIELD) or lifecycle.STAGE_MATERIAL_TRANSFER_PENDING
    initial_qc = wo.get(lifecycle.WORK_ORDER_INITIAL_QC_STATUS_FIELD) or lifecycle.INITIAL_QC_PENDING
    final_qc = wo.get(lifecycle.WORK_ORDER_FINAL_QC_STATUS_FIELD) or lifecycle.FINAL_QC_PENDING

    if key == "work_order_created":
        return STATUS_COMPLETED if wo.name else STATUS_NOT_STARTED
    if key == "production_readiness":
        return STATUS_COMPLETED if production_readiness_is_ready(wo, linked_docs) else STATUS_BLOCKED
    if key == "material_reservation":
        reservation = linked_docs.get("material_reservation") or {}
        state = reservation.get("state")
        if state in {"Reserved", "Partially Used", "Closed"}:
            return STATUS_COMPLETED
        if not production_readiness_is_ready(wo, linked_docs):
            return STATUS_BLOCKED
        if not reservation.get("sre_names"):
            return STATUS_NOT_STARTED
        if state == "Draft":
            return STATUS_DRAFT_RESERVATION
        return STATUS_BLOCKED
    if key == "material_transfer":
        reservation = linked_docs.get("material_reservation") or {}
        if reservation and reservation.get("state") not in {
            "Reserved",
            "Partially Used",
            "Closed",
        }:
            return STATUS_BLOCKED
        return (
            STATUS_COMPLETED
            if is_submitted(linked_docs.get("material_transfer"))
            else STATUS_IN_PROGRESS
        )
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        if not is_submitted(linked_docs.get("material_transfer")):
            return STATUS_NOT_STARTED
        if execution.get("manufacture_complete"):
            return STATUS_COMPLETED
        if execution.get("blocked_reason"):
            return STATUS_BLOCKED
        return STATUS_IN_PROGRESS if execution.get("job_cards") else STATUS_BLOCKED
    if key == "grade_change_checklist" and linked_docs.get("compounding_job_card"):
        from calco_erp.calco_production.job_card_execution import (
            derive_grade_change_status,
        )

        return derive_grade_change_status(linked_docs["compounding_job_card"])
    if key == "rm_loading" and linked_docs.get("compounding_job_card"):
        from calco_erp.calco_production.grade_change_control import get_grade_change_journey_state

        grade_state = get_grade_change_journey_state(
            wo.name, linked_docs["compounding_job_card"].get("name")
        )["status"]
        if grade_state not in {STATUS_COMPLETED, "Completed - Not Required"}:
            return STATUS_BLOCKED
    if key in FIELD_STAGE_KEYS:
        if stage_after_or_equal(stage, stage_for_field_key(key)):
            return STATUS_COMPLETED
        if STAGE_ORDER.index(key) == STAGE_ORDER.index(current_key):
            return STATUS_IN_PROGRESS
        return STATUS_NOT_STARTED
    if key == "initial_qc":
        if initial_qc == lifecycle.INITIAL_QC_FAILED or stage == lifecycle.STAGE_INITIAL_QC_FAILED:
            return STATUS_BLOCKED
        if initial_qc == lifecycle.INITIAL_QC_PASSED:
            return STATUS_COMPLETED
        if stage_after_or_equal(stage, lifecycle.STAGE_INITIAL_QC_PENDING):
            return STATUS_IN_PROGRESS
        return STATUS_NOT_STARTED
    if key == "fg_receipt":
        if initial_qc != lifecycle.INITIAL_QC_PASSED:
            return STATUS_BLOCKED
        if is_submitted(linked_docs.get("fg_receipt")) or stage_after_or_equal(stage, lifecycle.STAGE_FINAL_QC_PENDING):
            return STATUS_COMPLETED
        return STATUS_IN_PROGRESS
    if key == "final_qc":
        if not is_submitted(linked_docs.get("fg_receipt")) and not stage_after_or_equal(stage, lifecycle.STAGE_FINAL_QC_PENDING):
            return STATUS_NOT_STARTED
        if final_qc == lifecycle.FINAL_QC_PASSED:
            return STATUS_COMPLETED
        if final_qc == lifecycle.FINAL_QC_FAILED:
            return STATUS_BLOCKED
        return STATUS_IN_PROGRESS
    if key == "completed":
        if linked_docs.get("controlled"):
            release = linked_docs.get("fg_release") or {}
            return (
                STATUS_COMPLETED
                if release.get("docstatus") == 1 and release.get("status") == "Released"
                else STATUS_NOT_STARTED
            )
        return STATUS_COMPLETED if stage == lifecycle.STAGE_COMPLETED else STATUS_NOT_STARTED
    if key == "fg_release":
        if final_qc == lifecycle.FINAL_QC_FAILED:
            return STATUS_BLOCKED
        if final_qc != lifecycle.FINAL_QC_PASSED:
            return STATUS_NOT_STARTED
        release = linked_docs.get("fg_release")
        if release and release.get("docstatus") == 1 and release.get("status") == "Released":
            return STATUS_COMPLETED
        return STATUS_IN_PROGRESS
    return STATUS_NOT_STARTED


def get_blocked_reason(wo, key: str, status: str, linked_docs=None) -> str:
    if status != STATUS_BLOCKED:
        return ""
    initial_qc = wo.get(lifecycle.WORK_ORDER_INITIAL_QC_STATUS_FIELD) or lifecycle.INITIAL_QC_PENDING
    final_qc = wo.get(lifecycle.WORK_ORDER_FINAL_QC_STATUS_FIELD) or lifecycle.FINAL_QC_PENDING
    remarks = (wo.get(lifecycle.WORK_ORDER_QC_REMARKS_FIELD) or "").strip()
    linked_docs = linked_docs or {}

    if key == "material_reservation":
        reservation = linked_docs.get("material_reservation") or {}
        if not production_readiness_is_ready(wo, linked_docs):
            return _("Production Readiness must be Ready before Material Reservation.")
        if reservation.get("state") in {"Stale", "Cancelled", "Failed"}:
            return _(
                "Material Reservation is {0}; create or submit a valid complete reservation set."
            ).format(reservation.get("state"))
        return _("Complete Material Reservation before Material Transfer.")
    if key == "production_readiness":
        readiness = linked_docs.get("production_readiness") or {}
        blocker = (readiness.get("blockers") or [{}])[0]
        return blocker.get("reason") or _("Production prerequisites are incomplete.")
    if key == "material_transfer":
        return _("Complete and submit Material Reservation before creating Material Transfer.")
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        return execution.get("blocked_reason") or _("Production operation Job Cards are required.")
    if key == "rm_loading":
        return _("Approved F-GCL-01 Grade Change Clearance is required before RM Loading.")

    if key == "initial_qc" and initial_qc == lifecycle.INITIAL_QC_FAILED:
        if not remarks:
            return _("QC correction remarks are required before retest can continue.")
        return _("Initial QC failed. Correction and retest are required.")
    if key == "fg_receipt":
        if initial_qc == lifecycle.INITIAL_QC_FAILED:
            return _("FG Receipt is blocked because Initial QC failed and retest is required.")
        return _("Initial QC must be Passed before FG Receipt can be created.")
    if key == "final_qc" and final_qc == lifecycle.FINAL_QC_FAILED:
        return _("Final QC failed. Completion is blocked.")
    if key == "fg_release" and final_qc == lifecycle.FINAL_QC_FAILED:
        return _("FG Release is blocked because Final QC was rejected.")
    return ""


def get_stage_action(wo, key: str, status: str, linked_docs: dict[str, dict[str, Any] | None], blocked_reason: str) -> dict[str, Any]:
    if key == "work_order_created":
        return route_action(_("Open Work Order"), ["Form", "Work Order", wo.name])
    if key == "production_readiness":
        return {
            "label": _("View Readiness"),
            "type": "readiness",
            "target": "",
            "args": {},
            "disabled": False,
        }
    if key == "material_reservation":
        reservation = linked_docs.get("material_reservation") or {}
        state = reservation.get("state")
        if not reservation.get("sre_names") or state in {"Cancelled", "Failed"}:
            return method_action(
                _("Create Material Reservation"),
                "calco_erp.calco_production.material_reservation_draft.create_draft_material_reservation",
                {"work_order": wo.name, "reservation_action": "create"},
                disabled=bool(blocked_reason),
            )
        if state == "Draft":
            return method_action(
                _("Review Material Reservation"),
                "calco_erp.calco_production.material_reservation_submission.get_material_reservation_review",
                {"work_order": wo.name, "reservation_action": "review"},
            )
        if state not in {"Reserved", "Partially Used", "Closed"}:
            return method_action(
                _("Review Material Reservation"),
                "calco_erp.calco_production.material_reservation_submission.get_material_reservation_set_state",
                {"work_order": wo.name},
                disabled=True,
            )
        return route_action(_("Material Reservation Completed"), ["Form", "Work Order", wo.name])
    if key == "material_transfer":
        reservation = linked_docs.get("material_reservation") or {}
        reservation_ready = reservation.get("state") in {
            "Reserved",
            "Partially Used",
            "Closed",
        }
        if not reservation_ready:
            return method_action(
                _("Create Material Transfer"),
                "calco_erp.calco_production.work_order_journey.make_work_order_stock_entry",
                {"work_order": wo.name, "purpose": "Material Transfer for Manufacture"},
                disabled=True,
            )
        linked = linked_docs.get("material_transfer")
        if linked:
            return route_action(_("Open Stock Entry"), ["Form", "Stock Entry", linked["name"]])
        return method_action(
            _("Create Material Transfer"),
            "calco_erp.calco_production.work_order_journey.make_work_order_stock_entry",
            {"work_order": wo.name, "purpose": "Material Transfer for Manufacture"},
        )
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        active = execution.get("active_job_card") or {}
        if active:
            return route_action(_("Open Job Card"), ["Form", "Job Card", active["name"]])
        linked = linked_docs.get("fg_receipt")
        if linked:
            return route_action(_("Open FG Receipt"), ["Form", "Stock Entry", linked["name"]])
        if execution.get("all_operations_complete"):
            return method_action(
                _("Create FG Receipt"),
                "calco_erp.calco_production.manufacture_entry.make_controlled_manufacture_entry",
                {"work_order": wo.name},
                disabled=bool(blocked_reason),
            )
        action = route_action(_("Open Work Order"), ["Form", "Work Order", wo.name])
        action["disabled"] = True
        return action
    if key == "grade_change_checklist":
        from calco_erp.calco_production.grade_change_control import get_grade_change_journey_state

        job_card = linked_docs.get("compounding_job_card") or {}
        grade_state = get_grade_change_journey_state(wo.name, job_card.get("name"))
        if grade_state.get("clearance"):
            return route_action(
                _("Open Grade Change Clearance"),
                ["Form", "Grade Change Clearance", grade_state["clearance"]],
            )
        return method_action(
            _("Create Grade Change Clearance"),
            "calco_erp.calco_production.grade_change_control.create_grade_change_clearance",
            {"work_order": wo.name, "job_card": job_card.get("name")},
            disabled=not bool(job_card.get("name")),
        )
    if key in FIELD_STAGE_KEYS:
        job_card = linked_docs.get("compounding_job_card")
        if job_card and key in {
            "grade_change_checklist",
            "rm_loading",
            "compounding",
            "pelletizing",
        }:
            return route_action(
                _("Open Job Card"),
                ["Form", "Job Card", job_card["name"]],
            )
        return route_action(_("Open Work Order"), ["Form", "Work Order", wo.name])
    if key == "initial_qc":
        retest = linked_docs.get("initial_qc_retest")
        initial = linked_docs.get("initial_qc")
        if retest:
            return route_action(_("Open linked QI"), ["Form", "Quality Inspection", retest["name"]])
        if initial and status != STATUS_BLOCKED:
            return route_action(_("Open linked QI"), ["Form", "Quality Inspection", initial["name"]])
        if wo.get(lifecycle.WORK_ORDER_RETEST_REQUIRED_FIELD):
            return method_action(
                _("Create Initial QC"),
                "calco_erp.calco_production.work_order_lifecycle.make_work_order_quality_inspection",
                {"work_order": wo.name, "qc_stage": lifecycle.QI_STAGE_RETEST},
            )
        if status == STATUS_BLOCKED:
            return method_action(
                _("Mark Retest Required"),
                "calco_erp.calco_production.work_order_lifecycle.mark_retest_required_for_work_order",
                {"work_order": wo.name},
            )
        return method_action(
            _("Create Initial QC"),
            "calco_erp.calco_production.work_order_lifecycle.make_work_order_quality_inspection",
            {"work_order": wo.name, "qc_stage": lifecycle.QI_STAGE_INITIAL},
        )
    if key == "fg_receipt":
        linked = linked_docs.get("fg_receipt")
        if linked:
            return route_action(_("Open Stock Entry"), ["Form", "Stock Entry", linked["name"]])
        from calco_erp.calco_production.manufacture_entry import is_controlled_work_order

        if is_controlled_work_order(wo.name):
            return method_action(
                _("Create Manufacture Entry"),
                "calco_erp.calco_production.manufacture_entry.make_controlled_manufacture_entry",
                {"work_order": wo.name},
                disabled=bool(blocked_reason),
            )
        return method_action(
            _("Create FG Receipt"),
            "calco_erp.calco_production.work_order_journey.make_work_order_stock_entry",
            {"work_order": wo.name, "purpose": "Manufacture"},
            disabled=bool(blocked_reason),
        )
    if key == "final_qc":
        linked = linked_docs.get("final_qc")
        if linked:
            return route_action(_("Open linked QI"), ["Form", "Quality Inspection", linked["name"]])
        return method_action(
            _("Create Final QC"),
            "calco_erp.calco_production.work_order_lifecycle.make_work_order_quality_inspection",
            {"work_order": wo.name, "qc_stage": lifecycle.QI_STAGE_FINAL},
            disabled=status == STATUS_NOT_STARTED,
        )
    if key == "completed":
        return route_action(_("Open Work Order"), ["Form", "Work Order", wo.name])
    if key == "fg_release":
        linked = linked_docs.get("fg_release")
        if linked:
            return route_action(
                _("Open Final QC Release"),
                ["Form", "Final QC Release", linked["name"]],
            )
        final_qc_doc = linked_docs.get("final_qc")
        return method_action(
            _("Create Final QC Release"),
            "calco_erp.calco_quality.doctype.final_qc_release.final_qc_release.make_final_qc_release",
            {"quality_inspection": final_qc_doc["name"] if final_qc_doc else ""},
            disabled=status in {STATUS_NOT_STARTED, STATUS_BLOCKED} or not final_qc_doc,
        )
    return {}


def route_action(label: str, route: list[str]) -> dict[str, Any]:
    return {"label": label, "type": "route", "target": route, "args": {}}


def method_action(label: str, method: str, args: dict[str, Any], disabled: bool = False) -> dict[str, Any]:
    return {"label": label, "type": "method", "target": method, "args": args, "disabled": disabled}


def get_stage_linked_doc(key: str, linked_docs: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        return execution.get("active_job_card") or linked_docs.get("fg_receipt")
    if key in {
        "grade_change_checklist",
        "rm_loading",
        "compounding",
        "pelletizing",
    }:
        return linked_docs.get("compounding_job_card")
    if key == "material_transfer":
        return linked_docs.get("material_transfer")
    if key == "initial_qc":
        return linked_docs.get("initial_qc_retest") or linked_docs.get("initial_qc")
    if key == "fg_receipt":
        return linked_docs.get("fg_receipt")
    if key == "final_qc":
        return linked_docs.get("final_qc")
    if key == "fg_release":
        return linked_docs.get("fg_release")
    return None


def get_stage_summary(
    wo,
    key: str,
    status: str,
    linked: dict[str, Any] | None,
    blocked_reason: str,
    linked_docs=None,
) -> str:
    linked_docs = linked_docs or {}
    if blocked_reason:
        return blocked_reason
    if key == "grade_change_checklist" and linked:
        from calco_erp.calco_production.grade_change_control import get_grade_change_journey_state

        return get_grade_change_journey_state(wo.name, linked.get("name"))["summary"]
    if linked:
        return _("Linked %(doctype)s %(name)s is %(status)s.") % {
            "doctype": linked.get("doctype"),
            "name": linked.get("name"),
            "status": linked.get("status") or "available",
        }
    if key == "work_order_created":
        return _("Work Order is the production journey anchor.")
    if key == "production_readiness":
        readiness = linked_docs.get("production_readiness") or {}
        return readiness.get("overall_result") or _("Production prerequisites are incomplete.")
    if key == "material_reservation":
        if status == STATUS_COMPLETED:
            return _("Reserved - stock remains in Stores until Material Transfer.")
        return _("Reserve exact eligible RM batches before material transfer.")
    if key == "material_transfer":
        return _("Move required materials to WIP using standard Stock Entry.")
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        return execution.get("summary") or _("Execute the ordered Job Card operations and complete FG Receipt.")
    if key in FIELD_STAGE_KEYS:
        return _("Phase 1 records this stage on the Work Order; it can become a separate document later.")
    if key == "initial_qc":
        return _("Initial QC must pass before FG Receipt.")
    if key == "fg_receipt":
        return _("Create standard Manufacture Stock Entry after Initial QC passes.")
    if key == "final_qc":
        return _("Final QC must pass before completion.")
    if key == "completed":
        return _("Work Order journey is complete after controlled FG Release.")
    if key == "fg_release":
        return _("Move the accepted FG batch from quarantine to FG Released using standard Stock Entry.")
    return status


def get_material_reservation_journey_state(work_order: str) -> dict[str, Any]:
    from calco_erp.calco_production.material_reservation_submission import (
        get_material_reservation_set_state,
    )

    return get_material_reservation_set_state(work_order=work_order)


def get_production_readiness_state(wo) -> dict[str, Any]:
    from calco_erp.calco_production.production_readiness import (
        evaluate_production_readiness,
    )

    return evaluate_production_readiness(wo)


def get_production_execution_state(wo, manufacture_entry=None) -> dict[str, Any]:
    job_cards = get_work_order_job_cards(wo.name, wo.get("operations") or [])
    operation_groups = group_job_cards_by_operation(job_cards)
    active_group = next(
        (group for group in operation_groups if not group["complete"]), None
    )
    active_job_card = active_group["active_job_card"] if active_group else None
    all_operations_complete = bool(operation_groups) and not active_group
    manufacture_complete = is_submitted(manufacture_entry)
    blocked_reason = ""
    execution_stage = ""

    if active_job_card:
        from calco_erp.calco_production.job_card_execution import (
            get_current_execution_stage,
        )

        current = get_current_execution_stage(frappe._dict(active_job_card), wo)
        execution_stage = current.get("label") or ""
        blocked_reason = current.get("blocked_reason") or ""
    elif all_operations_complete and not manufacture_complete:
        execution_stage = _("FG Receipt Handoff")
    elif manufacture_complete:
        execution_stage = _("FG Receipt Complete")
    else:
        blocked_reason = _("No operation Job Cards are available for this Work Order.")

    compounding = next(
        (
            row
            for row in job_cards
            if cstr(row.get("operation")).strip() == "Compounding / Extrusion"
        ),
        None,
    )
    return {
        "job_cards": job_cards,
        "operation_groups": operation_groups,
        "active_job_card": active_job_card,
        "compounding_job_card": compounding,
        "all_operations_complete": all_operations_complete,
        "manufacture_complete": manufacture_complete,
        "execution_stage": execution_stage,
        "blocked_reason": blocked_reason,
        "summary": get_production_execution_summary(
            wo,
            active_group,
            active_job_card,
            execution_stage,
            manufacture_entry,
        ),
    }


def get_work_order_job_cards(work_order: str, operations=None) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Job Card",
        filters={"work_order": work_order, "docstatus": ("<", 2)},
        fields=[
            "name",
            "work_order",
            "operation",
            "operation_id",
            "sequence_id",
            "docstatus",
            "status",
            "for_quantity",
            "total_completed_qty",
            "workstation",
            "actual_start_date",
            "custom_production_line",
            "custom_machine",
            "custom_operator",
            "custom_shift_type",
            "custom_fg_batch_no",
            "custom_grade_change_required",
            "custom_grade_change_status",
            "custom_rm_loading_status",
            "custom_compounding_status",
            "custom_pelletizing_status",
            "custom_initial_qc_status",
            "creation",
        ],
        order_by="sequence_id asc, creation asc, name asc",
    )
    operation_sequence = {
        row.get("name"): int(row.get("sequence_id") or row.get("idx") or 0)
        for row in operations or []
        if row.get("name")
    }
    normalized = []
    for row in rows:
        row = {**dict(row), "doctype": "Job Card"}
        row["operation_sequence"] = operation_sequence.get(
            row.get("operation_id"), int(row.get("sequence_id") or 0)
        )
        normalized.append(row)
    return sorted(
        normalized,
        key=lambda row: (
            int(row.get("operation_sequence") or 0),
            cstr(row.get("creation")),
            cstr(row.get("name")),
        ),
    )


def group_job_cards_by_operation(job_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for row in job_cards:
        key = (
            int(row.get("operation_sequence") or row.get("sequence_id") or 0),
            cstr(row.get("operation")).strip(),
        )
        group = by_key.get(key)
        if not group:
            group = {
                "sequence_id": key[0],
                "operation": key[1],
                "job_cards": [],
            }
            by_key[key] = group
            groups.append(group)
        group["job_cards"].append(row)

    for group in groups:
        cards = group["job_cards"]
        incomplete = [row for row in cards if not job_card_is_complete(row)]
        group["complete"] = not incomplete
        group["active_job_card"] = incomplete[0] if incomplete else None
        group["completed_qty"] = round(
            sum(flt(row.get("total_completed_qty")) for row in cards), 3
        )
        group["planned_qty"] = round(
            sum(flt(row.get("for_quantity")) for row in cards), 3
        )
        group["status"] = STATUS_COMPLETED if group["complete"] else (
            STATUS_IN_PROGRESS
            if any(job_card_has_execution(row) for row in cards)
            else STATUS_NOT_STARTED
        )
    return groups


def job_card_is_complete(row: dict[str, Any]) -> bool:
    return row.get("docstatus") == 1 and cstr(row.get("status")).strip() in {
        "Completed",
        "Closed",
    }


def job_card_has_execution(row: dict[str, Any]) -> bool:
    return bool(
        row.get("actual_start_date")
        or flt(row.get("total_completed_qty"))
        or cstr(row.get("status")).strip() in {"Work in Progress", "In Process"}
        or any(
            cstr(row.get(fieldname)).strip() in {STATUS_IN_PROGRESS, STATUS_COMPLETED}
            for fieldname in (
                "custom_rm_loading_status",
                "custom_compounding_status",
                "custom_pelletizing_status",
            )
        )
    )


def get_production_execution_summary(
    wo,
    active_group,
    active_job_card,
    execution_stage: str,
    manufacture_entry,
) -> str:
    if active_job_card:
        machine = (
            active_job_card.get("custom_machine")
            or active_job_card.get("custom_production_line")
            or active_job_card.get("workstation")
            or "-"
        )
        operator = active_job_card.get("custom_operator") or "-"
        shift = active_job_card.get("custom_shift_type") or "-"
        batch = active_job_card.get("custom_fg_batch_no") or wo.get("custom_fg_batch_no") or _("Not Generated")
        return _(
            "%(job_card)s | %(operation)s | %(stage)s | Machine %(machine)s | "
            "Operator %(operator)s | Shift %(shift)s | FG Batch %(batch)s | "
            "%(completed)s / %(planned)s Kg"
        ) % {
            "job_card": active_job_card.get("name"),
            "operation": active_group.get("operation") or "-",
            "stage": execution_stage or "-",
            "machine": machine,
            "operator": operator,
            "shift": shift,
            "batch": batch,
            "completed": round(flt(active_group.get("completed_qty")), 3),
            "planned": round(flt(wo.get("qty")), 3),
        }
    if manufacture_entry:
        return _("Manufacture Stock Entry {0} completed the FG Receipt handoff.").format(
            manufacture_entry.get("name")
        )
    if active_group is None:
        return _("All operation Job Cards are complete; create the controlled FG Receipt.")
    return _("Production operation Job Cards are required before execution can continue.")


def get_compounding_job_card(work_order: str) -> dict[str, Any] | None:
    from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION

    row = frappe.db.get_value(
        "Job Card",
        {
            "work_order": work_order,
            "operation": COMPOUNDING_OPERATION,
            "docstatus": ("<", 2),
        },
        [
            "name",
            "work_order",
            "docstatus",
            "status",
            "custom_grade_change_required",
            "custom_grade_change_status",
            "custom_rm_loading_status",
            "custom_compounding_status",
            "custom_pelletizing_status",
        ],
        as_dict=True,
        order_by="creation asc",
    )
    if not row or not hasattr(row, "items"):
        return None
    return {
        **row,
        "doctype": "Job Card",
    }


def production_readiness_is_ready(wo, linked_docs=None) -> bool:
    """Use this journey's live evaluation, never the last persisted display field."""
    if linked_docs is not None and "production_readiness" in linked_docs:
        readiness = linked_docs["production_readiness"] or {}
    else:
        readiness = get_production_readiness_state(wo)
    return readiness.get("ready") is True


def get_responsible_role(spec, key, linked_docs):
    if key == "production_execution":
        execution = linked_docs.get("production_execution") or {}
        active = execution.get("active_job_card") or {}
        if active.get("custom_operator"):
            return active["custom_operator"]
        if execution.get("all_operations_complete") and not execution.get("manufacture_complete"):
            return "Production Head"
        return spec.responsible_role
    if key != "material_reservation":
        return spec.responsible_role
    reservation = linked_docs.get("material_reservation") or {}
    return (
        "Stores / Stock Manager"
        if reservation.get("sre_names") and reservation.get("state") == "Draft"
        else spec.responsible_role
    )


def get_stock_entries(work_order: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Stock Entry",
        filters={"work_order": work_order, "docstatus": ("<", 2)},
        fields=["name", "purpose", "stock_entry_type", "docstatus", "creation"],
        order_by="creation desc",
    )
    for row in rows:
        row["doctype"] = "Stock Entry"
        row["purpose_key"] = lifecycle.get_stock_entry_purpose(row)
        row["status"] = normalize_doc_status(row)
    return rows


def first_stock_entry(rows: list[dict[str, Any]], purpose: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("purpose_key") == purpose:
            return row
    return None


def get_linked_qi(wo, fieldname: str, qc_stage: str) -> dict[str, Any] | None:
    linked_name = wo.get(fieldname)
    if linked_name and frappe.db.exists("Quality Inspection", linked_name):
        return make_qi_row(linked_name)
    rows = frappe.get_all(
        "Quality Inspection",
        filters={
            lifecycle.QI_WORK_ORDER_FIELD: wo.name,
            lifecycle.QI_STAGE_FIELD: qc_stage,
            "docstatus": ("<", 2),
        },
        fields=["name", "status", "docstatus", "creation"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return make_qi_row(rows[0].name) if rows else None


def make_qi_row(name: str) -> dict[str, Any]:
    row = frappe.db.get_value("Quality Inspection", name, ["name", "status", "docstatus"], as_dict=True)
    row = dict(row or {})
    row["doctype"] = "Quality Inspection"
    row["status"] = normalize_doc_status(row)
    return row


def get_linked_final_qc_release(final_qc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not final_qc:
        return None
    rows = frappe.get_all(
        "Final QC Release",
        filters={
            "quality_inspection": final_qc["name"],
            "docstatus": ("<", 2),
        },
        fields=[
            "name",
            "status",
            "docstatus",
            "stock_entry",
            "release_stock_entry",
            "release_qty",
            "creation",
        ],
        order_by="creation desc",
        limit_page_length=1,
    )
    if not rows:
        return None
    row = dict(rows[0])
    row["doctype"] = "Final QC Release"
    return row


def normalize_doc_status(row: dict[str, Any]) -> str:
    if row.get("docstatus") == 1:
        return "Submitted"
    if row.get("docstatus") == 2:
        return "Cancelled"
    return row.get("status") or "Draft"


def is_submitted(row: dict[str, Any] | None) -> bool:
    return bool(row and row.get("docstatus") == 1)


def stage_for_field_key(key: str) -> str:
    return {
        "grade_change_checklist": lifecycle.STAGE_GRADE_CHANGE_CHECKLIST,
        "premix_preparation": lifecycle.STAGE_PREMIX_PREPARATION,
        "rm_loading": lifecycle.STAGE_RM_LOADING,
        "compounding": lifecycle.STAGE_COMPOUNDING,
        "pelletizing": lifecycle.STAGE_PELLETIZING,
    }[key]


def stage_after_or_equal(current_stage: str | None, target_stage: str) -> bool:
    if current_stage == target_stage:
        return True
    try:
        return lifecycle.PRODUCTION_STAGES.index(current_stage) >= lifecycle.PRODUCTION_STAGES.index(target_stage)
    except ValueError:
        return False


@frappe.whitelist()
def make_work_order_stock_entry(work_order: str | None = None, purpose: str | None = None, *args, **kwargs) -> dict[str, Any]:
    kwargs = _clean_kwargs(kwargs)
    work_order = work_order or kwargs.get("work_order")
    purpose = purpose or kwargs.get("purpose")
    if purpose not in {"Material Transfer for Manufacture", "Manufacture"}:
        frappe.throw(_("Unsupported Work Order Stock Entry purpose."))
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))

    from calco_erp.calco_production.production_readiness import (
        make_stock_entry_with_readiness,
    )

    doc = frappe.get_doc(
        make_stock_entry_with_readiness(
            work_order_id=work_order,
            purpose=purpose,
        )
    )
    return doc.as_dict()
