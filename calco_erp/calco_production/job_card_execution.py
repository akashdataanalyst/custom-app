from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, cstr, flt, now_datetime

from calco_erp.calco_production import work_order_lifecycle as lifecycle
from calco_erp.calco_production.in_process_quality import state_for_work_order


JOB_CARD_DOCTYPE = "Job Card"
EXECUTION_SECTION_FIELD = "custom_execution_capture_section"
JOURNEY_SECTION_FIELD = "custom_job_card_execution_journey_section"
JOURNEY_HTML_FIELD = "custom_job_card_execution_journey_html"
GRADE_CHANGE_TABLE_FIELD = "custom_grade_change_checklist"
RM_LOADING_TABLE_FIELD = "custom_rm_loading_details"
SHIFT_REPORT_TABLE_FIELD = "custom_shift_reports"

STATUS_NOT_STARTED = "Not Started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_ON_HOLD = "On Hold"
STATUS_COMPLETED = "Completed"
STATUS_BLOCKED = "Blocked"
COMPOUNDING_OPERATION = "Compounding / Extrusion"
PACKING_OPERATION = "Packing"
EXECUTION_STATUS_OPTIONS = [STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_BLOCKED]
GRADE_CHANGE_STATUS_OPTIONS = [
    "Pending Evaluation",
    "Required - Checklist Pending",
    STATUS_IN_PROGRESS,
    "Pending Approval",
    "Reset Approval Required",
    STATUS_COMPLETED,
    "Completed - Not Required",
    "Rejected",
]
QC_STATUS_OPTIONS = [lifecycle.INITIAL_QC_PENDING, lifecycle.INITIAL_QC_PASSED, lifecycle.INITIAL_QC_FAILED, lifecycle.INITIAL_QC_RETEST_REQUIRED]
FINAL_QC_STATUS_OPTIONS = [lifecycle.FINAL_QC_PENDING, lifecycle.FINAL_QC_PASSED, lifecycle.FINAL_QC_FAILED]

COLOR_BY_STATUS = {
    STATUS_NOT_STARTED: "grey",
    STATUS_IN_PROGRESS: "blue",
    STATUS_ON_HOLD: "orange",
    STATUS_COMPLETED: "green",
    STATUS_BLOCKED: "red",
    "Pending Evaluation": "orange",
    "Required - Checklist Pending": "orange",
    "Reset Approval Required": "orange",
    "Pending Approval": "orange",
    "Completed - Not Required": "green",
    "Rejected": "red",
}


def options(values: list[str]) -> str:
    return "\n" + "\n".join(values)


def ensure_job_card_execution_setup():
    if not frappe.db.exists("DocType", JOB_CARD_DOCTYPE):
        return
    ensure_custom_fields()
    frappe.clear_cache(doctype=JOB_CARD_DOCTYPE)


def ensure_custom_fields():
    create_custom_fields(
        {
            JOB_CARD_DOCTYPE: [
                {
                    "fieldname": JOURNEY_SECTION_FIELD,
                    "label": "Execution Journey",
                    "fieldtype": "Section Break",
                    "insert_after": "work_order",
                },
                {
                    "fieldname": JOURNEY_HTML_FIELD,
                    "label": "Execution Journey",
                    "fieldtype": "HTML",
                    "insert_after": JOURNEY_SECTION_FIELD,
                },
                {
                    "fieldname": EXECUTION_SECTION_FIELD,
                    "label": "Shop-floor Execution Capture",
                    "fieldtype": "Section Break",
                    "insert_after": JOURNEY_HTML_FIELD,
                },
                {
                    "fieldname": "custom_fg_batch_no",
                    "label": "FG Batch No",
                    "fieldtype": "Data",
                    "insert_after": EXECUTION_SECTION_FIELD,
                    "read_only": 1,
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "custom_production_line",
                    "label": "Production Line",
                    "fieldtype": "Link",
                    "options": "Workstation",
                    "insert_after": "custom_fg_batch_no",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "custom_machine",
                    "label": "Machine",
                    "fieldtype": "Link",
                    "options": "Workstation",
                    "insert_after": "custom_production_line",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "custom_operator",
                    "label": "Operator",
                    "fieldtype": "Link",
                    "options": "Employee",
                    "insert_after": "custom_machine",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "custom_shift_type",
                    "label": "Shift",
                    "fieldtype": "Link",
                    "options": "Shift Type",
                    "insert_after": "custom_operator",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "custom_grade_change_required",
                    "label": "Grade Change Required",
                    "fieldtype": "Check",
                    "insert_after": "custom_shift_type",
                },
                {
                    "fieldname": "custom_grade_change_status",
                    "label": "Grade Change Status",
                    "fieldtype": "Select",
                    "options": options(GRADE_CHANGE_STATUS_OPTIONS),
                    "default": "Pending Evaluation",
                    "insert_after": "custom_grade_change_required",
                },
                {
                    "fieldname": "custom_rm_loading_status",
                    "label": "RM Loading Status",
                    "fieldtype": "Select",
                    "options": options(EXECUTION_STATUS_OPTIONS),
                    "default": STATUS_NOT_STARTED,
                    "insert_after": "custom_grade_change_status",
                },
                {
                    "fieldname": "custom_compounding_status",
                    "label": "Compounding Status",
                    "fieldtype": "Select",
                    "options": options(EXECUTION_STATUS_OPTIONS),
                    "default": STATUS_NOT_STARTED,
                    "insert_after": "custom_rm_loading_status",
                },
                {
                    "fieldname": "custom_pelletizing_status",
                    "label": "Pelletizing Status",
                    "fieldtype": "Select",
                    "options": options(EXECUTION_STATUS_OPTIONS),
                    "default": STATUS_NOT_STARTED,
                    "insert_after": "custom_compounding_status",
                },
                {
                    "fieldname": "custom_initial_qc_status",
                    "label": "Initial QC Status",
                    "fieldtype": "Select",
                    "options": options(QC_STATUS_OPTIONS),
                    "default": lifecycle.INITIAL_QC_PENDING,
                    "insert_after": "custom_pelletizing_status",
                },
                {
                    "fieldname": "custom_final_qc_status",
                    "label": "Final QC Status",
                    "fieldtype": "Select",
                    "options": options(FINAL_QC_STATUS_OPTIONS),
                    "default": lifecycle.FINAL_QC_PENDING,
                    "insert_after": "custom_initial_qc_status",
                },
                {
                    "fieldname": "custom_qc_correction_remarks",
                    "label": "QC Correction Remarks",
                    "fieldtype": "Small Text",
                    "insert_after": "custom_final_qc_status",
                },
                {
                    "fieldname": "custom_grade_change_section",
                    "label": "Grade Change Checklist",
                    "fieldtype": "Section Break",
                    "insert_after": "custom_qc_correction_remarks",
                },
                {
                    "fieldname": GRADE_CHANGE_TABLE_FIELD,
                    "label": "Grade Change Checklist",
                    "fieldtype": "Table",
                    "options": "Job Card Grade Change Checklist",
                    "insert_after": "custom_grade_change_section",
                },
                {
                    "fieldname": "custom_rm_loading_section",
                    "label": "RM Loading",
                    "fieldtype": "Section Break",
                    "insert_after": GRADE_CHANGE_TABLE_FIELD,
                },
                {
                    "fieldname": RM_LOADING_TABLE_FIELD,
                    "label": "RM Loading Details",
                    "fieldtype": "Table",
                    "options": "Job Card RM Loading Detail",
                    "insert_after": "custom_rm_loading_section",
                },
                {
                    "fieldname": "custom_shift_report_section",
                    "label": "Shift Reporting",
                    "fieldtype": "Section Break",
                    "insert_after": RM_LOADING_TABLE_FIELD,
                },
                {
                    "fieldname": SHIFT_REPORT_TABLE_FIELD,
                    "label": "Shift Reports",
                    "fieldtype": "Table",
                    "options": "Job Card Shift Report",
                    "insert_after": "custom_shift_report_section",
                },
            ]
        },
        update=True,
    )


def sync_job_card_execution_context(doc, method=None):
    from calco_erp.calco_production.execution_policy import pausing
    if pausing(doc):
        return
    if doc.doctype != JOB_CARD_DOCTYPE or not doc.get("work_order"):
        return
    if not frappe.db.exists("Work Order", doc.work_order):
        return

    wo = frappe.get_doc("Work Order", doc.work_order)
    copy_if_empty(doc, "custom_fg_batch_no", wo.get("custom_fg_batch_no"))
    copy_if_empty(doc, "custom_production_line", wo.get("custom_production_line") or wo.get("custom_machine"))
    copy_if_empty(doc, "custom_machine", wo.get("custom_machine") or wo.get("custom_production_line"))
    copy_if_empty(doc, "custom_operator", wo.get("custom_operator"))
    copy_if_empty(doc, "custom_shift_type", wo.get("custom_shift_type"))
    copy_if_empty(doc, "custom_grade_change_required", wo.get("custom_grade_change_required"))
    sync_job_card_grade_change_display(doc)
    sync_status_from_work_order(doc, "custom_initial_qc_status", wo.get("custom_initial_qc_status"), lifecycle.INITIAL_QC_PENDING)
    sync_status_from_work_order(doc, "custom_final_qc_status", wo.get("custom_final_qc_status"), lifecycle.FINAL_QC_PENDING)
    if wo.get("custom_qc_correction_remarks"):
        doc.set("custom_qc_correction_remarks", wo.get("custom_qc_correction_remarks"))
    if (doc.get("time_logs") or flt(doc.get("total_completed_qty"))) and doc.get("operation"):
        lifecycle.assert_packing_initial_qc_passed(wo.name, doc.operation)


def sync_job_card_grade_change_display(doc, method=None):
    if (
        doc.doctype != JOB_CARD_DOCTYPE
        or cstr(doc.get("operation")).strip() != "Compounding / Extrusion"
        or not doc.get("work_order")
    ):
        return
    from calco_erp.calco_production.grade_change_control import get_grade_change_journey_state

    doc.set(
        "custom_grade_change_status",
        get_grade_change_journey_state(doc.get("work_order"), doc.get("name"))["status"],
    )


def sync_status_from_work_order(doc, fieldname: str, value: Any, default_value: str):
    if not doc.meta.has_field(fieldname) or value in (None, ""):
        return
    if doc.get(fieldname) in (None, "", default_value) or doc.get(fieldname) != value:
        doc.set(fieldname, value)

def copy_if_empty(doc, fieldname: str, value: Any):
    if not doc.meta.has_field(fieldname) or value in (None, ""):
        return
    if doc.get(fieldname) in (None, "", 0):
        doc.set(fieldname, value)


@frappe.whitelist()
def get_job_card_execution_journey(job_card: str | None = None, *args, **kwargs) -> dict[str, Any]:
    job_card = job_card or (kwargs or {}).get("job_card") or (kwargs or {}).get("docname")
    if not job_card or not frappe.db.exists(JOB_CARD_DOCTYPE, job_card):
        frappe.throw(_("Valid Job Card is required."))

    doc = frappe.get_doc(JOB_CARD_DOCTYPE, job_card)
    wo = frappe.get_doc("Work Order", doc.work_order) if doc.get("work_order") and frappe.db.exists("Work Order", doc.work_order) else None
    grade_change = get_job_card_grade_change_state(doc, wo)
    stages = build_journey_stages(doc, wo)
    initial_qc = get_linked_qi(wo, lifecycle.WORK_ORDER_INITIAL_QI_FIELD, lifecycle.QI_STAGE_INITIAL) if wo else None
    from calco_erp.calco_production.compounding_execution import build_compounding_cockpit

    cockpit = build_compounding_cockpit(
        doc,
        wo,
        grade_change,
        {
            "status": doc.get("custom_initial_qc_status")
            or (wo.get("custom_initial_qc_status") if wo else "")
            or lifecycle.INITIAL_QC_PENDING,
            "name": (initial_qc or {}).get("name") or "",
        },
    )
    return {
        "summary": {
            "job_card": doc.name,
            "work_order": doc.get("work_order") or "",
            "operation": doc.get("operation") or "",
            "operation_profile": get_operation_profile(doc.get("operation")),
            "fg_batch_no": doc.get("custom_fg_batch_no") or (wo.get("custom_fg_batch_no") if wo else "") or "",
            "production_line": doc.get("custom_production_line") or "",
            "machine": doc.get("custom_machine") or doc.get("workstation") or "",
            "operator": doc.get("custom_operator") or "",
            "shift_type": doc.get("custom_shift_type") or "",
            "grade_change_status": grade_change["status"],
            "grade_change_clearance": grade_change["clearance"],
            "grade_change_cleaning_level": grade_change["cleaning_level"],
            "grade_change_approval_status": grade_change["approval_status"],
            "grade_change_approved": grade_change["approved"],
        },
        "stages": stages,
        "controlled_compounding": bool(cockpit),
        "cockpit": cockpit,
        "in_process_qc": state_for_work_order(wo) if wo else {},
    }


def get_operation_profile(operation: str | None) -> str:
    operation = cstr(operation).strip()
    if operation == COMPOUNDING_OPERATION:
        return "compounding"
    if operation == PACKING_OPERATION:
        return "packing"
    return "generic"


def build_journey_stages(doc, wo) -> list[dict[str, Any]]:
    operation = cstr(doc.get("operation")).strip()
    if operation == COMPOUNDING_OPERATION:
        return build_compounding_journey(doc, wo)
    if operation == PACKING_OPERATION:
        return build_packing_journey(doc, wo)
    return build_generic_operation_journey(doc, wo)


def build_compounding_journey(doc, wo) -> list[dict[str, Any]]:
    initial_qc = doc.get("custom_initial_qc_status") or (wo.get("custom_initial_qc_status") if wo else "") or lifecycle.INITIAL_QC_PENDING
    grade_change = get_job_card_grade_change_state(doc, wo)
    from calco_erp.calco_production.in_process_quality import is_parallel, state_for_work_order
    parallel = is_parallel(wo)
    qc = state_for_work_order(wo) if parallel else {}
    qc_stage = make_stage("in_process_qc", _("In-Process QC (parallel)"), qc.get("status", STATUS_NOT_STARTED), _("Quality User"), _("Open Work Order"), ["Form", "Work Order", wo.name], summary=_("Runs alongside Compounding; unresolved QC blocks Manufacture.")) if parallel else make_qc_stage("initial_qc", _("Initial QC"), initial_qc, wo, lifecycle.WORK_ORDER_INITIAL_QI_FIELD, lifecycle.QI_STAGE_INITIAL)
    if parallel:
        qc_stage["parallel_with"] = "compounding_execution"
    return [
        make_stage("job_card_created", _("Job Card Created"), STATUS_COMPLETED if doc.name else STATUS_NOT_STARTED, _("Production Engineer"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("Shop-floor execution document is created.")),
        make_grade_change_stage(doc, wo, grade_change),
        make_holistic_compounding_stage(doc, grade_change),
        qc_stage,
        make_job_completion_stage(
            doc,
            "compounding_complete",
            _("Compounding Complete"),
            responsible_role=_("Manufacturing User"),
        ),
    ]


def make_holistic_compounding_stage(doc, grade_change) -> dict[str, Any]:
    if not grade_change.get("approved"):
        status = STATUS_BLOCKED
        blocker = _("Approved F-GCL-01 Grade Change Clearance is required before Job Start.")
    elif is_job_card_complete(doc):
        status = STATUS_COMPLETED
        blocker = ""
    elif doc.get("is_paused") or cstr(doc.get("status")).strip() == STATUS_ON_HOLD:
        status = STATUS_ON_HOLD
        blocker = ""
    elif job_card_has_execution(doc):
        status = STATUS_IN_PROGRESS
        blocker = ""
    else:
        status = STATUS_NOT_STARTED
        blocker = ""
    return make_stage(
        "compounding_execution",
        _("Compounding / Extrusion"),
        status,
        _("Manufacturing User"),
        _("Resume Job") if status == STATUS_ON_HOLD else _("Open Job Card"),
        ["Form", JOB_CARD_DOCTYPE, doc.name],
        blocked_reason=blocker,
        summary=blocker
        or (
            _("Job Card is paused. Resume Job to continue standard time-log capture.")
            if status == STATUS_ON_HOLD
            else _("Standard Job Card time and quantity remain execution authority.")
        ),
    )


def build_packing_journey(doc, wo) -> list[dict[str, Any]]:
    initial_qc = doc.get("custom_initial_qc_status") or (wo.get("custom_initial_qc_status") if wo else "") or lifecycle.INITIAL_QC_PENDING
    readiness = make_packing_readiness_stage(doc, wo, initial_qc)
    execution_status = (
        STATUS_COMPLETED
        if is_job_card_complete(doc)
        else STATUS_IN_PROGRESS
        if job_card_has_execution(doc)
        else STATUS_NOT_STARTED
    )
    if readiness["status"] != STATUS_COMPLETED:
        execution_status = STATUS_NOT_STARTED
    return [
        make_stage("job_card_created", _("Job Card Created"), STATUS_COMPLETED if doc.name else STATUS_NOT_STARTED, _("Production Engineer"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("Packing execution document is created.")),
        readiness,
        make_stage("packing_execution", _("Packing Execution"), execution_status, _("Packing Operator"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("Packing execution uses standard Job Card time and completed-quantity evidence.")),
        make_job_completion_stage(doc, "packing_complete", _("Packing Complete")),
    ]


def build_generic_operation_journey(doc, wo) -> list[dict[str, Any]]:
    execution_status = (
        STATUS_COMPLETED
        if is_job_card_complete(doc)
        else STATUS_IN_PROGRESS
        if job_card_has_execution(doc)
        else STATUS_NOT_STARTED
    )
    return [
        make_stage("job_card_created", _("Job Card Created"), STATUS_COMPLETED if doc.name else STATUS_NOT_STARTED, _("Production Engineer"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("Operation execution document is created.")),
        make_stage("operation_execution", _("Operation Execution"), execution_status, _("Production Operator"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("Execution remains authoritative in the standard ERPNext Job Card.")),
        make_job_completion_stage(doc, "operation_complete", _("Operation Complete")),
    ]


def get_current_execution_stage(doc, wo=None) -> dict[str, Any]:
    stages = build_journey_stages(doc, wo)
    return next((stage for stage in stages if not stage_status_is_complete(stage["status"])), stages[-1])


def stage_status_is_complete(status: str) -> bool:
    return status in {STATUS_COMPLETED, "Completed - Not Required"}


def make_execution_status_stage(doc, key: str, label: str, fieldname: str) -> dict[str, Any]:
    return make_stage(
        key,
        label,
        doc.get(fieldname) or STATUS_NOT_STARTED,
        _("Production Operator"),
        _("Open Job Card"),
        ["Form", JOB_CARD_DOCTYPE, doc.name],
        summary=_("Execution evidence remains on the ERPNext Job Card."),
    )


def make_production_running_stage(doc, initial_qc: str) -> dict[str, Any]:
    if initial_qc == lifecycle.INITIAL_QC_FAILED:
        status = STATUS_BLOCKED
        blocker = _("Production Running is blocked until Initial QC correction/retest passes.")
    elif initial_qc != lifecycle.INITIAL_QC_PASSED:
        status = STATUS_NOT_STARTED
        blocker = ""
    elif is_job_card_complete(doc):
        status = STATUS_COMPLETED
        blocker = ""
    elif job_card_has_execution(doc):
        status = STATUS_IN_PROGRESS
        blocker = ""
    else:
        status = STATUS_NOT_STARTED
        blocker = ""
    return make_stage(
        "production_running",
        _("Production Running"),
        status,
        _("Production Operator"),
        _("Open Job Card"),
        ["Form", JOB_CARD_DOCTYPE, doc.name],
        blocked_reason=blocker,
        summary=blocker or _("Standard Job Card time, quantity, operator, and shift evidence controls production running."),
    )


def make_packing_readiness_stage(doc, wo, initial_qc: str) -> dict[str, Any]:
    from calco_erp.calco_production.in_process_quality import is_parallel, state_for_work_order
    if is_parallel(wo):
        qc = state_for_work_order(wo)
        return make_stage("packing_readiness", _("Packing Readiness"), STATUS_BLOCKED if qc.get("hold") else STATUS_COMPLETED, _("Manufacturing User"), _("Open Work Order"), ["Form", "Work Order", wo.name], blocked_reason=_("Quality Hold requires Quality release.") if qc.get("hold") else "", summary=_("Pending In-Process QC gates downstream Manufacture; Quality Hold blocks Production execution."))
    linked = get_linked_qi(wo, lifecycle.WORK_ORDER_INITIAL_QI_FIELD, lifecycle.QI_STAGE_INITIAL) if wo else None
    if initial_qc == lifecycle.INITIAL_QC_PASSED:
        status = STATUS_COMPLETED
        blocker = ""
    else:
        status = STATUS_BLOCKED
        blocker = _("Initial QC must be Passed before Packing can start or record production.")
    return make_stage(
        "packing_readiness",
        _("Packing Readiness"),
        status,
        _("Quality User"),
        _("Open linked QI") if linked else _("Open Work Order"),
        ["Form", linked["doctype"], linked["name"]] if linked else ["Form", "Work Order", doc.get("work_order")],
        linked=linked,
        blocked_reason=blocker,
        summary=blocker or _("Initial QC has passed and Packing may begin."),
    )


def make_job_completion_stage(
    doc, key: str, label: str, responsible_role: str | None = None
) -> dict[str, Any]:
    complete = is_job_card_complete(doc)
    return make_stage(
        key,
        label,
        STATUS_COMPLETED if complete else STATUS_NOT_STARTED,
        responsible_role or _("Production Operator"),
        _("Open Job Card"),
        ["Form", JOB_CARD_DOCTYPE, doc.name],
        summary=_("ERPNext Job Card completion is the operation-completion authority."),
    )


def is_job_card_complete(doc) -> bool:
    return doc.get("docstatus") == 1 and cstr(doc.get("status")).strip() in {"Completed", "Closed"}


def job_card_has_execution(doc) -> bool:
    return bool(
        doc.get("actual_start_date")
        or doc.get("time_logs")
        or flt(doc.get("total_completed_qty"))
        or cstr(doc.get("status")).strip() in {"Work in Progress", "In Process"}
    )


def get_job_card_grade_change_state(doc, wo=None) -> dict[str, Any]:
    operation = cstr(doc.get("operation")).strip()
    if operation and operation != COMPOUNDING_OPERATION:
        return {
            "status": "Not Applicable",
            "approval_status": "Not Applicable",
            "summary": _("Grade Change does not apply to this operation profile."),
            "clearance": "",
            "cleaning_level": "",
            "approved": True,
        }
    if doc.get("work_order"):
        from calco_erp.calco_production.grade_change_control import get_grade_change_journey_state

        return get_grade_change_journey_state(doc.get("work_order"), doc.get("name"))
    return {
        "status": doc.get("custom_grade_change_status") or STATUS_NOT_STARTED,
        "approval_status": doc.get("custom_grade_change_status") or STATUS_NOT_STARTED,
        "summary": _("Legacy Grade Change status is retained for compatibility."),
        "clearance": "",
        "cleaning_level": "",
        "approved": False,
    }


def make_grade_change_stage(doc, wo, state=None) -> dict[str, Any]:
    state = state or get_job_card_grade_change_state(doc, wo)
    if state["clearance"]:
        return make_stage(
            "grade_change",
            _("Grade Change"),
            state["status"],
            _("Production Engineer"),
            _("Open Grade Change Clearance"),
            ["Form", "Grade Change Clearance", state["clearance"]],
            linked={"doctype": "Grade Change Clearance", "name": state["clearance"], "status": state["approval_status"]},
            summary=state["summary"],
        )
    stage = make_stage(
        "grade_change",
        _("Grade Change"),
        state["status"],
        _("Production Engineer"),
        _("Create Grade Change Clearance"),
        None,
        summary=state["summary"],
    )
    stage.update({
        "action_type": "method",
        "action_method_or_route": "calco_erp.calco_production.grade_change_control.create_grade_change_clearance",
        "action_args": {"work_order": doc.get("work_order"), "job_card": doc.get("name")},
    })
    return stage


def make_rm_loading_stage(doc, grade_change) -> dict[str, Any]:
    if cstr(doc.get("operation")).strip() == "Compounding / Extrusion" and not grade_change["approved"]:
        reason = _("Approved F-GCL-01 Grade Change Clearance is required before RM Loading.")
        return make_stage(
            "rm_loading",
            _("RM Loading"),
            STATUS_BLOCKED,
            _("Production Operator"),
            _("Open Grade Change Clearance") if grade_change["clearance"] else _("Open Job Card"),
            ["Form", "Grade Change Clearance", grade_change["clearance"]]
            if grade_change["clearance"]
            else ["Form", JOB_CARD_DOCTYPE, doc.name],
            blocked_reason=reason,
            summary=reason,
        )
    return make_stage(
        "rm_loading",
        _("RM Loading"),
        derive_table_status(doc, "custom_rm_loading_status", RM_LOADING_TABLE_FIELD),
        _("Production Operator"),
        _("Open Job Card"),
        ["Form", JOB_CARD_DOCTYPE, doc.name],
        summary=_("RM loading rows capture shop-floor loading only; Stock Entry remains inventory movement."),
    )


def derive_grade_change_status(doc) -> str:
    return get_job_card_grade_change_state(doc)["status"]


def derive_grade_change_summary(doc) -> str:
    return get_job_card_grade_change_state(doc)["summary"]


def derive_table_status(doc, status_field: str, table_field: str) -> str:
    status = doc.get(status_field) or STATUS_NOT_STARTED
    if status != STATUS_NOT_STARTED:
        return status
    return STATUS_IN_PROGRESS if len(doc.get(table_field) or []) else STATUS_NOT_STARTED


def make_qc_stage(key: str, label: str, qc_status: str, wo, qi_field: str, qc_stage: str) -> dict[str, Any]:
    linked = get_linked_qi(wo, qi_field, qc_stage) if wo else None
    if qc_status == lifecycle.INITIAL_QC_FAILED:
        status = STATUS_BLOCKED
    elif qc_status in {lifecycle.INITIAL_QC_PASSED, lifecycle.FINAL_QC_PASSED}:
        status = STATUS_COMPLETED
    elif linked:
        status = STATUS_IN_PROGRESS
    else:
        status = STATUS_NOT_STARTED
    return make_stage(key, label, status, _("Quality User"), _("Open linked QI") if linked else _("Open Job Card"), ["Form", linked["doctype"], linked["name"]] if linked else None, linked=linked, blocked_reason=_("Initial QC failed. Correction and retest are required.") if status == STATUS_BLOCKED else "", summary=_("QC decision remains in standard Quality Inspection."))


def make_retest_stage(doc, wo, initial_qc: str, retest_required: int) -> dict[str, Any]:
    linked = get_linked_qi(wo, lifecycle.WORK_ORDER_RETEST_QI_FIELD, lifecycle.QI_STAGE_RETEST) if wo else None
    if linked and initial_qc == lifecycle.INITIAL_QC_PASSED:
        status = STATUS_COMPLETED
    elif retest_required or initial_qc == lifecycle.INITIAL_QC_FAILED:
        status = STATUS_IN_PROGRESS
    else:
        status = STATUS_NOT_STARTED
    return make_stage("correction_retest", _("Correction / Retest"), status, _("Quality User"), _("Open linked QI") if linked else _("Open Job Card"), ["Form", linked["doctype"], linked["name"]] if linked else ["Form", JOB_CARD_DOCTYPE, doc.name], linked=linked, summary=_("Correction remarks are captured on Job Card; retest decision remains Quality Inspection."))


def make_ready_stage(doc, wo, initial_qc: str) -> dict[str, Any]:
    if initial_qc == lifecycle.INITIAL_QC_PASSED:
        return make_stage("ready_for_fg_receipt", _("Ready for FG Receipt"), STATUS_COMPLETED, _("Stores User"), _("Open Work Order") if wo else _("Open Job Card"), ["Form", "Work Order", wo.name] if wo else ["Form", JOB_CARD_DOCTYPE, doc.name], summary=_("FG Receipt can proceed through standard Manufacture Stock Entry."))
    blocked_reason = _("Initial QC must pass before FG Receipt.") if initial_qc != lifecycle.INITIAL_QC_FAILED else _("FG Receipt is blocked until correction/retest passes.")
    return make_stage("ready_for_fg_receipt", _("Ready for FG Receipt"), STATUS_BLOCKED, _("Stores User"), _("Open Job Card"), ["Form", JOB_CARD_DOCTYPE, doc.name], blocked_reason=blocked_reason, summary=blocked_reason)


def make_stage(key: str, label: str, status: str, role: str, action_label: str, route: list[str] | None, linked: dict[str, Any] | None = None, blocked_reason: str = "", summary: str = "") -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "color": COLOR_BY_STATUS.get(status, "grey"),
        "responsible_role": role,
        "action_label": action_label,
        "action_type": "route" if route else "",
        "action_method_or_route": route or [],
        "action_args": {},
        "action_disabled": False,
        "linked_doctype": linked.get("doctype", "") if linked else "",
        "linked_docname": linked.get("name", "") if linked else "",
        "linked_status": linked.get("status", "") if linked else "",
        "blocked_reason": blocked_reason,
        "summary": summary or status,
    }


def get_linked_qi(wo, fieldname: str, qc_stage: str) -> dict[str, Any] | None:
    if not wo:
        return None
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
    if row.get("docstatus") == 1:
        row["status"] = "Submitted"
    elif row.get("docstatus") == 2:
        row["status"] = "Cancelled"
    else:
        row["status"] = row.get("status") or "Draft"
    return row
