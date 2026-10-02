"""Quantity-only finalization bridge. Never posts stock or invokes valuation settlement."""
import frappe
from frappe.utils import flt
from calco_erp.calco_production import production_batch_closure as closure
from calco_erp.calco_production import receipt_policy_infrastructure as infra


def material_state(card):
    record = closure.latest(card.name)
    if record is None:
        if frappe.db.exists('Partial FG Lot', {'job_card':card.name, 'docstatus':1,
                'custom_receipt_policy_version':'provisional-receipt-v1'}):
            return dict(reconciled=False, reason='Confirmed Batch Closed evidence is required for provisional production',
                actual_consumption=0, materials=[], remaining_wip=None)
        return None
    from calco_erp.calco_production import physical_completion as physical
    wo = frappe.get_doc('Work Order', card.work_order)
    if not infra.records.verified(record):
        frappe.throw('Production Batch Closure fingerprint mismatch.')
    if (record.work_order,record.job_card,record.company,record.parent_batch) != (
            wo.name,card.name,wo.company,wo.get('custom_fg_batch_no')):
        frappe.throw('Production Batch Closure run lineage mismatch.')
    reasons = []
    source = infra.payload(record.source_snapshot)
    if source.get('version') != closure.VERSION or not record.final_closure_confirmed:
        reasons.append('A controlled final physical-consumption confirmation is required')
    if record.status != 'Batch Closed':
        reasons.append('Latest Production Batch Closure is not Batch Closed')
    if not physical.end_event(card) or infra.payload(record.physical_end_event) != physical.end_event(card):
        reasons.append('Physical End differs from the confirmed closure version')
    names = set(frappe.get_all('Partial FG Lot',filters={'work_order':wo.name,'docstatus':1},pluck='name'))
    if names != {r['name'] for r in source.get('lots',[])}:
        reasons.append('Production lot inventory changed after closure; controlled correction required')
    rows,ledger = closure.ledger_snapshot(wo,card)
    measured = closure.measure(rows,[dict(item_code=r.item_code,batch_no=r.batch_no,
        final_actual_qty=r.final_actual_qty) for r in record.materials])
    apply_restorations(record,wo,card,rows)
    result = closure.evaluate(rows,measured,reasons + closure.physical_balance(wo,card,rows,measured))
    return dict(reconciled=not result['reasons'],reason='; '.join(result['reasons']),closure=record.name,
        revision=record.revision,fingerprint=record.fingerprint,
        actual_consumption=sum(flt(v) for v in measured.values()),remaining_wip=sum(r['remaining_qty'] for r in rows),
        materials=[dict(r,final_actual_qty=flt(measured[(r['item_code'],r['batch_no'])]),
            quantity_delta=flt(measured[(r['item_code'],r['batch_no'])])-r['accounted_qty']) for r in rows],
        ledger=ledger,quantity_requirements=result['quantity_requirements'])


def apply_restorations(record,wo,card,rows):
    # Optional approved quantity references only. No settlement/LCV readiness gate.
    from calco_erp.calco_production import production_settlement as settlement
    preparation = settlement.latest(card.name)
    if not preparation or preparation.closure != record.name or preparation.closure_version != record.revision:
        return
    if not infra.records.verified(preparation):
        frappe.throw('Quantity correction evidence fingerprint mismatch.')
    evidence = infra.payload(preparation.quantity_evidence)
    by_pair = {(r['item_code'],r['batch_no']):r for r in rows}
    seen = set()
    for restored in evidence.get('restorations',[]):
        key = (restored['consumption_entry'],restored['consumption_detail'],restored['batch_no'])
        if key in seen or not restored.get('evidence') or not restored.get('original_source'):
            frappe.throw('Invalid or duplicate controlled restoration reference.')
        seen.add(key)
        entry = frappe.get_doc('Stock Entry',key[0])
        detail = next((r for r in entry.items if r.name == key[1]),None)
        if entry.docstatus != 1 or entry.purpose != 'Material Receipt' or entry.company != wo.company or not detail or detail.t_warehouse != wo.wip_warehouse or detail.s_warehouse:
            frappe.throw('Restoration requires its submitted WIP receipt lineage.')
        if entry.get('work_order') and entry.work_order != wo.name:
            frappe.throw('Restoration belongs to another Work Order.')
        if detail.serial_and_batch_bundle:
            bundle = frappe.get_doc('Serial and Batch Bundle',detail.serial_and_batch_bundle)
            if bundle.item_code != detail.item_code or bundle.warehouse != wo.wip_warehouse:
                frappe.throw('Restoration bundle lineage mismatch.')
            qty = sum(flt(r.qty) for r in bundle.entries if r.batch_no == key[2])
        else:
            qty = flt(detail.transfer_qty) if detail.batch_no == key[2] else 0
        ledger_qty = flt(frappe.db.sql('''select sum(actual_qty) from `tabStock Ledger Entry`
            where voucher_type='Stock Entry' and voucher_no=%s and voucher_detail_no=%s and warehouse=%s and is_cancelled=0''',
            (entry.name,detail.name,wo.wip_warehouse))[0][0])
        if abs(ledger_qty-flt(detail.transfer_qty)) > 1e-6 or qty <= 0 or abs(qty-flt(restored['qty'])) > 1e-6:
            frappe.throw('Restoration quantity differs from native stock evidence.')
        pair = (detail.item_code,key[2])
        if pair not in by_pair:
            frappe.throw('Restoration material is not attributed to this run.')
        row = by_pair[pair]
        row['accounted_qty'] -= qty
        row['remaining_qty'] += qty
        row['restored_qty'] = row.get('restored_qty',0) + qty


def operation_blockers(wo):
    """Every scheduled operation needs submitted quantity evidence; receipts are not job completion."""
    cards = frappe.get_all('Job Card',filters={'work_order':wo.name,'docstatus':['!=',2]},
        fields=['name','operation_id','docstatus','total_completed_qty','process_loss_qty','status'])
    blockers = []
    for operation in wo.operations:
        matching = [r for r in cards if r.operation_id == operation.name]
        from calco_erp.calco_production.integrated_packing import excluded
        if excluded(wo,operation,matching):continue
        completed = sum(flt(r.total_completed_qty)+flt(r.process_loss_qty) for r in matching if r.docstatus == 1)
        if completed + 1e-6 < flt(wo.qty) or any(r.docstatus != 1 for r in matching):
            blockers.append('Required operation remains incomplete: '+operation.operation)
    return blockers
