"""Incremental lot consumption accounting. No stock posting or inferred BOM cost.

A source is one submitted consumption detail/batch allocation valued by ERPNext.
Entitlement is recomputed from valid manufacture references, never a mutable balance.
"""
from decimal import Decimal, InvalidOperation
import frappe
from frappe import _

TOL = Decimal("0.000001")


def number(value):
    try: result = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw(_("Invalid lot allocation quantity or value."))
    if not result.is_finite() or result < 0:
        frappe.throw(_("Lot allocation quantities and values must be finite and non-negative."))
    return result


def source_key(row):
    fields = ("consumption_entry", "consumption_detail", "item_code", "batch_no")
    if any(not row.get(f) for f in fields):
        frappe.throw(_("Consumption entry, detail, RM item and RM batch are required."))
    return tuple(row[f] for f in fields)


def remaining_entitlements(sources, allocations):
    balances = {}
    for source in sources:
        key = source_key(source)
        if key in balances:frappe.throw(_("Duplicate actual consumption source allocation."))
        qty, value = number(source.get("qty")), number(source.get("stock_value"))
        if qty <= 0:frappe.throw(_("Actual consumption source quantity must be positive."))
        balances[key] = {"source":dict(source), "consumed_qty":qty, "consumed_value":value,
                         "allocated_qty":Decimal(0), "allocated_value":Decimal(0)}
    seen = set()
    for allocation in allocations:
        # Caller supplies current standard Manufacture docstatus; cancelled
        # receipts restore entitlement without deleting attribution history.
        if allocation.get("manufacture_docstatus") != 1:continue
        identity = (allocation.get("manufacture"), allocation.get("allocation_id"))
        if not all(identity) or identity in seen:
            frappe.throw(_("Missing or duplicate submitted lot allocation identity."))
        seen.add(identity)
        key = source_key(allocation)
        if key not in balances:frappe.throw(_("A valid lot references missing or reversed actual consumption."))
        row = balances[key]
        qty, value = number(allocation.get("qty")), number(allocation.get("stock_value"))
        if abs(value - qty * row["consumed_value"] / row["consumed_qty"]) > TOL:
            frappe.throw(_("Consumption valuation has changed. Reconcile/revalue the linked lot before further allocation."))
        row["allocated_qty"] += qty;row["allocated_value"] += value
    for row in balances.values():
        if row["allocated_qty"] > row["consumed_qty"] + TOL or row["allocated_value"] > row["consumed_value"] + TOL:
            frappe.throw(_("Actual consumption has been attributed more than once."))
        row["available_qty"] = max(row["consumed_qty"]-row["allocated_qty"],Decimal(0))
        row["available_value"] = max(row["consumed_value"]-row["allocated_value"],Decimal(0))
    return balances


def allocate(sources, previous, requested):
    balances = remaining_entitlements(sources, previous)
    seen = set();result = []
    for request in requested:
        key = source_key(request)
        if key in seen:frappe.throw(_("Select each actual RM/batch consumption source once per lot."))
        seen.add(key)
        if key not in balances:frappe.throw(_("Selected consumption is not available at this production cutoff."))
        row = balances[key];qty = number(request.get("qty"))
        if qty <= 0 or qty > row["available_qty"] + TOL:
            frappe.throw(_("Lot consumption exceeds its unallocated actual RM/batch entitlement."))
        # Allocate the remaining recorded value exactly when exhausting a source.
        value = row["available_value"] if abs(qty-row["available_qty"]) <= TOL else qty*row["consumed_value"]/row["consumed_qty"]
        result.append({**row["source"],"qty":str(qty),"stock_value":str(value)})
    if not result:frappe.throw(_("Confirm actual RM/batch consumption for this lot."))
    return result


def reconcile(sources, allocations):
    balances = remaining_entitlements(sources, allocations)
    return {key:sum((r[key] for r in balances.values()),Decimal(0)) for key in
            ("consumed_qty","consumed_value","allocated_qty","allocated_value","available_qty","available_value")}


def material_value(attribution):
    return sum((number(row.get("stock_value")) for row in attribution),Decimal(0))


def assert_cost_balance(input_value, conversion_value, fg_value, recovery_value, recognized_loss_value, unresolved_value=0):
    expected = number(input_value)+number(conversion_value)
    accounted = sum((number(v) for v in [fg_value,recovery_value,recognized_loss_value,unresolved_value]),Decimal(0))
    if abs(expected-accounted)>TOL:frappe.throw(_("FG, recovery, loss and unresolved cost do not reconcile to actual input/conversion cost."))
    return True


def actual_sources(work_order, cutoff):
    """Read posted WO-attributed consumption and actual outgoing ledger values.

    Bundle allocations retain individual batch values. No BOM rates or current
    Item prices are consulted. Repost inconsistencies fail closed.
    """
    from frappe.utils import get_datetime
    cutoff = get_datetime(cutoff)
    entries = frappe.db.sql("""select name from `tabStock Entry`
        where work_order=%s and purpose='Material Consumption for Manufacture'
        and docstatus=1 and timestamp(posting_date,posting_time)<=%s
        and creation<=%s order by posting_date,posting_time,name""",
        (work_order,cutoff,cutoff),as_dict=True)
    from calco_erp.calco_production.final_consumption import stock_entries
    known = {r.name for r in entries}
    entries += [r for r in stock_entries(work_order, cutoff) if r.name not in known]
    result=[]
    for entry in entries:
        doc=frappe.get_doc("Stock Entry",entry.name)
        for row in doc.items:
            if not row.s_warehouse or row.t_warehouse:
                frappe.throw(_("Consumption source must contain outgoing WIP only."))
            ledger=frappe.db.sql("""select actual_qty,stock_value_difference
                from `tabStock Ledger Entry` where voucher_type='Stock Entry'
                and voucher_no=%s and voucher_detail_no=%s and is_cancelled=0""",
                (doc.name,row.name),as_dict=True)
            if not ledger or any(number(abs(r.actual_qty))==0 or r.actual_qty>=0 for r in ledger):
                frappe.throw(_("Submitted consumption lacks valid outgoing Stock Ledger evidence."))
            ledger_qty=sum((number(-r.actual_qty) for r in ledger),Decimal(0))
            ledger_value=sum((number(-r.stock_value_difference) for r in ledger),Decimal(0))
            batches=[]
            if row.serial_and_batch_bundle:
                bundle=frappe.get_doc("Serial and Batch Bundle",row.serial_and_batch_bundle)
                if bundle.item_code!=row.item_code or bundle.warehouse!=row.s_warehouse:
                    frappe.throw(_("Consumption batch bundle lineage is invalid."))
                for b in bundle.entries:
                    if not b.batch_no or b.qty>=0:
                        frappe.throw(_("Consumption requires outgoing RM batch evidence."))
                    batches.append((b.batch_no,number(-b.qty),number(-b.stock_value_difference)))
            elif row.batch_no:
                batches.append((row.batch_no,ledger_qty,ledger_value))
            else:frappe.throw(_("Actual RM batch identity is required for lot attribution."))
            if abs(sum((q for b,q,v in batches),Decimal(0))-ledger_qty)>TOL or abs(sum((v for b,q,v in batches),Decimal(0))-ledger_value)>TOL:
                frappe.throw(_("RM batch values do not reconcile to their posted Stock Ledger. Complete valuation reposting before confirmation."))
            combined={}
            for batch,qty,value in batches:
                if batch not in combined:combined[batch]=[Decimal(0),Decimal(0)]
                combined[batch][0]+=qty;combined[batch][1]+=value
            for batch,(qty,value) in combined.items():
                result.append({"consumption_entry":doc.name,"consumption_detail":row.name,
                    "item_code":row.item_code,"batch_no":batch,"qty":str(qty),"stock_value":str(value),
                    "warehouse":row.s_warehouse,"work_order":work_order,
                    "posting_date":str(doc.posting_date),"posting_time":str(doc.posting_time),
                    "cutoff":str(cutoff),"serial_and_batch_bundle":row.serial_and_batch_bundle})
    return result


def validate_historical_allocation(current_sources, all_other_allocations,
                                   cutoff_sources, preceding_allocations, frozen):
    """Global entitlement plus historical eligibility; neither replaces the other."""
    allocate(current_sources, all_other_allocations, frozen)
    return allocate(cutoff_sources, preceding_allocations, frozen)
