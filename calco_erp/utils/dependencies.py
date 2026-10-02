import frappe
from frappe import _
from frappe.utils import flt


RM_RELEASED = "Released"
FINAL_QC_RELEASED = "Released"
DISPATCH_CLEARED = "Cleared"
MATERIAL_READY = "Ready"
REWORK_WAREHOUSE_PREFIX = "Rework"
RM_DEVIATION_ACCEPTED = "Accepted Under Deviation"


def get_submitted_value(doctype, filters, fieldname="name"):
    return frappe.db.get_value(doctype, {**filters, "docstatus": 1}, fieldname)


def get_rm_release_note(item_code, batch_no):
    return get_submitted_value(
        "RM Release Note",
        {
            "item_code": item_code,
            "batch_no": batch_no,
            "status": RM_RELEASED,
        },
    )


def get_material_readiness_check(work_order):
    return get_submitted_value(
        "Material Readiness Check",
        {"work_order": work_order, "status": MATERIAL_READY},
    )


def get_latest_material_readiness_check(work_order):
    rows = frappe.get_all(
        "Material Readiness Check",
        filters={"work_order": work_order},
        fields=["name", "status", "docstatus", "modified"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def get_final_qc_release(item_code, batch_no):
    return get_submitted_value(
        "Final QC Release",
        {
            "item_code": item_code,
            "batch_no": batch_no,
            "status": FINAL_QC_RELEASED,
        },
    )


def get_dispatch_clearance(delivery_note, item_code, batch_no):
    return get_submitted_value(
        "Dispatch Clearance",
        {
            "delivery_note": delivery_note,
            "item_code": item_code,
            "batch_no": batch_no,
            "status": DISPATCH_CLEARED,
        },
    )


def validate_work_order_material_readiness(doc, method=None):
    if not doc.get("bom_no"):
        return

    readiness_check = get_material_readiness_check(doc.name)
    if not readiness_check:
        latest_check = get_latest_material_readiness_check(doc.name)
        if latest_check:
            frappe.throw(
                _(
                    "Material Readiness Check {0} is required and currently {1}."
                ).format(latest_check.get("name"), latest_check.get("status") or _("Draft"))
            )
        frappe.throw(
            _("Material Readiness Check is required before submitting Work Order {0}. No check exists yet.").format(doc.name)
        )


def validate_stock_entry_chain(doc, method=None):
    purpose = doc.get("purpose")
    if purpose not in (
        "Material Transfer for Manufacture",
        "Material Consumption for Manufacture",
        "Manufacture",
    ):
        return

    submitted_reservation = False
    controlled_reserved_transfer = False
    if purpose in (
        "Material Transfer for Manufacture",
        "Material Consumption for Manufacture",
        "Manufacture",
    ) and doc.get("work_order"):
        from calco_erp.calco_production.material_reservation_transfer import (
            has_submitted_calco_reservation,
            require_submitted_material_reservation,
        )

        submitted_reservation = has_submitted_calco_reservation(doc.work_order)
        if purpose == "Material Transfer for Manufacture" and submitted_reservation:
            controlled_reserved_transfer = bool(
                require_submitted_material_reservation(doc.work_order)
            )

    if (
        doc.get("work_order")
        and not submitted_reservation
        and not get_material_readiness_check(doc.work_order)
    ):
        frappe.throw(
            _("A submitted Material Readiness Check is required before processing Stock Entry {0}.").format(
                doc.name or _("New")
            )
        )

    for row in doc.get("items", []):
        if row.get("is_finished_item"):
            continue

        if not row.get("item_code") or not row.get("s_warehouse"):
            continue

        source_warehouse = (row.get("s_warehouse") or "").strip()
        item_group = (frappe.db.get_value("Item", row.item_code, "item_group") or "").strip()
        if source_warehouse.startswith(REWORK_WAREHOUSE_PREFIX) and item_group == "Finished Goods":
            continue

        batch_no = get_stock_entry_row_batch_no(row)
        if not batch_no:
            item_has_batch = frappe.db.get_value("Item", row.item_code, "has_batch_no")
            if item_has_batch:
                frappe.throw(
                    _("Batch No is mandatory for item {0} in Stock Entry {1}.").format(
                        row.item_code, doc.name or _("New")
                    )
                )
            continue

        if controlled_reserved_transfer:
            from calco_erp.inventory.availability import validate_batch_selection

            posting_date = str(doc.get("posting_date") or "").strip()
            posting_time = str(doc.get("posting_time") or "").strip()
            posting_datetime = (
                f"{posting_date} {posting_time}"
                if posting_date and posting_time
                else posting_date or None
            )
            evidence = validate_batch_selection(
                item_code=row.item_code,
                batch_no=batch_no,
                warehouse=source_warehouse,
                work_order=doc.work_order,
                posting_datetime=posting_datetime,
                required_qty=flt(row.get("transfer_qty") or row.get("qty")),
            )
            if not evidence.get("valid"):
                frappe.throw(
                    _("Reserved batch {0} for item {1} is not eligible: {2}").format(
                        batch_no,
                        row.item_code,
                        "; ".join(
                            evidence.get("exclusion_reasons")
                            or [_("Unknown reason")]
                        ),
                    )
                )
            continue

        if not get_rm_release_note(row.item_code, batch_no):
            frappe.throw(
                _("Released RM batch is missing for item {0}, batch {1}.").format(
                    row.item_code, batch_no
                )
            )


def get_stock_entry_row_batch_no(row) -> str:
    batch_no = (row.get("batch_no") or "").strip()
    if batch_no:
        return batch_no

    bundle_name = (row.get("serial_and_batch_bundle") or "").strip()
    if not bundle_name or not frappe.db.exists("Serial and Batch Bundle", bundle_name):
        return ""

    batch_nos = frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle_name},
        pluck="batch_no",
        distinct=True,
        limit_page_length=2,
    )
    batch_nos = [value for value in batch_nos if value]
    if len(batch_nos) == 1:
        return batch_nos[0]

    return ""


def validate_delivery_note_chain(doc, method=None):
    from calco_erp.calco_dispatch.partial_lot_dispatch import validate_delivery, is_fg
    validate_delivery(doc)
    for row in doc.get("items", []):
        if is_fg(row.item_code):
            continue
        batch_no = get_stock_entry_row_batch_no(row)
        if not row.get("item_code") or not batch_no:
            continue

        if not get_final_qc_release(row.item_code, batch_no):
            frappe.throw(
                _("Final QC Release must be submitted for item {0}, batch {1} before dispatch.").format(
                    row.item_code, batch_no
                )
            )

        from calco_erp.calco_quality.doctype.final_qc_release.final_qc_release import (
            get_fg_released_batch_qty,
        )

        required_qty = flt(row.get("stock_qty") or row.get("qty"))
        released_qty = get_fg_released_batch_qty(row.item_code, batch_no)
        if released_qty + 1e-9 < required_qty:
            frappe.throw(
                _("FG Released stock for item {0}, batch {1} is {2}; {3} is required.").format(
                    row.item_code,
                    batch_no,
                    round(released_qty, 6),
                    round(required_qty, 6),
                )
            )

        if not get_dispatch_clearance(doc.name, row.item_code, batch_no):
            frappe.throw(
                _("Dispatch Clearance must be submitted for Delivery Note {0}, item {1}, batch {2}.").format(
                    doc.name, row.item_code, batch_no
                )
            )


def validate_purchase_invoice_chain(doc, method=None):
    for row in doc.get("items", []):
        purchase_receipt = row.get("purchase_receipt")
        if not purchase_receipt:
            continue

        source_row = get_linked_purchase_receipt_item(row)
        if not source_row:
            continue

        item_code = source_row.get("item_code") or row.get("item_code")
        if not requires_rm_qc(item_code):
            continue

        qc_status = (source_row.get("custom_qc_status") or "").strip()
        accepted_qty = flt(source_row.get("custom_accepted_qty") or 0)
        rejected_qty = flt(source_row.get("custom_rejected_qty") or source_row.get("rejected_qty") or 0)
        invoice_qty = abs(flt(row.get("qty") or 0))

        if qc_status not in (RM_RELEASED, RM_DEVIATION_ACCEPTED):
            frappe.throw(
                _("Purchase Invoice is not allowed for rejected or pending RM material. Item {0} on Purchase Receipt {1} is {2}.").format(
                    item_code,
                    purchase_receipt,
                    qc_status or _("QC Pending"),
                )
            )

        if accepted_qty <= 0:
            frappe.throw(
                _("Purchase Invoice is only allowed for accepted RM quantity. Item {0} on Purchase Receipt {1} has no accepted quantity.").format(
                    item_code,
                    purchase_receipt,
                )
            )

        if rejected_qty > 0 and invoice_qty - accepted_qty > 1e-9:
            frappe.throw(
                _("Purchase Invoice qty for item {0} cannot exceed accepted quantity ({1}) from Purchase Receipt {2}.").format(
                    item_code,
                    accepted_qty,
                    purchase_receipt,
                )
            )


def get_linked_purchase_receipt_item(row):
    pr_item = row.get("pr_detail") or row.get("purchase_receipt_item")
    if pr_item:
        return frappe.db.get_value(
            "Purchase Receipt Item",
            pr_item,
            [
                "item_code",
                "custom_qc_status",
                "custom_accepted_qty",
                "custom_rejected_qty",
                "rejected_qty",
            ],
            as_dict=True,
        )

    if not row.get("purchase_receipt") or not row.get("item_code"):
        return None

    matches = frappe.get_all(
        "Purchase Receipt Item",
        filters={
            "parent": row.purchase_receipt,
            "parenttype": "Purchase Receipt",
            "item_code": row.item_code,
        },
        fields=[
            "name",
            "item_code",
            "custom_qc_status",
            "custom_accepted_qty",
            "custom_rejected_qty",
            "rejected_qty",
        ],
        order_by="idx asc",
        limit_page_length=1,
    )
    return matches[0] if matches else None


def requires_rm_qc(item_code):
    if not item_code:
        return False
    item = frappe.db.get_value(
        "Item",
        item_code,
        ["custom_enable_rm_qc", "inspection_required_before_purchase", "item_group"],
        as_dict=True,
    ) or {}
    return bool(
        item.get("custom_enable_rm_qc")
        or item.get("inspection_required_before_purchase")
        or (item.get("item_group") or "").strip() == "Raw Material"
    )
