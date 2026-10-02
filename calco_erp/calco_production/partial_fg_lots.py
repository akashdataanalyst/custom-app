"""Controlled partial FG confirmations and standard Manufacture integration."""
import json
import frappe
from frappe import _
from frappe.utils import flt, now_datetime, get_datetime
from calco_erp.calco_production import partial_lot_costing as cost, in_process_quality as qc, shift_production_output as output

PROTECTED=('job_card','work_order','company','item_code','parent_batch','lot_batch','fg_qty','cutoff','output_evidence','consumption_evidence','material_value','confirmed_by','confirmed_on','confirmation_reason','qc_fingerprint')
ROLES={'Production Engineer','Production Head','Manufacturing Manager'}

def lock(work_order):qc._lock(work_order)

def fg_state(card,cutoff=None):
    rows=output.readings_for(card.name)
    if cutoff:rows=[r for r in rows if get_datetime(r.reading_time)<=get_datetime(cutoff)]
    state=output.snapshots.reduce_snapshots(rows)
    totals={f:0.0 for f in ['quantity','spy_qty','tpy_qty','loose_quantity','lab_samples','metal_separator_qty']}
    for r in state['effective']:
        values=json.loads(r.components_json)
        for f in totals:totals[f]+=flt(values.get(f))
    return {'fg_qty':totals['quantity'],'components':totals,'snapshots':[r.name for r in state['effective']]}

def prior_allocations(work_order,exclude=None,cutoff=None):
    result=[]
    for lot in frappe.get_all('Partial FG Lot',filters={'work_order':work_order,'docstatus':1},fields=['name','consumption_evidence','cutoff']):
        if lot.name==exclude:continue
        if cutoff is not None and get_datetime(lot.cutoff)>get_datetime(cutoff):continue
        # Submitted confirmations reserve entitlement, even before stock receipt.
        for index,row in enumerate(json.loads(lot.consumption_evidence)):
            result.append({**row,'manufacture':lot.name,'allocation_id':str(index),'manufacture_docstatus':1})
    return result

def reserved_fg(work_order,exclude=None):
    return sum(flt(r.fg_qty) for r in frappe.get_all('Partial FG Lot',filters={'work_order':work_order,'docstatus':1},fields=['name','fg_qty']) if r.name!=exclude)

def received_fg(work_order):
    return flt(frappe.db.sql("""select sum(d.transfer_qty) from `tabStock Entry` s
        join `tabStock Entry Detail` d on d.parent=s.name where s.work_order=%s
        and s.purpose='Manufacture' and s.docstatus=1 and d.is_finished_item=1""",(work_order,))[0][0])

def partial_quality(wo,card,cutoff):
    qc.assert_no_hold(wo.name)
    from calco_erp.calco_production.physical_completion import end_event
    physical_end=end_event(card)
    if physical_end and get_datetime(cutoff)>=get_datetime(physical_end['cutoff']):
        qc.assert_manufacture_allowed(wo.name)
    if card.docstatus==1 and card.status=='Completed':
        qc.assert_manufacture_allowed(wo.name)
        from calco_erp.calco_production.manufacture_entry import _assert_zero_remaining_wip
        from calco_erp.calco_production.physical_completion import controlled, wip_reconciliation
        if controlled(card):
            reconciliation=wip_reconciliation(wo.name)
            if not reconciliation['reconciled']:frappe.throw(reconciliation['reason'])
        else:_assert_zero_remaining_wip(wo.name)
        return
    plan=qc.frozen_plan(wo)
    rows=[r for r in output.readings_for(card.name) if get_datetime(r.reading_time)<=get_datetime(cutoff)]
    state=output.snapshots.reduce_snapshots(rows)
    obligations=qc.checkpoint_obligations(plan,qc.active_elapsed_minutes(card.time_logs,current=cutoff),state['high_water'],False,fg_qty=fg_progress(card,cutoff)['high_water'])
    obligations=[r for r in obligations if r['checkpoint']!='End-of-Batch']
    result=qc.aggregate(obligations,qc.inspection_rows(wo.name),frappe.parse_json(wo.get('custom_ipqc_missed_events') or '[]'))
    if not result['ready']:frappe.throw(_('Partial lot is blocked by applicable IPQC: {0}').format(', '.join(result['blockers'])))

def context(job_card):
    from calco_erp.calco_production.shift_reporting import _get_controlled_context
    card,wo=_get_controlled_context(job_card)
    card.check_permission('read')
    from calco_erp.calco_production.physical_completion import ended
    if wo.docstatus!=1 or wo.status in {'Stopped','Closed','Cancelled','Completed'} or not ((card.docstatus==0 and ((card.status=='Work In Progress' and not card.is_paused) or ended(card))) or (card.docstatus==1 and card.status=='Completed')):
        frappe.throw(_('Partial FG confirmation requires a valid running Compounding Job Card.'))
    if not output.snapshots.activation(card):frappe.throw(_('Controlled shift snapshot output is required.'))
    return card,wo

@frappe.whitelist()
def preview(job_card):
    card,wo=context(job_card);state=fg_state(card);reserved=reserved_fg(wo.name);received=received_fg(wo.name)
    return {'work_order':wo.name,'job_card':card.name,'planned_qty':wo.qty,'cumulative_fg_qty':state['fg_qty'],
        'confirmed_qty':reserved,'received_qty':received,'unreceived_qty':max(state['fg_qty']-received,0),
        'available_to_confirm':max(min(state['fg_qty']-reserved,flt(wo.qty)-reserved),0),
        'remaining_wo_qty':max(flt(wo.qty)-received,0),'parent_batch':wo.custom_fg_batch_no,'recovery':state['components'],
        'recorded_consumption_bases':frappe.get_all('Stock Entry',filters={'work_order':wo.name,'custom_live_consumption_job_card':card.name,'purpose':'Material Consumption for Manufacture','docstatus':1},fields=['name','fg_completed_qty'],order_by='creation'),
        'consumption':[{**r['source'],'available_qty':str(r['available_qty']),'available_value':str(r['available_value'])} for r in cost.remaining_entitlements(cost.actual_sources(wo.name,now_datetime()),prior_allocations(wo.name)).values()]}

@frappe.whitelist()
def confirm(job_card,fg_qty,consumption,reason='',proposed_batch=None):
    if not ROLES.intersection(frappe.get_roles()):frappe.throw(_('Production authority is required.'),frappe.PermissionError)
    card,wo=context(job_card);lock(wo.name);card,wo=context(job_card)
    card.check_permission('write')
    frappe.has_permission('Batch','create',throw=True)
    qty=cost.number(fg_qty);cutoff=now_datetime();state=fg_state(card,cutoff)
    if qty<=0 or float(qty)>min(state['fg_qty'],flt(wo.qty))-reserved_fg(wo.name)+1e-6:frappe.throw(_('FG quantity exceeds unconfirmed produced FG or Work Order authority.'))
    if received_fg(wo.name)>reserved_fg(wo.name)+1e-6:frappe.throw(_('Legacy receipts must be reconciled before partial confirmation.'))
    partial_quality(wo,card,cutoff)
    requested=frappe.parse_json(consumption)
    allocations=cost.allocate(cost.actual_sources(wo.name,cutoff),prior_allocations(wo.name),requested)
    assert_consumption_mass(wo.production_item,qty,allocations)
    import re
    batch_id=proposed_batch or wo.custom_fg_batch_no+'-L-'+frappe.generate_hash(length=8)
    if not re.fullmatch(re.escape(wo.custom_fg_batch_no)+r'-L-[a-zA-Z0-9]{8}',batch_id):frappe.throw('Invalid proposed lot Batch identity.')
    batch=frappe.get_doc({'doctype':'Batch','item':wo.production_item,'batch_id':batch_id}).insert()
    doc=frappe.get_doc({'doctype':'Partial FG Lot','job_card':card.name,'work_order':wo.name,'company':wo.company,
        'item_code':wo.production_item,'parent_batch':wo.custom_fg_batch_no,'lot_batch':batch.name,'fg_qty':float(qty),
        'cutoff':cutoff,'output_evidence':json.dumps(state),'consumption_evidence':json.dumps(allocations),
        'material_value':float(cost.material_value(allocations)),'confirmed_by':frappe.session.user,'confirmed_on':cutoff,
        'confirmation_reason':reason,'qc_fingerprint':wo.get(qc.FINGERPRINT)})
    previous=frappe.flags.creating_partial_fg_lot;frappe.flags.creating_partial_fg_lot=True
    try:doc.insert();doc.submit()
    finally:frappe.flags.creating_partial_fg_lot=previous
    return {'name':doc.name,'batch':batch.name}

def validate_confirmation(lot):
    if lot.get("custom_receipt_policy_version"):
        from calco_erp.calco_production.provisional_receipts import validate_lot
        return validate_lot(lot)
    lock(lot.work_order);card=frappe.get_doc('Job Card',lot.job_card);wo=frappe.get_doc('Work Order',lot.work_order)
    from calco_erp.calco_production.fg_planning_authority import assert_execution_allowed
    assert_execution_allowed(wo.name)
    if wo.docstatus!=1 or wo.status in {'Stopped','Closed','Cancelled'}:frappe.throw(_('Work Order is not executable.'))
    if card.work_order!=wo.name or lot.company!=wo.company or lot.item_code!=wo.production_item or lot.parent_batch!=wo.custom_fg_batch_no:
        frappe.throw(_('Partial lot production genealogy mismatch.'))
    if lot.qc_fingerprint!=wo.get(qc.FINGERPRINT):frappe.throw(_('Frozen QC genealogy mismatch.'))
    if lot.fg_qty+reserved_fg(wo.name,lot.name)>min(fg_state(card)['fg_qty'],flt(wo.qty))+1e-6:frappe.throw(_('Partial lot quantity exceeds current confirmed output.'))
    if frappe.db.get_value('Batch',lot.lot_batch,'item')!=wo.production_item:frappe.throw(_('Lot batch item mismatch.'))
    # Global protection always sees every current valid lot and source. Historical
    # replay separately uses the lot cutoff; later allocations are not erased.
    actual=cost.validate_historical_allocation(
        cost.actual_sources(wo.name,now_datetime()), prior_allocations(wo.name,lot.name),
        cost.actual_sources(wo.name,lot.cutoff), prior_allocations(wo.name,lot.name,cutoff=lot.cutoff),
        json.loads(lot.consumption_evidence))
    frozen=json.loads(lot.consumption_evidence)
    if any(cost.source_key(a)!=cost.source_key(b) or abs(cost.number(a['stock_value'])-cost.number(b['stock_value']))>cost.TOL for a,b in zip(actual,frozen)) or len(actual)!=len(frozen):
        frappe.throw(_('Attributed RM/batch valuation changed; reconfirm the lot.'))
    if abs(float(cost.material_value(actual))-flt(lot.material_value))>1e-6:frappe.throw(_('Actual consumption value changed; reconfirm the lot.'))
    partial_quality(wo,card,lot.cutoff)


def stock_lot(doc):
    if not doc.get('custom_partial_fg_lot'):return None
    lot=frappe.get_doc('Partial FG Lot',doc.custom_partial_fg_lot)
    if lot.docstatus!=1 or doc.work_order!=lot.work_order or doc.company!=lot.company:
        frappe.throw(_('Manufacture requires its submitted lot confirmation and matching WO/company.'))
    return lot

def validate_manufacture(doc):
    lot=stock_lot(doc)
    if not lot:return False
    lock(lot.work_order);validate_confirmation(lot)
    if doc.purpose!='Manufacture' or abs(flt(doc.fg_completed_qty)-lot.fg_qty)>1e-6 or flt(doc.process_loss_qty):
        frappe.throw(_('Partial Manufacture quantity must equal confirmed FG only.'))
    duplicates=frappe.get_all('Stock Entry',filters={'custom_partial_fg_lot':lot.name,'docstatus':('!=',2),'name':('!=',doc.name or '')},pluck='name')
    if duplicates:frappe.throw(_('This lot already has a Manufacture draft/receipt: {0}').format(', '.join(duplicates)))
    from calco_erp.calco_production.manufacture_entry import get_fg_quarantine_warehouse
    target=get_fg_quarantine_warehouse(lot.company)
    if len(doc.items)!=1:frappe.throw(_('Partial FG Manufacture must contain only its confirmed FG item; use controlled separate recovery settlement.'))
    row=doc.items[0]
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
    if not row.is_finished_item or row.item_code!=lot.item_code or row.s_warehouse or row.t_warehouse!=target or get_stock_entry_row_batch_no(row)!=lot.lot_batch or abs(flt(row.transfer_qty)-lot.fg_qty)>1e-6:
        frappe.throw(_('Partial Manufacture item, batch, quantity or quarantine lineage mismatch.'))
    if row.get('set_basic_rate_manually'):frappe.throw(_('Partial lot material valuation comes from actual consumption.'))
    existing=flt(frappe.db.sql("""select sum(d.transfer_qty) from `tabStock Entry` s join `tabStock Entry Detail` d on d.parent=s.name
       where s.work_order=%s and s.docstatus=1 and s.purpose='Manufacture' and s.name!=%s and d.is_finished_item=1""",(lot.work_order,doc.name or ''))[0][0])
    wo=frappe.get_doc('Work Order',lot.work_order);card=frappe.get_doc('Job Card',lot.job_card)
    if existing+lot.fg_qty>min(fg_state(card)['fg_qty'],flt(wo.qty))+1e-6:frappe.throw(_('Cumulative Manufacture exceeds confirmed FG or WO quantity.'))
    return True

class PartialFGStockEntryMixin:
    def check_if_operations_completed(self):
        if self.get('custom_live_consumption_job_card'):
            card,wo=context(self.custom_live_consumption_job_card)
            if self.work_order!=wo.name or self.purpose!='Material Consumption for Manufacture':frappe.throw(_('Live consumption lineage mismatch.'))
            if not ROLES.intersection(frappe.get_roles()):frappe.throw(_('Production authority required.'),frappe.PermissionError)
            basis=flt(self.fg_completed_qty)+flt(wo.produced_qty)
            for op in wo.operations:
                if op.operation=='Packing':continue
                permitted=fg_state(card)['fg_qty'] if op.name==card.operation_id else flt(op.completed_qty)+flt(op.process_loss_qty)
                if basis>permitted+1e-6:frappe.throw(_('Insufficient operation/output quantity for actual consumption.'))
            return
        if not self.get('custom_partial_fg_lot'):
            return super().check_if_operations_completed()
        lot=stock_lot(self);wo=frappe.get_doc('Work Order',lot.work_order)
        validate_confirmation(lot)
        basis=flt(self.fg_completed_qty)+flt(wo.produced_qty)
        # Receipt-specific packed FG confirmation establishes physical completion
        # of Compounding and Packing for this quantity, not final JC completion.
        for op in wo.operations:
            if op.operation in {'Compounding / Extrusion','Packing'}:
                available=fg_state(frappe.get_doc('Job Card',lot.job_card))['fg_qty']
            else:available=flt(op.completed_qty)+flt(op.process_loss_qty)
            if basis>available+1e-6:frappe.throw(_('Operation {0} lacks confirmed quantity for this receipt.').format(op.operation))
    def get_basic_rate_for_manufactured_item(self,finished_item_qty,outgoing_items_cost=0):
        if not self.get('custom_partial_fg_lot'):
            return super().get_basic_rate_for_manufactured_item(finished_item_qty,outgoing_items_cost)
        lot=stock_lot(self);validate_confirmation(lot)
        if abs(flt(finished_item_qty)-lot.fg_qty)>1e-6:frappe.throw(_('Lot valuation quantity mismatch.'))
        return flt(lot.material_value)/lot.fg_qty

@frappe.whitelist()
def make_manufacture(name,operator=None,shift=None):
    frappe.has_permission('Stock Entry','create',throw=True)
    lot=frappe.get_doc('Partial FG Lot',name);lot.check_permission('read');lock(lot.work_order)
    if lot.docstatus!=1:frappe.throw(_('Submit the lot confirmation first.'))
    old=frappe.db.get_value('Stock Entry',{'custom_partial_fg_lot':name,'docstatus':('!=',2)},'name')
    if old:return {'name':old}
    validate_confirmation(lot)
    wo=frappe.get_doc('Work Order',lot.work_order);card=frappe.get_doc('Job Card',lot.job_card)
    from calco_erp.calco_production.manufacture_entry import get_fg_quarantine_warehouse
    from calco_erp.machine_setup import MACHINE_FIELD,OPERATOR_FIELD,SHIFT_FIELD
    doc=frappe.new_doc('Stock Entry')
    doc.update({'purpose':'Manufacture','stock_entry_type':'Manufacture','work_order':wo.name,'company':wo.company,
        'from_bom':1,'bom_no':wo.bom_no,'use_multi_level_bom':wo.use_multi_level_bom,'fg_completed_qty':lot.fg_qty,
        'to_warehouse':get_fg_quarantine_warehouse(wo.company),'custom_partial_fg_lot':lot.name})
    for field in [MACHINE_FIELD,OPERATOR_FIELD,SHIFT_FIELD]:doc.set(field,card.get(field) or wo.get(field))
    if operator:doc.set(OPERATOR_FIELD,operator)
    if shift:doc.set(SHIFT_FIELD,shift)
    state=json.loads(lot.output_evidence)
    if not doc.get(SHIFT_FIELD) and state['snapshots']:
        report=frappe.db.get_value('Shift Production Output Reading',state['snapshots'][-1],'parent')
        doc.set(SHIFT_FIELD,frappe.db.get_value('Shift Report',report,'shift'))
    doc.get_items()
    fg=next(r for r in doc.items if r.is_finished_item and r.item_code==lot.item_code)
    doc.set('items',[fg]);fg.s_warehouse='';fg.t_warehouse=doc.to_warehouse;fg.qty=lot.fg_qty/(flt(fg.conversion_factor) or 1);fg.transfer_qty=lot.fg_qty
    fg.batch_no=lot.lot_batch;fg.serial_and_batch_bundle='';fg.use_serial_batch_fields=1
    # No automatic reuse of accumulated WO conversion costs. Any standard
    # additional-cost rows are explicit lot charges, reviewed on this draft.
    doc.set('additional_costs',[])
    doc.insert()
    return {'name':doc.name}

def before_stock_cancel(doc,method=None):
    if doc.get('custom_partial_fg_lot'):
        lot=stock_lot(doc);lock(lot.work_order)
        if frappe.db.exists('Final QC Release',{'batch_no':lot.lot_batch,'docstatus':1}):frappe.throw(_('Reverse the QC release and downstream dispatch before cancelling this partial receipt.'))
        if frappe.db.exists('Quality Inspection',{'reference_type':'Stock Entry','reference_name':doc.name,'docstatus':1}):frappe.throw(_('Cancel the lot Quality Inspection before cancelling its receipt.'))
        if frappe.db.exists('Batch Production Record',{'stock_entry':doc.name,'docstatus':1}):frappe.throw(_('Cancel the lot BPR before cancelling its receipt.'))
    if doc.purpose=='Material Consumption for Manufacture':
        lock(doc.work_order)
        for row in prior_allocations(doc.work_order):
            if row['consumption_entry']==doc.name:frappe.throw(_('Cancel the attributed partial lot and downstream receipt before reversing this consumption.'))

def after_stock_cancel(doc,method=None):
    if doc.get('custom_partial_fg_lot'):
        lot=frappe.get_doc('Partial FG Lot',doc.custom_partial_fg_lot)
        if lot.docstatus==1:lot.cancel()

@frappe.whitelist()
def make_quality_inspection(name):
    lot=frappe.get_doc('Partial FG Lot',name);lot.check_permission('read');lock(lot.work_order)
    receipt=frappe.db.get_value('Stock Entry',{'custom_partial_fg_lot':name,'docstatus':1},'name')
    if not receipt:frappe.throw(_('Submit this lot Manufacture before Final QC.'))
    old=frappe.db.get_value('Quality Inspection',{'reference_type':'Stock Entry','reference_name':receipt,'batch_no':lot.lot_batch,'docstatus':('!=',2)},'name')
    if old:return {'name':old}
    d=frappe.get_doc({'doctype':'Quality Inspection','inspection_type':'Outgoing','item_code':lot.item_code,'company':lot.company,
       'batch_no':lot.lot_batch,'reference_type':'Stock Entry','reference_name':receipt,'custom_work_order':lot.work_order,
       'custom_work_order_qc_stage':'Final QC','status':'Pending','inspected_by':frappe.session.user})
    d.insert();return {'name':d.name}

@frappe.whitelist()
def make_live_consumption(job_card,rows,basis_qty):
    card,wo=context(job_card);lock(wo.name)
    from calco_erp.calco_production import wip_consumption as wip
    frappe.has_permission('Stock Entry','create',throw=True)
    if not ROLES.intersection(frappe.get_roles()):frappe.throw(_('Production authority required.'),frappe.PermissionError)
    requested=wip._parse_requested_rows(rows)
    if not requested:frappe.throw(_('Enter measured actual RM/batch consumption; empty selection cannot infer BOM consumption.'))
    if flt(basis_qty)<=0:frappe.throw(_('FG quantity covered must be positive.'))
    wo=wip._validate_work_order(wo.name)
    if not wip.is_controlled_work_order(wo.name):frappe.throw(_('Controlled WO-attributed WIP is required.'))
    wip.assert_production_readiness(wo.name)
    selected=wip._validate_requested_rows(requested,wip._get_consumable_rows(wo))
    # Supply the real intermediate evidence before standard get_items validates
    # operation quantity. No transient global validation bypass or JC mutation.
    doc=frappe.new_doc('Stock Entry')
    doc.update({'purpose':wip.CONSUMPTION_PURPOSE,'work_order':wo.name,'company':wo.company,
        'from_bom':1,'bom_no':wo.bom_no,'use_multi_level_bom':wo.use_multi_level_bom,
        'fg_completed_qty':flt(basis_qty),'from_warehouse':wo.wip_warehouse,'project':wo.project,
        'custom_live_consumption_job_card':card.name})
    doc.set_stock_entry_type();doc.get_items()
    return wip._build_standard_consumption_draft(wo,selected,basis_qty,doc).as_dict()


def setup():
    frappe.reload_doc('calco_production','doctype','partial_fg_lot')
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'FG Control Plan':[{'fieldname':'custom_ipqc_quantity_basis','label':'Quantity Trigger Basis','fieldtype':'Select','options':'\nCumulative FG Qty\nUnique Physical Process Output','insert_after':'custom_ipqc_period_basis','depends_on':"eval:doc.custom_ipqc_period_basis=='Produced Quantity'",'description':'Prospective only. Blank preserves the existing quantity authority; frozen runs are unchanged.'}], 'Stock Entry':[
        {'fieldname':'custom_partial_fg_lot','label':'Partial FG Lot','fieldtype':'Link','options':'Partial FG Lot','read_only':1,'no_copy':1},
        {'fieldname':'custom_live_consumption_job_card','label':'Actual Consumption Compounding Run','fieldtype':'Link','options':'Job Card','read_only':1,'no_copy':1}
    ]},update=True)


def has_lots(work_order):
    return frappe.db.exists('Partial FG Lot',{'work_order':work_order,'docstatus':1})

def final_reconciliation(work_order, final_measurements=None, lot_value_targets=None):
    wo=frappe.get_doc('Work Order',work_order)
    names=frappe.get_all('Partial FG Lot',filters={'work_order':work_order,'docstatus':1},pluck='job_card')
    if not names:return {'ready':True,'blockers':[]}
    card=frappe.get_doc('Job Card',names[0]);state=fg_state(card);received=received_fg(work_order);blockers=[]
    if card.docstatus!=1 or card.status!='Completed':blockers.append('Compounding completion is required')
    if abs(state['fg_qty']-received)>1e-6:blockers.append('Confirmed FG and net Manufacture receipts differ')
    from calco_erp.calco_production.physical_completion import wip_reconciliation, controlled
    from calco_erp.calco_production.manufacture_entry import _get_reconciliation_result
    from calco_erp.calco_production.physical_closure_state import material_state, operation_blockers
    closure=material_state(card) if controlled(card) else None
    if closure is not None:
        blockers.extend(operation_blockers(wo))
    wip=closure if closure is not None else wip_reconciliation(work_order) if controlled(card) else _get_reconciliation_result(work_order)
    if not wip['reconciled']:blockers.append('WIP/material reconciliation is incomplete: '+wip['reason'])
    accounting=cost.reconcile(cost.actual_sources(work_order,now_datetime()),prior_allocations(work_order))
    if closure is None and (accounting['available_qty']>cost.TOL or accounting['available_value']>cost.TOL):blockers.append('Actual consumption remains unattributed')
    recovery={k:v for k,v in state['components'].items() if k!='quantity' and v}
    if recovery:blockers.append('Recovery/sample/reclassification disposition requires controlled final reconciliation; no automatic FG conversion')
    from calco_erp.calco_production.manufacture_entry import _resolve_quantity_authority
    final_authority=None
    messages=list(frappe.local.message_log or [])
    try:
        if controlled(card) and card.docstatus==1 and card.status=='Completed':
            final_authority={'actual_fg_qty':flt(card.total_completed_qty),'process_loss_qty':flt(card.process_loss_qty)}
        else:final_authority=_resolve_quantity_authority(wo)
    except frappe.ValidationError as exc:
        blockers.append(str(exc))
    finally:
        frappe.local.message_log=messages
    if final_authority and abs(flt(final_authority['actual_fg_qty'])-state['fg_qty'])>1e-6:
        blockers.append('Final operation quantity differs from confirmed cumulative FG')
    loss=flt((final_authority or {}).get('process_loss_qty'))
    consumed=closure['actual_consumption'] if closure is not None else float(accounting['consumed_qty'])
    if abs(consumed-state['fg_qty']-loss)>1e-6:blockers.append('Consumed mass and FG require recovery/sample/loss reconciliation')
    if closure is not None:
        if not qc.state_for_work_order(wo)['ready']:blockers.append('Final mandatory IPQC/EOB/Quality disposition is unresolved')
        return {'ready':not blockers,'blockers':blockers,'fg_qty':state['fg_qty'],'received_qty':received,
            'consumption':closure['materials'],'recovery':recovery,'wip':wip,'final_operation_authority':final_authority,
            'accounting_independent':True}
    receipts=frappe.get_all('Stock Entry',filters={'work_order':work_order,'purpose':'Manufacture','docstatus':1},fields=['name','custom_partial_fg_lot','total_additional_costs'])
    material=conversion=ledger=adjustments=0.0
    for receipt in receipts:
        if not receipt.custom_partial_fg_lot:
            blockers.append('Legacy and partial receipts require explicit reconciliation');continue
        from calco_erp.calco_production.partial_lot_reconciliation import receipt_valuation
        valued=receipt_valuation(receipt.name)
        adjustments+=flt(valued['previous_value_adjustments'])
        material+=flt(frappe.db.get_value('Partial FG Lot',receipt.custom_partial_fg_lot,'material_value'))
        conversion+=flt(receipt.total_additional_costs)
        ledger+=flt(frappe.db.sql("select sum(stock_value_difference) from `tabStock Ledger Entry` where voucher_type='Stock Entry' and voucher_no=%s and is_cancelled=0",receipt.name)[0][0])
    if abs(material+conversion+adjustments-ledger)>0.01:blockers.append('Manufacture Stock Ledger value differs from attributed material, conversion and submitted valuation adjustments')
    if not qc.state_for_work_order(wo)['ready']:blockers.append('Final mandatory IPQC/EOB/Quality disposition is unresolved')
    from calco_erp.calco_production.partial_lot_reconciliation import audit_true_up
    audit=audit_true_up(work_order,final_measurements,lot_value_targets)
    blockers.extend(audit['blockers'])
    return {'true_up':audit,'ready':not blockers,'blockers':blockers,'fg_qty':state['fg_qty'],'received_qty':received,'consumption':{k:str(v) for k,v in accounting.items()},'recovery':recovery,'wip':wip,'valuation':{'material':material,'conversion':conversion,'previous_value_adjustments':adjustments,'receipt_ledger':ledger},'final_operation_authority':final_authority}

class PartialFGWorkOrderMixin:
    def get_status(self,status=None):
        result=super().get_status(status)
        if result=='Completed' and has_lots(self.name):
            if not final_reconciliation(self.name)['ready']:return 'In Process'
        return result
    def update_status(self,status=None):
        if status=='Closed' and has_lots(self.name):
            state=final_reconciliation(self.name)
            if not state['ready']:frappe.throw(_('Partial production reconciliation is incomplete: {0}').format('; '.join(state['blockers'])))
        return super().update_status(status)

@frappe.whitelist()
def reconcile_run(job_card):
    card=frappe.get_doc('Job Card',job_card);card.check_permission('read');lock(card.work_order)
    state=final_reconciliation(card.work_order)
    if state['ready']:
        wo=frappe.get_doc('Work Order',card.work_order);wo.check_permission('write');wo.update_status()
    return state

@frappe.whitelist()
def summary(job_card):
    card=frappe.get_doc('Job Card',job_card);card.check_permission('read');wo=frappe.get_doc('Work Order',card.work_order)
    if not wo.get(qc.SNAPSHOT) or not output.snapshots.activation(card):return {'enabled':False}
    state=fg_state(card);received=received_fg(wo.name);lots=frappe.get_all('Partial FG Lot',filters={'job_card':card.name},fields=['name','lot_batch','fg_qty','docstatus','cutoff'])
    totals={'quarantine_qty':0.0,'released_qty':0.0,'dispatched_qty':0.0}
    from calco_erp.calco_quality.doctype.final_qc_release.final_qc_release import get_fg_batch_quantity
    from calco_erp.calco_dispatch.partial_lot_dispatch import warehouse
    quarantine=warehouse(wo.company,'FG Quarantine');released=warehouse(wo.company,'FG Released')
    for lot in lots:
        if lot.docstatus!=1:continue
        totals['quarantine_qty']+=get_fg_batch_quantity(wo.production_item,lot.lot_batch,quarantine)
        totals['released_qty']+=get_fg_batch_quantity(wo.production_item,lot.lot_batch,released)
        # Batch bundle allocations are the dispatch genealogy, including returns.
        totals['dispatched_qty']+=flt(frappe.db.sql("""select -sum(b.qty) from `tabSerial and Batch Entry` b join `tabSerial and Batch Bundle` s on s.name=b.parent
            join `tabDelivery Note` d on d.name=s.voucher_no where s.voucher_type='Delivery Note' and d.docstatus=1 and b.batch_no=%s""",lot.lot_batch)[0][0])
    return {'enabled':True,'planned_qty':wo.qty,'cumulative_fg_qty':state['fg_qty'],'received_qty':received,
        'confirmed_qty':reserved_fg(wo.name),'unreceived_qty':max(state['fg_qty']-received,0),
        'balance_to_produce':max(flt(wo.qty)-state['fg_qty'],0),'parent_batch':wo.custom_fg_batch_no,
        'recovery':{k:v for k,v in state['components'].items() if k!='quantity'},'lots':lots,**totals}


def assert_output_covers_lots(work_order, rows):
    reserved=reserved_fg(work_order)
    if not reserved:return
    state=output.snapshots.reduce_snapshots(rows)
    fg=sum(flt(json.loads(r['components_json']).get('quantity')) for r in state['effective'])
    if fg+1e-6<reserved:
        frappe.throw(_('Output correction falls below confirmed partial FG lots. Production / Stock reconciliation required. Received, released or dispatched stock cannot be silently reversed; use the controlled correction and reversal lifecycle.'))


def assert_consumption_mass(item_code, fg_qty, allocations):
    unit=frappe.db.get_value('Item',item_code,'stock_uom')
    if any(frappe.db.get_value('Item',r['item_code'],'stock_uom')!=unit for r in allocations):
        frappe.throw(_('Mixed consumption stock units require an approved mass conversion before partial confirmation.'))
    if sum((cost.number(r['qty']) for r in allocations),cost.number(0))+cost.TOL<cost.number(fg_qty):
        frappe.throw(_('Actual attributable RM consumption is insufficient for the confirmed FG mass.'))


def fg_progress(card, cutoff=None):
    rows=output.readings_for(card.name)
    if cutoff:rows=[r for r in rows if get_datetime(r['reading_time'])<=get_datetime(cutoff)]
    return fg_progress_rows(rows)

def fg_progress_rows(rows):
    # Reuse correction/abandonment and logical-shift authority; substitute FG
    # component only on transient copies, never on immutable output evidence.
    fgrows=[{**r,'shift_qty':flt(json.loads(r['components_json']).get('quantity'))} for r in rows]
    state=output.snapshots.reduce_snapshots(fgrows)
    return {'current':state['current_cumulative'],'high_water':state['high_water']}
