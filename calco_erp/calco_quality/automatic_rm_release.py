from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from calco_erp.calco_quality.purchase_receipt_qc import (
    validate_supplier_documents_and_rm_storage,
)


ACCEPTED_QI_BASIS = "Accepted Quality Inspection"
ACCEPTED_DEVIATION_BASIS = "Accepted Under Deviation"
RECONCILIATION_ERROR = "RELEASE_RECONCILIATION_REQUIRED"


def prepare_incoming_qi_release_context(doc, method=None):
    if not is_incoming_purchase_receipt_qi(doc) or doc.docstatus != 0:
        return

    row = get_exact_purchase_receipt_row(doc, throw=False)
    if not row:
        return

    doc.custom_accepted_qty_uom = row.get("stock_uom") or row.get("uom") or ""
    if flt(doc.get("custom_accepted_qty")) <= 0:
        doc.custom_accepted_qty = get_received_quantity(row)


def validate_incoming_qi_accepted_quantity(doc, method=None):
    if not is_incoming_purchase_receipt_qi(doc) or normalize_result(doc) != "ACCEPTED":
        return

    row = get_exact_purchase_receipt_row(doc)
    accepted_qty = flt(doc.get("custom_accepted_qty"))
    if accepted_qty <= 0:
        frappe.throw(_("Accepted Qty must be greater than zero for an Accepted Incoming Quality Inspection."))

    remaining_qty = get_remaining_releasable_quantity(row)
    if accepted_qty - remaining_qty > 1e-9:
        frappe.throw(
            _("Accepted Qty {0} cannot exceed the remaining releasable quantity {1} for PR Item Row {2}.").format(
                accepted_qty,
                remaining_qty,
                row.name,
            )
        )


def process_accepted_quality_inspection(doc, method=None):
    if not is_incoming_purchase_receipt_qi(doc):
        return None
    if doc.docstatus != 1 or normalize_result(doc) != "ACCEPTED":
        return None

    row_name = (doc.get("child_row_reference") or "").strip()
    lock_authority(row_name, "Quality Inspection", doc.name)
    row = get_exact_purchase_receipt_row(doc)
    purchase_receipt = validate_purchase_receipt(doc.reference_name)
    validate_supplier_documents_and_rm_storage(purchase_receipt)

    authority_key = build_release_authority_key(
        basis=ACCEPTED_QI_BASIS,
        source=doc.name,
        purchase_receipt=purchase_receipt.name,
        purchase_receipt_item=row.name,
        item_code=row.item_code,
        batch_no=row.batch_no,
    )
    existing = get_existing_release_result(authority_key)
    if existing:
        return existing

    accepted_qty = flt(doc.get("custom_accepted_qty"))
    validate_release_quantity(row, accepted_qty)
    return create_and_submit_release(
        authority_key=authority_key,
        basis=ACCEPTED_QI_BASIS,
        purchase_receipt=purchase_receipt,
        row=row,
        quantity=accepted_qty,
        quality_inspection=doc.name,
    )


def process_approved_deviation(deviation):
    if deviation.docstatus != 1 or deviation.approval_status != "Approved":
        return None

    row_name = (deviation.get("purchase_receipt_item") or "").strip()
    lock_authority(row_name, "RM Deviation Approval", deviation.name)
    row = get_exact_deviation_purchase_receipt_row(deviation)
    purchase_receipt = validate_purchase_receipt(deviation.purchase_receipt)
    validate_supplier_documents_and_rm_storage(purchase_receipt)

    authority_key = build_release_authority_key(
        basis=ACCEPTED_DEVIATION_BASIS,
        source=deviation.name,
        purchase_receipt=purchase_receipt.name,
        purchase_receipt_item=row.name,
        item_code=row.item_code,
        batch_no=row.batch_no,
    )
    existing = get_existing_release_result(authority_key)
    if existing:
        return existing

    approved_qty = flt(deviation.approved_qty)
    validate_release_quantity(row, approved_qty)
    return create_and_submit_release(
        authority_key=authority_key,
        basis=ACCEPTED_DEVIATION_BASIS,
        purchase_receipt=purchase_receipt,
        row=row,
        quantity=approved_qty,
        quality_inspection=deviation.quality_inspection,
        rm_qc_decision=deviation.rm_qc_decision,
        deviation_approval=deviation.name,
    )


def create_and_submit_release(
    *,
    authority_key,
    basis,
    purchase_receipt,
    row,
    quantity,
    quality_inspection="",
    rm_qc_decision="",
    deviation_approval="",
):
    trigger_user = frappe.session.user or "Administrator"
    release = frappe.get_doc(
        {
            "doctype": "RM Release Note",
            "rm_qc_decision": rm_qc_decision or "",
            "custom_rm_deviation_approval": deviation_approval or "",
            "custom_purchase_receipt": purchase_receipt.name,
            "custom_purchase_receipt_item": row.name,
            "custom_quality_inspection": quality_inspection or "",
            "custom_supplier": purchase_receipt.supplier,
            "item_code": row.item_code,
            "custom_item_name": row.item_name,
            "batch_no": row.batch_no,
            "release_qty": quantity,
            "status": "Released",
            "custom_release_authority_key": authority_key,
            "custom_release_basis": basis,
            "custom_trigger_user": trigger_user,
            "custom_trigger_timestamp": now_datetime(),
        }
    )
    release.flags.ignore_permissions = True
    release.flags.calco_automatic_release = True
    from calco_erp.calco_quality.rm_release_bundle_authority import release_bundle_authority
    with release_bundle_authority(release):
        release.insert(ignore_permissions=True)
        release.submit()
    release.reload()
    result = validate_release_result(release)
    result.reused = False
    return result


def get_existing_release_result(authority_key):
    rows = frappe.db.sql(
        """
        select
            name,
            docstatus,
            status,
            custom_generated_stock_entry,
            item_code,
            release_warehouse,
            release_qty,
            custom_release_authority_key
        from `tabRM Release Note`
        where custom_release_authority_key = %s
        for update
        """,
        authority_key,
        as_dict=True,
    )
    if not rows:
        return None

    release = rows[0]
    if release.docstatus != 1 or release.status != "Released":
        reconciliation_required(
            _("Release authority {0} is already linked to non-submitted RM Release Note {1}.").format(
                authority_key,
                release.name,
            )
        )

    stock_entry_name = (release.custom_generated_stock_entry or "").strip()
    if not stock_entry_name:
        reconciliation_required(_("RM Release Note {0} has no generated Stock Entry link.").format(release.name))

    stock_entries = frappe.db.sql(
        """
        select name, docstatus, custom_rm_release_note
        from `tabStock Entry`
        where name = %s
        for update
        """,
        stock_entry_name,
        as_dict=True,
    )
    stock_entry = stock_entries[0] if stock_entries else None
    if not stock_entry or stock_entry.docstatus != 1 or stock_entry.custom_rm_release_note != release.name:
        reconciliation_required(
            _("Stock Entry {0} is not a submitted movement for RM Release Note {1}.").format(
                stock_entry_name,
                release.name,
            )
        )

    stock_entry_items = frappe.db.sql(
        """
        select item_code, t_warehouse, qty
        from `tabStock Entry Detail`
        where parent = %s
        for update
        """,
        stock_entry_name,
        as_dict=True,
    )
    matching_rows = [
        row
        for row in stock_entry_items
        if row.item_code == release.item_code
        and row.t_warehouse == release.release_warehouse
        and abs(flt(row.qty) - flt(release.release_qty)) <= 1e-9
    ]
    if len(matching_rows) != 1:
        reconciliation_required(
            _("Stock Entry {0} does not contain the exact released Item and Qty for {1}.").format(
                stock_entry_name,
                release.name,
            )
        )

    return frappe._dict(
        rm_release_note=release.name,
        stock_entry=stock_entry.name,
        release_qty=flt(release.release_qty),
        authority_key=release.custom_release_authority_key,
        reused=True,
    )


def validate_release_result(release):
    stock_entry_name = (release.get("custom_generated_stock_entry") or "").strip()
    if not stock_entry_name:
        reconciliation_required(_("RM Release Note {0} has no generated Stock Entry link.").format(release.name))

    stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)
    if stock_entry.docstatus != 1 or stock_entry.get("custom_rm_release_note") != release.name:
        reconciliation_required(
            _("Stock Entry {0} is not a submitted movement for RM Release Note {1}.").format(
                stock_entry_name,
                release.name,
            )
        )

    matching_rows = [
        row
        for row in stock_entry.get("items", [])
        if row.item_code == release.item_code
        and row.t_warehouse == release.release_warehouse
        and abs(flt(row.qty) - flt(release.release_qty)) <= 1e-9
    ]
    if len(matching_rows) != 1:
        reconciliation_required(
            _("Stock Entry {0} does not contain the exact released Item and Qty for {1}.").format(
                stock_entry_name,
                release.name,
            )
        )

    return frappe._dict(
        rm_release_note=release.name,
        stock_entry=stock_entry.name,
        release_qty=flt(release.release_qty),
        authority_key=release.custom_release_authority_key,
    )


def validate_release_quantity(row, quantity):
    quantity = flt(quantity)
    if quantity <= 0:
        frappe.throw(_("Release quantity must be greater than zero."))

    remaining_qty = get_remaining_releasable_quantity(row)
    if quantity - remaining_qty > 1e-9:
        frappe.throw(
            _("Release quantity {0} exceeds remaining releasable quantity {1} for PR Item Row {2}.").format(
                quantity,
                remaining_qty,
                row.name,
            )
        )


def get_remaining_releasable_quantity(row):
    released_qty = frappe.db.sql(
        """
        select coalesce(sum(release_qty), 0)
        from `tabRM Release Note`
        where docstatus = 1
          and status = 'Released'
          and custom_purchase_receipt = %(purchase_receipt)s
          and item_code = %(item_code)s
          and ifnull(batch_no, '') = %(batch_no)s
          and (
                custom_purchase_receipt_item = %(purchase_receipt_item)s
                or ifnull(custom_purchase_receipt_item, '') = ''
              )
        """,
        {
            "purchase_receipt": row.parent,
            "purchase_receipt_item": row.name,
            "item_code": row.item_code,
            "batch_no": row.batch_no or "",
        },
    )[0][0]
    return max(get_received_quantity(row) - flt(released_qty), 0)


def get_exact_purchase_receipt_row(inspection, throw=True):
    row_name = (inspection.get("child_row_reference") or "").strip()
    if not row_name:
        if throw:
            frappe.throw(_("Incoming Quality Inspection requires an exact Purchase Receipt Item in Child Row Reference."))
        return None
    return load_and_validate_purchase_receipt_row(
        row_name,
        inspection.reference_name,
        inspection.item_code,
        inspection.batch_no,
        throw=throw,
    )


def get_exact_deviation_purchase_receipt_row(deviation):
    row_name = (deviation.get("purchase_receipt_item") or "").strip()
    if not row_name:
        frappe.throw(_("Approved RM Deviation requires an exact Purchase Receipt Item row."))
    return load_and_validate_purchase_receipt_row(
        row_name,
        deviation.purchase_receipt,
        deviation.item_code,
        deviation.batch_no,
    )


def load_and_validate_purchase_receipt_row(
    row_name,
    purchase_receipt,
    item_code,
    batch_no,
    *,
    throw=True,
):
    row = frappe.db.get_value(
        "Purchase Receipt Item",
        row_name,
        [
            "name",
            "parent",
            "item_code",
            "item_name",
            "batch_no",
            "received_qty",
            "qty",
            "stock_uom",
            "uom",
        ],
        as_dict=True,
    )
    valid = bool(
        row
        and row.parent == purchase_receipt
        and row.item_code == item_code
        and (row.batch_no or "") == (batch_no or "")
    )
    if valid:
        return row
    if throw:
        frappe.throw(
            _("Child Row Reference {0} does not match Purchase Receipt {1}, Item {2}, and Batch {3}.").format(
                row_name,
                purchase_receipt,
                item_code,
                batch_no or "",
            )
        )
    return None


def validate_purchase_receipt(purchase_receipt_name):
    purchase_receipt = frappe.get_doc("Purchase Receipt", purchase_receipt_name)
    if purchase_receipt.docstatus != 1 or purchase_receipt.is_return:
        frappe.throw(_("Automatic RM release requires a submitted, non-return Purchase Receipt."))
    return purchase_receipt


def lock_authority(row_name, source_doctype, source_name):
    if not row_name:
        frappe.throw(_("Exact Purchase Receipt Item row is required before RM release."))
    frappe.db.sql("select name from `tabPurchase Receipt Item` where name = %s for update", row_name)
    source_tables = {
        "Quality Inspection": "`tabQuality Inspection`",
        "RM Deviation Approval": "`tabRM Deviation Approval`",
    }
    source_table = source_tables.get(source_doctype)
    if not source_table:
        frappe.throw(_("Unsupported RM release authority type {0}.").format(source_doctype))
    frappe.db.sql(f"select name from {source_table} where name = %s for update", source_name)


def build_release_authority_key(
    *,
    basis,
    source,
    purchase_receipt,
    purchase_receipt_item,
    item_code,
    batch_no,
):
    payload = {
        "basis": basis,
        "source": source,
        "purchase_receipt": purchase_receipt,
        "purchase_receipt_item": purchase_receipt_item,
        "item_code": item_code,
        "batch_no": batch_no or "",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"rm-release:v1:{digest}"


def get_received_quantity(row):
    return flt(row.get("received_qty") or row.get("qty") or 0)


def is_incoming_purchase_receipt_qi(doc):
    return bool(
        doc.doctype == "Quality Inspection"
        and doc.inspection_type == "Incoming"
        and doc.reference_type == "Purchase Receipt"
        and doc.reference_name
    )


def normalize_result(doc):
    value = (doc.get("custom_overall_result") or doc.get("status") or "").strip().upper()
    return {"PASS": "ACCEPTED", "FAIL": "REJECTED"}.get(value, value)


def reconciliation_required(message):
    frappe.throw(f"{RECONCILIATION_ERROR}: {message}")
