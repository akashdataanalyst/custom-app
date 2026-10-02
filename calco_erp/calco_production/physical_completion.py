"""Physical stopping is independent of QC, quantity credit and stock settlement."""
import json
import math
from contextlib import contextmanager
from functools import wraps

import frappe
from frappe.utils import flt, now_datetime

FIELD = 'custom_physical_completion_audit'
TOKEN = object()
ROLES = {'Production Engineer', 'Production Head', 'Manufacturing Manager'}


def atomic(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        point='physical_end_'+frappe.generate_hash(length=10)
        frappe.db.savepoint(point)
        try:return fn(*args, **kwargs)
        except Exception:
            frappe.db.rollback(save_point=point)
            raise
    return run


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Job Card': [{'fieldname': FIELD, 'label': 'Physical End / Finalization Evidence',
        'fieldtype': 'Long Text', 'read_only': 1, 'no_copy': 1, 'insert_after': 'custom_execution_policy_audit'}]}, update=True)


def controlled(doc):
    from calco_erp.calco_production.shift_output_snapshot import activation
    return bool(doc.get('operation') == 'Compounding / Extrusion' and doc.get('work_order') and activation(doc))


def events(doc):
    return json.loads(doc.get(FIELD) or '[]')


def end_event(doc):
    for event in reversed(events(doc)):
        if event['action'] == 'Reopen': return None
        if event['action'] == 'Physical End': return event
    return None


def ended(doc):
    return bool(end_event(doc))


def authorized(doc):
    return doc.flags.get('physical_completion_token') is TOKEN


@contextmanager
def action(doc, kind):
    doc.flags.physical_completion_token = TOKEN
    doc.flags.physical_completion_action = kind
    try: yield
    finally: doc.flags.physical_completion_token = None


def append(doc, kind, timestamp, **evidence):
    history = events(doc)
    history.append(dict(action=kind, at=str(timestamp), by=frappe.session.user, **evidence))
    doc.set(FIELD, json.dumps(history, sort_keys=True, default=str))
    doc.flags.physical_completion_expected = doc.get(FIELD)


def quantity_guard(doc):
    if not controlled(doc): return
    from calco_erp.calco_production.partial_fg_lots import fg_state
    limit = fg_state(doc)['fg_qty']
    values = [flt(doc.get('total_completed_qty'))]
    for rows in (doc.get('time_logs') or [], doc.get('sub_operations') or []):
        quantities = [flt(r.get('completed_qty')) for r in rows]
        if any(not math.isfinite(q) or q < 0 for q in quantities):
            frappe.throw('Completed FG quantities must be finite and non-negative.')
        values.extend(quantities)
        if rows is doc.get('time_logs'): values.append(sum(quantities))
    if any(not math.isfinite(q) or q < 0 or q > limit + 1e-6 for q in values):
        frappe.throw(f'Native completed FG cannot exceed controlled cumulative FG evidence ({limit} Kg).')


def protect(doc, method=None):
    old = doc.get_doc_before_save()
    previous = old.get(FIELD) if old else None
    if doc.get(FIELD) != previous:
        if not authorized(doc) or doc.get(FIELD) != doc.flags.get('physical_completion_expected'):
            frappe.throw('Physical end and finalization history is immutable and server-controlled.')
        before, after = json.loads(previous or '[]'), events(doc)
        if after[:len(before)] != before or len(after) != len(before) + 1:
            frappe.throw('Physical execution history must append exactly one event.')
    if not controlled(doc): return
    quantity_guard(doc)
    if old and flt(doc.for_quantity)!=flt(old.for_quantity):
        frappe.throw('Frozen controlled Job Card target cannot be changed to bypass short-production approval.')
    if not authorized(doc):
        for field in ('total_completed_qty', 'process_loss_qty'):
            if flt(doc.get(field)) != flt(old.get(field) if old else 0):
                frappe.throw('Use controlled finalization to credit FG or reconciled process loss.')
        before = {r.name: flt(r.completed_qty) for r in (old.time_logs if old else [])}
        if any(flt(r.completed_qty) != before.get(r.name, 0) for r in doc.time_logs):
            frappe.throw('Time-log quantity credit requires controlled finalization.')
        if doc.docstatus == 1 and (not old or old.docstatus != 1):
            frappe.throw('Use Finalize Job Card after Physical End and reconciliation.')
        if doc.docstatus == 0 and ended(doc) and (not doc.is_paused or any(r.from_time and not r.to_time for r in doc.time_logs)):
            frappe.throw('Physical execution has ended. Use controlled Reopen Physical Execution.')


def prevent_delete(doc, method=None):
    if doc.get(FIELD): frappe.throw('Physical execution history must be preserved.')


def get_card(name, manager=False, allow_finalized=False):
    if not (ROLES - ({'Production Engineer'} if manager else set())).intersection(frappe.get_roles()):
        frappe.throw('Production authority is required.', frappe.PermissionError)
    doc = frappe.get_doc('Job Card', name)
    doc.check_permission('write')
    if not (allow_finalized and doc.docstatus == 1): doc.validate_docstatus()
    if not controlled(doc): frappe.throw('Controlled shift-snapshot Compounding is required.')
    from calco_erp.calco_production.stopped_execution import lock_execution
    lock_execution(doc)
    return doc


def reporting_blockers(doc):
    from calco_erp.calco_production.compounding_execution import COMPLETION_VALIDATORS
    blockers = []
    for path in COMPLETION_VALIDATORS:
        messages = list(frappe.local.message_log or [])
        try: frappe.get_attr(path)(doc)
        except frappe.ValidationError as exc: blockers.append(str(exc))
        finally: frappe.local.message_log = messages
    return blockers


@frappe.whitelist()
def preview(job_card):
    doc = frappe.get_doc('Job Card', job_card); doc.check_permission('read')
    if not controlled(doc): return {'enabled': False}
    from calco_erp.calco_production import partial_fg_lots as lots, in_process_quality as qc
    from calco_erp.calco_production.wip_consumption import get_execution_wip_context
    state = lots.fg_state(doc); wo = frappe.get_doc('Work Order', doc.work_order)
    wip = get_execution_wip_context(wo.name, include_history=True)
    sources = lots.cost.actual_sources(wo.name, now_datetime())
    consumed = sum(flt(r['qty']) for r in sources)
    from calco_erp.calco_production.physical_closure_state import material_state
    closure = material_state(doc)
    if closure is not None:
        consumed = closure['actual_consumption']
    received = lots.received_fg(wo.name)
    quality = qc.state_for_work_order(wo)
    return dict(enabled=True, ended=ended(doc), state='Finalized' if doc.docstatus == 1 else
        'Physically Ended — Finalization Pending' if ended(doc) else 'Physical execution',
        planned_qty=flt(wo.qty), cumulative_fg_qty=state['fg_qty'], received_qty=received,
        unreceived_qty=max(state['fg_qty']-received, 0), actual_consumption=consumed,
        remaining_wip=wip['totals']['wo_attributed_available_qty'], recovery={k:v for k,v in state['components'].items() if k != 'quantity'},
        output=state, consumption=sources, closure=closure, qc=quality, reporting=reporting_blockers(doc), history=events(doc))


@frappe.whitelist()
@atomic
def confirm_physical_end(job_card, confirmed=0):
    if str(confirmed) not in {'1', 'True', 'true'}: frappe.throw('Confirm that physical Compounding has ended.')
    doc = get_card(job_card)
    if ended(doc): frappe.throw('Physical End is already recorded.')
    if not any(r.from_time for r in doc.time_logs): frappe.throw('Compounding has not started.')
    from calco_erp.calco_production import execution_policy as policy, partial_fg_lots as lots
    policy.assert_saved_context(doc)
    timestamp = now_datetime()
    with action(doc, 'Physical End'), policy.action_context(doc, 'Pause', timestamp):
        warnings = policy.execution_warnings(doc)
        policy._event(doc, 'Pause', warnings, timestamp)
        append(doc, 'Physical End', timestamp, output=lots.fg_state(doc), cutoff=str(timestamp),
            closed_logs=[{'name':r.name, 'from_time':str(r.from_time)} for r in doc.time_logs if r.from_time and not r.to_time],
            warnings=warnings, runtime_verified=False)
        for row in doc.time_logs:
            if row.from_time and not row.to_time: row.to_time = timestamp
        doc.is_paused = 1; doc.status = 'On Hold'
        policy.validate_physical_timers(doc); doc.save(); policy.protect(doc); protect(doc)
        from calco_erp.calco_production.production_readiness import _sync_controlled_workstation_status
        _sync_controlled_workstation_status(doc)
    return {'name': doc.name, 'state': 'Physically Ended — Finalization Pending', 'ended_at': str(timestamp)}


@frappe.whitelist()
@atomic
def reopen(job_card, reason):
    if not (reason or '').strip(): frappe.throw('A reason for reopening physical execution is required.')
    doc = get_card(job_card, manager=True)
    event = end_event(doc)
    if not event: frappe.throw('Only physically ended execution can be reopened.')
    with action(doc, 'Reopen'):
        append(doc, 'Reopen', now_datetime(), reason=reason.strip(), physical_end=event['at'])
        # The normal Resume path rechecks machine, planning, WIP, Grade Change and Quality Hold.
        doc.resume_job()
    return {'name': doc.name}


def final_state(doc):
    state = preview(doc.name); blockers = []
    if not ended(doc): blockers.append('Confirm Physical End first')
    if any(r.from_time and not r.to_time for r in doc.time_logs): blockers.append('Open execution intervals remain')
    if not state['qc'].get('ready'): blockers.append('Mandatory IPQC/EOB/OOS/Quality disposition is unresolved')
    blockers.extend(state['reporting'])
    from calco_erp.calco_production import partial_fg_lots as lots
    closure = state.get('closure')
    reconciliation = closure if closure is not None else wip_reconciliation(doc.work_order)
    if not reconciliation['reconciled']: blockers.append('WIP reconciliation: ' + reconciliation['reason'])
    if any(state['recovery'].values()):
        blockers.append('Recovery/sample disposition and non-overlapping mass reconciliation remain required; these categories are not FG or automatic loss')
    loss = approved_loss(doc, state)
    if abs(state['actual_consumption']-state['cumulative_fg_qty']-loss) > 1e-6:
        blockers.append('Actual consumption differs from FG: controlled recovery/waste/loss disposition evidence is required')
    if state['cumulative_fg_qty'] + loss > flt(doc.for_quantity)+1e-6:
        blockers.append('Resolved FG plus evidenced loss exceeds native Job Card quantity authority')
    wo = frappe.get_doc('Work Order', doc.work_order)
    if state['cumulative_fg_qty'] < flt(wo.qty)-1e-6:
        if not (wo.get('custom_partial_production_approved') and
                abs(flt(wo.get('custom_partial_production_qty'))-state['cumulative_fg_qty']) < 1e-6 and
                all(wo.get('custom_partial_production_'+f) for f in ['reason','approved_by','approved_on'])):
            blockers.append('Existing short-production approval must authorize the resolved FG quantity')
    if abs(state['received_qty']-state['cumulative_fg_qty']) > 1e-6:
        blockers.append('Receive only the remaining confirmed FG through controlled lot confirmation before finalization')
    if state['received_qty'] and closure is None:
        accounting = lots.cost.reconcile(lots.cost.actual_sources(wo.name, now_datetime()), lots.prior_allocations(wo.name))
        if accounting['available_qty'] > lots.cost.TOL or accounting['available_value'] > lots.cost.TOL:
            blockers.append('Actual RM consumption quantity/value remains unattributed to valid FG lots')
    # Accounting/repost integrity remains enforced by settlement, not physical completion.
    return dict(state, blockers=blockers, ready=not blockers, process_loss_qty=loss)


@frappe.whitelist()
def finalization_preview(job_card):
    doc = frappe.get_doc('Job Card', job_card); doc.check_permission('read')
    if not controlled(doc): frappe.throw('Controlled Compounding is required.')
    return final_state(doc)


@frappe.whitelist()
@atomic
def finalize(job_card):
    from calco_erp.calco_production import partial_fg_lots as lots
    doc = get_card(job_card, manager=True, allow_finalized=True); doc.check_permission('submit')
    if doc.docstatus == 1:
        evidence = [e for e in events(doc) if e.get('action') == 'Finalize']
        if doc.status != 'Completed' or not evidence or abs(flt(evidence[-1]['total_fg_qty'])-flt(doc.total_completed_qty)) > 1e-6:
            frappe.throw('Submitted Job Card has no matching controlled finalization evidence.')
        lots.reconcile_run(doc.name)
        return {'name':doc.name, 'completed_qty':flt(doc.total_completed_qty), 'remaining_receipt_qty':0, 'reused':True}
    state = final_state(doc)
    if not state['ready']: frappe.throw('Finalization blocked: ' + '; '.join(state['blockers']))
    if doc.sub_operations or doc.track_semi_finished_goods:
        frappe.throw('Sub-operation/semi-finished completion requires its existing controlled quantity reconciliation.')
    # Standard strict timer validation remains authoritative at finalization.
    doc.validate_time_logs()
    qty = state['cumulative_fg_qty']
    credited = sum(flt(r.completed_qty) for r in doc.time_logs)
    if credited > qty + 1e-6: frappe.throw('Previously credited FG exceeds effective output.')
    with action(doc, 'Finalize'):
        doc.time_logs[-1].completed_qty = flt(doc.time_logs[-1].completed_qty) + qty - credited
        doc.total_completed_qty = qty
        doc.process_loss_qty = state['process_loss_qty']
        doc.pending_qty = max(flt(doc.for_quantity)-qty-doc.process_loss_qty, 0)  # unproduced shortfall, never physical loss
        doc.is_paused = 0
        append(doc, 'Finalize', now_datetime(), output=state['output'], consumption=state['consumption'], closure=state.get('closure'),
            total_fg_qty=qty, received_fg_qty=state['received_qty'], remaining_receipt_qty=0,
            process_loss_qty=doc.process_loss_qty, unproduced_shortfall=max(flt(doc.for_quantity)-qty,0))
        doc.submit()
    # Native Job Card submission writes In Process after the last partial receipt.
    # Reuse the existing guarded reconciliation/status path after operation credit.
    lots.reconcile_run(doc.name)
    return {'name':doc.name, 'completed_qty':qty, 'remaining_receipt_qty':0}


class PhysicalCompletionJobCardMixin:
    def before_validate(self):
        quantity_guard(self)
        return super().before_validate()

    def validate(self):
        protect(self)
        return super().validate()

    def set_process_loss(self):
        if controlled(self):
            protect(self)
            return  # target minus FG is not physical-loss evidence
        return super().set_process_loss()

    def before_submit(self):
        if controlled(self):
            if not authorized(self) or self.flags.physical_completion_action != 'Finalize':
                frappe.throw('Use Finalize Job Card after physical end and reconciliation.')
            quantity_guard(self)
        parent = getattr(super(), 'before_submit', None)
        if parent: return parent()

    @frappe.whitelist()
    def complete_job_card(self, **kwargs):
        if controlled(self):
            frappe.throw('Use Confirm Physical End; native target-based completion is disabled for controlled Compounding.')
        return super().complete_job_card(**kwargs)

    def validate_complete_job_card_qty(self, kwargs):
        if controlled(self): frappe.throw('Use controlled physical end and finalization.')
        return super().validate_complete_job_card_qty(kwargs)

    def add_time_logs(self, *args, **kwargs):
        if controlled(self) and flt(kwargs.get('completed_qty')) and not authorized(self):
            frappe.throw('Time-log FG credit requires controlled finalization.')
        return super().add_time_logs(*args, **kwargs)

    def validate_time_logs(self, save=False):
        quantity_guard(self)
        return super().validate_time_logs(save=save)

    def set_status(self, *args, **kwargs):
        result = super().set_status(*args, **kwargs)
        if controlled(self) and self.docstatus == 1 and any(e['action']=='Finalize' for e in events(self)):
            self.status = 'Completed'
        return result


def wip_reconciliation(work_order):
    """Reconcile this WO's ledger attribution; another WO may share the physical Batch."""
    from calco_erp.inventory.availability import get_work_order_wip_lineage
    rows = get_work_order_wip_lineage(work_order)
    blockers = []
    if not rows: blockers.append('No submitted WO-attributed WIP evidence exists')
    for (item, batch), row in rows.items():
        remaining = flt(row['transferred_qty'])-flt(row['consumed_qty'])-flt(row['returned_qty'])
        if abs(remaining)>1e-6 or abs(flt(row['net_wip_qty']))>1e-6:
            blockers.append(f"{item}/{batch}: transferred {row['transferred_qty']}, consumed {row['consumed_qty']}, returned {row['returned_qty']}, remaining {remaining}")
    return {'reconciled':not blockers,'reason':'; '.join(blockers)}


def mass_identity(state):
    # Source cutoff is a query bound, not part of immutable consumption identity.
    return {'output':state['output'], 'consumption':[{k:v for k,v in r.items() if k!='cutoff'} for r in state['consumption']]}


def approved_loss(doc, state):
    for event in reversed(events(doc)):
        if event['action'] in {'Reopen','Physical End'}: break
        if event['action']=='Approve Process Loss' and event['basis']==mass_identity(state):
            return flt(event['measured_loss_qty'])
    return 0.0


@frappe.whitelist()
@atomic
def approve_process_loss(job_card, measured_loss_qty, reason):
    doc=get_card(job_card,manager=True)
    if not ended(doc):frappe.throw('Confirm Physical End before approving measured process loss.')
    if not (reason or '').strip():frappe.throw('Measured loss evidence and explanation are required.')
    qty=flt(measured_loss_qty);state=preview(job_card)
    if not math.isfinite(qty) or qty<=0:frappe.throw('Enter the measured positive process loss; no target shortfall is assumed.')
    if any(state['recovery'].values()):frappe.throw('Resolve recovery/sample disposition first; it cannot be approved as process loss.')
    if abs(state['actual_consumption']-state['cumulative_fg_qty']-qty)>1e-6:
        frappe.throw('Measured loss does not reconcile submitted actual RM consumption to controlled FG.')
    if state['cumulative_fg_qty']+qty>flt(doc.for_quantity)+1e-6:frappe.throw('Quantity exceeds native Job Card authority.')
    with action(doc,'Approve Process Loss'):
        append(doc,'Approve Process Loss',now_datetime(),measured_loss_qty=qty,reason=reason.strip(),basis=mass_identity(state))
        doc.save()
    return {'name':doc.name,'approved_loss_qty':qty}


def receipt_valuation_blockers(work_order):
    blockers=[]
    for row in frappe.get_all('Stock Entry',filters={'work_order':work_order,'purpose':'Manufacture','docstatus':1},fields=['name','custom_partial_fg_lot','total_additional_costs']):
        if not row.custom_partial_fg_lot:
            blockers.append('Legacy receipt requires explicit lot/valuation reconciliation: '+row.name)
            continue
        lot=frappe.get_doc('Partial FG Lot',row.custom_partial_fg_lot)
        if lot.docstatus!=1:
            blockers.append('Receipt has no valid submitted lot: '+row.name)
            continue
        from calco_erp.calco_production.partial_lot_reconciliation import receipt_valuation
        if not receipt_valuation(row.name)['ledger_reconciled']:
            blockers.append('Receipt Stock Ledger value differs from material, conversion and submitted valuation adjustments: '+row.name)
    return blockers


def protect_output_floor(card, rows):
    if not controlled(card) or not flt(card.total_completed_qty):return
    from calco_erp.calco_production.partial_fg_lots import fg_progress_rows
    if fg_progress_rows(rows)['current']+1e-6<flt(card.total_completed_qty):
        frappe.throw('Output correction would fall below credited native FG. Resolve final Job Card authority through the controlled lifecycle first.')
