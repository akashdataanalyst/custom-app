"""Gate 4 settlement preparation. No quantity posting, LCV submission or activation."""
import json
from decimal import Decimal
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.utils import now_datetime
from calco_erp.calco_production import receipt_policy_infrastructure as infra, production_batch_closure as closure_api
from calco_erp.calco_production import partial_fg_lots as lots, partial_lot_reconciliation as audit, physical_completion as physical

VERSION='lot-settlement-v1'
ROLES={'Manufacturing Manager','Accounts Manager'}
WRITE=object()
TOL=Decimal('0.000001')
ROUND=Decimal('0.01')

def authority():
    if not production_site_allowed():frappe.throw('Settlement requires the configured manufacturing site.')
    if not ROLES.intersection(frappe.get_roles()):frappe.throw('Management/Accounts settlement authority required.',frappe.PermissionError)

def num(v):return lots.cost.number(v)
def key(r):return (r['consumption_entry'],r['consumption_detail'],r['item_code'],r['batch_no'])
def pair(r):return (r['item_code'],r['batch_no'])
def decode(v):return frappe.parse_json(v) if isinstance(v,str) else v

def confirmed(name):
    doc=frappe.get_doc('Production Batch Closure',name)
    wo=frappe.get_doc('Work Order',doc.work_order);wo.check_permission('read')
    infra.lock_run(wo.name)
    if not infra.records.verified(doc) or not doc.final_closure_confirmed:
        frappe.throw('An immutable confirmed physical closure is required.')
    if closure_api.latest(doc.job_card).name!=doc.name:frappe.throw('Use the latest corrected closure version.')
    return doc,wo,frappe.get_doc('Job Card',doc.job_card)


def stock_rows(entry,wo,card,direction,approved_reference=False):
    """Exact native ledger/bundle quantity and value; no BOM/current price lookup."""
    if entry.docstatus!=1 or entry.company!=wo.company:
        frappe.throw('Submitted same-company stock evidence is required.')
    linked=entry.work_order or entry.get('custom_provisional_work_order')
    run=entry.get('custom_live_consumption_job_card') or entry.get('custom_provisional_job_card')
    if (linked!=wo.name and not (approved_reference and not linked)) or (run and run!=card.name):frappe.throw('Stock evidence does not belong to this production run.')
    out=[]
    for row in entry.items:
        if row.stock_uom!='Kg':frappe.throw('An approved mass conversion is required for non-Kg stock evidence.')
        warehouse=row.s_warehouse if direction<0 else row.t_warehouse
        if not warehouse or (direction<0 and (warehouse!=wo.wip_warehouse or row.t_warehouse)):
            frappe.throw('Material relief must be outgoing production WIP.')
        sl=frappe.db.sql('''select name,actual_qty,stock_value_difference from `tabStock Ledger Entry`
            where voucher_type='Stock Entry' and voucher_no=%s and voucher_detail_no=%s and warehouse=%s and is_cancelled=0 order by name''',(entry.name,row.name,warehouse),as_dict=True)
        if not sl or any(Decimal(str(r.actual_qty))*direction<=0 for r in sl):frappe.throw('Invalid native stock ledger direction.')
        qty=sum((num(Decimal(str(r.actual_qty))*direction) for r in sl),Decimal(0))
        value=sum((num(Decimal(str(r.stock_value_difference))*direction) for r in sl),Decimal(0))
        batches={}
        if row.serial_and_batch_bundle:
            bundle=frappe.get_doc('Serial and Batch Bundle',row.serial_and_batch_bundle)
            if bundle.item_code!=row.item_code or bundle.warehouse!=warehouse:frappe.throw('Stock bundle genealogy mismatch.')
            for b in bundle.entries:
                if not b.batch_no:frappe.throw('Batch identity is required.')
                q=num(Decimal(str(b.qty))*direction);v=num(Decimal(str(b.stock_value_difference))*direction)
                previous=batches.get(b.batch_no,(Decimal(0),Decimal(0)));batches[b.batch_no]=(previous[0]+q,previous[1]+v)
        elif row.batch_no:batches[row.batch_no]=(qty,value)
        else:frappe.throw('Exact stock batch identity required.')
        if abs(sum(q for q,v in batches.values())-qty)>TOL or abs(sum(v for q,v in batches.values())-value)>TOL:
            frappe.throw('Bundle value/quantity differs from Stock Ledger. Complete native repost first.')
        for batch,(q,v) in batches.items():
            out.append(dict(consumption_entry=entry.name,consumption_detail=row.name,item_code=row.item_code,batch_no=batch,
                qty=str(q),stock_value=str(v),warehouse=warehouse,ledger=[dict(r) for r in sl],bundle=row.serial_and_batch_bundle,posting_date=str(entry.posting_date),posting_time=str(entry.posting_time),created_on=str(entry.creation),created_by=entry.owner,lineage_basis='Approved settlement reference' if approved_reference and not linked else 'Native WO/run reference'))
    return out


def assign(sources,frozen,adjustments,restorations,measured):
    """Deterministic frozen allocation plus explicitly approved source/lot corrections."""
    source_map={key(r):r for r in sources}
    if len(source_map)!=len(sources):frappe.throw('Duplicate material source identity.')
    used={k:Decimal(0) for k in source_map};result=[];seen=set()
    for row in frozen+adjustments:
        identity=(row['lot'],key(row))
        if identity in seen:frappe.throw('Duplicate source/lot attribution.')
        seen.add(identity)
        source=source_map.get(key(row))
        if not source:frappe.throw('Missing or reversed material source.')
        q=num(row['qty']);used[key(row)]+=q
        if used[key(row)]>num(source['qty'])+TOL:frappe.throw('Material source is over-allocated.')
        result.append({**source,'lot':row['lot'],'qty':str(q),'stock_value':str(q*num(source['stock_value'])/num(source['qty'])),
            'classification':source['classification'],'allocation_origin':row.get('allocation_origin','Frozen lot evidence')})
    if any(abs(used[k]-num(r['qty']))>TOL for k,r in source_map.items()):
        frappe.throw('Material attribution is incomplete. Management Review Required; no BOM distribution is allowed.')
    restored=set()
    for row in restorations:
        rid=key(row)
        if rid in restored:frappe.throw('Restoration evidence cannot be reused.')
        restored.add(rid)
        matches=[r for r in result if r['lot']==row['lot'] and key(r)==tuple(row['original_source'])]
        if len(matches)!=1 or pair(matches[0])!=pair(row):frappe.throw('Restoration must reference its original lot, RM batch and relieved source.')
        target=matches[0];q=num(row['qty']);v=num(row['stock_value'])
        if q>num(target['qty'])+TOL or v>num(target['stock_value'])+TOL:frappe.throw('Restoration exceeds attributed material.')
        target['qty']=str(num(target['qty'])-q);target['stock_value']=str(num(target['stock_value'])-v)
        target.setdefault('restorations',[]).append(row)
    net={}
    for r in result:net[pair(r)]=net.get(pair(r),Decimal(0))+num(r['qty'])
    if set(net)-set(measured) or any(abs(net.get(k,Decimal(0))-num(v))>TOL for k,v in measured.items()):
        frappe.throw('Final physical quantities and resolved WIP relief differ. Resolve quantity corrections/restorations before valuation.')
    return result


def conversion(policy,lot_data):
    if not isinstance(policy,dict) or not policy.get('approved') or not policy.get('version') or not str(policy.get('evidence','')).strip():
        frappe.throw('An explicitly approved conversion-cost policy/version and evidence are required.')
    method=policy.get('method')
    if method not in ('submitted-additional-costs','per-fg-unit','explicit-zero'):frappe.throw('Configure an approved conversion-cost calculation method.')
    if method=='per-fg-unit' and policy.get('rate') in (None,''):frappe.throw('An approved conversion rate is required.')
    return {r['lot']:str(num(r['additional_costs']) if method=='submitted-additional-costs' else
        num(r['fg_qty'])*num(policy['rate']) if method=='per-fg-unit' else Decimal(0)) for r in lot_data}


def targets(attribution,lot_data,policy,recovery,treatment):
    amounts=conversion(policy,lot_data)
    if treatment not in ('no-recovery-no-loss','process-loss-absorbed','separate-recovery-loss-absorbed'):
        frappe.throw('Explicit recovery/process-loss treatment required.')
    if recovery and treatment!='separate-recovery-loss-absorbed':frappe.throw('Separately posted recovery requires explicit deduction treatment.')
    material={r['lot']:Decimal(0) for r in lot_data};recovered={k:Decimal(0) for k in material};seen=set()
    for r in attribution:
        if r['lot'] not in material:frappe.throw('Attribution references another run/lot.')
        material[r['lot']]+=num(r['stock_value'])
    for r in recovery:
        if key(r) in seen or r['lot'] not in material:frappe.throw('Recovery source is duplicated or belongs to another lot.')
        seen.add(key(r));recovered[r['lot']]+=num(r['stock_value'])
    result=[]
    for r in lot_data:
        name=r['lot'];required=material[name]+num(amounts[name])-recovered[name]
        if required<0:frappe.throw('Recovery value exceeds the attributable input/conversion value.')
        rounded=required.quantize(Decimal('0.01'))
        result.append({**r,'material_value':str(material[name]),'conversion_value':amounts[name],
            'recovery_value':str(recovered[name]),'target_value':str(rounded),'rounding':str(rounded-required)})
    input_value=sum(material.values())+sum(num(v) for v in amounts.values())
    output_value=sum(num(r['target_value'])+num(r['recovery_value']) for r in result)
    if abs(input_value-output_value)>ROUND:frappe.throw('Run value conservation failed beyond controlled rounding.')
    return result,dict(material_value=str(sum(material.values())),conversion_value=str(sum(num(v) for v in amounts.values())),
        fg_value=str(sum(num(r['target_value']) for r in result)),recovery_value=str(sum(recovered.values())),rounding=str(output_value-input_value),balanced=True)


def collect(doc,wo,card,options):
    if not physical.end_event(card):frappe.throw('Physical End must remain confirmed.')
    from calco_erp.calco_production import in_process_quality as qc
    if not qc.state_for_work_order(wo).get('ready') or physical.reporting_blockers(card):frappe.throw('Final Quality/reporting obligations remain unresolved.')
    data=[];frozen=[];sources=[]
    actual=lots.cost.actual_sources(wo.name,now_datetime());lots.cost.reconcile(actual,lots.prior_allocations(wo.name))
    if any(r.stock_uom!='Kg' for r in doc.materials):frappe.throw('An approved mass conversion is required before settlement.')
    for r in actual:sources.append({**{k:v for k,v in r.items() if k!='cutoff'},'classification':'Actual-backed consumption'})
    records=frappe.get_all('Partial FG Lot',filters={'work_order':wo.name,'docstatus':1},pluck='name',order_by='cutoff,creation')
    for name in records:
        lot=frappe.get_doc('Partial FG Lot',name)
        if lot.job_card!=card.name:frappe.throw('Multiple run attribution requires Management Review Required.')
        entries=frappe.get_all('Stock Entry',filters={'custom_partial_fg_lot':name,'purpose':'Manufacture','docstatus':1},pluck='name')
        if len(entries)!=1:frappe.throw('Every FG lot must have one valid Manufacture receipt.')
        entry=frappe.get_doc('Stock Entry',entries[0])
        plan=json.loads(lot.custom_receipt_policy_snapshot or '{}')
        rows=json.loads(lot.consumption_evidence or '[]')
        historical={key(r):r for r in lots.cost.actual_sources(wo.name,lot.cutoff)}
        if any(key(r) not in historical or num(r['qty'])>num(historical[key(r)]['qty'])+TOL for r in rows):frappe.throw('Actual allocation violates its frozen lot cutoff.')
        frozen.extend({**r,'lot':name} for r in rows)
        if plan:
            issues=frappe.get_all('Stock Entry',filters={'custom_provisional_lot':name,'purpose':'Material Issue','docstatus':1},pluck='name')
            if len(issues)!=1:frappe.throw('Provisional receipt relief is missing or ambiguous.')
            issue=frappe.get_doc('Stock Entry',issues[0])
            for r in stock_rows(issue,wo,card,-1):
                source={**r,'classification':'Provisional WIP relief; final measurement in closure '+doc.name}
                sources.append(source);frozen.append({**source,'lot':name})
        data.append(dict(lot=name,batch=lot.lot_batch,manufacture=entry.name,fg_qty=lot.fg_qty,cutoff=str(lot.cutoff),
            interval_start=plan.get('interval_start'),formulation=plan.get('formulation_fingerprint'),policy_fingerprint=lot.custom_receipt_policy_fingerprint,
            output_evidence=json.loads(lot.output_evidence or '[]'),additional_costs=entry.total_additional_costs,
            conversion_source=[r.as_dict() for r in entry.additional_costs],frozen_snapshot=plan))
    if not data:frappe.throw('No submitted FG lots belong to this run.')
    if {r['name'] for r in infra.payload(doc.source_snapshot).get('lots',[])}!=set(records):
        frappe.throw('Lot inventory changed after physical confirmation; a controlled closure correction is required.')
    extra=options.get('attribution') or []
    for row in extra:
        if not row.get('evidence'):frappe.throw('Additional attribution requires approved interval/formulation evidence.')
        if row.get('lot') not in records:frappe.throw('Correction attribution belongs to another run.')
    lock_and_check_references(wo.name,options)
    restorations=[];recovery=[]
    for kind,destination in [('restorations',restorations),('recovery',recovery)]:
        for request in options.get(kind) or []:
            if not request.get('evidence') or request.get('lot') not in records:frappe.throw('Approved correction/recovery lineage and evidence are required.')
            entry=frappe.get_doc('Stock Entry',request['stock_entry'])
            if entry.purpose!='Material Receipt':frappe.throw('Use a submitted controlled Material Receipt for restoration/recovery evidence.')
            matches=[r for r in stock_rows(entry,wo,card,1,approved_reference=True) if r['consumption_detail']==request['detail'] and r['batch_no']==request['batch_no']]
            if len(matches)!=1:frappe.throw('Exact posted correction/recovery batch allocation is required.')
            row={**matches[0],'lot':request['lot'],'evidence':request['evidence']}
            if kind=='restorations':
                if row['warehouse']!=wo.wip_warehouse:frappe.throw('Restoration must return material to attributed WIP.')
                row['original_source']=request['original_source']
            elif row['warehouse'] in (wo.wip_warehouse,'FG Quarantine - CPPL','FG Released - CPPL'):
                frappe.throw('Recovery must be separately stocked outside FG/WIP.')
            destination.append(row)
    measured={ (r.item_code,r.batch_no):r.final_actual_qty for r in doc.materials }
    attribution=assign(sources,frozen,[{**r,'allocation_origin':'Approved correction: '+r['evidence']} for r in extra],restorations,measured)
    rows,ledger=closure_api.ledger_snapshot(wo,card)
    current={pair(r):r for r in rows}
    restored={}
    for r in restorations:restored[pair(r)]=restored.get(pair(r),Decimal(0))+num(r['qty'])
    conservation=[]
    for k,v in measured.items():
        if k not in current:frappe.throw('Issued material/batch genealogy is missing.')
        row=current[k];net=num(row['accounted_qty'])-restored.get(k,Decimal(0))
        remaining=num(row['issued_qty'])-net-num(row['returned_qty'])
        if remaining < -TOL or abs(num(row['issued_qty'])-num(v)-num(row['returned_qty'])-remaining)>TOL:
            frappe.throw('Run physical quantity conservation failed.')
        conservation.append(dict(item_code=k[0],batch_no=k[1],issued=row['issued_qty'],actual_relief=row['actual_accounted_qty'],provisional_relief=row['provisional_qty'],restored=str(restored.get(k,0)),returned=row['returned_qty'],remaining=str(remaining),final_actual=str(v),quantity_delta=str(num(v)-net)))
    fg=sum(num(r['fg_qty']) for r in data);recovery_qty=sum(num(r['qty']) for r in recovery)
    loss=physical.approved_loss(card,{'output':lots.fg_state(card),'consumption':actual})
    if loss and options.get('treatment')=='no-recovery-no-loss':frappe.throw('Approved process loss needs explicit absorbed-cost treatment.')
    if abs(sum(num(v) for v in measured.values())-fg-recovery_qty-num(loss))>TOL:
        frappe.throw('FG/recovery/approved loss does not reconcile final physical consumption.')
    loss_evidence=[e for e in physical.events(card) if e.get('action')=='Approve Process Loss' and e.get('basis')==physical.mass_identity({'output':lots.fg_state(card),'consumption':actual})]
    return data,attribution,recovery,dict(loss_evidence=loss_evidence,rows=conservation,sources=sources,restorations=restorations,ledger=ledger,approved_loss_qty=str(loss),balanced=True)


def calculation(doc,options):
    doc,wo,card=confirmed(doc.name)
    data,attribution,recovery,quantities=collect(doc,wo,card,options)
    resolved,balance=targets(attribution,data,options.get('conversion'),recovery,options.get('treatment'))
    return dict(closure=doc.name,closure_version=doc.revision,closure_fingerprint=doc.fingerprint,work_order=wo.name,job_card=card.name,
        options=options,quantity=quantities,attribution=attribution,recovery=recovery,targets=resolved,conservation=balance,version=VERSION)


def latest(job_card):
    name=frappe.db.get_value('Production Settlement Preparation',{'job_card':job_card},'name',order_by='revision desc')
    return frappe.get_doc('Production Settlement Preparation',name) if name else None


@frappe.whitelist()
@physical.atomic
def prepare(closure,options=None,approved=0,reason=None):
    authority();doc,wo,card=confirmed(closure)
    options=decode(options) or {}
    if str(approved) not in ('1','true','True') or not str(reason or '').strip():frappe.throw('Explicit Management/Accounts approval and evidence reason are required.')
    if not isinstance(options,dict):frappe.throw('Invalid settlement policy inputs.')
    failures=[];body={}
    messages=list(frappe.local.message_log or [])
    try:body=calculation(doc,options)
    except frappe.ValidationError as exc:failures=[str(exc)]
    finally:frappe.local.message_log=messages
    evidence=dict(version=VERSION,closure_fingerprint_seal=infra.records.seal(doc),closure=doc.name,closure_version=doc.revision,closure_fingerprint=doc.fingerprint,options=options,calculation=body,exceptions=failures,approval_reason=reason.strip())
    digest=infra.fingerprint(evidence)
    old=latest(card.name)
    if old and infra.payload(old.value_evidence).get('calculation_fingerprint')==digest:return {'name':old.name,'status':old.status,'reused':True}
    evidence['calculation_fingerprint']=digest
    record=frappe.get_doc(dict(doctype='Production Settlement Preparation',company=wo.company,work_order=wo.name,job_card=card.name,parent_batch=doc.parent_batch,
        closure=doc.name,closure_version=doc.revision,quantity_evidence=infra.canonical(body.get('quantity',{})),lot_targets=infra.canonical({'lots':body.get('targets',[])}),
        value_evidence=infra.canonical(evidence),exceptions=infra.canonical({'reasons':failures}),reconciliation_version=VERSION,linked_adjustments='{}',
        supersedes=old.name if old else None,correction_reason=reason.strip() if old else None))
    record.flags.receipt_infrastructure_token=infra.WRITE_TOKEN;record.flags.settlement_token=WRITE;record.insert(ignore_permissions=True)
    return {'name':record.name,'status':record.status,'reused':False}


def validate_record(doc):
    data=infra.payload(doc.value_evidence)
    if data.get('version')!=VERSION:return
    if doc.flags.get('settlement_token') is not WRITE:frappe.throw('Use controlled settlement preparation.')
    doc.status='Management Review Required' if data['exceptions'] else 'Ready for Accounts'
    doc.fingerprint=infra.records.fingerprint(doc)


def fresh(name):
    authority();doc=frappe.get_doc('Production Settlement Preparation',name);doc.check_permission('read')
    if not infra.records.verified(doc):frappe.throw('Settlement evidence fingerprint mismatch.')
    closure,wo,card=confirmed(doc.closure)
    if latest(card.name).name!=doc.name:frappe.throw('Use the latest settlement preparation version.')
    old=infra.payload(doc.value_evidence)
    if old.get('exceptions'):frappe.throw('Management Review Required: resolve preparation exceptions first.')
    current=calculation(closure,old['options'])
    if infra.canonical(current)!=infra.canonical(old['calculation']):frappe.throw('Settlement source evidence changed. Prepare a new reviewed version.')
    return doc,current


@frappe.whitelist()
def details(name):
    authority();doc=frappe.get_doc('Production Settlement Preparation',name);doc.check_permission('read')
    body=infra.payload(doc.value_evidence)
    if doc.status=='Management Review Required':return dict(name=name,status=doc.status,exceptions=body.get('exceptions',[]))
    doc,current=fresh(name)
    values=audit.audit_true_up(doc.work_order,None,{r['lot']:r['target_value'] for r in current['targets']})
    return dict(name=name,status=doc.status,preparation_version=doc.revision,closure=doc.closure,closure_version=doc.closure_version,
        calculation=current,reconciliation=values,automatic_submit=False)


def draft_state(reference,lot_name,target,account,reason):
    """Narrow adapter for the existing Draft LCV engine; historical inputs unchanged."""
    from calco_erp.calco_production import fg_true_up_draft as flow
    if set(reference)!={'settlement_preparation'}:frappe.throw('Invalid settlement measurement reference.')
    doc,current=fresh(reference['settlement_preparation'])
    chosen=[r for r in current['targets'] if r['lot']==lot_name]
    if len(chosen)!=1 or num(target)!=num(chosen[0]['target_value']):frappe.throw('Use the immutable derived lot target.')
    if reason!=f'Settlement {doc.name} / Closure {doc.closure} v{doc.closure_version}':frappe.throw('Settlement evidence/version reason cannot change.')
    flow.validate_account_choice(doc.company,account)
    entry=frappe.get_doc('Stock Entry',chosen[0]['manufacture']);entry.check_permission('read');flow.native_period_check(entry)
    acct=frappe.get_doc('Account',account);acct.check_permission('read')
    if acct.company!=doc.company or acct.is_group or acct.disabled or acct.account_currency!=frappe.get_cached_value('Company',doc.company,'default_currency'):
        frappe.throw('Select a valid same-company currency valuation account.')
    engine=audit.audit_true_up(doc.work_order,None,{lot_name:target})
    value=next(r for r in engine['valuation_reconciliation'] if r['lot']==lot_name)
    quantities=[dict(item_code=r['item_code'],batch_no=r['batch_no'],final_measured_qty=r['final_actual'],already_accounted_qty=r['final_actual'],quantity_delta=r['quantity_delta'],resolution='Resolved in settlement preparation') for r in current['quantity']['rows']]
    return dict(work_order=doc.work_order,lot=lot_name,manufacture=entry.name,company=doc.company,measurements=reference,target=str(num(target)),expense_account=account,reason=reason,
        sources=current['quantity']['sources'],quantity=quantities,valuation=value,settlement_preparation=doc.name,settlement_fingerprint=doc.fingerprint,closure=doc.closure,closure_version=doc.closure_version)


@frappe.whitelist()
def draft_preview(name,lot,account=None):
    authority();doc,current=fresh(name)
    chosen=next((r for r in current['targets'] if r['lot']==lot),None)
    if not chosen:frappe.throw('Lot does not belong to this preparation.')
    from calco_erp.calco_production import fg_true_up_draft as flow
    return flow.preview(lot,{'settlement_preparation':name},chosen['target_value'],account or frappe.get_cached_value('Company',doc.company,'stock_adjustment_account'),
        f'Settlement {doc.name} / Closure {doc.closure} v{doc.closure_version}')


def lock_and_check_references(work_order,options):
    names=sorted({r['stock_entry'] for kind in ('restorations','recovery') for r in options.get(kind) or []})
    for name in names:frappe.db.sql('select name from `tabStock Entry` where name=%s for update',name)
    if not names:return
    for record in frappe.get_all('Production Settlement Preparation',filters={'work_order':('!=',work_order)},fields=['value_evidence']):
        evidence=infra.payload(record.value_evidence)
        if evidence.get('exceptions'):continue
        previous=evidence.get('options',{})
        if any(r['stock_entry'] in names for kind in ('restorations','recovery') for r in previous.get(kind) or []):
            frappe.throw('Restoration/recovery stock evidence is already assigned to another production run.')
