from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

import frappe
from frappe import _
from frappe.utils import flt, get_datetime


LPP_PATCH = "calco_erp.patches.v1_0_7.setup_supplier_quotation_lpp"
LPP_CLIENT_SCRIPT_NAME = "Supplier Quotation Supplier-Specific LPP"

LPP_FIELDS = (
    "custom_last_purchase_price",
    "custom_last_purchase_po",
    "custom_last_purchase_date",
    "custom_last_purchase_currency",
    "custom_last_purchase_uom",
    "custom_price_difference",
    "custom_price_difference_percent",
)

LPP_ITEM_CONTEXT_FIELDS = (
    "name",
    "idx",
    "item_code",
    "uom",
    "stock_uom",
    "conversion_factor",
)

LPP_QUERY = """
    select
        pri.item_code,
        pri.uom,
        pri.stock_uom,
        pri.conversion_factor,
        pri.rate,
        pri.base_rate,
        pri.purchase_order,
        pr.name as purchase_receipt,
        pr.posting_date as transaction_date,
        pr.posting_time,
        pr.currency,
        pr.conversion_rate,
        pr.supplier,
        pr.company
    from `tabPurchase Receipt Item` pri
    inner join `tabPurchase Receipt` pr on pr.name = pri.parent
    where pr.docstatus = 1
      and ifnull(pr.is_return, 0) = 0
      and pr.supplier = %(supplier)s
      and (%(company)s = '' or pr.company = %(company)s)
      and pri.item_code in %(item_codes)s
      and ifnull(pri.rate, 0) > 0
      and ifnull(pri.qty, 0) > 0
    order by
        pr.posting_date desc,
        pr.posting_time desc,
        pr.creation desc,
        pr.name desc,
        pri.idx desc
"""


def normalize_context(value: str | None) -> str:
    return (value or "").strip()


def calculate_price_variance(
    current_rate,
    last_purchase_price,
) -> tuple[float | None, float | None]:
    current = flt(current_rate)
    lpp = flt(last_purchase_price)
    if current <= 0 or lpp <= 0:
        return None, None
    difference = current - lpp
    return round(difference, 6), round((difference / lpp) * 100, 2)


def normalize_supplier_quotation_item(row) -> frappe._dict:
    if callable(getattr(row, "as_dict", None)):
        values = row.as_dict()
    elif isinstance(row, Mapping):
        values = row
    else:
        raise TypeError(
            "Supplier Quotation item must be a Frappe Document or mapping; "
            f"received {type(row).__name__}"
        )

    normalized = frappe._dict(
        {fieldname: values.get(fieldname) for fieldname in LPP_ITEM_CONTEXT_FIELDS}
    )
    if not normalized.stock_uom:
        normalized.stock_uom = normalized.uom
    return normalized


def _query_latest_comparable_purchase_receipts(filters: dict) -> list[dict]:
    return frappe.db.sql(LPP_QUERY, filters, as_dict=True)


def _get_conversion_factor(row) -> float | None:
    conversion_factor = flt(row.get("conversion_factor"))
    if conversion_factor > 0:
        return conversion_factor
    if normalize_context(row.get("uom")) == normalize_context(row.get("stock_uom")):
        return 1.0
    return None


def _convert_receipt_rate(
    receipt,
    item_row,
    current_currency: str,
    current_conversion_rate,
) -> tuple[float | None, str | None]:
    historical_factor = _get_conversion_factor(receipt)
    current_factor = _get_conversion_factor(item_row)
    if not historical_factor or not current_factor:
        return None, None
    if normalize_context(receipt.stock_uom) != normalize_context(item_row.stock_uom):
        return None, None

    if normalize_context(receipt.currency) == current_currency:
        rate = flt(receipt.rate) / historical_factor * current_factor
        if rate > 0:
            return round(rate, 6), "Transaction currency through stock UOM"
        return None, None

    conversion_rate = flt(current_conversion_rate)
    if conversion_rate <= 0 or flt(receipt.base_rate) <= 0:
        return None, None
    rate = flt(receipt.base_rate) / historical_factor * current_factor / conversion_rate
    if rate > 0:
        return round(rate, 6), "Company currency through stock UOM"
    return None, None


def resolve_supplier_last_purchase_prices(
    supplier: str | None,
    currency: str | None,
    items: Iterable[dict],
    *,
    company: str | None = None,
    conversion_rate=None,
) -> dict[tuple[str, str], dict]:
    supplier = normalize_context(supplier)
    currency = normalize_context(currency)
    item_rows = []
    for row in items:
        normalized = normalize_supplier_quotation_item(row)
        if normalized.item_code and normalized.uom:
            item_rows.append(normalized)
    if not supplier or not currency or not item_rows:
        return {}

    item_codes = tuple(sorted({normalize_context(row.item_code) for row in item_rows}))
    rows = _query_latest_comparable_purchase_receipts(
        {
            "supplier": supplier,
            "company": normalize_context(company),
            "item_codes": item_codes,
        }
    )
    receipts_by_item: dict[str, list] = {}
    for receipt in rows:
        receipts_by_item.setdefault(normalize_context(receipt.item_code), []).append(receipt)

    resolved = {}
    for item_row in item_rows:
        item_code = normalize_context(item_row.item_code)
        target_uom = normalize_context(item_row.uom)
        for receipt in receipts_by_item.get(item_code, []):
            converted_rate, conversion_basis = _convert_receipt_rate(
                receipt,
                item_row,
                currency,
                conversion_rate,
            )
            if converted_rate is None:
                continue
            resolved[(item_code, target_uom)] = {
                "last_purchase_price": converted_rate,
                "last_purchase_po": receipt.purchase_order,
                "last_purchase_receipt": receipt.purchase_receipt,
                "last_purchase_date": receipt.transaction_date,
                "last_purchase_currency": receipt.currency,
                "last_purchase_uom": receipt.uom,
                "supplier": receipt.supplier,
                "conversion_basis": conversion_basis,
            }
            break
    return resolved


def build_supplier_quotation_lpp_rows(doc) -> list[dict]:
    items = list(doc.get("items") or [])
    resolved = resolve_supplier_last_purchase_prices(
        doc.get("supplier"),
        doc.get("currency"),
        items,
        company=doc.get("company"),
        conversion_rate=doc.get("conversion_rate"),
    )
    output = []
    for index, row in enumerate(items, start=1):
        item_code = normalize_context(row.get("item_code"))
        uom = normalize_context(row.get("uom") or row.get("stock_uom"))
        reference = resolved.get((item_code, uom))
        lpp = reference.get("last_purchase_price") if reference else None
        difference, difference_percent = calculate_price_variance(row.get("rate"), lpp)
        output.append(
            {
                "row_key": row.get("name") or str(row.get("idx") or index),
                "idx": row.get("idx") or index,
                "item_code": item_code,
                "uom": uom,
                "last_purchase_price": lpp,
                "last_purchase_po": reference.get("last_purchase_po") if reference else None,
                "last_purchase_receipt": (
                    reference.get("last_purchase_receipt") if reference else None
                ),
                "last_purchase_date": reference.get("last_purchase_date") if reference else None,
                "last_purchase_currency": (
                    reference.get("last_purchase_currency") if reference else None
                ),
                "last_purchase_uom": reference.get("last_purchase_uom") if reference else None,
                "price_difference": difference,
                "price_difference_percent": difference_percent,
                "comparable": bool(reference),
                "conversion_basis": reference.get("conversion_basis") if reference else None,
            }
        )
    return output


def _apply_supplier_quotation_lpp(doc):
    if not doc or doc.doctype != "Supplier Quotation":
        return
    resolved_rows = build_supplier_quotation_lpp_rows(doc)
    by_key = {row["row_key"]: row for row in resolved_rows}
    by_idx = {int(row["idx"]): row for row in resolved_rows}
    for index, row in enumerate(doc.get("items") or [], start=1):
        resolved = by_key.get(row.get("name")) or by_idx.get(int(row.get("idx") or index))
        if not resolved:
            continue
        row.custom_last_purchase_price = resolved["last_purchase_price"]
        row.custom_last_purchase_po = resolved["last_purchase_po"]
        row.custom_last_purchase_date = resolved["last_purchase_date"]
        row.custom_last_purchase_currency = resolved["last_purchase_currency"]
        row.custom_last_purchase_uom = resolved["last_purchase_uom"]
        row.custom_price_difference = resolved["price_difference"]
        row.custom_price_difference_percent = resolved["price_difference_percent"]
    doc.flags.calco_supplier_specific_lpp = True


def apply_supplier_quotation_lpp(doc, method=None):
    if int(doc.docstatus or 0) != 0:
        return
    _apply_supplier_quotation_lpp(doc)


def snapshot_supplier_quotation_lpp(doc, method=None):
    _apply_supplier_quotation_lpp(doc)


def uses_supplier_specific_lpp(doc) -> bool:
    if getattr(getattr(doc, "flags", None), "calco_supplier_specific_lpp", False):
        return True
    if int(doc.get("docstatus") or 0) == 0:
        return True
    has_snapshot = any(
        any(row.get(fieldname) not in (None, "", 0) for fieldname in LPP_FIELDS[:5])
        for row in doc.get("items") or []
    )
    if has_snapshot:
        return True
    patch_time = get_lpp_patch_time()
    return bool(
        patch_time
        and doc.get("creation")
        and get_datetime(doc.creation) >= get_datetime(patch_time)
    )


def get_lpp_patch_time():
    return frappe.db.get_value("Patch Log", LPP_PATCH, "creation")


def get_supplier_specific_benchmark(doc, row) -> dict[str, object]:
    if not uses_supplier_specific_lpp(doc):
        return {"applicable": False}
    rate = flt(row.get("custom_last_purchase_price"))
    if rate > 0:
        return {
            "applicable": True,
            "rate": rate,
            "source": "Supplier-specific Last Purchase Price",
            "reference": row.get("custom_last_purchase_po"),
        }
    return {
        "applicable": True,
        "rate": None,
        "source": "No Comparable Supplier LPP",
        "reference": None,
    }


@frappe.whitelist()
def preview_supplier_quotation_lpp(doc=None):
    if not frappe.has_permission("Supplier Quotation", "read"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if isinstance(doc, str):
        doc = json.loads(doc)
    doc = frappe._dict(doc or {})
    doc.items = [frappe._dict(row) for row in doc.get("items") or []]
    return {"rows": build_supplier_quotation_lpp_rows(doc)}
