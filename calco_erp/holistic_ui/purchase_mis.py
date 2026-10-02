"""Read-only adapters for the live MIS layout and existing Calco authorities.

No assignment synchronization, document save, currency substitution or stock action.
All parent populations use native permission-aware list queries without pagination.
"""
from collections import defaultdict

import frappe
from frappe.utils import add_days, getdate, nowdate

from calco_erp.planning_upgrade import purchase_assignments as assignments
from calco_erp.planning_upgrade.purchase_policy import due_date
from calco_erp.calco_purchase.purchase_journey import get_po_row_received_qty


def require_read(doctype):
    if not frappe.has_permission(doctype, "read"):
        frappe.throw("Read permission required for " + doctype, frappe.PermissionError)


@frappe.whitelist()
def commercial_approval_sla(period=90):
    require_read("Purchase Commercial Approval")
    period = int(period)
    if period not in (30, 90, 180, 365):
        frappe.throw("Unsupported MIS period")
    today = getdate(nowdate())
    pending = frappe.get_list("Purchase Commercial Approval",
        filters={"approval_status": ["in", ["Draft", "Reopened"]], "docstatus": ["<", 2]},
        fields=["name"], limit_page_length=0)
    result = dict(pending=0, late=0, unknown=0, decided=0, approved=0, rejected=0, rows=[])
    for row in pending:
        doc = frappe.get_doc("Purchase Commercial Approval", row.name)
        doc.check_permission("read")
        state = assignments.requirement(doc)
        if not state["needed"]:
            continue
        result["pending"] += 1
        start = state.get("start")
        holidays = assignments.calendar_dates(state.get("company"), start) if start else None
        due = due_date("pca", start, holidays=holidays)
        if due is None:
            result["unknown"] += 1
        elif due < today:
            result["late"] += 1
        result["rows"].append(dict(document=doc.name, due_date=str(due) if due else None,
            status="Review Required" if due is None else "Overdue" if due < today else "Within SLA"))
    decisions = frappe.get_list("Purchase Commercial Approval",
        filters={"approval_status": ["in", ["Approved", "Rejected"]], "docstatus": ["<", 2]},
        fields=["approval_status", "approval_date", "modified"], limit_page_length=0)
    cutoff = getdate(add_days(today, -period))
    for row in decisions:
        date = row.approval_date or row.modified
        if date and getdate(date) >= cutoff:
            result["decided"] += 1
            result[row.approval_status.lower()] += 1
    return result


def pending_lines(orders, details, receipts):
    """Use exact submitted non-return PR detail quantities, not cached PO counters."""
    parents = {row["name"]: row for row in orders
               if row.get("docstatus", 1) == 1 and row.get("status") not in
               {"Closed", "Completed", "Cancelled", "Delivered", "On Hold", "Stopped"}}
    evidence = defaultdict(list)
    for row in receipts:
        if row.get("docstatus") == 1 and not row.get("is_return"):
            evidence[row.get("purchase_order_item")].append(row)
    result = []
    for row in details:
        if row["parent"] not in parents:
            continue
        linked = evidence[row["name"]]
        if any(r.get("item_code") != row.get("item_code") or
               r.get("stock_uom") != row.get("stock_uom") or
               float(r.get("conversion_factor") or 1) <= 0 for r in linked):
            frappe.throw("Purchase receipt quantity lineage requires review: " + row["parent"])
        received = get_po_row_received_qty(row, linked)
        if received < float(row.get("qty") or 0):
            result.append(dict(parent=row["parent"], detail=row["name"], qty=row["qty"],
                received_qty=received, base_rate=row["base_rate"], schedule_date=row["schedule_date"],
                item_code=row.get("item_code"),
                currency=parents[row["parent"]].get("currency"),
                conversion_rate=parents[row["parent"]].get("conversion_rate")))
    return result


@frappe.whitelist()
def pending_po_lines():
    require_read("Purchase Order")
    require_read("Purchase Receipt")
    orders = frappe.get_list("Purchase Order", filters={"docstatus": 1,
        "status": ["not in", ["Closed", "Completed", "Cancelled", "Delivered", "On Hold", "Stopped"]]},
        fields=["name", "currency", "conversion_rate"], limit_page_length=0)
    result = []
    # Child queries are bounded by the already-authorized parent names. No caller
    # supplies a parent list; no unrelated receipt identity is returned.
    for offset in range(0, len(orders), 400):
        group = orders[offset:offset + 400]
        details = frappe.db.sql("""SELECT name,parent,item_code,stock_uom,qty,
            conversion_factor,base_rate,schedule_date FROM `tabPurchase Order Item`
            WHERE parent IN %s ORDER BY parent,idx,name""",
            (tuple(row.name for row in group),), as_dict=True)
        if not details:
            continue
        receipts = frappe.db.sql("""SELECT r.purchase_order_item,r.item_code,r.stock_uom,
            r.received_qty,r.qty,r.conversion_factor,p.docstatus,p.is_return
            FROM `tabPurchase Receipt Item` r JOIN `tabPurchase Receipt` p ON p.name=r.parent
            WHERE r.purchase_order_item IN %s AND p.docstatus=1 AND p.is_return=0""",
            (tuple(row.name for row in details),), as_dict=True)
        result.extend(pending_lines(group, details, receipts))
    return result
