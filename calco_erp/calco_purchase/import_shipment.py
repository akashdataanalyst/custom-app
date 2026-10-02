from __future__ import annotations

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint, flt, getdate


LC_IMPORT_SHIPMENT_DOCTYPE = "LC Import Shipment"
OVERSEAS_SUPPLIER_TYPE = "Overseas"


def doctype_exists(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def filter_existing_fields(doctype: str, fields: list[str]) -> list[str]:
    if not doctype_exists(doctype):
        return []
    meta = frappe.get_meta(doctype)
    return [fieldname for fieldname in fields if meta.get_field(fieldname)]


def unique_non_empty(values) -> list[str]:
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def normalize_supplier_key(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def get_supplier_display_name(supplier: str | None) -> str:
    if not supplier:
        return ""
    return frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier


def get_purchase_order_items(purchase_order: str) -> list[dict]:
    if not purchase_order or not doctype_exists("Purchase Order Item"):
        return []
    return frappe.get_all(
        "Purchase Order Item",
        filters={"parent": purchase_order},
        fields=[
            "name",
            "parent",
            *filter_existing_fields(
                "Purchase Order Item",
                [
                    "item_code",
                    "item_name",
                    "uom",
                    "qty",
                    "schedule_date",
                    "material_request",
                    "material_request_item",
                    "supplier_quotation",
                    "supplier_quotation_item",
                ],
            ),
        ],
        limit_page_length=0,
    )


def get_supplier_quotation_items(supplier_quotation_names: list[str]) -> list[dict]:
    if not supplier_quotation_names or not doctype_exists("Supplier Quotation Item"):
        return []
    return frappe.get_all(
        "Supplier Quotation Item",
        filters={"parent": ("in", supplier_quotation_names)},
        fields=[
            "name",
            "parent",
            *filter_existing_fields(
                "Supplier Quotation Item",
                [
                    "request_for_quotation",
                    "material_request",
                    "material_request_item",
                    "item_code",
                    "qty",
                ],
            ),
        ],
        limit_page_length=0,
    )


def get_matching_supplier_matrix_rows(supplier: str | None, item_codes: list[str]) -> list[dict]:
    if not supplier or not item_codes or not doctype_exists("Supplier Approval Matrix"):
        return []
    normalized_items = unique_non_empty((code or "").strip().upper() for code in item_codes if code)
    if not normalized_items:
        return []
    supplier_keys = {
        normalize_supplier_key(supplier),
        normalize_supplier_key(get_supplier_display_name(supplier)),
    }
    rows = frappe.get_all(
        "Supplier Approval Matrix",
        filters={"item_code": ("in", normalized_items)},
        fields=["name", "item_code", "supplier", "supplier_type", "approval_status", "payment_terms"],
        limit_page_length=0,
    )
    return [
        row
        for row in rows
        if normalize_supplier_key(row.get("supplier")) in supplier_keys
        or normalize_supplier_key(get_supplier_display_name(row.get("supplier"))) in supplier_keys
    ]


def is_overseas_purchase_order(purchase_order: str | object) -> bool:
    context = get_import_shipment_context_from_purchase_order(purchase_order)
    return bool(context.get("overseas_supplier"))


def get_import_shipment_context_from_purchase_order(purchase_order: str | object) -> dict[str, object]:
    po_doc = purchase_order
    if isinstance(purchase_order, str):
        po_doc = frappe.get_doc("Purchase Order", purchase_order)

    item_rows = get_purchase_order_items(po_doc.name)
    supplier_quotation_names = unique_non_empty(row.get("supplier_quotation") for row in item_rows)
    supplier_quotation_rows = get_supplier_quotation_items(supplier_quotation_names)
    request_for_quotation_names = unique_non_empty(row.get("request_for_quotation") for row in supplier_quotation_rows)
    material_requests = unique_non_empty(
        [row.get("material_request") for row in item_rows] + [row.get("material_request") for row in supplier_quotation_rows]
    )
    item_codes = unique_non_empty(row.get("item_code") for row in item_rows)
    item_names = unique_non_empty(row.get("item_name") for row in item_rows if row.get("item_name"))
    uoms = unique_non_empty(row.get("uom") for row in item_rows if row.get("uom"))
    required_by_dates = unique_non_empty(row.get("schedule_date") for row in item_rows if row.get("schedule_date"))
    matrix_rows = get_matching_supplier_matrix_rows(po_doc.get("supplier"), item_codes)
    supplier_types = unique_non_empty(row.get("supplier_type") for row in matrix_rows)
    overseas_supplier = any((row.get("supplier_type") or "").strip() == OVERSEAS_SUPPLIER_TYPE for row in matrix_rows)
    total_qty = round(sum(flt(row.get("qty") or 0) for row in item_rows), 3)
    item_code_value = ", ".join(item_codes) if len(item_codes) > 1 else (item_codes[0] if item_codes else "")
    item_name_value = ", ".join(item_names) if len(item_names) > 1 else (item_names[0] if item_names else "")
    uom_value = ", ".join(uoms) if len(uoms) > 1 else (uoms[0] if uoms else "")

    return {
        "purchase_order": po_doc.name,
        "material_request": material_requests[0] if material_requests else "",
        "request_for_quotation": request_for_quotation_names[0] if request_for_quotation_names else "",
        "supplier_quotation": supplier_quotation_names[0] if supplier_quotation_names else "",
        "supplier": po_doc.get("supplier") or "",
        "item_code": item_code_value,
        "item_name": item_name_value,
        "qty": total_qty,
        "uom": uom_value,
        "po_date": po_doc.get("transaction_date"),
        "required_by": required_by_dates[0] if required_by_dates else po_doc.get("schedule_date"),
        "payment_terms": po_doc.get("payment_terms_template") or "",
        "currency": po_doc.get("currency") or "",
        "item_codes": item_codes,
        "material_requests": material_requests,
        "request_for_quotation_names": request_for_quotation_names,
        "supplier_quotation_names": supplier_quotation_names,
        "supplier_types": supplier_types,
        "overseas_supplier": 1 if overseas_supplier else 0,
        "supplier_type": OVERSEAS_SUPPLIER_TYPE if overseas_supplier else (supplier_types[0] if supplier_types else "Local"),
    }


def get_import_shipment_gate_for_purchase_order(purchase_order: str) -> dict[str, object]:
    context = get_import_shipment_context_from_purchase_order(purchase_order)
    docs = []
    if doctype_exists(LC_IMPORT_SHIPMENT_DOCTYPE):
        docs = frappe.get_all(
            LC_IMPORT_SHIPMENT_DOCTYPE,
            filters={"purchase_order": purchase_order, "docstatus": ("<", 2)},
            fields=[
                "name",
                "docstatus",
                *filter_existing_fields(
                    LC_IMPORT_SHIPMENT_DOCTYPE,
                    [
                        "status",
                        "supplier",
                        "material_request",
                        "request_for_quotation",
                        "supplier_quotation",
                        "purchase_order",
                        "item_code",
                        "qty",
                        "overseas_supplier",
                        "eta",
                        "etd",
                    ],
                ),
            ],
            order_by="modified desc, name desc",
            limit_page_length=0,
        )
    submitted_docs = [row for row in docs if cint(row.get("docstatus")) == 1]
    draft_docs = [row for row in docs if cint(row.get("docstatus")) == 0]
    return {
        **context,
        "required": bool(context.get("overseas_supplier")),
        "active_docs": docs,
        "submitted_docs": submitted_docs,
        "draft_docs": draft_docs,
        "has_submitted": bool(submitted_docs),
    }


def populate_lc_import_shipment_prefill(target_doc, context: dict[str, object]):
    target_doc.material_request = context.get("material_request") or target_doc.get("material_request")
    target_doc.request_for_quotation = context.get("request_for_quotation") or target_doc.get("request_for_quotation")
    target_doc.supplier_quotation = context.get("supplier_quotation") or target_doc.get("supplier_quotation")
    target_doc.purchase_order = context.get("purchase_order") or target_doc.get("purchase_order")
    target_doc.supplier = context.get("supplier") or target_doc.get("supplier")
    target_doc.item_code = context.get("item_code") or target_doc.get("item_code")
    if target_doc.meta.get_field("item_name"):
        target_doc.item_name = context.get("item_name") or target_doc.get("item_name")
    if target_doc.meta.get_field("uom"):
        target_doc.uom = context.get("uom") or target_doc.get("uom")
    target_doc.qty = context.get("qty") or target_doc.get("qty")
    target_doc.overseas_supplier = cint(context.get("overseas_supplier"))
    if target_doc.meta.get_field("po_date"):
        target_doc.po_date = context.get("po_date") or target_doc.get("po_date")
    if target_doc.meta.get_field("required_by"):
        target_doc.required_by = context.get("required_by") or target_doc.get("required_by")
    if target_doc.meta.get_field("payment_terms"):
        target_doc.payment_terms = context.get("payment_terms") or target_doc.get("payment_terms")
    if target_doc.meta.get_field("currency"):
        target_doc.currency = context.get("currency") or target_doc.get("currency")
    if target_doc.meta.get_field("status") and not target_doc.get("status"):
        target_doc.status = "Draft"


@frappe.whitelist()
def make_lc_import_shipment_from_purchase_order(source_name: str, target_doc=None):
    if not source_name:
        frappe.throw(_("Purchase Order is required to create LC Import Shipment."))

    context = get_import_shipment_gate_for_purchase_order(source_name)
    if not context.get("required"):
        frappe.throw(_("LC Import Shipment is required only for overseas suppliers."))

    if context.get("draft_docs"):
        return frappe.get_doc(LC_IMPORT_SHIPMENT_DOCTYPE, context["draft_docs"][0]["name"])

    doc = get_mapped_doc(
        "Purchase Order",
        source_name,
        {
            "Purchase Order": {
                "doctype": LC_IMPORT_SHIPMENT_DOCTYPE,
                "field_map": {
                    "name": "purchase_order",
                    "supplier": "supplier",
                    "transaction_date": "po_date",
                    "schedule_date": "required_by",
                    "payment_terms_template": "payment_terms",
                    "currency": "currency",
                },
            }
        },
        target_doc,
        postprocess=lambda source, target: populate_lc_import_shipment_prefill(target, context),
    )
    populate_lc_import_shipment_prefill(doc, context)
    return doc


@frappe.whitelist()
def create_lc_import_shipment_from_purchase_order(source_name: str):
    return make_lc_import_shipment_from_purchase_order(source_name)


def validate_purchase_receipt_import_shipment_gate(doc, method=None):
    if doc.doctype != "Purchase Receipt" or cint(doc.get("is_return")):
        return

    purchase_order_names = unique_non_empty(row.get("purchase_order") for row in doc.get("items", []) if row.get("purchase_order"))
    if not purchase_order_names:
        return

    blocked_orders = []
    for purchase_order in purchase_order_names:
        gate = get_import_shipment_gate_for_purchase_order(purchase_order)
        if not gate.get("required"):
            continue
        if gate.get("has_submitted"):
            continue
        if gate.get("draft_docs"):
            blocked_orders.append(
                _("{0} (draft LC {1} pending submit)").format(
                    purchase_order,
                    ", ".join(row.get("name") for row in gate["draft_docs"]),
                )
            )
        else:
            blocked_orders.append(purchase_order)

    if blocked_orders:
        frappe.throw(
            _("Purchase Receipt is blocked for overseas supplier until LC Import Shipment is submitted for Purchase Order(s): {0}.").format(
                ", ".join(blocked_orders)
            )
        )
