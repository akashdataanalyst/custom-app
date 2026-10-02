from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


STAGE_MATERIAL_TRANSFER_PENDING = "Material Transfer Pending"
STAGE_MATERIAL_TRANSFERRED = "Material Transferred"
STAGE_GRADE_CHANGE_CHECKLIST = "Grade Change Checklist"
STAGE_PREMIX_PREPARATION = "Premix Preparation"
STAGE_RM_LOADING = "RM Loading"
STAGE_COMPOUNDING = "Compounding"
STAGE_PELLETIZING = "Pelletizing"
STAGE_INITIAL_QC_PENDING = "Initial QC Pending"
STAGE_INITIAL_QC_FAILED = "Initial QC Failed / Correction Required"
STAGE_INITIAL_QC_PASSED = "Initial QC Passed"
STAGE_PRODUCTION_RUNNING = "Production Running"
STAGE_FINAL_QC_PENDING = "Final QC Pending"
STAGE_COMPLETED = "Completed"

INITIAL_QC_PENDING = "Pending"
INITIAL_QC_PASSED = "Passed"
INITIAL_QC_FAILED = "Failed"
INITIAL_QC_RETEST_REQUIRED = "Retest Required"
FINAL_QC_PENDING = "Pending"
FINAL_QC_PASSED = "Passed"
FINAL_QC_FAILED = "Failed"

QI_STAGE_INITIAL = "Initial QC"
QI_STAGE_RETEST = "Initial QC Retest"
QI_STAGE_FINAL = "Final QC"
QI_STATUS_PENDING = "Pending"

WORK_ORDER_STAGE_FIELD = "custom_production_stage"
WORK_ORDER_LINE_FIELD = "custom_production_line"
WORK_ORDER_GRADE_CHANGE_FIELD = "custom_grade_change_required"
WORK_ORDER_INITIAL_QC_STATUS_FIELD = "custom_initial_qc_status"
WORK_ORDER_FINAL_QC_STATUS_FIELD = "custom_final_qc_status"
WORK_ORDER_QC_REMARKS_FIELD = "custom_qc_correction_remarks"
WORK_ORDER_INITIAL_QI_FIELD = "custom_initial_quality_inspection"
WORK_ORDER_FINAL_QI_FIELD = "custom_final_quality_inspection"
WORK_ORDER_RETEST_REQUIRED_FIELD = "custom_qc_retest_required"
WORK_ORDER_RETEST_QI_FIELD = "custom_qc_retest_quality_inspection"
QI_WORK_ORDER_FIELD = "custom_work_order"
QI_STAGE_FIELD = "custom_work_order_qc_stage"

PRODUCTION_STAGES = [
    STAGE_MATERIAL_TRANSFER_PENDING,
    STAGE_MATERIAL_TRANSFERRED,
    STAGE_GRADE_CHANGE_CHECKLIST,
    STAGE_PREMIX_PREPARATION,
    STAGE_RM_LOADING,
    STAGE_COMPOUNDING,
    STAGE_PELLETIZING,
    STAGE_INITIAL_QC_PENDING,
    STAGE_INITIAL_QC_FAILED,
    STAGE_INITIAL_QC_PASSED,
    STAGE_PRODUCTION_RUNNING,
    STAGE_FINAL_QC_PENDING,
    STAGE_COMPLETED,
]


def options(values: list[str]) -> str:
    return "\n" + "\n".join(values)


def ensure_work_order_lifecycle_setup():
    ensure_custom_fields()
    frappe.clear_cache()


def ensure_custom_fields():
    if not frappe.db.exists("DocType", "Work Order"):
        return

    create_custom_fields(
        {
            "Work Order": [
                {
                    "fieldname": "custom_work_order_lifecycle_section",
                    "label": "Production Lifecycle",
                    "fieldtype": "Section Break",
                    "insert_after": "custom_fg_batch_no",
                },
                {
                    "fieldname": WORK_ORDER_STAGE_FIELD,
                    "label": "Production Stage",
                    "fieldtype": "Select",
                    "options": options(PRODUCTION_STAGES),
                    "default": STAGE_MATERIAL_TRANSFER_PENDING,
                    "insert_after": "custom_work_order_lifecycle_section",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": WORK_ORDER_LINE_FIELD,
                    "label": "Production Line",
                    "fieldtype": "Link",
                    "options": "Workstation",
                    "insert_after": WORK_ORDER_STAGE_FIELD,
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": WORK_ORDER_GRADE_CHANGE_FIELD,
                    "label": "Grade Change Required",
                    "fieldtype": "Check",
                    "insert_after": WORK_ORDER_LINE_FIELD,
                },
                {
                    "fieldname": "custom_work_order_qc_section",
                    "label": "Production QC Gates",
                    "fieldtype": "Section Break",
                    "insert_after": WORK_ORDER_GRADE_CHANGE_FIELD,
                },
                {
                    "fieldname": WORK_ORDER_INITIAL_QC_STATUS_FIELD,
                    "label": "Initial QC Status",
                    "fieldtype": "Select",
                    "options": options([INITIAL_QC_PENDING, INITIAL_QC_PASSED, INITIAL_QC_FAILED, INITIAL_QC_RETEST_REQUIRED]),
                    "default": INITIAL_QC_PENDING,
                    "insert_after": "custom_work_order_qc_section",
                    "in_list_view": 1,
                },
                {
                    "fieldname": WORK_ORDER_FINAL_QC_STATUS_FIELD,
                    "label": "Final QC Status",
                    "fieldtype": "Select",
                    "options": options([FINAL_QC_PENDING, FINAL_QC_PASSED, FINAL_QC_FAILED]),
                    "default": FINAL_QC_PENDING,
                    "insert_after": WORK_ORDER_INITIAL_QC_STATUS_FIELD,
                    "in_list_view": 1,
                },
                {
                    "fieldname": WORK_ORDER_QC_REMARKS_FIELD,
                    "label": "QC Correction Remarks",
                    "fieldtype": "Small Text",
                    "insert_after": WORK_ORDER_FINAL_QC_STATUS_FIELD,
                },
                {
                    "fieldname": WORK_ORDER_INITIAL_QI_FIELD,
                    "label": "Initial Quality Inspection",
                    "fieldtype": "Link",
                    "options": "Quality Inspection",
                    "insert_after": WORK_ORDER_QC_REMARKS_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": WORK_ORDER_FINAL_QI_FIELD,
                    "label": "Final Quality Inspection",
                    "fieldtype": "Link",
                    "options": "Quality Inspection",
                    "insert_after": WORK_ORDER_INITIAL_QI_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": WORK_ORDER_RETEST_REQUIRED_FIELD,
                    "label": "QC Retest Required",
                    "fieldtype": "Check",
                    "insert_after": WORK_ORDER_FINAL_QI_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": WORK_ORDER_RETEST_QI_FIELD,
                    "label": "QC Retest Quality Inspection",
                    "fieldtype": "Link",
                    "options": "Quality Inspection",
                    "insert_after": WORK_ORDER_RETEST_REQUIRED_FIELD,
                    "read_only": 1,
                },
            ],
            "Quality Inspection": [
                {
                    "fieldname": QI_WORK_ORDER_FIELD,
                    "label": "Work Order",
                    "fieldtype": "Link",
                    "options": "Work Order",
                    "insert_after": "reference_name",
                    "in_list_view": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": QI_STAGE_FIELD,
                    "label": "Work Order QC Stage",
                    "fieldtype": "Select",
                    "options": options([QI_STAGE_INITIAL, QI_STAGE_RETEST, QI_STAGE_FINAL, "In-Process QC"]),
                    "insert_after": QI_WORK_ORDER_FIELD,
                    "in_list_view": 1,
                },
            ],
        },
        update=True,
    )


def validate_work_order_lifecycle(doc, method=None):
    if not doc.get(WORK_ORDER_STAGE_FIELD):
        doc.set(WORK_ORDER_STAGE_FIELD, STAGE_MATERIAL_TRANSFER_PENDING)
    if not doc.get(WORK_ORDER_INITIAL_QC_STATUS_FIELD):
        doc.set(WORK_ORDER_INITIAL_QC_STATUS_FIELD, INITIAL_QC_PENDING)
    if not doc.get(WORK_ORDER_FINAL_QC_STATUS_FIELD):
        doc.set(WORK_ORDER_FINAL_QC_STATUS_FIELD, FINAL_QC_PENDING)

    initial_status = doc.get(WORK_ORDER_INITIAL_QC_STATUS_FIELD)
    stage = doc.get(WORK_ORDER_STAGE_FIELD)
    if initial_status == INITIAL_QC_FAILED or stage == STAGE_INITIAL_QC_FAILED:
        if not (doc.get(WORK_ORDER_QC_REMARKS_FIELD) or "").strip():
            frappe.throw(_("QC Correction Remarks are mandatory when Initial QC fails."))
        doc.set(WORK_ORDER_STAGE_FIELD, STAGE_INITIAL_QC_FAILED)

    if stage == STAGE_COMPLETED and doc.get(WORK_ORDER_FINAL_QC_STATUS_FIELD) != FINAL_QC_PASSED:
        frappe.throw(_("Final QC must be Passed before Production Stage can be Completed."))



def validate_quality_inspection_lifecycle(doc, method=None):
    if doc.get(QI_STAGE_FIELD) == "In-Process QC":
        from calco_erp.calco_production.in_process_quality import validate_qi_identity
        return validate_qi_identity(doc)
    work_order = doc.get(QI_WORK_ORDER_FIELD)
    qc_stage = doc.get(QI_STAGE_FIELD)
    if not work_order and not qc_stage:
        return

    if not work_order:
        frappe.throw(_("Work Order is mandatory for Work Order QC inspections."))
    if not qc_stage:
        frappe.throw(_("Work Order QC Stage is mandatory for Work Order QC inspections."))
    if qc_stage not in {QI_STAGE_INITIAL, QI_STAGE_RETEST, QI_STAGE_FINAL}:
        frappe.throw(_("Invalid Work Order QC Stage."))
    if qc_stage in {QI_STAGE_INITIAL, QI_STAGE_RETEST}:
        from calco_erp.calco_production.in_process_quality import MODEL, MODEL_FIELD
        if frappe.db.get_value("Work Order", work_order, MODEL_FIELD) == MODEL:
            frappe.throw(_("Use In-Process QC checkpoints for this Work Order."))
        _validate_initial_qc_job_card_reference(doc, work_order)
    if doc.docstatus == 1 and doc.get("status") not in {"Accepted", "Rejected"}:
        frappe.throw(_("Quality Inspection Status must be Accepted or Rejected for Work Order QC."))


def _validate_initial_qc_job_card_reference(doc, work_order: str):
    job_card = get_compounding_job_card(work_order)
    if not job_card:
        return
    if doc.get("reference_type") != "Job Card" or doc.get("reference_name") != job_card:
        frappe.throw(_("Initial QC must reference Compounding Job Card {0}.").format(job_card))
def validate_stock_entry_lifecycle(doc, method=None):
    if doc.get("custom_partial_fg_lot"):
        from calco_erp.calco_production.partial_fg_lots import validate_manufacture
        validate_manufacture(doc)
        return
    if get_stock_entry_purpose(doc) != "Manufacture" or not doc.get("work_order"):
        return

    from calco_erp.calco_production.in_process_quality import assert_manufacture_allowed
    if assert_manufacture_allowed(doc.work_order):
        return
    work_order = frappe.db.get_value(
        "Work Order",
        doc.work_order,
        [
            WORK_ORDER_STAGE_FIELD,
            WORK_ORDER_INITIAL_QC_STATUS_FIELD,
            WORK_ORDER_QC_REMARKS_FIELD,
        ],
        as_dict=True,
    )
    if not work_order:
        return

    stage = work_order.get(WORK_ORDER_STAGE_FIELD)
    initial_status = work_order.get(WORK_ORDER_INITIAL_QC_STATUS_FIELD)
    remarks = (work_order.get(WORK_ORDER_QC_REMARKS_FIELD) or "").strip()

    if stage == STAGE_INITIAL_QC_FAILED or initial_status == INITIAL_QC_FAILED:
        if not remarks:
            frappe.throw(_("QC Correction Remarks are mandatory before correction processing can continue."))
        frappe.throw(_("Manufacture Stock Entry is blocked because Initial QC failed and correction/retest is required."))

    if initial_status != INITIAL_QC_PASSED:
        frappe.throw(_("Initial QC must be Passed before Manufacture Stock Entry can be submitted."))


def sync_stock_entry_lifecycle_on_submit(doc, method=None):
    if doc.get("custom_partial_fg_lot"):
        return
    if not doc.get("work_order"):
        return

    purpose = get_stock_entry_purpose(doc)
    if purpose == "Material Transfer for Manufacture":
        set_work_order_values_if_not_cancelled(
            doc.work_order,
            {
                WORK_ORDER_STAGE_FIELD: STAGE_MATERIAL_TRANSFERRED,
            },
            only_if_stage_before=STAGE_MATERIAL_TRANSFERRED,
        )
    elif purpose == "Manufacture":
        set_work_order_values_if_not_cancelled(
            doc.work_order,
            {
                WORK_ORDER_STAGE_FIELD: STAGE_FINAL_QC_PENDING,
            },
            only_if_not_completed=True,
        )


def sync_quality_inspection_lifecycle_on_submit(doc, method=None):
    if doc.get("reference_type") == "Stock Entry" and frappe.db.get_value("Stock Entry",doc.get("reference_name"),"custom_partial_fg_lot"):
        return
    work_order = doc.get(QI_WORK_ORDER_FIELD)
    qc_stage = doc.get(QI_STAGE_FIELD)
    if not work_order or not qc_stage:
        return

    status = (doc.get("status") or "").strip()
    passed = status == "Accepted"
    failed = status == "Rejected"
    if not (passed or failed):
        return

    updates: dict[str, object] = {}
    if qc_stage == QI_STAGE_INITIAL:
        updates[WORK_ORDER_INITIAL_QI_FIELD] = doc.name
        if passed:
            updates[WORK_ORDER_INITIAL_QC_STATUS_FIELD] = INITIAL_QC_PASSED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_INITIAL_QC_PASSED
            updates[WORK_ORDER_RETEST_REQUIRED_FIELD] = 0
        elif failed:
            updates[WORK_ORDER_INITIAL_QC_STATUS_FIELD] = INITIAL_QC_FAILED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_INITIAL_QC_FAILED
            updates[WORK_ORDER_RETEST_REQUIRED_FIELD] = 1
    elif qc_stage == QI_STAGE_RETEST:
        updates[WORK_ORDER_RETEST_QI_FIELD] = doc.name
        if passed:
            updates[WORK_ORDER_INITIAL_QC_STATUS_FIELD] = INITIAL_QC_PASSED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_INITIAL_QC_PASSED
            updates[WORK_ORDER_RETEST_REQUIRED_FIELD] = 0
        elif failed:
            updates[WORK_ORDER_INITIAL_QC_STATUS_FIELD] = INITIAL_QC_FAILED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_INITIAL_QC_FAILED
            updates[WORK_ORDER_RETEST_REQUIRED_FIELD] = 1
    elif qc_stage == QI_STAGE_FINAL:
        updates[WORK_ORDER_FINAL_QI_FIELD] = doc.name
        if passed:
            updates[WORK_ORDER_FINAL_QC_STATUS_FIELD] = FINAL_QC_PASSED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_COMPLETED
        elif failed:
            updates[WORK_ORDER_FINAL_QC_STATUS_FIELD] = FINAL_QC_FAILED
            updates[WORK_ORDER_STAGE_FIELD] = STAGE_FINAL_QC_PENDING

    if updates:
        set_work_order_values_if_not_cancelled(work_order, updates)
    if qc_stage in {QI_STAGE_INITIAL, QI_STAGE_RETEST} and doc.get("reference_type") == "Job Card":
        _link_quality_inspection_to_job_card(doc.get("reference_name"), doc.name)


def _link_quality_inspection_to_job_card(job_card: str, quality_inspection: str):
    if not job_card or not frappe.db.exists("Job Card", job_card):
        return
    frappe.db.set_value("Job Card", job_card, "quality_inspection", quality_inspection, update_modified=True)


def mark_retest_required(work_order: str):
    if not work_order:
        frappe.throw(_("Work Order is required."))
    set_work_order_values_if_not_cancelled(
        work_order,
        {
            WORK_ORDER_INITIAL_QC_STATUS_FIELD: INITIAL_QC_RETEST_REQUIRED,
            WORK_ORDER_RETEST_REQUIRED_FIELD: 1,
            WORK_ORDER_STAGE_FIELD: STAGE_INITIAL_QC_FAILED,
        },
    )
    return frappe.get_doc("Work Order", work_order)


@frappe.whitelist()
def mark_retest_required_for_work_order(work_order: str):
    return mark_retest_required(work_order).as_dict()


@frappe.whitelist()
def make_work_order_quality_inspection(work_order: str, qc_stage: str):
    if qc_stage not in {QI_STAGE_INITIAL, QI_STAGE_RETEST, QI_STAGE_FINAL}:
        frappe.throw(_("Invalid Work Order QC Stage."))
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))

    wo = frappe.get_doc("Work Order", work_order)
    from calco_erp.calco_production.manufacture_entry import is_controlled_work_order

    controlled = is_controlled_work_order(wo.name)
    from calco_erp.calco_production.partial_fg_lots import has_lots
    if controlled and qc_stage == QI_STAGE_FINAL and has_lots(wo.name):
        frappe.throw(_("Open the specific Partial FG Lot to create its lot-specific Final Quality Inspection."))
    from calco_erp.calco_production.in_process_quality import is_parallel
    if is_parallel(wo) and qc_stage in {QI_STAGE_INITIAL, QI_STAGE_RETEST}:
        frappe.throw(_("Use In-Process QC checkpoints for this Work Order."))
    if controlled and qc_stage == QI_STAGE_FINAL:
        _lock_work_order_for_final_qc(wo.name)

    existing = _get_existing_work_order_qi(work_order, qc_stage)
    if isinstance(existing, str) and existing:
        if qc_stage == QI_STAGE_FINAL and not wo.get(WORK_ORDER_FINAL_QI_FIELD):
            set_work_order_values_if_not_cancelled(wo.name, {WORK_ORDER_FINAL_QI_FIELD: existing})
        return frappe.get_doc("Quality Inspection", existing).as_dict()

    doc = frappe.new_doc("Quality Inspection")
    doc.inspection_type = "In Process" if qc_stage in {QI_STAGE_INITIAL, QI_STAGE_RETEST} else "Outgoing"
    doc.item_code = wo.production_item
    doc.company = wo.company
    doc.set(QI_WORK_ORDER_FIELD, wo.name)
    doc.set(QI_STAGE_FIELD, qc_stage)
    if controlled and qc_stage == QI_STAGE_FINAL:
        _apply_final_qc_manufacture_reference(doc, wo)
        doc.status = QI_STATUS_PENDING
        doc.inspected_by = frappe.session.user
    elif controlled and qc_stage in {QI_STAGE_INITIAL, QI_STAGE_RETEST}:
        job_card = get_compounding_job_card(wo.name)
        if job_card:
            doc.reference_type = "Job Card"
            doc.reference_name = job_card
            production_batch = wo.get("custom_fg_batch_no") or ""
            doc.batch_no = (
                production_batch
                if production_batch and frappe.db.exists("Batch", production_batch)
                else ""
            )
        else:
            doc.batch_no = ""
    else:
        doc.batch_no = "" if controlled else wo.get("custom_fg_batch_no") or ""

    if controlled and qc_stage == QI_STAGE_FINAL:
        doc.insert()
        set_work_order_values_if_not_cancelled(
            wo.name,
            {WORK_ORDER_FINAL_QI_FIELD: doc.name},
        )
    return doc.as_dict()


def _lock_work_order_for_final_qc(work_order: str) -> None:
    frappe.db.sql(
        "select name from `tabWork Order` where name = %s for update",
        (work_order,),
    )


def _get_existing_work_order_qi(work_order: str, qc_stage: str) -> str | None:
    return frappe.db.get_value(
        "Quality Inspection",
        {QI_WORK_ORDER_FIELD: work_order, QI_STAGE_FIELD: qc_stage, "docstatus": ("<", 2)},
        "name",
        order_by="creation desc",
    )


def get_compounding_job_card(work_order: str) -> str | None:
    from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION

    return frappe.db.get_value(
        "Job Card",
        {"work_order": work_order, "operation": COMPOUNDING_OPERATION, "docstatus": ("<", 2)},
        "name",
        order_by="creation desc",
    )


def assert_packing_initial_qc_passed(work_order: str, operation: str):
    from calco_erp.calco_production.operation_master import PACKING_OPERATION

    if operation != PACKING_OPERATION:
        return
    from calco_erp.calco_production.in_process_quality import is_parallel, assert_no_hold
    if is_parallel(frappe.get_doc("Work Order", work_order)):
        assert_no_hold(work_order)
        return
    status = frappe.db.get_value("Work Order", work_order, WORK_ORDER_INITIAL_QC_STATUS_FIELD)
    if status != INITIAL_QC_PASSED:
        frappe.throw(_("Initial QC must be Passed before Packing can start or record production."))


def _apply_final_qc_manufacture_reference(doc, wo):
    rows = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": wo.name,
            "purpose": "Manufacture",
            "docstatus": 1,
        },
        fields=["name", "posting_date", "posting_time"],
        order_by="posting_date desc, posting_time desc, creation desc",
        limit_page_length=1,
    )
    if not rows:
        frappe.throw(_("Submitted Manufacture Stock Entry is required before Final QC."))
    stock_entry = frappe.get_doc("Stock Entry", rows[0].name)
    finished = next(
        (
            row
            for row in stock_entry.get("items") or []
            if row.get("is_finished_item") and row.get("item_code") == wo.production_item
        ),
        None,
    )
    if not finished:
        frappe.throw(_("Manufacture Stock Entry has no finished item row for {0}.").format(wo.production_item))
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no

    batch_no = get_stock_entry_row_batch_no(finished)
    if not batch_no:
        frappe.throw(_("Manufacture Stock Entry has no authoritative FG Batch."))
    doc.reference_type = "Stock Entry"
    doc.reference_name = stock_entry.name
    doc.item_code = finished.item_code
    doc.batch_no = batch_no


def set_work_order_values_if_not_cancelled(
    work_order: str,
    values: dict[str, object],
    only_if_stage_before: str | None = None,
    only_if_not_completed: bool = False,
):
    if not work_order or not frappe.db.exists("Work Order", work_order):
        return
    current = frappe.db.get_value("Work Order", work_order, ["docstatus", WORK_ORDER_STAGE_FIELD], as_dict=True)
    if not current or current.get("docstatus") == 2:
        return
    if only_if_not_completed and current.get(WORK_ORDER_STAGE_FIELD) == STAGE_COMPLETED:
        return
    if only_if_stage_before and not should_advance_stage(current.get(WORK_ORDER_STAGE_FIELD), only_if_stage_before):
        return

    for fieldname, value in values.items():
        frappe.db.set_value("Work Order", work_order, fieldname, value, update_modified=True)
    frappe.clear_cache(doctype="Work Order")


def should_advance_stage(current_stage: str | None, next_stage: str) -> bool:
    if not current_stage:
        return True
    try:
        return PRODUCTION_STAGES.index(current_stage) <= PRODUCTION_STAGES.index(next_stage)
    except ValueError:
        return True


def get_stock_entry_purpose(doc) -> str:
    return (doc.get("stock_entry_type") or doc.get("purpose") or "").strip()
