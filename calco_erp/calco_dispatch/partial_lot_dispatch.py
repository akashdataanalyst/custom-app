"""Lot-aware delivery checks. Stock posting stays with standard Delivery Note."""
from collections import defaultdict
import frappe
from frappe import _
from frappe.utils import flt


def allocations(row):
    expected = abs(flt(row.get("stock_qty") or flt(row.qty) * flt(row.get("conversion_factor") or 1)))
    if row.get("serial_and_batch_bundle"):
        bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
        if bundle.item_code != row.item_code or bundle.warehouse != row.warehouse:
            frappe.throw(_("Dispatch batch bundle must match its item and actual warehouse."))
        result = defaultdict(float)
        for entry in bundle.entries:
            if not entry.batch_no or not flt(entry.qty):
                frappe.throw(_("Each FG bundle allocation requires a batch and quantity."))
            result[entry.batch_no] += abs(flt(entry.qty))
    else:
        if not row.get("batch_no"):
            frappe.throw(_("FG dispatch requires explicit batch allocation."))
        result = {row.batch_no: expected}
    if expected <= 0 or abs(sum(result.values()) - expected) > 0.000001:
        frappe.throw(_("Dispatch batch allocations do not match the row stock quantity."))
    return dict(result)


def aggregate_allocations(rows):
    result = defaultdict(float)
    for row in rows:
        for batch, qty in allocations(row).items():
            result[(row.item_code, batch, row.warehouse)] += qty
    return dict(result)


def warehouse(company, label):
    name = frappe.db.get_value("Warehouse", {"company":company,"warehouse_name":label,"is_group":0,"disabled":0}, "name")
    if not name: frappe.throw(_("Company {0} has no usable {1} warehouse.").format(company,label))
    return name


def is_fg(item):
    group = frappe.db.get_value("Item", item, "item_group")
    while group:
        if group == "Finished Goods":return True
        group = frappe.db.get_value("Item Group", group, "parent_item_group")
    return False


def validate_delivery(doc, method=None):
    rows = [r for r in doc.items if is_fg(r.item_code)]
    if not rows:return
    target = warehouse(doc.company, "FG Quarantine" if doc.get("is_return") else "FG Released")
    if any(r.warehouse != target for r in rows):
        frappe.throw(_("FG {0} must use actual warehouse {1}.").format("returns" if doc.get("is_return") else "dispatch",target))
    grouped = aggregate_allocations(rows)
    if doc.get("is_return"):
        if not doc.get("return_against"):frappe.throw(_("FG return requires its original Delivery Note."))
        original = frappe.get_doc("Delivery Note", doc.return_against)
        if original.docstatus != 1 or original.company != doc.company or original.customer != doc.customer or original.get("is_return"):
            frappe.throw(_("FG return source is not a valid submitted customer delivery."))
        original_rows = aggregate_allocations([r for r in original.items if is_fg(r.item_code)])
        for item,batch,_wh in grouped:
            if not any(i==item and b==batch for i,b,w in original_rows):
                frappe.throw(_("Returned FG batch was not in the original delivery."))
        # Standard return quantity validation remains authoritative. Quarantine
        # receipt confers no new release authority.
        return
    from erpnext.stock.doctype.batch.batch import get_batch_qty
    for (item,batch,wh),qty in sorted(grouped.items()):
        frappe.db.sql("select name from `tabBatch` where name=%s for update",(batch,))
        if frappe.db.get_value("Batch",batch,"item") != item:
            frappe.throw(_("Dispatch batch does not belong to its item."))
        available=flt(get_batch_qty(batch_no=batch,warehouse=wh,item_code=item,for_stock_levels=True))
        if qty > available+0.000001:frappe.throw(_("Insufficient FG Released quantity for {0}, batch {1}.").format(item,batch))
        clearance_name=frappe.db.get_value("Dispatch Clearance",{"delivery_note":doc.name,"item_code":item,"batch_no":batch,"docstatus":1,"status":"Cleared"},"name")
        if not clearance_name:frappe.throw(_("Submitted lot-specific Dispatch Clearance is required."))
        clearance=frappe.get_doc("Dispatch Clearance",clearance_name)
        release=frappe.get_doc("Final QC Release",clearance.final_qc_release)
        if release.docstatus!=1 or release.status!='Released' or release.item_code!=item or release.batch_no!=batch:
            frappe.throw(_("Dispatch release lineage does not match this lot."))
        if not clearance.coa_record:frappe.throw(_("Lot COA is required."))
        coa=frappe.get_doc("COA Record",clearance.coa_record)
        if coa.status!='Issued' or coa.final_qc_release!=release.name or coa.item_code!=item or coa.batch_no!=batch:
            frappe.throw(_("COA does not match this released lot."))
        transfer=frappe.get_doc("Stock Entry",release.release_stock_entry)
        if transfer.docstatus!=1 or transfer.company!=doc.company:
            frappe.throw(_("Submitted release transfer for this company is required."))


def before_release_cancel(doc, method=None):
    if not frappe.db.exists('Partial FG Lot',{'lot_batch':doc.batch_no}):return
    # Clearance cancellation alone cannot authorize reversal of dispatched stock.
    deliveries=frappe.db.sql("""select distinct d.name from `tabDelivery Note` d
        join `tabDelivery Note Item` i on i.parent=d.name
        left join `tabSerial and Batch Entry` b on b.parent=i.serial_and_batch_bundle
        where d.docstatus=1 and d.is_return=0 and i.item_code=%s
        and (i.batch_no=%s or b.batch_no=%s)""",(doc.item_code,doc.batch_no,doc.batch_no),as_dict=True)
    if deliveries:
        frappe.throw(_('Partial lot has submitted dispatch {0}. Use explicit downstream cancellation/return disposition; release cancellation cannot unwind dispatch.').format(', '.join(r.name for r in deliveries)))
