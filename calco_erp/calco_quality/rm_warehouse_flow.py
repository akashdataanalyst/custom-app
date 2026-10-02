from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from erpnext.stock.serial_batch_bundle import SerialBatchCreation

from calco_erp.calco_quality.purchase_receipt_qc import get_item_qc_config
from calco_erp.calco_quality.rm_purchase_flow_setup import (
    RM_HOLD_WAREHOUSE_FIELD,
    RM_QUARANTINE_WAREHOUSE_FIELD,
    RM_REJECTED_WAREHOUSE_FIELD,
    RM_RELEASED_WAREHOUSE_FIELD,
    get_stock_setting_value,
    get_or_create_company_rm_warehouses,
)


def apply_purchase_receipt_quarantine(doc, method=None):
    if doc.doctype != "Purchase Receipt" or cint(doc.get("is_return")):
        return

    warehouses = get_rm_flow_warehouses(doc.get("company"))
    quarantine_warehouse = warehouses.get("quarantine")
    rejected_warehouse = warehouses.get("rejected")
    released_warehouse = warehouses.get("released")

    for row in doc.get("items", []):
        if not row.get("item_code"):
            continue

        item_qc_config = get_item_qc_config(row.item_code)
        if not item_qc_config.required:
            continue

        if quarantine_warehouse:
            row.warehouse = quarantine_warehouse
        if rejected_warehouse and hasattr(row, "rejected_warehouse"):
            row.rejected_warehouse = rejected_warehouse

    if quarantine_warehouse and should_route_parent_set_warehouse(doc, released_warehouse):
        doc.set_warehouse = quarantine_warehouse


def should_route_parent_set_warehouse(doc, released_warehouse: str | None) -> bool:
    rows = [row for row in doc.get("items", []) if row.get("item_code")]
    if not rows:
        return False

    routed_rows = 0
    for row in rows:
        item_qc_config = get_item_qc_config(row.item_code)
        if not item_qc_config.required:
            return False
        routed_rows += 1

    return bool(routed_rows) and (doc.get("set_warehouse") or "").strip() in {"", released_warehouse or ""}


def get_rm_flow_warehouses(company: str | None) -> dict[str, str]:
    company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    if not company:
        return {"quarantine": "", "released": "", "hold": "", "rejected": ""}

    stock_settings = {
        "quarantine": get_stock_setting_value(RM_QUARANTINE_WAREHOUSE_FIELD),
        "released": get_stock_setting_value(RM_RELEASED_WAREHOUSE_FIELD),
        "hold": get_stock_setting_value(RM_HOLD_WAREHOUSE_FIELD),
        "rejected": get_stock_setting_value(RM_REJECTED_WAREHOUSE_FIELD),
    }
    if all(stock_settings.values()):
        return stock_settings

    company_abbr = frappe.db.get_value("Company", company, "abbr") or company
    ensured = get_or_create_company_rm_warehouses(company, company_abbr)
    return {
        "quarantine": stock_settings["quarantine"] or ensured.get(RM_QUARANTINE_WAREHOUSE_FIELD, ""),
        "released": stock_settings["released"] or ensured.get(RM_RELEASED_WAREHOUSE_FIELD, ""),
        "hold": stock_settings["hold"] or ensured.get(RM_HOLD_WAREHOUSE_FIELD, ""),
        "rejected": stock_settings["rejected"] or ensured.get(RM_REJECTED_WAREHOUSE_FIELD, ""),
    }


def handle_rm_release_note_submit(doc):
    company = get_company_from_purchase_receipt(
        doc.get("rm_qc_decision"),
        purchase_receipt=doc.get("custom_purchase_receipt"),
        quality_inspection=doc.get("custom_quality_inspection"),
    )
    warehouses = get_rm_flow_warehouses(company)
    target_warehouse = (doc.get("release_warehouse") or warehouses.get("released") or "").strip()
    if not target_warehouse:
        frappe.throw(_("RM Released warehouse is not configured in Stock Settings."))

    doc.release_warehouse = target_warehouse
    return move_batch_between_warehouses(
        item_code=doc.item_code,
        batch_no=doc.batch_no,
        qty=flt(doc.release_qty),
        target_warehouse=target_warehouse,
        preferred_source_warehouses=[warehouses.get("quarantine"), warehouses.get("hold"), warehouses.get("rejected")],
        company=company,
        remark_source=f"RM Release Note {doc.name or _('New')}",
        rm_release_note=doc.name,
    )


def handle_rm_qc_decision_submit(doc):
    if doc.decision not in ("Hold for Review", "Return to Supplier", "Deviation Required"):
        return

    warehouses = get_rm_flow_warehouses(get_company_from_purchase_receipt(doc.name))
    target_warehouse = warehouses.get("hold") if doc.decision == "Hold for Review" else warehouses.get("rejected")
    if not target_warehouse:
        frappe.throw(_("Target warehouse is not configured for RM QC decision {0}.").format(doc.decision))

    move_batch_between_warehouses(
        item_code=doc.item_code,
        batch_no=doc.batch_no,
        qty=flt(doc.sample_qty),
        target_warehouse=target_warehouse,
        preferred_source_warehouses=[warehouses.get("quarantine"), warehouses.get("released")],
        company=get_company_from_purchase_receipt(doc.name),
        remark_source=f"RM QC Decision {doc.name}",
    )


def get_company_from_purchase_receipt(
    rm_doc_reference: str | None,
    purchase_receipt: str | None = None,
    quality_inspection: str | None = None,
) -> str:
    if purchase_receipt:
        return frappe.db.get_value("Purchase Receipt", purchase_receipt, "company") or ""

    if quality_inspection and frappe.db.exists("Quality Inspection", quality_inspection):
        linked_pr = frappe.db.get_value("Quality Inspection", quality_inspection, "reference_name") or ""
        return frappe.db.get_value("Purchase Receipt", linked_pr, "company") or ""

    if not rm_doc_reference:
        return ""

    purchase_receipt = frappe.db.get_value("RM QC Decision", rm_doc_reference, "purchase_receipt") or ""
    if not purchase_receipt and frappe.db.exists("RM Release Note", rm_doc_reference):
        decision_name = frappe.db.get_value("RM Release Note", rm_doc_reference, "rm_qc_decision") or ""
        purchase_receipt = frappe.db.get_value("RM QC Decision", decision_name, "purchase_receipt") or ""

    if not purchase_receipt and frappe.db.exists("Purchase Receipt", rm_doc_reference):
        purchase_receipt = rm_doc_reference

    return frappe.db.get_value("Purchase Receipt", purchase_receipt, "company") or ""


def move_batch_between_warehouses(
    item_code: str,
    batch_no: str,
    qty: float,
    target_warehouse: str,
    preferred_source_warehouses: list[str | None],
    company: str,
    remark_source: str,
    rm_release_note: str | None = None,
):
    qty = flt(qty)
    if qty <= 0:
        frappe.throw(_("Transfer quantity must be greater than zero for {0}.").format(remark_source))

    source_warehouse = find_source_warehouse(item_code, batch_no, qty, target_warehouse, preferred_source_warehouses)
    if not source_warehouse or source_warehouse == target_warehouse:
        return None

    posting_datetime = now_datetime()
    transfer_bundle = create_outward_batch_bundle(
        item_code=item_code,
        batch_no=batch_no,
        qty=qty,
        warehouse=source_warehouse,
        company=company,
        posting_datetime=posting_datetime,
    )

    stock_entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Transfer",
            "stock_entry_type": "Material Transfer",
            "company": company,
            "posting_date": posting_datetime.date(),
            "posting_time": posting_datetime.strftime("%H:%M:%S"),
            "remarks": f"Auto transfer from {remark_source}",
            "custom_rm_release_note": rm_release_note or "",
            "items": [
                {
                    "item_code": item_code,
                    "qty": qty,
                    "transfer_qty": qty,
                    "uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                    "stock_uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                    "conversion_factor": 1,
                    "s_warehouse": source_warehouse,
                    "t_warehouse": target_warehouse,
                    "batch_no": "",
                    "serial_no": "",
                    "serial_and_batch_bundle": transfer_bundle,
                    "use_serial_batch_fields": 0,
                }
            ],
        }
    )
    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()
    return stock_entry.name


def create_outward_batch_bundle(
    item_code: str,
    batch_no: str,
    qty: float,
    warehouse: str,
    company: str,
    posting_datetime,
) -> str:
    bundle = (
        SerialBatchCreation(
            {
                "item_code": item_code,
                "warehouse": warehouse,
                "voucher_type": "Stock Entry",
                "total_qty": -1 * flt(qty),
                "batches": frappe._dict({batch_no: flt(qty)}),
                "type_of_transaction": "Outward",
                "company": company,
                "posting_datetime": posting_datetime,
                "do_not_submit": True,
            }
        )
        .make_serial_and_batch_bundle()
        .name
    )
    return bundle


def find_source_warehouse(
    item_code: str,
    batch_no: str,
    qty: float,
    target_warehouse: str,
    preferred_source_warehouses: list[str | None],
) -> str:
    balances = get_batch_balances_by_warehouse(item_code, batch_no)

    for warehouse in preferred_source_warehouses:
        if not warehouse or warehouse == target_warehouse:
            continue
        if flt(balances.get(warehouse) or 0) + 1e-9 >= qty:
            return warehouse

    for warehouse, balance in balances.items():
        if warehouse == target_warehouse:
            continue
        if flt(balance) + 1e-9 >= qty:
            return warehouse

    frappe.throw(
        _("Unable to find enough stock for item {0}, batch {1} to move into {2}.").format(
            item_code, batch_no, target_warehouse
        )
    )


def get_batch_balance(item_code: str, batch_no: str, warehouse: str) -> float:
    return flt(get_batch_balances_by_warehouse(item_code, batch_no).get(warehouse) or 0)


def get_batch_balances_by_warehouse(item_code: str, batch_no: str) -> dict[str, float]:
    balances: dict[str, float] = {}

    direct_rows = frappe.db.sql(
        """
        select warehouse, coalesce(sum(actual_qty), 0) as qty
        from `tabStock Ledger Entry`
        where item_code = %(item_code)s
          and ifnull(batch_no, '') = %(batch_no)s
          and is_cancelled = 0
        group by warehouse
        """,
        {"item_code": item_code, "batch_no": batch_no or ""},
        as_dict=True,
    )
    for row in direct_rows:
        balances[row.warehouse] = flt(balances.get(row.warehouse) or 0) + flt(row.qty)

    bundle_rows = frappe.db.sql(
        """
        select
            sle.warehouse,
            coalesce(
                sum(
                    case
                        when sle.actual_qty < 0 then -1 * abs(ifnull(sbe.qty, 0))
                        else abs(ifnull(sbe.qty, 0))
                    end
                ),
                0
            ) as qty
        from `tabStock Ledger Entry` sle
        inner join `tabSerial and Batch Entry` sbe
            on sbe.parent = sle.serial_and_batch_bundle
        where sle.item_code = %(item_code)s
          and sbe.item_code = %(item_code)s
          and ifnull(sbe.batch_no, '') = %(batch_no)s
          and ifnull(sle.batch_no, '') = ''
          and sle.is_cancelled = 0
        group by sle.warehouse
        """,
        {"item_code": item_code, "batch_no": batch_no or ""},
        as_dict=True,
    )
    for row in bundle_rows:
        balances[row.warehouse] = flt(balances.get(row.warehouse) or 0) + flt(row.qty)

    return balances
