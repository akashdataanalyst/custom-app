"""Physical handover adapter. No stock on submission; receipt engines are reused."""
import hashlib
import json
import math
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.utils import flt, now_datetime, nowdate
from calco_erp.calco_production import partial_fg_lots as lots, fg_confirmation as fg
from calco_erp.calco_production import physical_completion as physical

VERSION = 'fg-handover-v1'
WRITE = object()
PRODUCTION = lots.ROLES
MANAGERS = {'Production Head', 'Manufacturing Manager'}
QUALITY = {'Quality Calco', 'Quality User', 'Quality Manager'}
STORES = {'Stock User', 'Stock Manager'}
COMPONENTS = {'prime_fg_qty':'quantity', 'loose_qty':'loose_quantity', 'spy_qty':'spy_qty',
              'tpy_qty':'tpy_qty', 'samples_qty':'lab_samples', 'metal_separator_qty':'metal_separator_qty', 'others_qty':'other_new_output'}
SERVER = ('work_order','company','item_code','grade_name','parent_batch','handover_date','status',
          'handover_version','output_cutoff','output_evidence','output_fingerprint',
          'production_by','production_on','qa_by','qa_on','qa_observation','stores_by','stores_on',
          'partial_fg_lot','fg_batch','manufacture_entry','audit_events')
FROZEN = ('compounding_job_card','job_card','bags','kg_per_bag',*COMPONENTS,'others_reason','remarks','is_final',
          'prime_fg_qty','total_output_qty',*SERVER)


def canonical(value):
    return json.dumps(value,sort_keys=True,default=str,separators=(',',':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def authority(roles):
    from calco_erp.calco_production.receipt_policy_infrastructure import SITE
    if not production_site_allowed():frappe.throw('FG handover requires the configured manufacturing site.')
    if not roles.intersection(frappe.get_roles()):
        frappe.throw('Authorized FG handover role required.',frappe.PermissionError)


def calculate(values):
    result={}
    for key in ('bags','kg_per_bag',*COMPONENTS):
        try:value=float(values.get(key) or 0)
        except (ValueError,TypeError):frappe.throw('Handover quantities must be numeric.')
        if not math.isfinite(value) or value<0:frappe.throw('Handover quantities must be finite and non-negative.')
        result[key]=value
    if result['bags']!=int(result['bags']):frappe.throw('No. of Bags must be a whole number.')
    if result['bags'] and result['kg_per_bag']<=0:frappe.throw('Kg per Bag must be positive.')
    result['prime_fg_qty']=round(result['bags']*result['kg_per_bag'],6)
    result['total_output_qty']=round(sum(result[k] for k in COMPONENTS),6)
    if result['others_qty'] and not (values.get('others_reason') or '').strip():frappe.throw('Others requires an evidence description.')
    return result


def protect(doc):
    old=doc.get_doc_before_save()
    internal=doc.flags.fg_handover_write is WRITE
    if old and old.compounding_job_card and not doc.compounding_job_card:
        frappe.throw('Controlled handover cannot be changed into a legacy note.')
    if not doc.compounding_job_card:return
    if old and old.docstatus==1:
        for field in FROZEN:
            if internal and field in SERVER:continue
            if canonical(old.get(field))!=canonical(doc.get(field)):
                frappe.throw('Submitted FG handover evidence is immutable. Use controlled cancellation/amendment after downstream reversal.')
    if not internal:
        for field in SERVER:
            if not old and field in ('work_order','company','item_code','grade_name','parent_batch','handover_date'):continue
            if canonical(doc.get(field) or None)!=canonical(old.get(field) or None if old else None):
                if not old and field=='status' and doc.get(field)=='Draft':continue
                frappe.throw('FG handover lineage and acknowledgements are server controlled: '+field)


def balances(card,exclude=None):
    state=lots.fg_state(card)
    values=dict(state['components'])
    # Optional Others can be used only where the controlled source actually records it.
    values['other_new_output']=sum(flt(json.loads(frappe.db.get_value('Shift Production Output Reading',n,'components_json') or '{}').get('other_new_output')) for n in state['snapshots'])
    notes=frappe.get_all('FG Delivery Note',filters={'compounding_job_card':card.name,'docstatus':1},fields=['name','partial_fg_lot',*COMPONENTS])
    used={k:0.0 for k in COMPONENTS}
    linked=set()
    for row in notes:
        if row.name==exclude:continue
        for k in used:used[k]+=flt(row.get(k))
        if row.partial_fg_lot:linked.add(row.partial_fg_lot)
    # Earlier receipts retain their interpretation; they cannot be handed over again.
    for row in frappe.get_all('Partial FG Lot',filters={'job_card':card.name,'docstatus':1},fields=['name','fg_qty']):
        if row.name not in linked:
            own=frappe.db.get_value('FG Delivery Note',exclude,'partial_fg_lot') if exclude else None
            if row.name!=own:used['prime_fg_qty']+=flt(row.fg_qty)
    return state,{k:flt(values.get(source))-used[k] for k,source in COMPONENTS.items()}


def assert_available(doc,card):
    state,available=balances(card,doc.name)
    if not state['snapshots']:frappe.throw('Saved controlled Shift Report output is required.')
    for key in COMPONENTS:
        if flt(doc.get(key))>available[key]+1e-6:
            frappe.throw(f'{doc.meta.get_label(key)} exceeds unhanded Shift Report evidence. Production / Stores review required.')
    if doc.is_final and not physical.end_event(card):frappe.throw('Confirm Physical End before the final handover.')
    return state


def validate(doc):
    protect(doc)
    if not doc.compounding_job_card:
        if not doc.job_card:frappe.throw('Select a Compounding Job Card.')
        from calco_erp.calco_production.production_execution import FGDeliveryNoteMixin
        FGDeliveryNoteMixin.validate(doc)
        return
    if doc.job_card:frappe.throw('A handover cannot mix legacy and standard Job Cards.')
    if doc.docstatus==2:return
    if doc.get_doc_before_save() and doc.get_doc_before_save().docstatus==1:return
    authority(PRODUCTION)
    card,wo=lots.context(doc.compounding_job_card);lots.lock(wo.name)
    card,wo=lots.context(doc.compounding_job_card);card.check_permission('write')
    if frappe.db.get_value('Item',wo.production_item,'stock_uom')!='Kg':frappe.throw('This handover form requires FG stock UOM Kg.')
    doc.update(calculate(doc))
    doc.update(dict(work_order=wo.name,company=wo.company,item_code=wo.production_item,
               grade_name=frappe.db.get_value('Item',wo.production_item,'item_name'),parent_batch=wo.custom_fg_batch_no,
               handover_date=(doc.get_doc_before_save().handover_date if doc.get_doc_before_save() else nowdate()),handover_version=VERSION,status='Draft'))
    assert_available(doc,card)
    if doc.amended_from:
        previous=frappe.get_doc('FG Delivery Note',doc.amended_from)
        if previous.docstatus!=2 or previous.compounding_job_card!=card.name:frappe.throw('Invalid handover correction lineage.')
        if not (doc.correction_reason or '').strip():frappe.throw('Correction reason is required.')


def event(doc,action,reason=''):
    events=json.loads(doc.audit_events or '[]')
    events.append({'action':action,'by':frappe.session.user,'on':str(now_datetime()),'reason':reason,
                   'note':doc.name,'original':doc.amended_from,'lot':doc.partial_fg_lot})
    doc.audit_events=canonical(events)


def before_submit(doc):
    if not doc.compounding_job_card:frappe.throw('Legacy FG notes retain their original saved-document workflow.')
    authority(PRODUCTION);lots.lock(doc.work_order)
    card,wo=lots.context(doc.compounding_job_card)
    state=assert_available(doc,card)
    if doc.total_output_qty<=0:frappe.throw('A positive evidenced handover quantity is required.')
    doc.output_cutoff=now_datetime();doc.output_evidence=canonical(state)
    doc.output_fingerprint=fingerprint(state)
    doc.production_by=frappe.session.user;doc.production_on=now_datetime();doc.status='Production Submitted'
    event(doc,'Production Submitted',doc.correction_reason or '')


def before_cancel(doc):
    protect(doc)
    if not doc.compounding_job_card:return
    authority(MANAGERS);lots.lock(doc.work_order)
    if not (doc.correction_reason or '').strip():frappe.throw('Cancellation reason is required.')
    if doc.partial_fg_lot and frappe.db.get_value('Partial FG Lot',doc.partial_fg_lot,'docstatus')!=2:
        frappe.throw('Reverse the existing receipt and cancel its Partial FG Lot through the controlled stock/QC workflow first. No stock will be reversed here.')
    doc.status='Cancelled';event(doc,'Cancelled',doc.correction_reason)


def load_locked(name):
    doc=frappe.get_doc('FG Delivery Note',name);doc.check_permission('read')
    lots.lock(doc.work_order);doc.reload()
    if not doc.compounding_job_card or doc.docstatus!=1:frappe.throw('A submitted controlled FG Delivery Note is required.')
    return doc


def save_action(doc):
    doc.flags.fg_handover_write=WRITE
    # Role-specific endpoint is authority; no generic write permission is granted to QA/Stores.
    doc.save(ignore_permissions=True)


@frappe.whitelist()
@physical.atomic
def acknowledge(name,action,observation=''):
    authority(QUALITY if action=='QA Accepted' else STORES if action=='Stores Received' else set())
    doc=load_locked(name)
    if doc.status=='Reversal Pending':frappe.throw('Handover reversal is pending.')
    if action=='QA Accepted':
        if doc.qa_by:return {'name':doc.name,'status':doc.status}
        if not (observation or '').strip():frappe.throw('QA handover observation is required. This acknowledgement does not pass the lot QI.')
        doc.qa_by=frappe.session.user;doc.qa_on=now_datetime();doc.qa_observation=observation;doc.status=action
    else:
        if doc.stores_by:return {'name':doc.name,'status':doc.status}
        if doc.status=='Reversal Pending':frappe.throw('Handover reversal is pending.')
        if not doc.qa_by:frappe.throw('QA handover acceptance is required first.')
        if doc.prime_fg_qty>0:
            if not doc.partial_fg_lot or not fg.receipt_result(frappe.get_doc('Partial FG Lot',doc.partial_fg_lot))['received']:
                frappe.throw('Receive FG into Quarantine before Stores acknowledgement.')
        doc.stores_by=frappe.session.user;doc.stores_on=now_datetime();doc.status=action
    event(doc,action,observation);save_action(doc)
    return {'name':doc.name,'status':doc.status}


@frappe.whitelist()
def receipt_preview(name,selection=None,reason=None):
    authority(PRODUCTION);doc=load_locked(name)
    if doc.status=='Reversal Pending':frappe.throw('Handover reversal is pending.')
    if doc.partial_fg_lot:
        return {'status':'Receipt already prepared','existing':fg.receipt_result(frappe.get_doc('Partial FG Lot',doc.partial_fg_lot)),'token':None}
    if not doc.qa_by:frappe.throw('QA handover acceptance is required before Receive FG. Lot-specific QI remains downstream.')
    card,wo=lots.context(doc.compounding_job_card);assert_available(doc,card)
    if doc.prime_fg_qty<=0:return {'status':'No packed FG to receive','token':None}
    result,_=fg.proposal(card.name,doc.prime_fg_qty,selection,reason)
    result.update(delivery_note=doc.name,destination='FG Quarantine',qty=doc.prime_fg_qty)
    if result.get('token'):
        data=fg.decode(result['token']);data.update(handover=doc.name,handover_fingerprint=doc.output_fingerprint,purpose=VERSION)
        result['token']=fg.sign(data)
    return result


def receipt_guard(data):
    """Prevent stale/legacy receipt tokens from bypassing a submitted handover."""
    job=(data.get('basis') or {}).get('job_card')
    if not job:frappe.throw('Receipt token is missing its controlled Job Card lineage.')
    if not frappe.db.exists('FG Delivery Note',{'compounding_job_card':job,'docstatus':1}):return
    if not data.get('handover') or data.get('purpose')!=VERSION:
        frappe.throw('Receive FG from the submitted FG Delivery Note for this run.')
    doc=load_locked(data['handover'])
    if doc.compounding_job_card!=job or doc.work_order!=data['basis']['work_order'] or doc.output_fingerprint!=data.get('handover_fingerprint') or abs(doc.prime_fg_qty-flt(data['qty']))>1e-6:
        frappe.throw('FG Delivery Note quantity or lineage changed.')
    if not doc.qa_by:frappe.throw('QA handover acceptance is required.')


@frappe.whitelist()
@physical.atomic
def receive(token):
    authority(PRODUCTION);data=fg.decode(token)
    if data.get('purpose')!=VERSION:frappe.throw('Open Receive FG from its delivery note.')
    doc=load_locked(data['handover']);receipt_guard(data)
    if doc.status=='Reversal Pending':frappe.throw('Handover reversal is pending.')
    if doc.partial_fg_lot:return fg.receipt_result(frappe.get_doc('Partial FG Lot',doc.partial_fg_lot))
    card,wo=lots.context(doc.compounding_job_card);assert_available(doc,card)
    previous=frappe.flags.fg_handover_receipt
    frappe.flags.fg_handover_receipt={'token':WRITE,'name':doc.name,'qty':doc.prime_fg_qty,'job_card':doc.compounding_job_card}
    try:
        if data.get('route')=='provisional-receipt-v1':
            from calco_erp.calco_production.provisional_receipts import receive as post
            result=post(data,'FG Delivery Note '+doc.name)
        else:result=fg.confirm_production(token,'FG Delivery Note '+doc.name)
    finally:frappe.flags.fg_handover_receipt=previous
    doc.partial_fg_lot=result['lot'];doc.fg_batch=result['batch'];doc.manufacture_entry=result['stock_entry']
    event(doc,'Receipt prepared');save_action(doc)
    return result


@frappe.whitelist()
def context(job_card):
    card=frappe.get_doc('Job Card',job_card);card.check_permission('read')
    notes=frappe.get_all('FG Delivery Note',filters={'compounding_job_card':job_card},fields=['name','docstatus','status','prime_fg_qty','total_output_qty','partial_fg_lot','fg_batch','is_final'],order_by='creation')
    delivered=received=0
    for row in notes:
        row['quality_inspections']=[];row['releases']=[]
        if row.docstatus==1:delivered+=flt(row.prime_fg_qty)
        if row.partial_fg_lot:
            receipt=fg.receipt_result(frappe.get_doc('Partial FG Lot',row.partial_fg_lot));row['receipt']=receipt
            if row.docstatus==1 and receipt['received']:received+=flt(receipt['qty'])
            row['releases']=frappe.get_all('Final QC Release',filters={'batch_no':row.fg_batch},fields=['name','status','docstatus'])
            # QI identity is obtained through the existing lot-specific authority, not handover QA acknowledgement.
            row['quality_inspections']=frappe.get_all('Quality Inspection',filters={'reference_type':'Stock Entry','reference_name':receipt['stock_entry'],'batch_no':row.fg_batch},fields=['name','status','docstatus']) if receipt['stock_entry'] else []
    return {'notes':notes,'delivered_fg':delivered,'received_fg':received,
        'can_create':frappe.db.get_value('Work Order',card.work_order,'status') not in ('Completed','Stopped','Closed','Cancelled') and card.docstatus!=2}


def protect_new_lot(doc,method=None):
    if not doc.is_new():return
    lots.lock(doc.work_order)
    if not frappe.db.exists('FG Delivery Note',{'compounding_job_card':doc.job_card,'docstatus':1}):return
    value=frappe.flags.fg_handover_receipt or {}
    if value.get('token') is not WRITE or value.get('job_card')!=doc.job_card or abs(flt(value.get('qty'))-flt(doc.fg_qty))>1e-6:
        frappe.throw('Create this production lot through Receive FG on its FG Delivery Note.')


@frappe.whitelist()
@physical.atomic
def authorize_reversal(name,reason):
    authority(MANAGERS)
    if not (reason or '').strip():frappe.throw('A controlled reversal reason is required.')
    doc=load_locked(name)
    if doc.status=='Reversal Pending':return {'name':doc.name,'status':doc.status}
    doc.status='Reversal Pending';event(doc,'Reversal authorized',reason);save_action(doc)
    return {'name':doc.name,'status':doc.status,'message':'Use existing downstream QC/stock reversal workflows. Nothing has been reversed. Cancel and amend the note only after its lot is cancelled.'}


def referenced_reversal(doc,method=None):
    field='manufacture_entry' if doc.doctype=='Stock Entry' else 'partial_fg_lot'
    notes=frappe.get_all('FG Delivery Note',filters={field:doc.name,'docstatus':1},fields=['name','status','audit_events'])
    if not notes:return
    for row in notes:
        if row.status!='Reversal Pending' or not any(e.get('action')=='Reversal authorized' and e.get('reason') for e in json.loads(row.audit_events or '[]')):
            frappe.throw('Authorize controlled reversal on FG Delivery Note '+row.name+' before reversing its receipt/lot.')
    # Only the handover back-reference is retained as audit. Native downstream
    # QI, release, delivery and accounting cancellation checks remain in force.
    doc.ignore_linked_doctypes=tuple(set(doc.get('ignore_linked_doctypes') or ())|{'FG Delivery Note'})


def on_trash(doc):
    if doc.compounding_job_card and (doc.production_on or doc.audit_events):
        frappe.throw('Submitted handover evidence must be retained. Use controlled cancellation/amendment, not deletion.')


@frappe.whitelist()
def entry_context(job_card):
    """Read-only defaults for the handover form; Save revalidates all lineage."""
    authority(PRODUCTION)
    card,wo=lots.context(job_card)
    card.check_permission('read');wo.check_permission('read')
    return dict(work_order=wo.name,company=wo.company,item_code=wo.production_item,
                grade_name=frappe.db.get_value('Item',wo.production_item,'item_name'),
                parent_batch=wo.custom_fg_batch_no,handover_date=nowdate())
