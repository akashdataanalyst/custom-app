from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, today

from calco_erp.calco_purchase.supplier_last_purchase_price import (
    get_supplier_specific_benchmark,
)


COMMERCIAL_APPROVAL_DOCTYPE = "Purchase Commercial Approval"
APPROVAL_PENDING_STATUS = "Draft"
APPROVAL_APPROVED_STATUS = "Approved"
APPROVAL_REJECTED_STATUS = "Rejected"


def ensure_supplier_quotation_commercial_approvals(doc, method=None):
    if not doc or doc.doctype != "Supplier Quotation" or int(doc.docstatus or 0) != 1:
        return []

    created = []
    for evaluation in evaluate_supplier_quotation(doc):
        if not evaluation["approval_required"]:
            continue
        if get_existing_commercial_approval(evaluation):
            continue
        approval = create_commercial_approval(evaluation)
        created.append(approval.name)
    return created


def validate_purchase_order_commercial_approval_gate(doc, method=None):
    if not doc or doc.doctype != "Purchase Order":
        return

    blocking_rows = [
        row
        for row in get_purchase_order_commercial_approval_rows(doc)
        if row["approval_required"] and not row["approval_valid"]
    ]
    if not blocking_rows:
        return

    row_lines = [
        _("{0} ({1}) in {2}: Commercial Approval status is {3}{4}").format(
            row["item_code"],
            row["item_name"] or "-",
            row["supplier_quotation"],
            row["approval_status"],
            f" [{row['approval']}]" if row["approval"] else "",
        )
        for row in blocking_rows
    ]
    statuses = {normalize_approval_status(row["approval_status"]) for row in blocking_rows}
    message = (
        _("Purchase Order cannot proceed because Commercial Approval is Rejected.")
        if statuses == {APPROVAL_REJECTED_STATUS.lower()}
        else _("Approved Commercial Approval is required before Purchase Order.")
    )
    frappe.throw(message + "<br><br>" + "<br>".join(row_lines))


def get_purchase_order_commercial_approval_rows(doc) -> list[dict]:
    """Resolve the current approval authority for each controlled PO row."""
    resolved = []
    for po_row in doc.get("items", []):
        for supplier_quotation_name in _get_supplier_quotation_sources(doc, po_row):
            if cint(frappe.db.get_value("Supplier Quotation", supplier_quotation_name, "docstatus")) != 1:
                continue

            supplier_quotation = frappe.get_doc("Supplier Quotation", supplier_quotation_name)
            if supplier_quotation.get("supplier") != doc.get("supplier"):
                continue

            ensure_supplier_quotation_commercial_approvals(supplier_quotation)
            evaluations = [
                evaluation
                for evaluation in evaluate_supplier_quotation(supplier_quotation)
                if _evaluation_matches_purchase_order_row(evaluation, po_row)
            ]
            for evaluation in evaluations:
                approval_doc = evaluation.get("approval_doc")
                approval_exists = bool(approval_doc)
                resolved.append(
                    {
                        **evaluation,
                        "approval_required": bool(evaluation.get("approval_required") or approval_exists),
                        "approval": approval_doc.get("name") if approval_doc else None,
                        "approval_status": _approval_display_status(approval_doc),
                        "approval_valid": is_approved_commercial_approval(approval_doc),
                    }
                )
            break
    return resolved


def is_approved_commercial_approval(approval_doc) -> bool:
    if not approval_doc or cint(approval_doc.get("docstatus")) == 2:
        return False
    return (
        normalize_approval_status(approval_doc.get("approval_status"))
        == APPROVAL_APPROVED_STATUS.lower()
        and normalize_approval_status(approval_doc.get("decision"))
        == APPROVAL_APPROVED_STATUS.lower()
    )


def _approval_display_status(approval_doc) -> str:
    if not approval_doc:
        return "Missing"
    if cint(approval_doc.get("docstatus")) == 2:
        return "Cancelled"
    return approval_doc.get("approval_status") or APPROVAL_PENDING_STATUS


def _get_supplier_quotation_sources(doc, po_row) -> list[str]:
    direct = po_row.get("supplier_quotation")
    if direct:
        return [direct]

    material_request = po_row.get("material_request")
    item_code = po_row.get("item_code")
    if not material_request or not item_code:
        return []

    filters = {"material_request": material_request, "item_code": item_code, "docstatus": 1}
    if po_row.get("material_request_item"):
        filters["material_request_item"] = po_row.get("material_request_item")
    quotation_rows = frappe.get_all(
        "Supplier Quotation Item",
        filters=filters,
        fields=["parent", "name"],
        order_by="creation desc",
        limit_page_length=0,
    )
    sources = []
    for quotation_row in quotation_rows:
        supplier_quotation = frappe.db.get_value(
            "Supplier Quotation",
            quotation_row.get("parent"),
            ["name", "supplier", "docstatus"],
            as_dict=True,
        )
        if not supplier_quotation or cint(supplier_quotation.get("docstatus")) != 1:
            continue
        if supplier_quotation.get("supplier") != doc.get("supplier"):
            continue
        sources.append(supplier_quotation.get("name"))
    return sources


def _evaluation_matches_purchase_order_row(evaluation: dict, po_row) -> bool:
    if evaluation.get("item_code") != po_row.get("item_code"):
        return False
    supplier_quotation_item = po_row.get("supplier_quotation_item")
    if supplier_quotation_item:
        return evaluation.get("supplier_quotation_item") == supplier_quotation_item
    material_request_item = po_row.get("material_request_item")
    if material_request_item:
        return evaluation.get("material_request_item") == material_request_item
    return evaluation.get("material_request") == po_row.get("material_request")


def evaluate_supplier_quotation(doc) -> list[dict]:
    evaluations = []
    for row in doc.get("items", []):
        item_code = row.get("item_code")
        if not item_code:
            continue

        supplier_benchmark = get_supplier_specific_benchmark(doc, row)
        if supplier_benchmark.get("applicable"):
            benchmark = supplier_benchmark
            quoted_rate = flt(row.get("rate") or 0)
        else:
            benchmark = get_benchmark_rate(item_code)
            quoted_rate = get_supplier_quotation_item_rate(row)
        benchmark_rate = benchmark.get("rate")
        benchmark_missing = benchmark_rate is None
        approval_required = benchmark_missing or flt(quoted_rate) > flt(benchmark_rate or 0)
        approval_doc = get_existing_commercial_approval_for_row(doc.name, row.name)

        evaluations.append(
            {
                "supplier_quotation": doc.name,
                "supplier_quotation_item": row.name,
                "material_request": row.get("material_request"),
                "material_request_item": row.get("material_request_item"),
                "request_for_quotation": row.get("request_for_quotation"),
                "supplier": doc.get("supplier"),
                "item_code": item_code,
                "item_name": row.get("item_name"),
                "uom": row.get("uom") or row.get("stock_uom"),
                "qty": flt(row.get("qty") or 0),
                "quoted_rate": quoted_rate,
                "benchmark_rate": benchmark_rate,
                "benchmark_source": benchmark.get("source"),
                "benchmark_reference": benchmark.get("reference"),
                "benchmark_missing": benchmark_missing,
                "variance_amount": flt(quoted_rate) - flt(benchmark_rate or 0),
                "variance_percent": ((flt(quoted_rate) - flt(benchmark_rate)) / flt(benchmark_rate) * 100) if flt(benchmark_rate) else 0,
                "approval_required": approval_required,
                "approval_doc": approval_doc,
            }
        )
    return evaluations


def get_existing_commercial_approval(evaluation: dict) -> str | None:
    return frappe.db.get_value(
        COMMERCIAL_APPROVAL_DOCTYPE,
        {"supplier_quotation_item": evaluation["supplier_quotation_item"]},
        "name",
    )


def get_existing_commercial_approval_for_row(supplier_quotation: str, supplier_quotation_item: str):
    fields = [
        "name",
        "supplier_quotation",
        "supplier_quotation_item",
        "item_code",
        "approval_status",
        "decision",
        "benchmark_rate",
        "quoted_rate",
        "variance_amount",
        "variance_percent",
        "reason_for_higher_price",
        "approved_by",
        "approval_date",
        "docstatus",
    ]
    rows = frappe.get_all(
        COMMERCIAL_APPROVAL_DOCTYPE,
        filters={"supplier_quotation": supplier_quotation, "supplier_quotation_item": supplier_quotation_item},
        fields=fields,
        order_by="creation desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def create_commercial_approval(evaluation: dict):
    approval = frappe.get_doc(
        {
            "doctype": COMMERCIAL_APPROVAL_DOCTYPE,
            "material_request": evaluation.get("material_request"),
            "request_for_quotation": evaluation.get("request_for_quotation"),
            "supplier_quotation": evaluation.get("supplier_quotation"),
            "supplier_quotation_item": evaluation.get("supplier_quotation_item"),
            "supplier": evaluation.get("supplier"),
            "item_code": evaluation.get("item_code"),
            "item_name": evaluation.get("item_name"),
            "uom": evaluation.get("uom"),
            "qty": evaluation.get("qty"),
            "benchmark_rate": evaluation.get("benchmark_rate"),
            "benchmark_source": evaluation.get("benchmark_source"),
            "benchmark_reference": evaluation.get("benchmark_reference"),
            "quoted_rate": evaluation.get("quoted_rate"),
            "variance_amount": evaluation.get("variance_amount"),
            "variance_percent": evaluation.get("variance_percent"),
            "approval_status": APPROVAL_PENDING_STATUS,
        }
    )
    approval.insert(ignore_permissions=True)
    return approval


def get_benchmark_rate(item_code: str) -> dict[str, object]:
    item_doc = frappe.get_cached_doc("Item", item_code)
    last_purchase_rate = flt(item_doc.get("last_purchase_rate"))
    if last_purchase_rate > 0:
        return {
            "rate": last_purchase_rate,
            "source": "Last Purchase Rate",
            "reference": item_code,
        }

    recent_rows = frappe.db.sql(
        """
        select
            poi.parent as purchase_order,
            poi.base_rate as base_rate,
            po.transaction_date as transaction_date
        from `tabPurchase Order Item` poi
        inner join `tabPurchase Order` po on po.name = poi.parent
        where po.docstatus = 1
          and poi.item_code = %s
          and ifnull(poi.base_rate, 0) > 0
        order by po.transaction_date desc, po.creation desc, poi.creation desc
        limit 3
        """,
        item_code,
        as_dict=True,
    )
    if recent_rows:
        average_rate = sum(flt(row.get("base_rate")) for row in recent_rows) / len(recent_rows)
        return {
            "rate": average_rate,
            "source": "Average of Last 3 Purchase Orders",
            "reference": ", ".join(row.get("purchase_order") for row in recent_rows if row.get("purchase_order")),
        }

    standard_rate = flt(item_doc.get("standard_rate"))
    if standard_rate > 0:
        return {
            "rate": standard_rate,
            "source": "Item Standard Buying Rate",
            "reference": item_code,
        }

    return {
        "rate": None,
        "source": None,
        "reference": None,
    }


def get_supplier_quotation_item_rate(row) -> float:
    return flt(row.get("base_rate") or row.get("rate") or 0)


def normalize_approval_status(value: str | None) -> str:
    return (value or "").strip().lower()


def get_commercial_approval_snapshot(supplier_quotation_names: list[str]) -> dict[str, object]:
    if not supplier_quotation_names:
        return {"evaluations": [], "approval_docs": []}

    approval_docs = frappe.get_all(
        COMMERCIAL_APPROVAL_DOCTYPE,
        filters={"supplier_quotation": ("in", supplier_quotation_names)},
        fields=[
            "name",
            "supplier_quotation",
            "supplier_quotation_item",
            "item_code",
            "item_name",
            "approval_status",
            "decision",
            "benchmark_rate",
            "benchmark_source",
            "quoted_rate",
            "variance_amount",
            "variance_percent",
            "reason_for_higher_price",
            "approved_by",
            "approval_date",
        ],
        order_by="creation asc",
        limit_page_length=0,
    )

    evaluations = []
    for supplier_quotation_name in supplier_quotation_names:
        if frappe.db.get_value("Supplier Quotation", supplier_quotation_name, "docstatus") != 1:
            continue
        doc = frappe.get_doc("Supplier Quotation", supplier_quotation_name)
        evaluations.extend(evaluate_supplier_quotation(doc))

    return {
        "evaluations": evaluations,
        "approval_docs": approval_docs,
    }


@frappe.whitelist()
def get_supplier_quotation_commercial_approval_debug(supplier_quotation: str) -> dict[str, object]:
    if not supplier_quotation:
        frappe.throw(_("Supplier Quotation is required."))

    if frappe.db.get_value("Supplier Quotation", supplier_quotation, "docstatus") != 1:
        frappe.throw(_("Supplier Quotation must be submitted first."))

    doc = frappe.get_doc("Supplier Quotation", supplier_quotation)
    ensure_supplier_quotation_commercial_approvals(doc)
    return {
        "supplier_quotation": supplier_quotation,
        "evaluations": evaluate_supplier_quotation(doc),
    }
