"""Read-only quantity/value true-up analysis. No posting or stock restoration API."""
from decimal import Decimal, InvalidOperation
import frappe
from frappe.utils import flt, now_datetime
from calco_erp.calco_production import partial_lot_costing as cost


def signed_amount(value):
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw('Invalid valuation adjustment.')
    if not result.is_finite():
        frappe.throw('Valuation adjustment must be finite.')
    return result


def quantity_deltas(measurements, sources):
    """Compare explicit final measurements to net supported consumption evidence.

    Measurements are audit inputs, not an authorization to post or restore stock.
    Each Item/Batch must be present explicitly; omission never means zero.
    """
    accounted = {}
    for source in sources:
        key = (source['item_code'], source['batch_no'])
        accounted[key] = accounted.get(key, Decimal(0)) + cost.number(source['qty'])
    measured = {}
    for row in measurements:
        key = (row.get('item_code'), row.get('batch_no'))
        if not all(key) or key in measured or not row.get('measurement_reference'):
            frappe.throw('Unique Item/Batch and final measurement reference are required.')
        measured[key] = row
    if set(accounted) - set(measured):
        frappe.throw('Final measurements must explicitly cover every consumed Item/Batch.')
    result = []
    for key, row in measured.items():
        qty = cost.number(row['qty']); posted = accounted.get(key, Decimal(0))
        delta = qty - posted
        result.append({'item_code':key[0], 'batch_no':key[1],
            'measurement_reference':row['measurement_reference'],
            'final_measured_qty':str(qty), 'already_accounted_qty':str(posted),
            'quantity_delta':str(delta),
            'resolution':'Controlled referenced restoration required' if delta < -cost.TOL else
                         'Additional measured consumption required' if delta > cost.TOL else 'No quantity adjustment',
            'automatic_restoration_allowed':False})
    return result


def residual_value(target_value, material_value, conversion_value, previous_adjustments):
    return cost.number(target_value) - (cost.number(material_value) +
        cost.number(conversion_value) + signed_amount(previous_adjustments))


def receipt_valuation(entry):
    """Read submitted LCV item links, never mutable totals or cancelled LCVs."""
    if isinstance(entry, str): entry = frappe.get_doc('Stock Entry', entry)
    if entry.docstatus != 1 or entry.purpose != 'Manufacture' or not entry.custom_partial_fg_lot:
        frappe.throw('A submitted partial Manufacture receipt is required.')
    lot = frappe.get_doc('Partial FG Lot', entry.custom_partial_fg_lot)
    if lot.docstatus != 1 or lot.work_order != entry.work_order:
        frappe.throw('Receipt has no valid submitted lot.')
    adjustments = frappe.db.sql("""select l.name, i.name as detail, i.stock_entry_item,
        i.applicable_charges from `tabLanded Cost Voucher` l
        join `tabLanded Cost Item` i on i.parent=l.name
        where l.docstatus=1 and i.receipt_document_type='Stock Entry'
        and i.receipt_document=%s order by l.creation,i.idx""",entry.name,as_dict=True)
    finished = {d.name for d in entry.items if d.is_finished_item}
    if any(a.stock_entry_item not in finished for a in adjustments):
        frappe.throw('Landed cost adjustment has invalid partial receipt item lineage.')
    landed = sum((signed_amount(a.applicable_charges) for a in adjustments),Decimal(0))
    material = cost.number(lot.material_value); conversion = cost.number(entry.total_additional_costs)
    ledger = signed_amount(frappe.db.sql("""select sum(stock_value_difference)
        from `tabStock Ledger Entry` where voucher_type='Stock Entry'
        and voucher_no=%s and is_cancelled=0""",entry.name)[0][0])
    expected = material + conversion + landed
    return {'stock_entry':entry.name,'lot':lot.name,'material':str(material),
        'conversion':str(conversion),'previous_value_adjustments':str(landed),
        'current_accounted_value':str(expected),'receipt_ledger':str(ledger),
        'ledger_reconciled':abs(expected-ledger)<=Decimal('0.01'),
        'adjustments':[dict(a) for a in adjustments]}


def audit_true_up(work_order, final_measurements=None, lot_value_targets=None):
    """Internal audit preview only. Does not authorize or execute adjustments."""
    from calco_erp.calco_production import partial_fg_lots as lots
    lots.lock(work_order)
    sources = cost.actual_sources(work_order, now_datetime())
    cost.reconcile(sources, lots.prior_allocations(work_order))
    quantities = None if final_measurements is None else quantity_deltas(final_measurements, sources)
    targets = lot_value_targets or {}
    values = []
    for name in frappe.get_all('Stock Entry', filters={'work_order':work_order,
            'purpose':'Manufacture','docstatus':1,'custom_partial_fg_lot':('is','set')},pluck='name'):
        value = receipt_valuation(name)
        target = targets.get(value['lot'])
        value.update(final_target_value=None if target is None else str(cost.number(target)),
            residual_value_delta=None if target is None else str(residual_value(target,
                value['material'],value['conversion'],value['previous_value_adjustments'])))
        values.append(value)
    if set(targets) - {v['lot'] for v in values}:
        frappe.throw('Value target references a lot without a valid submitted receipt in this Work Order.')
    blockers = [row['item_code']+'/'+row['batch_no']+': '+row['resolution']
        for row in quantities or [] if abs(signed_amount(row['quantity_delta'])) > cost.TOL]
    blockers += [value['stock_entry']+': Valuation reposting must reconcile before further adjustment'
        for value in values if not value['ledger_reconciled']]
    blockers += [value['stock_entry']+': Residual valuation adjustment requires controlled review'
        for value in values if value['residual_value_delta'] is not None
        and abs(signed_amount(value['residual_value_delta'])) > cost.TOL]
    return {'blockers':blockers,'audit_only':True,'automatic_posting_enabled':False,
        'measurement_status':'Final measurement evidence required' if quantities is None else 'Supplied for audit only',
        'quantity_reconciliation':quantities,'valuation_reconciliation':values}
