"""One production confirmation; existing lot, costing and stock authorities remain final."""
import base64
import hashlib
import hmac
import json
import math
from datetime import timedelta

import frappe
from frappe.utils import flt, now_datetime, get_datetime
from calco_erp.calco_production import partial_fg_lots as lots, physical_completion as completion


def head():
    if 'Production Head' not in frappe.get_roles():
        frappe.throw('Production Head authority is required to review consumption attribution.',frappe.PermissionError)


def sign(data):
    from frappe.utils.password import get_encryption_key
    raw=json.dumps(data,sort_keys=True,default=str).encode()
    return base64.urlsafe_b64encode(raw).decode()+'.'+hmac.new(get_encryption_key().encode(),raw,hashlib.sha256).hexdigest()


def decode(token):
    from frappe.utils.password import get_encryption_key
    try:
        body,signature=token.split('.')
        raw=base64.urlsafe_b64decode(body)
        expected=hmac.new(get_encryption_key().encode(),raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        data=json.loads(raw)
        if data['user']!=frappe.session.user or now_datetime()>get_datetime(data['expires']):raise ValueError()
        return data
    except (ValueError,KeyError,TypeError,AttributeError):
        frappe.throw('Confirmation preview expired or changed. Open Receive FG again.')


def auto_selection(qty,available,rows,recovery):
    if not math.isfinite(qty) or not math.isfinite(available) or qty<=0 or qty>available+1e-6:
        return None,'Enter a positive quantity within the unconfirmed physical FG.'
    if abs(qty-available)>1e-6:
        return None,'The reduced FG quantity needs a reviewed consumption boundary; no proportional RM allocation is assumed.'
    if any(flt(v) for k,v in recovery.items() if k!='quantity'):
        return None,'Recovery/reclassification evidence needs a reviewed, non-overlapping lot attribution.'
    selected=[{**r,'qty':r['available_qty']} for r in rows if flt(r['available_qty'])>1e-6]
    if not selected or sum(flt(r['qty']) for r in selected)+1e-6<qty:
        return None,'Submitted actual consumption is missing or insufficient for this FG quantity.'
    return selected,None


def tracking(card,state):
    from calco_erp.machine_setup import OPERATOR_FIELD,SHIFT_FIELD
    operator=card.get(OPERATOR_FIELD)
    if not operator:
        employees={r.employee for r in card.employee if r.employee}
        if len(employees)==1:operator=next(iter(employees))
    shift=card.get(SHIFT_FIELD)
    if not shift and state['snapshots']:
        report=frappe.db.get_value('Shift Production Output Reading',state['snapshots'][-1],'parent')
        shift=frappe.db.get_value('Shift Report',report,'shift')
    return {'operator':operator,'shift':shift}


def basis(card,wo,s):
    state=lots.fg_state(card)
    return {'job_card':card.name,'work_order':wo.name,'item':wo.production_item,'planned_qty':flt(wo.qty),
        'parent_batch':s['parent_batch'],'output':state,'confirmed_qty':s['confirmed_qty'],'received_qty':s['received_qty'],
        'consumption':[{k:v for k,v in r.items() if k!='cutoff'} for r in s['consumption']],
        'tracking':tracking(card,state),'qc_fingerprint':wo.get(lots.qc.FINGERPRINT)}


def proposal(job_card,qty=None,selection=None,review_reason=None):
    card,wo=lots.context(job_card);lots.lock(wo.name)
    card,wo=lots.context(job_card);s=lots.preview(job_card)
    amount=flt(s['available_to_confirm'] if qty is None else qty)
    available=flt(s['available_to_confirm'])
    selected,reason=auto_selection(amount,available,s['consumption'],s['recovery'])
    b=basis(card,wo,s)
    if s['received_qty']>s['confirmed_qty']+1e-6:reason='Existing receipt/confirmation lineage requires review.'
    # A source from a different/unknown run or left over from an earlier cutoff
    # is not silently treated as consumption of this new production lot.
    previous=frappe.db.get_value('Partial FG Lot',{'work_order':wo.name,'docstatus':1},'cutoff',order_by='cutoff desc')
    for row in s['consumption']:
        if flt(row['available_qty'])<=1e-6:continue
        source=frappe.db.get_value('Stock Entry',row['consumption_entry'],['custom_live_consumption_job_card','creation'],as_dict=True)
        if source.custom_live_consumption_job_card!=card.name:
            reason='Consumption run identity requires review.'
        if previous and get_datetime(source.creation)<=get_datetime(previous):
            reason='Unallocated consumption from an earlier lot cutoff requires review.'
    if selection is not None:
        head()
        if not (review_reason or '').strip():frappe.throw('Consumption attribution review reason is required.')
        selected=frappe.parse_json(selection);reason=None
    if not b['tracking']['operator'] or not b['tracking']['shift']:
        reason='Production operator/shift evidence requires review. Resolve the Job Card tracking authority first.'
    if amount<=0 or amount>available+1e-6:reason='No eligible unconfirmed FG quantity at this amount.'
    if selected and not reason:
        messages=list(frappe.local.message_log or [])
        try:
            allocated=lots.cost.allocate(lots.cost.actual_sources(wo.name,now_datetime()),lots.prior_allocations(wo.name),selected)
            lots.assert_consumption_mass(wo.production_item,amount,allocated)
        except frappe.ValidationError as exc:
            reason='Consumption quantity, unit or valuation needs review: '+str(exc)
        finally:frappe.local.message_log=messages
    proposed=wo.custom_fg_batch_no+'-L-'+frappe.generate_hash(length=8)
    result={'item':wo.production_item,'grade':frappe.db.get_value('Item',wo.production_item,'item_name'),
        'planned_qty':flt(wo.qty),'cumulative_fg_qty':s['cumulative_fg_qty'],'confirmed_qty':s['confirmed_qty'],
        'received_qty':s['received_qty'],'available_qty':available,'qty':amount,
        'type':'Final' if completion.ended(card) and abs(amount-available)<1e-6 else 'Partial',
        'proposed_batch':proposed,'status':'Consumption requires review' if reason else 'Ready',
        'review_reason':reason,'can_review':'Production Head' in frappe.get_roles(),'token':None}
    if not reason:
        result['token']=sign({'user':frappe.session.user,'expires':str(now_datetime()+timedelta(minutes=30)),
            'basis':b,'qty':amount,'selection':selected,'batch':proposed,'review_reason':review_reason or ''})
    result['can_confirm']=all(frappe.has_permission(dt,perm) for dt,perm in [('Batch','create'),('Partial FG Lot','create'),('Partial FG Lot','submit'),('Stock Entry','create')])
    if selection is None:
        from calco_erp.calco_production.provisional_receipts import candidate
        result=candidate(card,wo,result)
    return result,s


@frappe.whitelist()
def preview(job_card,qty=None):
    return proposal(job_card,qty)[0]


@frappe.whitelist()
def review_preview(job_card,qty=None):
    head();result,state=proposal(job_card,qty)
    result['rows']=state['consumption']
    return result


@frappe.whitelist()
def reviewed_proposal(job_card,qty,selection,reason):
    head();return proposal(job_card,qty,selection,reason)[0]


def receipt_result(lot):
    from frappe.model.workflow import get_workflow_name
    workflow=get_workflow_name('Stock Entry')
    entry=frappe.db.get_value('Stock Entry',{'custom_partial_fg_lot':lot.name,'docstatus':('!=',2)},['name','docstatus'],as_dict=True)
    return {'lot':lot.name,'batch':lot.lot_batch,'qty':lot.fg_qty,'stock_entry':entry.name if entry else None,
        'received':bool(entry and entry.docstatus==1),'status':'FG Quarantine' if entry and entry.docstatus==1 else 'Review & Confirm',
        'can_submit':bool(entry and not workflow and frappe.has_permission('Stock Entry','submit',doc=entry.name)),
        'workflow':workflow,
        'can_quality':bool(entry and entry.docstatus==1 and frappe.has_permission('Quality Inspection','create'))}


@frappe.whitelist()
@completion.atomic
def confirm_production(token,note='',review_before_submit=0):
    data=decode(token);b=data['basis']
    from calco_erp.calco_production.fg_handover import receipt_guard
    receipt_guard(data)
    if not lots.ROLES.intersection(frappe.get_roles()):frappe.throw('Production authority required.',frappe.PermissionError)
    if data['review_reason']:head()
    lots.lock(b['work_order'])
    existing=frappe.db.get_value('Partial FG Lot',{'lot_batch':data['batch']},'name')
    if existing:
        lot=frappe.get_doc('Partial FG Lot',existing);lot.check_permission('read')
        if lot.docstatus!=1 or lot.job_card!=b['job_card'] or lot.work_order!=b['work_order'] or lot.confirmed_by!=data['user'] or abs(lot.fg_qty-data['qty'])>1e-6:
            frappe.throw('Confirmation has changed or was cancelled. Open a fresh preview.')
        return receipt_result(lot)
    card,wo=lots.context(b['job_card']);card.check_permission('write')
    fresh=basis(card,wo,lots.preview(card.name))
    if json.dumps(fresh,sort_keys=True,default=str)!=json.dumps(b,sort_keys=True,default=str):
        frappe.throw('Production or consumption evidence changed. Refresh Receive FG before receiving.')
    frappe.has_permission('Stock Entry','create',throw=True)
    reason=('Consumption attribution reviewed: '+data['review_reason']+chr(10) if data['review_reason'] else '')+(note or '')
    result=lots.confirm(card.name,data['qty'],data['selection'],reason,proposed_batch=data['batch'])
    lot=frappe.get_doc('Partial FG Lot',result['name'])
    entry=frappe.get_doc('Stock Entry',lots.make_manufacture(lot.name,**b['tracking'])['name'])
    from frappe.model.workflow import get_workflow_name
    # Existing workflow/submit authority is never bypassed by the convenience action.
    if not int(review_before_submit or 0) and entry.has_permission('submit') and not get_workflow_name('Stock Entry'):
        entry.submit()
    return receipt_result(lot)


@frappe.whitelist()
@completion.atomic
def submit_receipt(lot_name):
    lot=frappe.get_doc('Partial FG Lot',lot_name);lot.check_permission('read');lots.lock(lot.work_order)
    result=receipt_result(lot)
    if not result['stock_entry']:frappe.throw('The receipt requires advanced recovery review.')
    entry=frappe.get_doc('Stock Entry',result['stock_entry']);entry.check_permission('submit')
    from frappe.model.workflow import get_workflow_name
    if get_workflow_name('Stock Entry'):frappe.throw('Use the existing Stock Entry approval workflow for this receipt.')
    if entry.docstatus==0:entry.submit()
    return receipt_result(lot)


@frappe.whitelist()
def status(job_card):
    card=frappe.get_doc('Job Card',job_card);card.check_permission('read')
    if not completion.controlled(card):return {'enabled':False}
    state=completion.final_state(card)
    # Physical end, an open interval and future EOB are normal while running.
    pending=state['blockers'] if completion.ended(card) else [b for b in state['blockers'] if not (
        b in {'Confirm Physical End first','Open execution intervals remain'} or b.startswith('Mandatory IPQC/EOB') or b.startswith('Existing short-production'))]
    return {'enabled':True,'ended':completion.ended(card),'complete':state['ready'],
        'message':'Production reconciliation complete' if state['ready'] else 'Production exceptions require review' if completion.ended(card) else 'Production reconciliation updates automatically',
        'exceptions':list(dict.fromkeys(human_exception(x) for x in pending)) if completion.ended(card) else [],
        'receipts':[receipt_result(frappe.get_doc('Partial FG Lot',r)) for r in frappe.get_all('Partial FG Lot',filters={'job_card':job_card,'docstatus':1},pluck='name')]}


def human_exception(text):
    lower=text.lower()
    if 'wip' in lower:return 'Remaining WIP requires disposition.'
    if 'short-production' in lower:return 'Short-production approval required.'
    if 'ipqc' in lower or 'quality' in lower:return 'Unresolved QC.'
    if 'recovery' in lower or 'sample' in lower:return 'Recovery / sample disposition requires review.'
    if 'ledger' in lower or 'valuation' in lower:return 'Stock valuation requires review.'
    if 'consumption' in lower:return 'Consumption attribution or measured process loss requires review.'
    if 'receive' in lower or 'receipt' in lower:return 'FG awaiting receipt.'
    if any(x in lower for x in ['shift','blending','premix','feeder','report','monitor','silo']):return 'Production reporting / sign-off incomplete.'
    return 'Production evidence requires review; see Advanced / Exceptions.'


def receipt_entitlement(cumulative, received):
    """Physical evidence less net submitted receipts; never target or high-water."""
    cumulative, received = flt(cumulative), flt(received)
    if not math.isfinite(cumulative) or not math.isfinite(received):
        frappe.throw('Invalid FG evidence. Production / Stock reconciliation required.')
    if cumulative + 1e-6 < received:
        frappe.throw('Corrected cumulative FG is below FG already received. Production / Stock reconciliation required; existing stock, QC releases and dispatches are not reversed automatically.')
    return max(cumulative - received, 0)


def receipt_proposal(job_card, selection=None, reason=None):
    card, wo = lots.context(job_card)
    lots.lock(wo.name)
    state = lots.preview(job_card)
    qty = receipt_entitlement(state['cumulative_fg_qty'], state['received_qty'])
    result, _ = proposal(job_card, qty, selection, reason)
    result.update(qty=qty, available_qty=qty, destination='FG Quarantine')
    if qty <= 1e-6:
        result.update(status='No unreceived FG', review_reason=None, token=None, proposed_batch=None, can_review=False)
    elif state['confirmed_qty'] > state['received_qty'] + 1e-6:
        result.update(status='Receipt awaiting review', review_reason='Open the existing pending receipt under Review & Confirm. A new lot cannot duplicate its reserved output.', token=None, proposed_batch=None, can_review=False)
    elif result['token']:
        data = decode(result['token'])
        data['purpose'] = 'receive-fg-v1'
        result['token'] = sign(data)
    return result


@frappe.whitelist()
def receipt_preview(job_card):
    return receipt_proposal(job_card)


@frappe.whitelist()
def receipt_review_preview(job_card):
    head()
    result = receipt_proposal(job_card)
    result['rows'] = lots.preview(job_card)['consumption']
    return result


@frappe.whitelist()
def reviewed_receipt_proposal(job_card, selection, reason):
    head()
    return receipt_proposal(job_card, selection, reason)


@frappe.whitelist()
@completion.atomic
def receive_fg(token, note=''):
    data = decode(token)
    from calco_erp.calco_production.fg_handover import receipt_guard
    receipt_guard(data)
    if data.get('purpose') != 'receive-fg-v1':
        frappe.throw('Open Receive FG to calculate the current receipt quantity.')
    expected = receipt_entitlement(data['basis']['output']['fg_qty'], data['basis']['received_qty'])
    if expected <= 1e-6 or abs(flt(data['qty']) - expected) > 1e-6:
        frappe.throw('Receive FG quantity must equal the full unreceived effective FG output.')
    # The existing atomic receipt chain locks the WO, rechecks the complete
    # signed evidence basis, and handles retries without duplicate stock.
    if data.get("route")=="provisional-receipt-v1":
        from calco_erp.calco_production.provisional_receipts import receive
        return receive(data,note)
    return confirm_production(token, note)
