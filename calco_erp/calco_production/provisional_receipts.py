"""Gate 2 receipt adapter. Explicit policy only; production activation stays OFF."""
import json
from decimal import Decimal
import frappe
from frappe.utils import flt,now_datetime,get_datetime
from calco_erp.calco_production import partial_fg_lots as lots,receipt_policy_infrastructure as infra,physical_completion as completion

from calco_erp.calco_production import stock_quantity_precision as precision

VERSION='provisional-receipt-v1'
POSTING=object()

def snapshot(lot):return infra.payload(lot.custom_receipt_policy_snapshot)
def number(value):return lots.cost.number(value)
def review(result,reason):
    return dict(result,status='Management Review Required',review_reason=reason,token=None)

def actual_available(wo,card,cutoff):
    rows=lots.cost.remaining_entitlements(lots.cost.actual_sources(wo.name,cutoff),lots.prior_allocations(wo.name))
    result=[]
    previous=frappe.db.get_value('Partial FG Lot',{'job_card':card.name,'docstatus':1},'cutoff',order_by='cutoff desc')
    for r in rows.values():
        if r['available_qty']<=lots.cost.TOL:continue
        source=r['source']
        entry=frappe.db.get_value('Stock Entry',source['consumption_entry'],['custom_live_consumption_job_card','creation'],as_dict=True)
        if entry.custom_live_consumption_job_card!=card.name or (previous and get_datetime(entry.creation)<=get_datetime(previous)):
            frappe.throw('Actual consumption run/interval identity requires Management review.')
        result.append(dict(source,qty=str(r['available_qty'])))
    return result

def lock_materials(wo,materials):
    lots.lock(wo.name)
    for item,batch in sorted({(r['item_code'],r['batch_no']) for r in materials}):
        frappe.db.sql('select name from `tabBatch` where name=%s for update',batch)
        frappe.db.sql('select name from `tabBin` where item_code=%s and warehouse=%s for update',(item,wo.wip_warehouse))

def wip(wo,item,batch):
    from calco_erp.inventory.availability import get_work_order_wip_lineage,get_external_wip_lineage_claims
    from erpnext.stock.doctype.batch.batch import get_batch_qty
    lineage=get_work_order_wip_lineage(wo.name).get((item,batch),{})
    issued=number(lineage.get('transferred_qty'));consumed=number(lineage.get('consumed_qty'));returned=number(lineage.get('returned_qty'))
    relieved=Decimal(0)
    for name in frappe.get_all('Stock Entry',filters={'custom_provisional_work_order':wo.name,'purpose':'Material Issue','docstatus':1},pluck='name'):
        entry=frappe.get_doc('Stock Entry',name)
        for row in entry.items:
            from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
            if row.item_code==item and get_stock_entry_row_batch_no(row)==batch:relieved+=number(row.transfer_qty)
    remaining=max(issued-consumed-returned-relieved,Decimal(0))
    foreign=get_external_wip_lineage_claims(item,wo.name).get(batch,{})
    physical=number(max(flt(get_batch_qty(batch_no=batch,warehouse=wo.wip_warehouse,item_code=item))-flt(foreign.get('quantity')),0))
    # Ledger availability and this WO's exact transfer entitlement must both cover it.
    return {'issued':str(issued),'actual_posted':str(consumed),'returned':str(returned),'provisional_posted':str(relieved),
        'available':str(min(remaining,physical)),'transfer_entries':lineage.get('transfer_entries',[]),'warehouse':wo.wip_warehouse}

def issue_document(wo,materials,lot=None):
    company=frappe.get_cached_doc('Company',wo.company)
    rows=[{'item_code':r['item_code'],'qty':r['provisional_qty'],'uom':r['stock_uom'],'stock_uom':r['stock_uom'],'conversion_factor':1,
        's_warehouse':wo.wip_warehouse,'batch_no':r['batch_no'],'use_serial_batch_fields':1,
        'expense_account':company.stock_adjustment_account,'cost_center':company.cost_center} for r in materials if number(r['provisional_qty'])>0]
    doc=frappe.get_doc({'doctype':'Stock Entry','purpose':'Material Issue','stock_entry_type':'Material Issue','company':wo.company,'items':rows})
    if lot:
        doc.update({'custom_provisional_work_order':wo.name,'custom_provisional_job_card':lot.job_card,'custom_provisional_lot':lot.name,'custom_provisional_fingerprint':lot.custom_receipt_policy_fingerprint})
        doc.flags.receipt_infrastructure_token=infra.WRITE_TOKEN;doc.flags.provisional_posting=POSTING
    return doc

def policy_plan(card,wo,qty,cutoff):
    activation=infra.activation_context(wo.name,cutoff)
    if not activation['enabled']:frappe.throw('No active approved provisional policy; Management Review Required.')
    boundary=frappe.get_doc('Production Receipt Policy Boundary',activation['boundary'])
    policy=infra.payload(boundary.policy_snapshot)
    if boundary.job_card!=card.name or boundary.policy_version!=VERSION or infra.fingerprint(policy)!=boundary.policy_fingerprint:
        frappe.throw('Approved policy identity is invalid.')
    if policy.get('method')!='explicit-item-batch-per-fg-v1':frappe.throw('No deterministic approved allocation rule. No BOM fallback is permitted.')
    form=policy.get('formulation') or {}
    if form.get('doctype') not in ('Premix Run','Feeder Run'):frappe.throw('Controlled formulation evidence is required.')
    source=frappe.get_doc(form['doctype'],form['name'])
    if source.get('job_card')!=card.name or source.get('status') in ('Draft','Superseded','Cancelled') or infra.fingerprint(source.as_dict())!=form.get('fingerprint'):
        frappe.throw('Formulation/run evidence changed; approve a new prospective policy.')
    state=lots.fg_state(card,cutoff)
    if any(flt(v) for k,v in state['components'].items() if k!='quantity'):frappe.throw('Recovery allocation requires Management review.')
    if frappe.get_cached_value('Item',wo.production_item,'stock_uom')!='Kg':frappe.throw('This policy requires explicit Kg material and FG units.')
    targets={}
    for row in policy.get('materials',[]):
        key=(row.get('item_code'),row.get('batch_no'))
        if not all(key) or key in targets or row.get('stock_uom')!='Kg' or frappe.get_cached_value('Item',key[0],'stock_uom')!='Kg':frappe.throw('Ambiguous policy material identity or units.')
        if frappe.db.get_value('Batch',key[1],'item')!=key[0]:frappe.throw('Policy batch does not belong to its RM item.')
        targets[key]=number(qty)*number(row.get('qty_per_fg'))
    if not targets or abs(sum(targets.values())-number(qty))>lots.cost.TOL:frappe.throw('Approved material rule must conserve mass; unexplained variance is not a BOM estimate.')
    actual=actual_available(wo,card,cutoff)
    if sum(number(r['qty']) for r in actual)>=number(qty):frappe.throw('Sufficient actual evidence exists but needs review; do not replace it with provisional relief.')
    for r in actual:
        key=(r['item_code'],r['batch_no'])
        if key not in targets or number(r['qty'])>targets[key]+lots.cost.TOL:frappe.throw('Actual material evidence does not fit the approved formulation interval.')
        targets[key]-=number(r['qty'])
    actual=lots.cost.allocate(lots.cost.actual_sources(wo.name,cutoff),lots.prior_allocations(wo.name),actual) if actual else []
    materials=[dict(item_code=k[0],batch_no=k[1],stock_uom='Kg',provisional_qty=precision.canonical(q),calculated_qty=precision.canonical(q)) for k,q in targets.items() if q>0]
    lock_materials(wo,materials)
    carry={}
    for name in frappe.get_all('Partial FG Lot',filters={'job_card':card.name,'docstatus':1},pluck='name'):
        previous_plan=infra.payload(frappe.db.get_value('Partial FG Lot',name,'custom_receipt_policy_snapshot') or '{}')
        if previous_plan.get('calculation_version')!=precision.VERSION or previous_plan.get('formulation_fingerprint')!=form['fingerprint']:continue
        for old in previous_plan['materials']:
            key=(old['item_code'],old['batch_no'])
            carry[key]=carry.get(key,Decimal(0))+precision.decimal(old['calculated_qty'])-precision.decimal(old['provisional_qty'])
    probe=issue_document(wo,materials)
    for row,detail in zip(materials,probe.items):
        row['evidence_source']=wip(wo,row['item_code'],row['batch_no'])
        row['precision_authority']=precision.authority(probe,detail)
        row['rounding_carry_in']=precision.canonical(carry.get((row['item_code'],row['batch_no']),0))
    materials,rounding=precision.apportion(materials,number(qty)-sum(number(r['qty']) for r in actual))
    # Native draft valuation is read-only. No estimated item/BOM rate is supplied.
    issue=issue_document(wo,materials);issue.set_missing_values()
    for row,detail in zip([r for r in materials if number(r['provisional_qty'])>0],issue.items):row['valuation_basis']={'native_outgoing_rate':str(detail.basic_rate),'value':str(detail.basic_amount),'warehouse':wo.wip_warehouse}
    for row in materials:
        row.setdefault('valuation_basis',{'native_outgoing_rate':'0','value':'0','warehouse':wo.wip_warehouse})
    previous=frappe.db.get_value('Partial FG Lot',{'job_card':card.name,'docstatus':1},'cutoff',order_by='cutoff desc')
    start=max(get_datetime(boundary.effective_from),get_datetime(previous or boundary.effective_from))
    if number(qty)>number(state['fg_qty'])-number(lots.fg_state(card,start)['fg_qty'])+lots.cost.TOL:frappe.throw('Unreceived output crosses the approved formulation boundary; Management review required.')
    return {'calculation_version':precision.VERSION,'rounding':rounding,'policy_version':VERSION,'boundary':boundary.name,'company':wo.company,'work_order':wo.name,'job_card':card.name,'parent_batch':wo.custom_fg_batch_no,
        'interval_start':str(start),'cutoff':str(cutoff),'formulation_version':form['name'],'formulation_fingerprint':form['fingerprint'],
        'policy_fingerprint':boundary.policy_fingerprint,'output_evidence':state,'materials':materials,'actual_attribution':actual,
        'classification':'Mixed' if actual else 'Provisional','fg_qty':str(qty),'clearing_account':frappe.get_cached_value('Company',wo.company,'stock_adjustment_account')}

def candidate(card,wo,result):
    if result.get('token') or not infra.enabled():return result
    if result.get('qty',0)<=0:return result
    from calco_erp.calco_production import fg_confirmation as fg
    try:
        if not result.get('can_confirm'):frappe.throw('Production receipt authority required.')
        if not frappe.has_permission('Stock Entry','submit'):frappe.throw('Management stock submission authority required for both receipt legs.')
        from frappe.model.workflow import get_workflow_name
        if get_workflow_name('Stock Entry'):frappe.throw('Existing stock approval workflow requires Management review.')
        tracking=fg.tracking(card,lots.fg_state(card))
        if not all(tracking.values()):frappe.throw('Operator and shift evidence requires review.')
        plan=policy_plan(card,wo,result['qty'],now_datetime());lots.partial_quality(wo,card,plan['cutoff'])
        basis=fg.basis(card,wo,lots.preview(card.name))
        from datetime import timedelta
        token=fg.sign({'user':frappe.session.user,'expires':str(now_datetime()+timedelta(minutes=30)),'basis':basis,'qty':result['qty'],
            'batch':result['proposed_batch'],'review_reason':'','route':VERSION,'plan':plan})
        return dict(result,status='Ready',review_reason=None,token=token,receipt_policy=VERSION)
    except frappe.ValidationError as exc:return review(result,str(exc))

def validate_lot(lot):
    infra.protect_lot(lot)
    plan=snapshot(lot);wo=frappe.get_doc('Work Order',lot.work_order);card=frappe.get_doc('Job Card',lot.job_card)
    lots.lock(wo.name)
    if (card.work_order,lot.company,lot.item_code,lot.parent_batch)!=(wo.name,wo.company,wo.production_item,wo.custom_fg_batch_no):frappe.throw('Provisional lot genealogy mismatch.')
    if wo.docstatus!=1 or wo.status in ('Stopped','Closed','Cancelled'):frappe.throw('Work Order is not executable.')
    from calco_erp.calco_production.fg_planning_authority import assert_execution_allowed
    assert_execution_allowed(wo.name)
    if lot.qc_fingerprint!=wo.get(lots.qc.FINGERPRINT):frappe.throw('Frozen QC fingerprint changed.')
    if lot.fg_qty+lots.reserved_fg(wo.name,lot.name)>min(lots.fg_state(card)['fg_qty'],flt(wo.qty))+1e-6:frappe.throw('Provisional lot exceeds produced/authorized FG.')
    actual=json.loads(lot.consumption_evidence)
    lots.cost.reconcile(lots.cost.actual_sources(wo.name,now_datetime()),lots.prior_allocations(wo.name))
    if actual:
        verified=lots.cost.validate_historical_allocation(lots.cost.actual_sources(wo.name,now_datetime()),lots.prior_allocations(wo.name,lot.name),lots.cost.actual_sources(wo.name,lot.cutoff),lots.prior_allocations(wo.name,lot.name,cutoff=lot.cutoff),actual)
        if infra.canonical(verified)!=infra.canonical(actual):
            # Reader cutoff metadata is not attribution identity.
            clean=lambda rows:[{k:v for k,v in r.items() if k!='cutoff'} for r in rows]
            if infra.canonical(clean(verified))!=infra.canonical(clean(actual)):frappe.throw('Actual-backed portion changed.')
    total=sum(number(r['qty']) for r in actual)+sum(number(r['provisional_qty']) for r in plan['materials'])
    value=lots.cost.material_value(actual)+sum(number(r['valuation_basis']['value']) for r in plan['materials'])
    if abs(total-number(lot.fg_qty))>lots.cost.TOL or abs(value-number(lot.material_value))>Decimal('0.000001'):frappe.throw('Mixed attribution quantity/value mismatch.')
    lots.partial_quality(wo,card,lot.cutoff)
    issues=frappe.get_all('Stock Entry',filters={'custom_provisional_lot':lot.name,'purpose':'Material Issue','docstatus':1},pluck='name')
    if len(issues)>1:frappe.throw('Duplicate provisional WIP relief for this lot.')
    if issues:check_ledger(frappe.get_doc('Stock Entry',issues[0]),plan)
    return plan

def check_ledger(issue,plan):
    rows=frappe.db.sql("select voucher_detail_no,item_code,actual_qty,stock_value_difference,valuation_rate from `tabStock Ledger Entry` where voucher_no=%s and is_cancelled=0",issue.name,as_dict=True)
    if plan.get('calculation_version')==precision.VERSION:
        planned={(r['item_code'],r['batch_no']):r for r in plan['materials'] if number(r['provisional_qty'])>0}
        from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
        if len(issue.items)!=len(planned):frappe.throw('Canonical provisional detail identity mismatch.')
        for detail in issue.items:
            material=planned.get((detail.item_code,get_stock_entry_row_batch_no(detail)))
            if not material:frappe.throw('Canonical provisional batch identity mismatch.')
            p=material['posting_precision'];qty=material['postable_qty']
            precision.require_exact(detail.transfer_qty,qty,p)
            precision.require_exact(detail.qty,qty,p)
            entries=frappe.get_all('Serial and Batch Entry',filters={'parent':detail.serial_and_batch_bundle},fields=['batch_no','qty'])
            if not entries or any(e.batch_no!=material['batch_no'] for e in entries):frappe.throw('Canonical bundle batch mismatch.')
            precision.require_exact(sum(abs(precision.decimal(e.qty)) for e in entries),qty,p)
            movement=sum(-precision.decimal(r.actual_qty) for r in rows if r.voucher_detail_no==detail.name)
            precision.require_exact(movement,qty,p)
    if abs(sum(-flt(r.actual_qty) for r in rows)-sum(flt(r['provisional_qty']) for r in plan['materials']))>1e-6:frappe.throw('Provisional stock quantity does not reconcile.')
    if abs(sum(-flt(r.stock_value_difference) for r in rows)-sum(flt(r['valuation_basis']['value']) for r in plan['materials']))>0.000001:frappe.throw('Native WIP valuation changed before posting. Recalculate the receipt.')

def verify_accounts(issue,receipt,plan):
    names=[issue.name,receipt.name]+list({r['consumption_entry'] for r in plan['actual_attribution']})
    # Each actual source is exhausted by this route; no whole-source costs are reused.
    rows=frappe.db.sql('select account,sum(debit-credit) as net from `tabGL Entry` where voucher_no in %(names)s and is_cancelled=0 group by account',{'names':tuple(names)},as_dict=True)
    for r in rows:
        root=frappe.get_cached_value('Account',r.account,'root_type')
        if root in ('Income','Expense') and abs(flt(r.net))>0.01:frappe.throw('Gate 2 accounting stop: unintended expense/income remains. No balancing customization is permitted.')

def require_posting():
    if not infra.enabled():frappe.throw('Provisional receipts remain disabled.')
    if not lots.ROLES.intersection(frappe.get_roles()):frappe.throw('Production authority required.',frappe.PermissionError)
    frappe.has_permission('Stock Entry','submit',throw=True)

def receive(data,note=''):
    require_posting();lots.lock(data['basis']['work_order'])
    from calco_erp.calco_production import fg_confirmation as fg
    existing=frappe.db.get_value('Partial FG Lot',{'lot_batch':data['batch']},'name')
    if existing:
        lot=frappe.get_doc('Partial FG Lot',existing);lot.check_permission('read')
        if lot.docstatus!=1 or lot.job_card!=data['basis']['job_card'] or snapshot(lot)!=data['plan']:frappe.throw('Receipt changed or was cancelled.')
        return fg.receipt_result(lot)
    card,wo=lots.context(data['basis']['job_card']);card.check_permission('write')
    current=fg.basis(card,wo,lots.preview(card.name))
    if infra.canonical(current)!=infra.canonical(data['basis']):frappe.throw('Receipt evidence changed. Refresh Receive FG.')
    plan=policy_plan(card,wo,data['qty'],data['plan']['cutoff'])
    if infra.canonical(plan)!=infra.canonical(data['plan']):frappe.throw('Provisional policy/WIP evidence changed. Refresh Receive FG.')
    lock_materials(wo,plan['materials']);lots.partial_quality(wo,card,plan['cutoff'])
    batch=frappe.get_doc({'doctype':'Batch','item':wo.production_item,'batch_id':data['batch']}).insert()
    value=lots.cost.material_value(plan['actual_attribution'])+sum(number(r['valuation_basis']['value']) for r in plan['materials'])
    lot=frappe.get_doc({'doctype':'Partial FG Lot','job_card':card.name,'work_order':wo.name,'company':wo.company,'item_code':wo.production_item,'parent_batch':wo.custom_fg_batch_no,'lot_batch':batch.name,'fg_qty':data['qty'],'cutoff':plan['cutoff'],
        'output_evidence':json.dumps(plan['output_evidence']),'consumption_evidence':json.dumps(plan['actual_attribution']),'material_value':float(value),'confirmed_by':frappe.session.user,'confirmed_on':now_datetime(),'confirmation_reason':note,'qc_fingerprint':wo.get(lots.qc.FINGERPRINT),
        'custom_receipt_policy_version':VERSION,'custom_receipt_policy_boundary':plan['boundary'],'custom_receipt_policy_snapshot':infra.canonical(plan),'custom_receipt_policy_fingerprint':infra.fingerprint(plan)})
    lot.flags.receipt_infrastructure_token=infra.WRITE_TOKEN
    previous=frappe.flags.creating_partial_fg_lot;frappe.flags.creating_partial_fg_lot=True
    try:lot.insert();lot.submit()
    finally:frappe.flags.creating_partial_fg_lot=previous
    issue=issue_document(wo,plan['materials'],lot);issue.insert();issue.submit();check_ledger(issue,plan)
    # Existing builder creates the FG-only standard Manufacture. Metadata is set
    # before insert by the dedicated hook, not patched after stock posting.
    old=frappe.flags.provisional_issue;frappe.flags.provisional_issue=issue.name
    try:receipt=frappe.get_doc('Stock Entry',lots.make_manufacture(lot.name,**data['basis']['tracking'])['name']);receipt.flags.provisional_posting=POSTING;receipt.submit()
    finally:frappe.flags.provisional_issue=old
    verify_accounts(issue,receipt,plan)
    return fg.receipt_result(lot)

def bind_manufacture(doc,method=None):
    if not doc.is_new() or not doc.get('custom_partial_fg_lot'):return
    lot=frappe.get_doc('Partial FG Lot',doc.custom_partial_fg_lot)
    if not lot.custom_receipt_policy_version:return
    issue=frappe.flags.provisional_issue
    if not issue:frappe.throw('Use the linked provisional receipt operation.')
    doc.update({'custom_provisional_work_order':lot.work_order,'custom_provisional_job_card':lot.job_card,'custom_provisional_lot':lot.name,'custom_provisional_wip_entry':issue,'custom_provisional_fingerprint':lot.custom_receipt_policy_fingerprint})
    doc.flags.receipt_infrastructure_token=infra.WRITE_TOKEN

def validate_issue(doc,method=None):
    if not doc.get('custom_provisional_lot') or doc.purpose!='Material Issue':return
    lot=frappe.get_doc('Partial FG Lot',doc.custom_provisional_lot);plan=validate_lot(lot)
    if getattr(doc,'_action',None)=='submit' and doc.flags.get('provisional_posting') is not POSTING:frappe.throw('Submit both provisional receipt legs through Receive FG.')
    expected={(r['item_code'],r['batch_no']):number(r['provisional_qty']) for r in plan['materials'] if number(r['provisional_qty'])>0}
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
    found={}
    for r in doc.items:
        key=(r.item_code,get_stock_entry_row_batch_no(r))
        if key in found or r.s_warehouse!=frappe.get_cached_value('Work Order',lot.work_order,'wip_warehouse') or r.t_warehouse or r.expense_account!=plan['clearing_account']:frappe.throw('Provisional issue item/warehouse/clearing lineage mismatch.')
        found[key]=number(r.transfer_qty)
    if plan.get('calculation_version')==precision.VERSION:
        for row in plan['materials']:
            key=(row['item_code'],row['batch_no'])
            precision.require_exact(found.get(key,0),row['postable_qty'],row['posting_precision'])
    if found!=expected:frappe.throw('Provisional issue must match the frozen uncovered quantity exactly.')

def before_cancel(doc,method=None):
    if doc.get('custom_provisional_lot') and doc.purpose=='Material Issue':
        if frappe.db.exists('Stock Entry',{'custom_partial_fg_lot':doc.custom_provisional_lot,'purpose':'Manufacture','docstatus':1}):frappe.throw('Reverse the linked Manufacture and downstream records before WIP relief.')

def after_cancel(doc,method=None):
    if not doc.get('custom_provisional_lot') or doc.purpose!='Manufacture':return
    issue=frappe.get_doc('Stock Entry',doc.custom_provisional_wip_entry)
    if issue.docstatus==1:issue.check_permission('cancel');issue.cancel()
