from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from urllib.parse import urlencode

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, now_datetime

from calco_erp.coa_setup import COA_PRINT_FORMAT


TRACKER_SECTION_FIELD = "custom_order_journey_section"
TRACKER_HTML_FIELD = "custom_order_journey_tracker"

STAGE_STATUS_COLORS = {
    "Not Started": "grey",
    "In Progress": "blue",
    "Completed": "green",
    "Waiting": "orange",
    "Blocked / Hold": "red",
}

BLOCKED_STATUS_KEYWORDS = ("hold", "blocked", "rejected", "stopped")
WAITING_STATUS_KEYWORDS = ("pending", "review required")


def ensure_sales_order_journey_setup():
    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": TRACKER_SECTION_FIELD,
                    "fieldtype": "Section Break",
                    "label": "Order Journey Tracker",
                    "insert_after": "items",
                },
                {
                    "fieldname": TRACKER_HTML_FIELD,
                    "fieldtype": "HTML",
                    "label": "Order Journey Tracker",
                    "insert_after": TRACKER_SECTION_FIELD,
                },
            ]
        },
        update=True,
    )
    frappe.clear_cache()


@frappe.whitelist()
def get_sales_order_journey(sales_order: str) -> dict[str, object]:
    if not sales_order:
        frappe.throw("Sales Order is required.")

    sales_order_doc = frappe.get_doc("Sales Order", sales_order)
    sales_order_items = [row for row in sales_order_doc.get("items", []) if row.get("item_code")]
    sales_order_item_names = unique_non_empty(row.name for row in sales_order_items)
    sales_order_item_codes = unique_non_empty(row.item_code for row in sales_order_items)
    component_codes = get_default_bom_component_codes(sales_order_item_codes)

    production_context = get_production_context(sales_order_doc.name, sales_order_item_names)
    dispatch_context = get_dispatch_context(sales_order_doc.name, sales_order_item_names)

    return {
        "sales_order": sales_order_doc.name,
        "generated_on": str(now_datetime()),
        "stages": [
            build_sales_order_stage(sales_order_doc),
            build_rm_procurement_stage(
                sales_order_doc,
                sales_order_item_names,
                component_codes,
                production_context,
            ),
            build_production_stage(production_context),
            build_qc_stage(sales_order_item_codes, production_context),
            build_dispatch_stage(dispatch_context),
            build_invoice_stage(sales_order_doc.name, sales_order_item_names, dispatch_context),
        ],
    }


def build_sales_order_stage(sales_order_doc) -> dict[str, object]:
    if cint(sales_order_doc.docstatus) == 2:
        status = "Blocked / Hold"
        summary = "Sales Order is cancelled."
    elif cint(sales_order_doc.docstatus) == 1:
        status = "Completed"
        summary = "Sales Order is submitted."
    else:
        status = "In Progress"
        summary = "Sales Order is still in draft."

    documents = [
        {
            "label": "Sales Order",
            "doctype": "Sales Order",
            "name": sales_order_doc.name,
            "status": format_doc_status(sales_order_doc.as_dict()),
            "detail": join_parts(
                f"Customer {sales_order_doc.customer}" if sales_order_doc.get("customer") else "",
                f"Status {sales_order_doc.status}" if sales_order_doc.get("status") else "",
            ),
        }
    ]

    return make_stage(
        key="sales_order",
        label="Sales Order",
        status=status,
        summary=summary,
        current_sub_stage=format_doc_status(sales_order_doc.as_dict()),
        documents=documents,
    )


def build_rm_procurement_stage(
    sales_order_doc,
    sales_order_item_names: list[str],
    component_codes: list[str],
    production_context: dict[str, object],
) -> dict[str, object]:
    production_plan_item_names = get_names(production_context["production_plan_rows"])
    material_request_fields = [
        "item_code",
        "qty",
        "schedule_date",
        "sales_order",
        "sales_order_item",
        "production_plan",
    ]
    purchase_order_fields = [
        "item_code",
        "qty",
        "schedule_date",
        "material_request",
        "material_request_item",
        "sales_order",
        "sales_order_item",
        "production_plan",
        "production_plan_item",
    ]
    purchase_receipt_fields = [
        "item_code",
        "qty",
        "received_qty",
        "batch_no",
        "purchase_order",
        "purchase_order_item",
        "sales_order",
        "sales_order_item",
    ]

    initial_purchase_order_rows = collect_rows(
        "Purchase Order Item",
        [
            {"sales_order": sales_order_doc.name},
            {"sales_order_item": ("in", sales_order_item_names)},
            {"production_plan_item": ("in", production_plan_item_names)},
        ],
        purchase_order_fields,
    )
    initial_purchase_order_rows = filter_purchase_order_rows(
        initial_purchase_order_rows,
        sales_order=sales_order_doc.name,
        sales_order_item_names=sales_order_item_names,
        production_plan_item_names=production_plan_item_names,
    )

    material_request_rows = collect_rows(
        "Material Request Item",
        [
            {"sales_order": sales_order_doc.name},
            {"sales_order_item": ("in", sales_order_item_names)},
        ],
        material_request_fields,
    )
    material_request_rows = merge_named_records(
        material_request_rows,
        load_rows_by_names(
            "Material Request Item",
            get_field_values(initial_purchase_order_rows, "material_request_item"),
            material_request_fields,
        ),
    )
    material_request_rows = filter_material_request_rows(
        material_request_rows,
        sales_order=sales_order_doc.name,
        sales_order_item_names=sales_order_item_names,
        allowed_parent_names=get_field_values(initial_purchase_order_rows, "material_request"),
        allowed_row_names=get_field_values(initial_purchase_order_rows, "material_request_item"),
    )
    material_requests = load_docs_by_names(
        "Material Request",
        unique_non_empty(get_parents(material_request_rows) + get_field_values(initial_purchase_order_rows, "material_request")),
        ["status", "material_request_type", "transaction_date", "schedule_date"],
    )
    material_requests = [
        row for row in material_requests if not row.get("material_request_type") or row.material_request_type == "Purchase"
    ]

    request_for_quotation_rows = collect_rows(
        "Request for Quotation Item",
        [
            {"material_request": ("in", get_names(material_requests))},
            {"material_request_item": ("in", get_names(material_request_rows))},
        ],
        ["item_code", "qty", "material_request", "material_request_item"],
    )
    request_for_quotations = load_docs_by_names(
        "Request for Quotation",
        get_parents(request_for_quotation_rows),
        ["status", "transaction_date"],
    )

    supplier_quotation_rows = collect_rows(
        "Supplier Quotation Item",
        [
            {"material_request": ("in", get_names(material_requests))},
            {"material_request_item": ("in", get_names(material_request_rows))},
            {"request_for_quotation": ("in", get_names(request_for_quotations))},
        ],
        ["item_code", "qty", "material_request", "material_request_item", "request_for_quotation"],
    )
    supplier_quotations = load_docs_by_names(
        "Supplier Quotation",
        get_parents(supplier_quotation_rows),
        ["status", "transaction_date", "supplier"],
    )

    purchase_order_rows = collect_rows(
        "Purchase Order Item",
        [
            {"sales_order": sales_order_doc.name},
            {"sales_order_item": ("in", sales_order_item_names)},
            {"production_plan_item": ("in", production_plan_item_names)},
            {"material_request": ("in", get_names(material_requests))},
            {"material_request_item": ("in", get_names(material_request_rows))},
        ],
        purchase_order_fields,
    )
    purchase_order_rows = merge_named_records(initial_purchase_order_rows, purchase_order_rows)
    purchase_order_rows = filter_purchase_order_rows(
        purchase_order_rows,
        sales_order=sales_order_doc.name,
        sales_order_item_names=sales_order_item_names,
        production_plan_item_names=production_plan_item_names,
        material_request_names=get_names(material_requests),
        material_request_item_names=get_names(material_request_rows),
    )
    purchase_orders = merge_named_records(
        load_docs_by_names(
            "Purchase Order",
            get_parents(purchase_order_rows),
            ["status", "transaction_date", "schedule_date", "supplier", "supplier_quotation"],
        ),
        collect_docs(
            "Purchase Order",
            [{"supplier_quotation": ("in", get_names(supplier_quotations))}],
            ["status", "transaction_date", "schedule_date", "supplier", "supplier_quotation"],
        ),
    )

    purchase_receipt_rows = collect_rows(
        "Purchase Receipt Item",
        [
            {"sales_order": sales_order_doc.name},
            {"sales_order_item": ("in", sales_order_item_names)},
            {"purchase_order": ("in", get_names(purchase_orders))},
            {"purchase_order_item": ("in", get_names(purchase_order_rows))},
        ],
        purchase_receipt_fields,
    )
    traced_purchase_receipt_rows = get_traced_purchase_receipt_rows(production_context)
    purchase_receipt_rows = merge_named_records(purchase_receipt_rows, traced_purchase_receipt_rows)
    purchase_receipt_rows = filter_purchase_receipt_rows(
        purchase_receipt_rows,
        sales_order=sales_order_doc.name,
        sales_order_item_names=sales_order_item_names,
        purchase_order_names=get_names(purchase_orders),
        purchase_order_item_names=get_names(purchase_order_rows),
        traced_row_names=get_names(traced_purchase_receipt_rows),
    )
    purchase_receipts = load_docs_by_names(
        "Purchase Receipt",
        get_parents(purchase_receipt_rows),
        ["status", "posting_date", "supplier", "is_return"],
    )
    purchase_receipts = [row for row in purchase_receipts if not cint(row.get("is_return"))]

    any_docs_present = any(
        (
            material_requests,
            request_for_quotations,
            supplier_quotations,
            purchase_orders,
            purchase_receipts,
        )
    )
    blocked = any_blocked_documents(material_requests + request_for_quotations + supplier_quotations + purchase_orders)
    submitted_receipts = [row for row in purchase_receipts if cint(row.docstatus) == 1]
    draft_receipts = [row for row in purchase_receipts if cint(row.docstatus) == 0]

    if not any_docs_present:
        status = "Not Started"
        summary = "No RM procurement documents are linked yet."
        current_sub_stage = "No RM documents"
    elif blocked and not submitted_receipts:
        status = "Blocked / Hold"
        summary = "A linked RM procurement document is on hold or rejected."
        current_sub_stage = "Procurement blocked"
    elif draft_receipts:
        status = "In Progress"
        summary = "Purchase Receipt is in draft."
        current_sub_stage = "Purchase Receipt draft"
    elif submitted_receipts:
        status = "Completed"
        summary = "Purchase Receipt is submitted for linked RM batches."
        current_sub_stage = "Purchase Receipt submitted"
    elif purchase_orders:
        status = "Waiting"
        summary = "Purchase Order exists and is waiting for receipt."
        current_sub_stage = "Purchase Receipt pending"
    else:
        status = "In Progress"
        summary = "RM procurement has started."
        current_sub_stage = derive_first_available_sub_stage(
            material_requests=material_requests,
            request_for_quotations=request_for_quotations,
            supplier_quotations=supplier_quotations,
        )

    procurement_documents = []
    procurement_documents.extend(build_document_entries("Material Request", material_requests, group_rows_by_parent(material_request_rows)))
    procurement_documents.extend(
        build_document_entries(
            "Request for Quotation",
            request_for_quotations,
            group_rows_by_parent(request_for_quotation_rows),
        )
    )
    procurement_documents.extend(
        build_document_entries(
            "Supplier Quotation",
            supplier_quotations,
            group_rows_by_parent(supplier_quotation_rows),
        )
    )
    procurement_documents.extend(build_document_entries("Purchase Order", purchase_orders, group_rows_by_parent(purchase_order_rows)))
    procurement_documents.extend(
        build_document_entries("Purchase Receipt", purchase_receipts, group_rows_by_parent(purchase_receipt_rows))
    )

    return make_stage(
        key="rm_procurement",
        label="RM Procurement",
        status=status,
        summary=summary,
        current_sub_stage=current_sub_stage,
        documents=procurement_documents,
        details=[
            {"label": "Current sub-stage", "value": current_sub_stage},
            {
                "label": "RM Components",
                "value": f"{len(component_codes)} BOM component item(s)" if component_codes else "No default BOM components found",
            },
        ],
        empty_message="No linked RM procurement documents were found for this Sales Order.",
    )


def build_production_stage(production_context: dict[str, object]) -> dict[str, object]:
    production_plans = production_context["production_plans"]
    work_orders = production_context["work_orders"]
    readiness_checks = production_context["readiness_checks"]
    manufacture_entries = production_context["manufacture_entries"]
    batch_records = production_context["batch_records"]

    if not production_plans and not work_orders and not manufacture_entries:
        status = "Not Started"
        summary = "No production documents are linked yet."
        current_sub_stage = "No production documents"
    elif any_blocked_documents(work_orders):
        status = "Blocked / Hold"
        summary = "A linked Work Order is on hold or stopped."
        current_sub_stage = "Production blocked"
    elif any(cint(row.docstatus) == 0 for row in manufacture_entries):
        status = "In Progress"
        summary = "Manufacture Stock Entry is in draft."
        current_sub_stage = "Manufacture entry draft"
    elif any(cint(row.docstatus) == 1 for row in manufacture_entries):
        status = "Completed"
        summary = "Manufacture Stock Entry is submitted."
        current_sub_stage = "Manufacture entry submitted"
    elif work_orders:
        status = "In Progress"
        summary = "Work Order is created and production is underway."
        current_sub_stage = "Work Order created"
    else:
        status = "In Progress"
        summary = "Production Plan is created."
        current_sub_stage = "Production Plan created"

    production_documents = []
    production_documents.extend(build_document_entries("Production Plan", production_plans))
    production_documents.extend(build_document_entries("Work Order", work_orders))
    production_documents.extend(build_document_entries("Material Readiness Check", readiness_checks))
    production_documents.extend(build_document_entries("Stock Entry", manufacture_entries, group_rows_by_parent(production_context["finished_rows"])))
    production_documents.extend(build_document_entries("Batch Production Record", batch_records))

    return make_stage(
        key="production",
        label="Production",
        status=status,
        summary=summary,
        current_sub_stage=current_sub_stage,
        documents=production_documents,
        details=[{"label": "Current sub-stage", "value": current_sub_stage}],
        empty_message="No linked production documents were found for this Sales Order.",
    )


def build_qc_stage(sales_order_item_codes: list[str], production_context: dict[str, object]) -> dict[str, object]:
    manufacture_entry_names = get_names(production_context["manufacture_entries"])
    batch_record_names = get_names(production_context["batch_records"])
    finished_batches = {
        (row.get("item_code"), row.get("batch_no"))
        for row in production_context["finished_rows"]
        if row.get("item_code") and row.get("batch_no")
    }

    quality_inspections = collect_docs(
        "Quality Inspection",
        [{"reference_type": "Stock Entry", "reference_name": ("in", manufacture_entry_names)}],
        ["status", "inspection_type", "reference_type", "reference_name", "item_code", "batch_no", "report_date"],
    )
    quality_inspections = [
        row
        for row in quality_inspections
        if not sales_order_item_codes or row.get("item_code") in sales_order_item_codes
    ]

    final_qc_releases = collect_docs(
        "Final QC Release",
        [
            {"batch_production_record": ("in", batch_record_names)},
            {"quality_inspection": ("in", get_names(quality_inspections))},
            {"stock_entry": ("in", manufacture_entry_names)},
        ],
        ["status", "stock_entry", "quality_inspection", "batch_production_record", "item_code", "batch_no", "released_on"],
    )
    final_qc_releases = [
        row
        for row in final_qc_releases
        if not finished_batches or (row.get("item_code"), row.get("batch_no")) in finished_batches
    ]

    inspection_statuses = {normalize_status(row.get("status")) for row in quality_inspections}
    release_statuses = {normalize_status(row.get("status")) for row in final_qc_releases}

    if not quality_inspections and not final_qc_releases:
        status = "Not Started"
        summary = "FG QC has not started yet."
        current_sub_stage = "No FG QC documents"
    elif any(status_name in release_statuses for status_name in ("hold", "rejected")) or "rejected" in inspection_statuses:
        status = "Blocked / Hold"
        summary = "A linked FG QC record is on hold or rejected."
        current_sub_stage = "QC hold or rejection"
    elif any(cint(row.docstatus) == 0 for row in quality_inspections):
        status = "In Progress"
        summary = "Quality Inspection is in draft."
        current_sub_stage = "Quality Inspection draft"
    elif any(cint(row.docstatus) == 0 for row in final_qc_releases) or any(
        waiting_keyword in inspection_statuses for waiting_keyword in WAITING_STATUS_KEYWORDS
    ):
        status = "Waiting"
        summary = "QC is pending review or release."
        current_sub_stage = "Pending QC review"
    elif any(normalize_status(row.get("status")) == "released" and cint(row.docstatus) == 1 for row in final_qc_releases):
        status = "Completed"
        summary = "Final QC Release is submitted."
        current_sub_stage = "Final QC released"
    elif any(normalize_status(row.get("status")) == "accepted" and cint(row.docstatus) == 1 for row in quality_inspections):
        status = "Completed"
        summary = "Quality Inspection is submitted and accepted."
        current_sub_stage = "Quality Inspection accepted"
    else:
        status = "Waiting"
        summary = "QC is waiting for the next action."
        current_sub_stage = "Pending QC action"

    qc_documents = []
    qc_documents.extend(build_document_entries("Quality Inspection", quality_inspections))
    qc_documents.extend(build_document_entries("Final QC Release", final_qc_releases))

    return make_stage(
        key="qc",
        label="QC",
        status=status,
        summary=summary,
        current_sub_stage=current_sub_stage,
        documents=qc_documents,
        details=[{"label": "Current sub-stage", "value": current_sub_stage}],
        empty_message="No FG QC documents were found for this Sales Order.",
    )


def get_dispatch_context(sales_order: str, sales_order_item_names: list[str]) -> dict[str, object]:
    delivery_note_rows = collect_rows(
        "Delivery Note Item",
        [
            {"against_sales_order": sales_order},
            {"so_detail": ("in", sales_order_item_names)},
        ],
        ["item_code", "qty", "batch_no", "against_sales_order", "so_detail"],
    )
    delivery_notes = load_docs_by_names(
        "Delivery Note",
        get_parents(delivery_note_rows),
        ["status", "posting_date", "customer"],
    )
    return {
        "delivery_note_rows": delivery_note_rows,
        "delivery_notes": delivery_notes,
    }


def build_dispatch_stage(dispatch_context: dict[str, object]) -> dict[str, object]:
    delivery_notes = dispatch_context["delivery_notes"]

    if not delivery_notes:
        status = "Not Started"
        summary = "No Delivery Note is linked yet."
        current_sub_stage = "No Delivery Note"
    elif any(cint(row.docstatus) == 0 for row in delivery_notes):
        status = "In Progress"
        summary = "Delivery Note is in draft."
        current_sub_stage = "Delivery Note draft"
    else:
        status = "Completed"
        summary = "Delivery Note is submitted."
        current_sub_stage = "Delivery Note submitted"

    return make_stage(
        key="dispatch",
        label="Dispatch",
        status=status,
        summary=summary,
        current_sub_stage=current_sub_stage,
        documents=build_document_entries("Delivery Note", delivery_notes, group_rows_by_parent(dispatch_context["delivery_note_rows"])),
        details=[{"label": "Current sub-stage", "value": current_sub_stage}],
        empty_message="No Delivery Notes were found for this Sales Order.",
    )


def build_invoice_stage(
    sales_order: str,
    sales_order_item_names: list[str],
    dispatch_context: dict[str, object],
) -> dict[str, object]:
    delivery_note_names = get_names(dispatch_context["delivery_notes"])
    delivery_note_item_names = get_names(dispatch_context["delivery_note_rows"])

    sales_invoice_rows = collect_rows(
        "Sales Invoice Item",
        [
            {"sales_order": sales_order},
            {"so_detail": ("in", sales_order_item_names)},
            {"delivery_note": ("in", delivery_note_names)},
            {"dn_detail": ("in", delivery_note_item_names)},
        ],
        ["item_code", "qty", "sales_order", "so_detail", "delivery_note", "dn_detail"],
    )
    sales_invoices = load_docs_by_names(
        "Sales Invoice",
        get_parents(sales_invoice_rows),
        ["status", "posting_date", "customer"],
    )
    sales_invoice_documents = [
        {
            **document,
            "actions": [
                build_form_action("Open Invoice", document["doctype"], document["name"]),
                build_url_action("Download Invoice PDF", build_pdf_download_url(document["doctype"], document["name"])),
            ],
        }
        for document in build_document_entries("Sales Invoice", sales_invoices, group_rows_by_parent(sales_invoice_rows))
    ]

    dispatched_batches = get_dispatched_batch_rows(dispatch_context["delivery_note_rows"])
    final_qc_releases = get_final_qc_releases_for_batches(dispatched_batches)
    coa_documents = build_coa_document_entries(dispatched_batches, final_qc_releases)

    if not sales_invoices:
        status = "Not Started"
        summary = "No Sales Invoice is linked yet."
        current_sub_stage = "No Sales Invoice"
    elif any(cint(row.docstatus) == 0 for row in sales_invoices):
        status = "In Progress"
        summary = "Sales Invoice is in draft."
        current_sub_stage = "Sales Invoice draft"
    else:
        status = "Completed"
        summary = "Sales Invoice is submitted."
        current_sub_stage = "Sales Invoice submitted"

    return make_stage(
        key="invoice",
        label="Invoice",
        status=status,
        summary=summary,
        current_sub_stage=current_sub_stage,
        documents=sales_invoice_documents,
        details=[
            {"label": "Current sub-stage", "value": current_sub_stage},
            {
                "label": "Test Certificates",
                "value": f"{len(coa_documents)} linked TC / COA record(s)" if coa_documents else "Test Certificate not available",
            },
        ],
        empty_message="No Sales Invoices were found for this Sales Order.",
        sections=[
            make_stage_section("Sales Invoice", sales_invoice_documents, "Sales Invoice not available"),
            make_stage_section("Test Certificate / COA", coa_documents, "Test Certificate not available"),
        ],
    )


def get_production_context(sales_order: str, sales_order_item_names: list[str]) -> dict[str, object]:
    production_plan_rows = collect_rows(
        "Production Plan Item",
        [
            {"sales_order": sales_order},
            {"sales_order_item": ("in", sales_order_item_names)},
        ],
        ["item_code", "planned_qty", "sales_order", "sales_order_item", "bom_no"],
    )
    production_plans = load_docs_by_names(
        "Production Plan",
        get_parents(production_plan_rows),
        ["status", "posting_date"],
    )
    production_plan_item_names = get_names(production_plan_rows)

    work_orders = collect_docs(
        "Work Order",
        [
            {"sales_order": sales_order},
            {"sales_order_item": ("in", sales_order_item_names)},
            {"production_plan_item": ("in", production_plan_item_names)},
        ],
        [
            "status",
            "production_item",
            "qty",
            "produced_qty",
            "planned_start_date",
            "production_plan",
            "sales_order",
            "sales_order_item",
            "production_plan_item",
        ],
    )
    work_orders = filter_work_orders(
        work_orders,
        sales_order=sales_order,
        sales_order_item_names=sales_order_item_names,
        production_plan_item_names=production_plan_item_names,
    )
    work_order_names = get_names(work_orders)

    readiness_checks = collect_docs(
        "Material Readiness Check",
        [{"work_order": ("in", work_order_names)}],
        ["status", "work_order"],
    )

    manufacture_entries = merge_named_records(
        collect_docs(
            "Stock Entry",
            [{"work_order": ("in", work_order_names), "purpose": "Manufacture"}],
            ["status", "purpose", "stock_entry_type", "posting_date", "work_order"],
        ),
        collect_docs(
            "Stock Entry",
            [{"work_order": ("in", work_order_names), "stock_entry_type": "Manufacture"}],
            ["status", "purpose", "stock_entry_type", "posting_date", "work_order"],
        ),
    )
    manufacture_entry_names = get_names(manufacture_entries)

    batch_records = collect_docs(
        "Batch Production Record",
        [
            {"stock_entry": ("in", manufacture_entry_names)},
            {"work_order": ("in", work_order_names)},
        ],
        ["status", "item_code", "fg_batch_no", "produced_qty", "stock_entry", "work_order", "production_plan"],
    )

    finished_rows = collect_rows(
        "Stock Entry Detail",
        [{"parent": ("in", manufacture_entry_names), "is_finished_item": 1}],
        ["item_code", "qty", "batch_no", "t_warehouse", "is_finished_item"],
    )
    batch_materials = collect_rows(
        "Batch Production Material",
        [{"parent": ("in", get_names(batch_records))}],
        ["item_code", "batch_no", "qty", "source_warehouse"],
    )

    return {
        "production_plans": production_plans,
        "production_plan_rows": production_plan_rows,
        "work_orders": work_orders,
        "readiness_checks": readiness_checks,
        "manufacture_entries": manufacture_entries,
        "finished_rows": finished_rows,
        "batch_records": batch_records,
        "batch_materials": batch_materials,
    }


def get_default_bom_component_codes(item_codes: list[str]) -> list[str]:
    bom_names = unique_non_empty(
        frappe.db.get_value("BOM", {"item": item_code, "is_default": 1, "docstatus": 1}, "name")
        for item_code in item_codes
    )
    if not bom_names:
        return []

    return unique_non_empty(
        row.item_code
        for row in frappe.get_all(
            "BOM Item",
            filters={"parent": ("in", bom_names)},
            fields=["item_code"],
            limit_page_length=0,
        )
    )


def get_traced_purchase_receipt_rows(production_context: dict[str, object]) -> list[frappe._dict]:
    batch_materials = production_context["batch_materials"]
    allowed_pairs = {
        (row.get("item_code"), row.get("batch_no"))
        for row in batch_materials
        if row.get("item_code") and row.get("batch_no")
    }
    if not allowed_pairs:
        return []

    traced_rows = collect_rows(
        "Purchase Receipt Item",
        [{"batch_no": ("in", unique_non_empty(pair[1] for pair in allowed_pairs))}],
        ["item_code", "qty", "received_qty", "batch_no", "purchase_order", "purchase_order_item"],
    )
    return [
        row
        for row in traced_rows
        if (row.get("item_code"), row.get("batch_no")) in allowed_pairs
    ]


def build_document_entries(
    doctype: str,
    documents: list[frappe._dict],
    rows_by_parent: dict[str, list[frappe._dict]] | None = None,
) -> list[dict[str, object]]:
    rows_by_parent = rows_by_parent or {}
    entries = []
    for row in documents:
        entries.append(
            {
                "label": doctype,
                "doctype": doctype,
                "name": row.name,
                "status": format_doc_status(row),
                "detail": build_document_detail(row, rows_by_parent.get(row.name) or []),
            }
        )
    return entries


def build_coa_document_entries(
    dispatched_batches: list[frappe._dict],
    final_qc_releases: list[frappe._dict],
) -> list[dict[str, object]]:
    batch_pairs = {
        (row.get("item_code"), row.get("batch_no"))
        for row in dispatched_batches
        if row.get("item_code") and row.get("batch_no")
    }
    if not batch_pairs:
        return []

    releases_by_name = {row.name: row for row in final_qc_releases}
    release_names = get_names(final_qc_releases)
    coa_names = get_field_values(final_qc_releases, "coa_record")
    coa_records = merge_named_records(
        load_docs_by_names(
            "COA Record",
            coa_names,
            ["status", "issue_date", "final_qc_release", "item_code", "batch_no"],
        ),
        collect_docs(
            "COA Record",
            [{"final_qc_release": ("in", release_names)}],
            ["status", "issue_date", "final_qc_release", "item_code", "batch_no"],
        ),
    )
    coa_records = [
        row
        for row in coa_records
        if (row.get("item_code"), row.get("batch_no")) in batch_pairs
    ]
    if not coa_records:
        return []

    batches_by_pair = group_batches_by_pair(dispatched_batches)
    file_lookup = get_coa_file_lookup(dispatched_batches)
    entries = []

    for row in coa_records:
        pair = (row.get("item_code"), row.get("batch_no"))
        batch_rows = batches_by_pair.get(pair) or []
        delivery_notes = unique_non_empty(batch_row.get("delivery_note") for batch_row in batch_rows)
        file_doc = get_matching_coa_file(batch_rows, file_lookup)
        download_url = build_file_download_url(file_doc.get("file_url")) if file_doc else build_pdf_download_url(
            "COA Record",
            row.name,
            COA_PRINT_FORMAT,
        )
        release = releases_by_name.get(row.get("final_qc_release"))

        entries.append(
            {
                "label": "COA Record",
                "doctype": "COA Record",
                "name": row.name,
                "status": format_doc_status(row),
                "detail": join_parts(
                    get_first_value(row, ["issue_date"]),
                    f"Batch {row.get('batch_no')}" if row.get("batch_no") else "",
                    f"Final QC {release.name}" if release else "",
                    format_delivery_notes_detail(delivery_notes),
                ),
                "actions": [
                    build_form_action("Open TC / COA", "COA Record", row.name),
                    build_url_action("Download TC / COA PDF", download_url),
                ],
            }
        )

    return entries


def build_document_detail(document: frappe._dict, child_rows: list[frappe._dict]) -> str:
    item_codes = unique_non_empty(row.get("item_code") for row in child_rows)
    batch_nos = unique_non_empty(row.get("batch_no") for row in child_rows)
    return join_parts(
        get_first_value(document, ["posting_date", "transaction_date", "schedule_date", "released_on", "planned_start_date"]),
        document.get("supplier"),
        document.get("customer"),
        document.get("material_request_type"),
        ", ".join(item_codes[:2]) + ("..." if len(item_codes) > 2 else "") if item_codes else "",
        f"{len(batch_nos)} batch(es)" if batch_nos else "",
    )


def derive_first_available_sub_stage(**documents) -> str:
    stage_labels = {
        "supplier_quotations": "Supplier Quotation received",
        "request_for_quotations": "Request for Quotation sent",
        "material_requests": "Material Request created",
    }
    for key in ("supplier_quotations", "request_for_quotations", "material_requests"):
        if documents.get(key):
            return stage_labels[key]
    return "Procurement started"


def make_stage(
    key: str,
    label: str,
    status: str,
    summary: str,
    current_sub_stage: str,
    documents: list[dict[str, object]],
    details: list[dict[str, str]] | None = None,
    empty_message: str | None = None,
    sections: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "color": STAGE_STATUS_COLORS[status],
        "summary": summary,
        "current_sub_stage": current_sub_stage,
        "details": details or [{"label": "Current sub-stage", "value": current_sub_stage}],
        "documents": documents,
        "sections": sections or [],
        "empty_message": empty_message or "No linked documents found.",
    }


def make_stage_section(label: str, documents: list[dict[str, object]], empty_message: str) -> dict[str, object]:
    return {
        "label": label,
        "documents": documents,
        "empty_message": empty_message,
    }


def collect_rows(
    doctype: str,
    filters_list: list[dict[str, object]],
    fields: list[str] | None = None,
) -> list[frappe._dict]:
    if not doctype_available(doctype):
        return []

    requested_fields = unique_non_empty(["name", "parent", "parenttype", "parentfield"] + existing_fields(doctype, fields or []))
    rows_by_name: dict[str, frappe._dict] = {}

    for raw_filters in filters_list:
        usable_filters = sanitize_filters(doctype, raw_filters)
        if not usable_filters:
            continue

        for row in frappe.get_all(
            doctype,
            filters=usable_filters,
            fields=requested_fields,
            limit_page_length=0,
            order_by="modified desc",
        ):
            rows_by_name[row.name] = frappe._dict(row)

    return list(rows_by_name.values())


def collect_docs(
    doctype: str,
    filters_list: list[dict[str, object]],
    fields: list[str] | None = None,
) -> list[frappe._dict]:
    if not doctype_available(doctype):
        return []

    requested_fields = unique_non_empty(["name", "docstatus", "modified"] + existing_fields(doctype, fields or []))
    docs_by_name: dict[str, frappe._dict] = {}

    for raw_filters in filters_list:
        usable_filters = sanitize_filters(doctype, raw_filters)
        if not usable_filters:
            continue

        for row in frappe.get_all(
            doctype,
            filters=usable_filters,
            fields=requested_fields,
            limit_page_length=0,
            order_by="modified desc",
        ):
            docs_by_name[row.name] = frappe._dict(row)

    return list(docs_by_name.values())


def load_docs_by_names(doctype: str, names: list[str], fields: list[str] | None = None) -> list[frappe._dict]:
    if not names:
        return []
    return collect_docs(doctype, [{"name": ("in", names)}], fields)


def load_rows_by_names(doctype: str, names: list[str], fields: list[str] | None = None) -> list[frappe._dict]:
    if not names:
        return []
    return collect_rows(doctype, [{"name": ("in", names)}], fields)


def merge_named_records(*record_lists):
    merged: dict[str, frappe._dict] = {}
    for record_list in record_lists:
        for row in record_list or []:
            merged[row.name] = frappe._dict(row)
    return list(merged.values())


def group_rows_by_parent(rows: list[frappe._dict]) -> dict[str, list[frappe._dict]]:
    grouped: dict[str, list[frappe._dict]] = defaultdict(list)
    for row in rows:
        grouped[row.parent].append(row)
    return grouped


def get_names(records: list[frappe._dict]) -> list[str]:
    return unique_non_empty(row.get("name") for row in records)


def get_parents(records: list[frappe._dict]) -> list[str]:
    return unique_non_empty(row.get("parent") for row in records)


def get_field_values(records: list[frappe._dict], fieldname: str) -> list[str]:
    return unique_non_empty(row.get(fieldname) for row in records)


def any_blocked_documents(documents: list[frappe._dict]) -> bool:
    return any(is_blocked_status(row.get("status")) for row in documents)


def is_blocked_status(value: str | None) -> bool:
    normalized = normalize_status(value)
    return any(keyword in normalized for keyword in BLOCKED_STATUS_KEYWORDS)


def format_doc_status(document: frappe._dict) -> str:
    if cint(document.get("docstatus")) == 2:
        return "Cancelled"

    explicit_status = (document.get("status") or "").strip()
    if explicit_status:
        return explicit_status

    if cint(document.get("docstatus")) == 1:
        return "Submitted"

    return "Draft"


def get_first_value(document: frappe._dict, fields: list[str]) -> str:
    for fieldname in fields:
        value = document.get(fieldname)
        if value not in (None, ""):
            return str(value)
    return ""


def join_parts(*parts) -> str:
    return " | ".join(str(part).strip() for part in parts if part not in (None, ""))


def build_form_action(label: str, doctype: str, name: str) -> dict[str, str]:
    return {
        "label": label,
        "type": "form",
        "doctype": doctype,
        "name": name,
    }


def build_url_action(label: str, url: str) -> dict[str, str]:
    return {
        "label": label,
        "type": "url",
        "url": url,
        "target": "_blank",
    }


def build_pdf_download_url(doctype: str, name: str, print_format: str | None = None) -> str:
    query = {"doctype": doctype, "name": name}
    if print_format:
        query["format"] = print_format
    return f"/api/method/frappe.utils.print_format.download_pdf?{urlencode(query)}"


def build_file_download_url(file_url: str | None) -> str:
    if not file_url:
        return ""
    if file_url.startswith(("http://", "https://")):
        return file_url
    return file_url


def get_dispatched_batch_rows(delivery_note_rows: list[frappe._dict]) -> list[frappe._dict]:
    return [
        frappe._dict(
            {
                "delivery_note": row.parent,
                "delivery_note_item": row.name,
                "item_code": row.get("item_code"),
                "batch_no": row.get("batch_no"),
            }
        )
        for row in delivery_note_rows
        if row.get("item_code") and row.get("batch_no")
    ]


def get_final_qc_releases_for_batches(dispatched_batches: list[frappe._dict]) -> list[frappe._dict]:
    batch_pairs = {
        (row.get("item_code"), row.get("batch_no"))
        for row in dispatched_batches
        if row.get("item_code") and row.get("batch_no")
    }
    if not batch_pairs:
        return []

    releases = collect_docs(
        "Final QC Release",
        [
            {
                "item_code": ("in", unique_non_empty(pair[0] for pair in batch_pairs)),
                "batch_no": ("in", unique_non_empty(pair[1] for pair in batch_pairs)),
            }
        ],
        ["status", "released_on", "item_code", "batch_no", "coa_record"],
    )
    return [
        row
        for row in releases
        if (row.get("item_code"), row.get("batch_no")) in batch_pairs
    ]


def group_batches_by_pair(dispatched_batches: list[frappe._dict]) -> dict[tuple[str, str], list[frappe._dict]]:
    grouped: dict[tuple[str, str], list[frappe._dict]] = defaultdict(list)
    for row in dispatched_batches:
        item_code = row.get("item_code")
        batch_no = row.get("batch_no")
        if not item_code or not batch_no:
            continue
        grouped[(item_code, batch_no)].append(row)
    return grouped


def get_coa_file_lookup(dispatched_batches: list[frappe._dict]) -> dict[tuple[str, str], frappe._dict]:
    delivery_note_names = unique_non_empty(row.get("delivery_note") for row in dispatched_batches)
    expected_file_names = unique_non_empty(
        build_coa_attachment_filename(row.get("item_code"), row.get("batch_no"))
        for row in dispatched_batches
        if row.get("item_code") and row.get("batch_no")
    )
    if not delivery_note_names or not expected_file_names:
        return {}

    files = collect_docs(
        "File",
        [
            {
                "attached_to_doctype": "Delivery Note",
                "attached_to_name": ("in", delivery_note_names),
                "file_name": ("in", expected_file_names),
            }
        ],
        ["attached_to_doctype", "attached_to_name", "file_name", "file_url"],
    )
    return {
        (row.get("attached_to_name"), row.get("file_name")): row
        for row in files
        if row.get("attached_to_name") and row.get("file_name")
    }


def get_matching_coa_file(
    batch_rows: list[frappe._dict],
    file_lookup: dict[tuple[str, str], frappe._dict],
) -> frappe._dict | None:
    for row in batch_rows:
        delivery_note = row.get("delivery_note")
        file_name = build_coa_attachment_filename(row.get("item_code"), row.get("batch_no"))
        if not delivery_note or not file_name:
            continue
        file_doc = file_lookup.get((delivery_note, file_name))
        if file_doc:
            return file_doc
    return None


def build_coa_attachment_filename(item_code: str | None, batch_no: str | None) -> str:
    if not item_code or not batch_no:
        return ""
    return f"COA-{item_code}-{batch_no}.pdf"


def format_delivery_notes_detail(delivery_notes: list[str]) -> str:
    if not delivery_notes:
        return ""
    if len(delivery_notes) == 1:
        return f"Delivery Note {delivery_notes[0]}"
    return f"{len(delivery_notes)} Delivery Note(s)"


def filter_material_request_rows(
    rows: list[frappe._dict],
    *,
    sales_order: str,
    sales_order_item_names: list[str],
    allowed_parent_names: list[str] | None = None,
    allowed_row_names: list[str] | None = None,
) -> list[frappe._dict]:
    sales_order_item_set = set(sales_order_item_names)
    allowed_parent_set = set(allowed_parent_names or [])
    allowed_row_set = set(allowed_row_names or [])

    return [
        row
        for row in rows
        if row.name in allowed_row_set
        or row.parent in allowed_parent_set
        or row.get("sales_order") == sales_order
        or row.get("sales_order_item") in sales_order_item_set
    ]


def filter_purchase_order_rows(
    rows: list[frappe._dict],
    *,
    sales_order: str,
    sales_order_item_names: list[str],
    production_plan_item_names: list[str],
    material_request_names: list[str] | None = None,
    material_request_item_names: list[str] | None = None,
) -> list[frappe._dict]:
    sales_order_item_set = set(sales_order_item_names)
    production_plan_item_set = set(production_plan_item_names)
    material_request_set = set(material_request_names or [])
    material_request_item_set = set(material_request_item_names or [])

    return [
        row
        for row in rows
        if row.get("sales_order") == sales_order
        or row.get("sales_order_item") in sales_order_item_set
        or row.get("production_plan_item") in production_plan_item_set
        or row.get("material_request") in material_request_set
        or row.get("material_request_item") in material_request_item_set
    ]


def filter_purchase_receipt_rows(
    rows: list[frappe._dict],
    *,
    sales_order: str,
    sales_order_item_names: list[str],
    purchase_order_names: list[str],
    purchase_order_item_names: list[str],
    traced_row_names: list[str] | None = None,
) -> list[frappe._dict]:
    sales_order_item_set = set(sales_order_item_names)
    purchase_order_set = set(purchase_order_names)
    purchase_order_item_set = set(purchase_order_item_names)
    traced_row_set = set(traced_row_names or [])

    return [
        row
        for row in rows
        if row.name in traced_row_set
        or row.get("sales_order") == sales_order
        or row.get("sales_order_item") in sales_order_item_set
        or row.get("purchase_order") in purchase_order_set
        or row.get("purchase_order_item") in purchase_order_item_set
    ]


def filter_work_orders(
    rows: list[frappe._dict],
    *,
    sales_order: str,
    sales_order_item_names: list[str],
    production_plan_item_names: list[str],
) -> list[frappe._dict]:
    sales_order_item_set = set(sales_order_item_names)
    production_plan_item_set = set(production_plan_item_names)

    return [
        row
        for row in rows
        if row.get("sales_order") == sales_order
        or row.get("sales_order_item") in sales_order_item_set
        or row.get("production_plan_item") in production_plan_item_set
    ]


def sanitize_filters(doctype: str, raw_filters: dict[str, object]) -> dict[str, object]:
    usable_filters = {}
    for fieldname, value in raw_filters.items():
        if not filter_has_value(value):
            return {}
        if not doctype_has_field(doctype, fieldname):
            continue
        usable_filters[fieldname] = value
    return usable_filters


def filter_has_value(value: object) -> bool:
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "in":
        return bool(value[1])
    return value not in (None, "", [])


def existing_fields(doctype: str, fields: list[str]) -> list[str]:
    return [fieldname for fieldname in fields if doctype_has_field(doctype, fieldname)]


def unique_non_empty(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in (None, "") or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_status(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


@lru_cache(maxsize=None)
def doctype_available(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


@lru_cache(maxsize=None)
def doctype_has_field(doctype: str, fieldname: str) -> bool:
    if not doctype_available(doctype):
        return False
    if fieldname in {"name", "parent", "parenttype", "parentfield", "modified", "docstatus"}:
        return True
    return fieldname in (frappe.get_meta(doctype).get_valid_columns() or [])
