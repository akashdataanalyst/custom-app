from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from html import escape

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, now_datetime


CLOSURE_REASON_FIELD = "custom_closure_reason"
CLOSED_BY_FIELD = "custom_closed_by"
CLOSED_ON_FIELD = "custom_closed_on"
PERMANENT_CLOSURE_FIELD = "custom_calco_permanent_closure"
CANCELLED_QTY_FIELD = "custom_cancelled_qty"
_CLOSURE_LIFECYCLE_AUTHORIZED = ContextVar("calco_closure_lifecycle_authorized", default=False)


def ensure_purchase_closure_setup():
    fields = [
        {
            "fieldname": "custom_closure_audit_section",
            "fieldtype": "Section Break",
            "label": "Closure Audit",
            "insert_after": "items",
            "collapsible": 1,
        },
        {
            "fieldname": CLOSURE_REASON_FIELD,
            "fieldtype": "Small Text",
            "label": "Closure Reason",
            "insert_after": "custom_closure_audit_section",
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": PERMANENT_CLOSURE_FIELD,
            "fieldtype": "Check",
            "label": "Permanent Balance Cancellation",
            "insert_after": CLOSURE_REASON_FIELD,
            "read_only": 1,
            "no_copy": 1,
            "default": "0",
        },
        {
            "fieldname": CANCELLED_QTY_FIELD,
            "fieldtype": "Float",
            "label": "Cancelled Qty",
            "insert_after": PERMANENT_CLOSURE_FIELD,
            "read_only": 1,
            "no_copy": 1,
            "precision": "3",
            "default": "0",
        },
        {
            "fieldname": CLOSED_BY_FIELD,
            "fieldtype": "Link",
            "label": "Closed By",
            "options": "User",
            "insert_after": CANCELLED_QTY_FIELD,
            "read_only": 1,
            "no_copy": 1,
        },
        {
            "fieldname": CLOSED_ON_FIELD,
            "fieldtype": "Datetime",
            "label": "Closed On",
            "insert_after": CLOSED_BY_FIELD,
            "read_only": 1,
            "no_copy": 1,
        },
    ]
    create_custom_fields(
        {
            "Material Request": [dict(field) for field in fields],
            "Purchase Order": [dict(field) for field in fields],
        },
        update=True,
    )
    frappe.clear_cache(doctype="Material Request")
    frappe.clear_cache(doctype="Purchase Order")


def get_material_request_closure_rows(doc) -> list[dict]:
    rows = []
    for row in doc.get("items") or []:
        requested_qty = flt(row.get("stock_qty") or row.get("qty"))
        fulfilled_qty = flt(row.get("ordered_qty"))
        remaining_qty = max(requested_qty - fulfilled_qty, 0)
        rows.append(
            {
                "item_code": row.get("item_code"),
                "requested_qty": requested_qty,
                "fulfilled_qty": fulfilled_qty,
                "remaining_qty": remaining_qty,
                "uom": row.get("stock_uom") or row.get("uom"),
            }
        )
    return rows


def get_material_request_open_rows(doc) -> list[dict]:
    return [row for row in get_material_request_closure_rows(doc) if row["remaining_qty"] > 1e-9]


def get_purchase_order_closure_rows(doc) -> list[dict]:
    rows = []
    for row in doc.get("items") or []:
        ordered_qty = flt(row.get("stock_qty") or row.get("qty"))
        fulfilled_qty = flt(row.get("received_qty"))
        remaining_qty = max(ordered_qty - fulfilled_qty, 0)
        rows.append(
            {
                "item_code": row.get("item_code"),
                "ordered_qty": ordered_qty,
                "fulfilled_qty": fulfilled_qty,
                "remaining_qty": remaining_qty,
                "uom": row.get("stock_uom") or row.get("uom"),
            }
        )
    return rows


def get_purchase_order_open_rows(doc) -> list[dict]:
    return [row for row in get_purchase_order_closure_rows(doc) if row["remaining_qty"] > 1e-9]


@frappe.whitelist()
def update_material_request_status(name, status, closure_reason=None, **_kwargs):
    from erpnext.stock.doctype.material_request.material_request import update_status

    doc = _get_locked_document("Material Request", name)
    doc.check_permission("write")
    snapshot = None
    if status == "Stopped":
        snapshot = _record_permanent_closure(
            doc,
            closure_reason,
            get_material_request_closure_rows(doc),
        )
    else:
        _block_permanent_reopen(doc, status)
    with _authorize_closure_lifecycle():
        update_status(name=name, status=status)
    if snapshot:
        _add_closure_comment(doc, snapshot)


@frappe.whitelist()
def update_purchase_order_status(status, name, closure_reason=None, **_kwargs):
    from erpnext.buying.doctype.purchase_order.purchase_order import update_status

    doc = _get_locked_document("Purchase Order", name)
    doc.check_permission("submit")
    snapshot = None
    if status == "Closed":
        snapshot = _record_permanent_closure(
            doc,
            closure_reason,
            get_purchase_order_closure_rows(doc),
        )
    else:
        _block_permanent_reopen(doc, status)
    with _authorize_closure_lifecycle():
        update_status(status=status, name=name)
    if snapshot:
        _add_closure_comment(doc, snapshot)


def _get_locked_document(doctype, name):
    if not frappe.db.get_value(doctype, name, "name", for_update=True):
        frappe.throw(_("{0} {1} does not exist.").format(doctype, name))
    return frappe.get_doc(doctype, name)


def _record_permanent_closure(doc, closure_reason, rows):
    reason = (closure_reason or "").strip()
    if not reason:
        frappe.throw(_("Closure Reason is mandatory for permanent procurement balance cancellation."))

    snapshot = _make_closure_snapshot(rows)
    closed_on = now_datetime()
    values = {
        CLOSURE_REASON_FIELD: reason,
        PERMANENT_CLOSURE_FIELD: 1,
        CANCELLED_QTY_FIELD: snapshot["cancelled_qty"],
        CLOSED_BY_FIELD: frappe.session.user,
        CLOSED_ON_FIELD: closed_on,
    }
    frappe.db.set_value(doc.doctype, doc.name, values)
    for fieldname, value in values.items():
        doc.set(fieldname, value)
    snapshot.update(reason=reason, actor=frappe.session.user, closed_on=closed_on)
    return snapshot


def _make_closure_snapshot(rows):
    open_rows = [row for row in rows if flt(row.get("remaining_qty")) > 1e-9]
    uoms = {str(row.get("uom") or "").strip() for row in open_rows}
    if "" in uoms:
        frappe.throw(_("Every outstanding row must have a Stock UOM before permanent closure."))
    if len(uoms) > 1:
        frappe.throw(
            _(
                "Permanent closure cannot aggregate outstanding quantities with different UOMs ({0}). "
                "Close only documents whose outstanding rows share one Stock UOM."
            ).format(", ".join(sorted(uoms)))
        )
    return {
        "cancelled_qty": round(sum(flt(row.get("remaining_qty")) for row in open_rows), 6),
        "uom": next(iter(uoms), ""),
        "rows": [dict(row) for row in rows],
    }


def _add_closure_comment(doc, snapshot):
    if doc.doctype == "Material Request":
        original_label = _("Requested")
        fulfilled_label = _("Ordered")
    else:
        original_label = _("Ordered")
        fulfilled_label = _("Received")
    row_lines = []
    for row in snapshot["rows"]:
        original_qty = row.get("requested_qty") if doc.doctype == "Material Request" else row.get("ordered_qty")
        uom = escape(row.get("uom") or "")
        row_lines.append(
            _(
                "Item {0}: {1} {2} {3}; {4} {5} {3}; Cancelled {6} {3}; Balance 0 {3}"
            ).format(
                escape(row.get("item_code") or ""),
                original_label,
                frappe.format_value(original_qty, {"fieldtype": "Float"}),
                uom,
                fulfilled_label,
                frappe.format_value(row.get("fulfilled_qty"), {"fieldtype": "Float"}),
                frappe.format_value(row.get("remaining_qty"), {"fieldtype": "Float"}),
            )
        )
    message = _(
        "Permanent procurement balance cancellation.<br>{0}<br>Total Cancelled Qty: {1} {2}"
        "<br>Balance after closure: 0 {2}<br>Reason: {3}<br>Actor: {4}<br>Timestamp: {5}"
    ).format(
        "<br>".join(row_lines),
        frappe.format_value(snapshot["cancelled_qty"], {"fieldtype": "Float"}),
        escape(snapshot["uom"]),
        escape(snapshot["reason"]),
        escape(snapshot["actor"]),
        escape(str(snapshot["closed_on"])),
    )
    doc.add_comment("Comment", message)


def _is_permanent_closure(doc):
    return bool(cint(doc.get(PERMANENT_CLOSURE_FIELD)))


def _block_permanent_reopen(doc, target_status):
    if not _is_permanent_closure(doc):
        return
    current_status = str(doc.get("status") or "").strip().lower()
    target_status = str(target_status or "").strip().lower()
    if doc.doctype == "Material Request" and current_status == "stopped" and target_status != "stopped":
        frappe.throw(
            _(
                "This procurement balance was permanently cancelled through the controlled closure process. "
                "Create a new Material Request for any additional requirement."
            )
        )
    if doc.doctype == "Purchase Order" and current_status == "closed" and target_status != "closed":
        frappe.throw(
            _(
                "This Purchase Order balance was permanently cancelled through the controlled closure process. "
                "Create a new procurement document for any additional supply."
            )
        )


@contextmanager
def _authorize_closure_lifecycle():
    token = _CLOSURE_LIFECYCLE_AUTHORIZED.set(True)
    try:
        yield
    finally:
        _CLOSURE_LIFECYCLE_AUTHORIZED.reset(token)


class MaterialRequestClosureAuditMixin:
    def update_status(self, status):
        _block_permanent_reopen(self, status)
        if status == "Stopped" and not _CLOSURE_LIFECYCLE_AUTHORIZED.get():
            frappe.throw(_("Use the controlled Stop action and provide a Closure Reason."))
        return super().update_status(status)


class PurchaseOrderClosureAuditMixin:
    def update_status(self, status):
        _block_permanent_reopen(self, status)
        if status == "Closed" and not _CLOSURE_LIFECYCLE_AUTHORIZED.get():
            frappe.throw(_("Use the controlled Close action and provide a Closure Reason."))
        return super().update_status(status)
