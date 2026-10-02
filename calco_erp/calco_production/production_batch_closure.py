"""Recovery Gate 3: immutable run-level physical confirmation; no stock/accounting posting."""
from calco_erp.calco_production.stock_quantity_precision import decimal as stock_decimal
import json
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.utils import flt, now_datetime
from calco_erp.calco_production import receipt_policy_infrastructure as infra
from calco_erp.calco_production import physical_completion as physical, partial_fg_lots as lots

VERSION = 'physical-batch-closure-v1'
ROLES = {'Production Engineer', 'Production Head', 'Manufacturing Manager'}
MANAGERS = {'Production Head', 'Manufacturing Manager'}
WRITE = object()
TOL = 0.000001


def authority(manager=False):
    if not production_site_allowed():
        frappe.throw('Production closure requires the configured manufacturing site.')
    if not (MANAGERS if manager else ROLES).intersection(frappe.get_roles()):
        frappe.throw('Production closure authority required.', frappe.PermissionError)


def latest(job_card):
    name = frappe.db.get_value('Production Batch Closure', {'job_card': job_card}, 'name', order_by='revision desc')
    return frappe.get_doc('Production Batch Closure', name) if name else None


def context(final_release):
    release = frappe.get_doc('Final QC Release', final_release)
    release.check_permission('read')
    lot_name = frappe.db.get_value('Partial FG Lot', {'lot_batch': release.batch_no, 'docstatus': 1}, 'name')
    if not lot_name or release.docstatus != 1:
        frappe.throw('A submitted production lot release is required.')
    lot = frappe.get_doc('Partial FG Lot', lot_name)
    infra.lock_run(lot.work_order)
    wo = frappe.get_doc('Work Order', lot.work_order)
    card = frappe.get_doc('Job Card', lot.job_card)
    card.check_permission('read'); wo.check_permission('read')
    if wo.docstatus != 1 or wo.status in ('Stopped', 'Cancelled') or card.docstatus == 2:
        frappe.throw('A valid production Work Order and run are required.')
    if not physical.controlled(card) or not physical.end_event(card):
        frappe.throw('Confirm Physical End before final production closure.')
    if card.work_order != wo.name or lot.company != wo.company or lot.parent_batch != wo.custom_fg_batch_no:
        frappe.throw('Production run lineage is incomplete.')
    run_lots = frappe.get_all('Partial FG Lot', filters={'work_order': wo.name, 'docstatus': 1},
        fields=['name','job_card','lot_batch','cutoff','creation','fg_qty','custom_receipt_policy_snapshot'], order_by='cutoff desc, creation desc, name desc')
    if not run_lots or run_lots[0].name != lot.name:
        frappe.throw('Use the final production lot release; an earlier partial release cannot close the run.')
    if any(r.job_card != card.name for r in run_lots):
        frappe.throw('Multiple production runs require Management review before final closure.')
    output = lots.fg_state(card)
    end = physical.end_event(card)
    if abs(flt(end.get('output', {}).get('fg_qty')) - flt(output['fg_qty'])) > TOL:
        frappe.throw('Production output changed after Physical End. Management review is required.')
    if abs(lots.received_fg(wo.name) - flt(output['fg_qty'])) > TOL:
        frappe.throw('Receive the remaining final FG before final production closure.')
    return release, lot, wo, card, run_lots


def ledger_snapshot(wo, card):
    from calco_erp.inventory.availability import get_work_order_wip_lineage
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
    lineage = get_work_order_wip_lineage(wo.name)
    rows = {}
    names = set()
    for (item, batch), r in lineage.items():
        names.update(r['transfer_entries'] + r['consumption_entries'] + r['return_entries'])
        rows[(item,batch)] = dict(item_code=item,batch_no=batch,stock_uom=r['stock_uom'],
            issued_qty=flt(r['transferred_qty']),actual_accounted_qty=flt(r['consumed_qty']),provisional_qty=0,
            returned_qty=flt(r['returned_qty']),source_entries=r)
    issues = frappe.get_all('Stock Entry', filters={'custom_provisional_work_order':wo.name,'purpose':'Material Issue','docstatus':1}, pluck='name')
    for name in issues:
        entry = frappe.get_doc('Stock Entry', name)
        if entry.custom_provisional_job_card != card.name:
            frappe.throw('Material Batch attribution is incomplete. Management review is required.')
        names.add(name)
        for r in entry.items:
            key = (r.item_code,get_stock_entry_row_batch_no(r))
            if key not in rows or r.s_warehouse != wo.wip_warehouse or r.t_warehouse:
                frappe.throw('Material Batch attribution is incomplete. Management review is required.')
            rows[key]['provisional_qty'] = stock_decimal(rows[key]['provisional_qty']) + stock_decimal(r.transfer_qty)
    evidence = frappe.db.sql('''select name,voucher_no,voucher_detail_no,item_code,batch_no,serial_and_batch_bundle,warehouse,actual_qty,posting_date,posting_time,is_cancelled
        from `tabStock Ledger Entry` where voucher_no in %(names)s order by name''', {'names':tuple(sorted(names)) or ('',)}, as_dict=True)
    for row in rows.values():
        row['accounted_qty'] = stock_decimal(row['actual_accounted_qty']) + stock_decimal(row['provisional_qty'])
        row['remaining_qty'] = stock_decimal(row['issued_qty']) - row['accounted_qty'] - stock_decimal(row['returned_qty'])
        for field in ('provisional_qty','accounted_qty','remaining_qty'):
            row[field] = float(row[field])
        if not row['batch_no']:
            frappe.throw('Material Batch attribution is incomplete. Management review is required.')
    return list(sorted(rows.values(),key=lambda r:(r['item_code'],r['batch_no']))), evidence


def requirements(wo, card, run_lots):
    # Reuse strict physical/QC/reporting/valuation checks without treating
    # provisional WIP relief as measured actual consumption in the legacy preview.
    from calco_erp.calco_production import in_process_quality as qc
    messages = list(frappe.local.message_log or [])
    reasons = []
    try:
        if not qc.state_for_work_order(wo).get('ready'):
            reasons.append('Final QC obligations remain unresolved.')
        if physical.reporting_blockers(card):
            reasons.append('Required production reports or sign-offs remain unresolved.')
        if any(r.from_time and not r.to_time for r in card.time_logs):
            reasons.append('Open production execution intervals remain.')
        output = lots.fg_state(card)
        if any(value for key,value in output['components'].items() if key!='quantity'):
            reasons.append('Recovery, samples or waste disposition requires Management review.')
        if output['fg_qty'] < flt(wo.qty)-TOL and not (
            wo.get('custom_partial_production_approved') and
            abs(flt(wo.get('custom_partial_production_qty'))-output['fg_qty'])<TOL and
            all(wo.get('custom_partial_production_'+f) for f in ('reason','approved_by','approved_on'))):
            reasons.append('Short production requires the existing controlled approval.')
        sources = lots.cost.actual_sources(wo.name,now_datetime())
        allocation = lots.cost.reconcile(sources,lots.prior_allocations(wo.name))
        if allocation['available_qty']>lots.cost.TOL or allocation['available_value']>lots.cost.TOL:
            reasons.append('Material Batch attribution is incomplete. Management review is required.')
        if physical.receipt_valuation_blockers(wo.name):
            reasons.append('Final production reconciliation requires Management review.')
    except frappe.ValidationError:
        reasons.append('Existing final production controls require Management review.')
    finally:
        frappe.local.message_log = messages
    for lot in run_lots:
        if not frappe.db.exists('Final QC Release', {'batch_no':lot.lot_batch,'docstatus':1,'status':'Released'}):
            reasons.append('Final QC obligations remain unresolved.')
    formulations = {json.loads(lot.custom_receipt_policy_snapshot or '{}').get('formulation_fingerprint')
        for lot in run_lots if lot.custom_receipt_policy_snapshot}
    if len(formulations)>1:
        reasons.append('Material attribution across production formulations requires Management review.')
    return list(dict.fromkeys(reasons))


def physical_balance(wo,card,rows,measured):
    reasons=[]
    output=lots.fg_state(card)
    sources=lots.cost.actual_sources(wo.name,now_datetime())
    loss=physical.approved_loss(card,{'output':output,'consumption':sources})
    total=sum(flt(v) for v in measured.values())
    if any(r['stock_uom']!='Kg' for r in rows) or abs(total-output['fg_qty']-loss)>TOL:
        reasons.append('Material quantity does not reconcile with FG and approved physical disposition.')
    if output['fg_qty']+loss>flt(card.for_quantity)+TOL:
        reasons.append('Physical quantities exceed the approved production authority.')
    if any(abs(r['remaining_qty'])>TOL for r in rows):
        reasons.append('Remaining WIP requires controlled final disposition.')
    return reasons


def measure(materials, values):
    values = frappe.parse_json(values)
    if not isinstance(values,list): frappe.throw('Enter final physical consumption for every RM batch.')
    expected = {(r['item_code'],r['batch_no']) for r in materials}
    result = {}
    for r in values:
        if not isinstance(r,dict) or set(r)-{'item_code','batch_no','final_actual_qty'}:
            frappe.throw('Only RM item, batch and final physical quantity may be supplied.')
        key = (r.get('item_code'),r.get('batch_no'))
        if key in result or key not in expected: frappe.throw('Final measurements must match every required RM batch once.')
        result[key] = str(infra.quantity(r.get('final_actual_qty')))
    if set(result)!=expected or not expected: frappe.throw('Enter final physical consumption for every RM batch.')
    return result


def evaluate(materials, measured, blockers):
    reasons = list(blockers); deltas = []
    for r in materials:
        actual = flt(measured[(r['item_code'],r['batch_no'])])
        delta = actual-r['accounted_qty']
        disposition = 'Restoration Required' if delta < -TOL else 'Additional Quantity Resolution Required' if delta > TOL else 'Matched'
        deltas.append(dict(item_code=r['item_code'],batch_no=r['batch_no'],quantity_delta=delta,requirement=disposition))
        if delta < -TOL: reasons.append('Actual consumption is lower than quantity already accounted from WIP. Management review is required.')
        if delta > TOL: reasons.append('Additional physical consumption requires controlled quantity resolution. Management review is required.')
        if r['remaining_qty'] < -TOL or abs(r['issued_qty']-actual-r['returned_qty']-r['remaining_qty'])>TOL:
            reasons.append('Material quantity does not reconcile with the production run.')
    return dict(status='Management Review Required' if reasons else 'Batch Closed',reasons=list(dict.fromkeys(reasons)),quantity_requirements=deltas)


def public(record):
    data = infra.payload(record.source_snapshot)
    result = data.get('evaluation',{})
    return dict(name=record.name,version=record.revision,work_order=record.work_order,job_card=record.job_card,
        physical_end_confirmed=True,final_consumption_confirmed=True,status=result.get('status',record.status),
        reasons=result.get('reasons',[]),recorded_by=record.recorded_by,recorded_on=str(record.recorded_on),
        materials=[{k:r.get(k) for k in ('item_code','batch_no','stock_uom','issued_qty','accounted_qty','final_actual_qty')} for r in record.materials])


@frappe.whitelist()
def preview(final_release):
    authority()
    messages = list(frappe.local.message_log or [])
    try: release,lot,wo,card,run_lots = context(final_release)
    except frappe.ValidationError as exc: return dict(available=False,reason=str(exc))
    finally: frappe.local.message_log = messages
    old = latest(card.name)
    if old: return dict(available=True,confirmed=public(old),can_correct=bool(MANAGERS.intersection(frappe.get_roles())))
    rows,_ = ledger_snapshot(wo,card)
    return dict(available=True,work_order=wo.name,job_card=card.name,physical_end_confirmed=True,
        materials=[{**{k:r[k] for k in ('item_code','batch_no','stock_uom','issued_qty','accounted_qty')},'final_actual_qty':''} for r in rows])


@frappe.whitelist()
@physical.atomic
def confirm(final_release, measurements, production_complete=0, supersedes=None, correction_reason=None):
    authority(manager=bool(supersedes))
    if str(production_complete) not in ('1','true','True'): frappe.throw('Explicitly confirm that production is complete.')
    release,lot,wo,card,run_lots = context(final_release)
    old = latest(card.name)
    if old and not supersedes:
        previous = [dict(item_code=r.item_code,batch_no=r.batch_no) for r in old.materials]
        supplied = measure(previous,measurements)
        if any(infra.quantity(supplied[(r.item_code,r.batch_no)]) != infra.quantity(r.final_actual_qty) for r in old.materials):
            frappe.throw('Final consumption is already confirmed. A controlled correction version and reason are required.')
        return public(old)
    if supersedes:
        if not old or old.name!=supersedes or not (correction_reason or '').strip():
            frappe.throw('Correction must reference the latest confirmation and include a reason.')
    rows,ledger = ledger_snapshot(wo,card)
    measured = measure(rows,measurements)
    result = evaluate(rows,measured,requirements(wo,card,run_lots)+physical_balance(wo,card,rows,measured))
    source = dict(version=VERSION,measurement_state='Final Consumption Confirmed',physical_end=physical.end_event(card),materials=rows,ledger=ledger,
        lots=[dict(r) for r in run_lots],evaluation=result)
    source['ledger_fingerprint']=infra.fingerprint(ledger)
    source['source_fingerprint']=infra.fingerprint(source)
    doc = frappe.get_doc(dict(doctype='Production Batch Closure',company=wo.company,work_order=wo.name,
        job_card=card.name,parent_batch=lot.parent_batch,final_release=release.name,final_closure_confirmed=1,
        supersedes=supersedes,correction_reason=correction_reason,source_snapshot=infra.canonical(source),
        materials=[dict(item_code=r['item_code'],batch_no=r['batch_no'],stock_uom=r['stock_uom'],
            issued_qty=r['issued_qty'],accounted_qty=r['accounted_qty'],final_actual_qty=measured[(r['item_code'],r['batch_no'])],
            source_evidence=infra.canonical(r)) for r in rows]))
    doc.flags.receipt_infrastructure_token=infra.WRITE_TOKEN
    doc.flags.production_closure_token=WRITE
    doc.insert(ignore_permissions=True)
    return public(doc)


def validate_record(doc):
    source = infra.payload(doc.source_snapshot)
    if source.get('version')!=VERSION: return  # Preserve Gate 1 historical schema evidence.
    if doc.flags.get('production_closure_token') is not WRITE:
        frappe.throw('Use the controlled final production confirmation.')
    doc.status=source['evaluation']['status']
    doc.fingerprint=infra.records.fingerprint(doc)


def protect_confirmed_run(doc, method=None):
    old = doc.get_doc_before_save()
    if not old or doc.get(physical.FIELD)==old.get(physical.FIELD): return
    previous = latest(doc.name)
    if previous and any(e.get('action')=='Reopen' for e in physical.events(doc)[len(physical.events(old)):]):
        frappe.throw('Final consumption is already confirmed. Management must resolve the recorded batch closure before reopening production.')
