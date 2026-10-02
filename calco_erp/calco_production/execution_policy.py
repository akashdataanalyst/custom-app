"""Physical execution policy: machine safety is independent of labour warnings."""
import copy
import json
from collections import defaultdict
from contextlib import contextmanager

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime, time_diff_in_hours
from calco_erp.calco_production import stopped_execution as locks

FIELD = 'custom_execution_policy_audit'
_TOKEN = object()


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Job Card':[{'fieldname':FIELD,'label':'Execution Warning / Pause Audit',
        'fieldtype':'Long Text','read_only':1,'no_copy':1,'insert_after':locks.AUDIT_FIELD,
        'description':'Server-controlled physical execution events. Historical time anomalies require review before authoritative runtime finalization.'}]},update=True)


def physical(doc):
    return (getattr(doc,'flags',None) or {}).get('physical_execution_token') is _TOKEN


def pausing(doc):
    return physical(doc) and doc.flags.physical_execution_action == 'Pause'


def _equipment_state(machine):
    return frappe.db.get_value('Workstation',machine,['status','disabled'],as_dict=True)


def _current_machine_cards(machine):
    return frappe.db.sql('''select jc.name,jc.status,jc.is_paused,jc.operation,
        exists(select 1 from `tabJob Card Time Log` tl where tl.parent=jc.name
          and tl.parenttype='Job Card' and tl.parentfield='time_logs'
          and tl.from_time is not null and tl.to_time is null) as has_open
        from `tabJob Card` jc where jc.workstation=%s and jc.docstatus<2 lock in share mode''',machine,as_dict=True)


def machine_state(doc, exclude_self=False):
    machine = doc.get('workstation') or doc.get('custom_machine')
    if not machine:
        frappe.throw('A valid machine/workstation is required for execution.')
    state = _equipment_state(machine)
    if not state:
        frappe.throw('A valid machine/workstation is required for execution.')
    # Current reads are serialized by the workstation lock for physical actions.
    cards = _current_machine_cards(machine)
    running=[r for r in cards if (not exclude_self or r.name!=doc.name) and
             (r.has_open or (not cint(r.is_paused) and r.status in locks.RUNNING))]
    safety=bool(state.disabled or state.status in {'Maintenance','Problem'})
    paused=any(cint(r.is_paused) for r in cards)
    status=state.status if safety else 'Production' if running else 'Idle' if paused or state.status=='Production' else state.status
    return {'workstation':machine,'effective_status':status,
            'operational_for_resume':not safety and not running and status not in {'Off','Maintenance','Problem'},
            'reason':'safety_state' if safety else 'running_job_card' if running else 'paused_controlled_job_card' if paused else 'standard_state',
            'blocking_job_cards':[r.name for r in running]}


def assert_machine_free(doc):
    state=machine_state(doc,exclude_self=True)
    if not state['operational_for_resume']:
        frappe.throw('Machine {0} cannot Start/Resume: {1}. Blocking Job Cards: {2}.'.format(
            state['workstation'],state['effective_status'],', '.join(state['blocking_job_cards']) or 'none (equipment state)'))
    return state


def assert_saved_context(doc):
    saved=frappe.get_doc('Job Card',doc.name)
    fields=('work_order','operation','operation_id','bom_no','workstation','custom_machine','custom_production_line',
            'for_quantity','total_completed_qty','process_loss_qty','custom_fg_batch_no','docstatus','is_paused','status','time_logs',FIELD)
    if any(locks._canonical(saved.get(f))!=locks._canonical(doc.get(f)) for f in fields):
        frappe.throw('Save/reload production configuration before using execution controls. Historical time edits require normal validation.')
    return saved


def validate_lineage(doc, resume=False):
    from calco_erp.calco_production import production_readiness as pr, in_process_quality as qc
    from calco_erp.calco_production.wip_consumption import is_controlled_work_order, get_execution_wip_context
    wo=frappe.get_doc('Work Order',doc.work_order)
    machine=doc.workstation
    if (doc.bom_no and doc.bom_no!=wo.bom_no) or any(wo.get(f) and wo.get(f)!=machine for f in ('custom_machine','custom_production_line')):
        frappe.throw('Job Card BOM/production line differs from Work Order authority.')
    op=next((r for r in wo.operations if r.name==doc.operation_id),None)
    if wo.operations and (not op or op.operation!=doc.operation or (op.workstation and op.workstation!=machine)):
        frappe.throw('Job Card operation/workstation does not match its Work Order operation.')
    planning=pr._evaluate_planning(wo,doc.bom_no or wo.bom_no)
    if not planning['ready']:
        frappe.throw(' '.join(planning['blockers']))
    qc.assert_no_hold(wo.name)
    if not resume:
        if doc.operation=='Compounding / Extrusion':
            from calco_erp.calco_production.grade_change_control import assert_grade_change_approved
            assert_grade_change_approved(doc,'first Compounding Start')
            qc.prepare_start(doc)
            from calco_erp.fg_batch_setup import get_line_number
            get_line_number(wo)
        pr.assert_production_readiness(wo,persist=False,overrides={'material_warehouse':'consumed' if doc.operation=='Packing' else 'wip','physical_execution':True})
        return
    if doc.operation=='Compounding / Extrusion':
        from calco_erp.calco_production.grade_change_control import assert_grade_change_approved
        assert_grade_change_approved(doc,'resuming production (existing clearance authority)')
    if is_controlled_work_order(wo.name):
        batch=wo.get('custom_fg_batch_no')
        if not batch or (doc.get('custom_fg_batch_no') and doc.custom_fg_batch_no!=batch) or frappe.db.get_value('Batch',batch,'item')!=wo.production_item:
            frappe.throw('Valid, matching production-start FG batch lineage is required to Resume.')
        context=get_execution_wip_context(wo.name)
        available=defaultdict(float)
        for row in context['rows']:
            available[row['item_code']]+=flt(row['wo_attributed_available_qty'])
        required,consumed=defaultdict(float),defaultdict(float)
        effective_qty,_=pr._effective_quantity(wo)
        scale=flt(effective_qty)/flt(wo.qty) if flt(wo.qty) else 1
        for row in wo.required_items:
            if not frappe.db.get_value('Item',row.item_code,'is_stock_item'):continue
            required[row.item_code]+=flt(row.required_qty)*scale
            consumed[row.item_code]+=flt(row.consumed_qty)
        missing=[item for item,qty in required.items() if available[item]+consumed[item]+1e-6<qty]
        if missing:
            frappe.throw('WO-attributed eligible WIP/batch lineage is insufficient for Resume: '+', '.join(missing))
    if qc.is_parallel(wo):
        plan=qc.frozen_plan(wo)
        if plan['job_card']!=doc.name and doc.operation=='Compounding / Extrusion':
            frappe.throw('Frozen IPQC plan belongs to another Compounding Job Card.')
        if plan.get('batch')!=wo.get('custom_fg_batch_no'):
            frappe.throw('Frozen IPQC batch lineage differs from the Work Order.')


def _employee_intervals(name,employees):
    return frappe.db.sql('''select jc.name as job_card,jc.workstation,jc.status,jc.is_paused,jc.docstatus,
            tl.name as time_log,tl.employee,tl.from_time,tl.to_time
            from `tabJob Card Time Log` tl join `tabJob Card` jc on jc.name=tl.parent
            where tl.parenttype='Job Card' and tl.parentfield='time_logs' and tl.from_time is not null
            and tl.employee in %(employees)s and jc.name!=%(name)s and jc.docstatus<2
            order by tl.from_time''',{'employees':employees,'name':name},as_dict=True)


def execution_warnings(doc, selected_employees=None):
    selected=selected_employees or []
    if isinstance(selected,str):
        selected=json.loads(selected) if selected.startswith('[') else [{'employee':selected}]
    employees=sorted({r.employee for r in (doc.time_logs or [])+(doc.employee or []) if r.employee} |
                     {r.get('employee') for r in selected if r.get('employee')})
    warnings=[]
    if employees:
        rows=_employee_intervals(doc.name,employees)
        assignments={}
        for row in rows:
            key=(row.employee,row.job_card)
            if row.docstatus==0 and (key not in assignments or not row.to_time or assignments[key].to_time):
                assignments[key]=row
            for own in doc.time_logs:
                if own.employee!=row.employee or not own.from_time or not own.to_time or not row.to_time:continue
                if get_datetime(own.from_time)<get_datetime(row.to_time) and get_datetime(row.from_time)<get_datetime(own.to_time):
                    warnings.append({'kind':'Historical overlap','employee':row.employee,'job_card':row.job_card,
                        'machine':row.workstation,'time_log':row.time_log,'own_time_log':own.name,
                        'from_time':str(row.from_time),'to_time':str(row.to_time),'runtime_verified':False})
        for row in assignments.values():
            warnings.append({'kind':'Current employee assignment' if not row.to_time else 'Historical employee assignment (not current)',
                'employee':row.employee,'job_card':row.job_card,'machine':row.workstation,'time_log':row.time_log,
                'from_time':str(row.from_time),'to_time':str(row.to_time) if row.to_time else None})
    for dt in ('Shift Report','Premix Run','Blending Run','Silo Control','Feeder Run','Process Parameter Monitor','Bulk Density Monitor'):
        if not frappe.db.table_exists(dt) or not frappe.get_meta(dt).has_field('job_card'):continue
        for row in frappe.get_all(dt,filters={'job_card':doc.name,'status':['in',['Draft','Active','In Progress','Awaiting Approval']]},fields=['name','status']):
            warnings.append({'kind':'Reporting / process finalization pending','doctype':dt,'document':row.name,'status':row.status})
    wo=frappe.get_doc('Work Order',doc.work_order)
    now=now_datetime()
    if (wo.planned_start_date and now<get_datetime(wo.planned_start_date)) or (wo.planned_end_date and now>get_datetime(wo.planned_end_date)):
        warnings.append({'kind':'Actual execution outside planned schedule','work_order':wo.name,
                         'planned_start':str(wo.planned_start_date),'planned_end':str(wo.planned_end_date)})
    return warnings


def _event(doc,action,warnings,timestamp):
    events=json.loads(doc.get(FIELD) or '[]')
    event={'action':action,'at':str(timestamp),'by':frappe.session.user,'warnings':warnings,
           'runtime_verified':False if any(w['kind']=='Historical overlap' for w in warnings) else None}
    if action=='Pause':
        event['closed_logs']=[{'name':r['name'],'from_time':str(r['from_time'])} for r in doc.flags.physical_before['time_logs'] if r.get('from_time') and not r.get('to_time')]
    events.append(event)
    doc.set(FIELD,json.dumps(events,sort_keys=True))
    doc.flags.physical_expected_audit=doc.get(FIELD)


def validate_physical_timers(doc):
    before={r['name']:r for r in doc.flags.physical_before.get('time_logs',[])}
    for row in doc.time_logs:
        old=before.get(row.name)
        if old and old.get('to_time'):
            if locks._canonical(row)!=locks._canonical(old):
                frappe.throw('Physical execution cannot rewrite historical time logs.')
        elif old:
            for f in ('from_time','employee','completed_qty','operation'):
                if locks._canonical(row.get(f))!=locks._canonical(old.get(f)):
                    frappe.throw('Physical execution cannot rewrite an existing timer identity or quantity.')
        else:
            if pausing(doc) or flt(row.completed_qty) or get_datetime(row.from_time)!=doc.flags.physical_timestamp:
                frappe.throw('Invalid new physical execution interval.')
        if row.to_time and get_datetime(row.from_time)>get_datetime(row.to_time):
            frappe.throw('From time must not be after To time.')
        if not old or not old.get('to_time'):
            if row.from_time and row.to_time:row.time_in_mins=time_diff_in_hours(row.to_time,row.from_time)*60
    if set(before)-{r.name for r in doc.time_logs}:
        frappe.throw('Physical execution cannot remove historical time logs.')
    doc.total_time_in_mins=sum(flt(r.time_in_mins) for r in doc.time_logs)
    if flt(doc.total_completed_qty)!=flt(doc.flags.physical_before['total_completed_qty']):
        frappe.throw('Timer actions cannot change production quantity.')


def prevent_delete(doc,method=None):
    if doc.get(FIELD):frappe.throw('Execution audit history must be retained; use controlled lifecycle actions.')


def protect(doc,method=None):
    previous=doc.get_doc_before_save()
    if not physical(doc):
        old_open={r.name for r in (previous.time_logs if previous else []) if r.from_time and not r.to_time}
        if any(r.from_time and not r.to_time and r.name not in old_open for r in doc.time_logs):
            frappe.throw('Use controlled Start/Resume to open execution; machine exclusivity must be checked.')
        if doc.get(FIELD)!=(previous.get(FIELD) if previous else None) and (doc.get(FIELD) or (previous and previous.get(FIELD))):
            frappe.throw('Execution warning evidence is server-controlled and immutable.')
        return
    validate_physical_timers(doc)
    if doc.get(FIELD)!=doc.flags.physical_expected_audit:
        frappe.throw('Execution audit was unexpectedly changed.')
    if pausing(doc):
        a,b=locks._canonical(doc.flags.physical_before),locks._canonical(doc)
        for value in (a,b):
            for f in ('time_logs','total_time_in_mins','status','is_paused',FIELD):value.pop(f,None)
        from calco_erp.calco_production import physical_completion as completion
        if completion.authorized(doc) and doc.flags.physical_completion_action == 'Physical End':
            a.pop(completion.FIELD,None);b.pop(completion.FIELD,None);completion.protect(doc)
        if a!=b or doc.status!='On Hold' or not doc.is_paused:
            frappe.throw('Unexpected production/configuration mutation during Pause.')


@contextmanager
def action_context(doc,action,timestamp):
    doc.flags.physical_execution_token=_TOKEN
    doc.flags.physical_execution_action=action
    doc.flags.physical_timestamp=timestamp
    doc.flags.physical_before=copy.deepcopy(doc.as_dict())
    try:yield
    finally:doc.flags.physical_execution_token=None


def preflight(doc,action):
    doc.check_permission('write');doc.validate_docstatus()
    locks.lock_execution(doc)
    assert_saved_context(doc)
    if action=='Pause':
        if not any(r.from_time and not r.to_time for r in doc.time_logs):frappe.throw('No current open Job Card interval to Pause.')
        return None
    from calco_erp.calco_production import physical_completion as completion
    if completion.ended(frappe.get_doc('Job Card',doc.name)) and not (completion.authorized(doc) and doc.flags.physical_completion_action=='Reopen'):
        frappe.throw('Physical execution has ended. Use Reopen Physical Execution with a reason.')
    if any(r.from_time and not r.to_time for r in doc.time_logs):frappe.throw('Job Card already has open execution. Pause it before starting another interval.')
    if action=='Resume' and not doc.is_paused:frappe.throw('Resume requires an On Hold Job Card.')
    if action=='Start' and any(r.from_time for r in doc.time_logs):frappe.throw('This Job Card already started. Use Resume Job.')
    state=assert_machine_free(doc)
    validate_lineage(doc,resume=any(r.from_time for r in doc.time_logs))
    return state


def _show(warnings):
    if warnings:
        lines=[]
        for w in warnings:
            lines.append(frappe.utils.escape_html(' | '.join(str(v) for k,v in w.items() if v is not None)))
        frappe.msgprint('<br>'.join(lines),title='Execution allowed with warnings',indicator='orange')


class ExecutionPolicyJobCardMixin:
    def before_validate(self):
        if pausing(self):return
        return super().before_validate()

    def on_update(self):
        if pausing(self):return protect(self)
        return super().on_update()

    def add_time_log(self,args):
        if args.get('start_time'):
            return self.start_timer(employees=args.get('employees'))
        return super().add_time_log(args)

    @frappe.whitelist()
    def start_timer(self,**kwargs):
        return self._physical_timer('Start',kwargs)

    @frappe.whitelist()
    def resume_job(self,**kwargs):
        return self._physical_timer('Resume',kwargs)

    def _physical_timer(self,action,kwargs):
        frappe.db.savepoint('physical_execution')
        try:
            preflight(self,action);timestamp=now_datetime();warnings=execution_warnings(self,kwargs.get('employees'))
            with action_context(self,action,timestamp):
                _event(self,action,warnings,timestamp)
                kwargs['start_time']=str(timestamp)
                result=super().start_timer(**kwargs) if action=='Start' else super().resume_job(**kwargs)
                protect(self)
            _show(warnings)
            return result
        except Exception:
            frappe.db.rollback(save_point='physical_execution');raise

    @frappe.whitelist()
    def pause_job(self,**kwargs):
        frappe.db.savepoint('physical_pause')
        try:
            preflight(self,'Pause');timestamp=now_datetime();warnings=execution_warnings(self)
            with action_context(self,'Pause',timestamp):
                _event(self,'Pause',warnings,timestamp)
                for row in self.time_logs:
                    if row.from_time and not row.to_time:row.to_time=timestamp
                self.is_paused=1;self.status='On Hold'
                validate_physical_timers(self);self.save();protect(self)
                from calco_erp.calco_production.production_readiness import _sync_controlled_workstation_status
                _sync_controlled_workstation_status(self)
            _show(warnings)
            return {'status':self.status,'closed_at':str(timestamp)}
        except Exception:
            frappe.db.rollback(save_point='physical_pause');raise

    def validate(self):
        if pausing(self):return protect(self)
        return super().validate()

    def before_save(self):
        if pausing(self):return protect(self)
        return super().before_save()

    def validate_time_logs(self,save=False):
        if physical(self):return validate_physical_timers(self)
        return super().validate_time_logs(save=save)


@frappe.whitelist()
def get_execution_preflight(job_card,action='Resume'):
    if action not in {'Start','Resume','Pause'}:frappe.throw('Invalid execution action.')
    doc=frappe.get_doc('Job Card',job_card)
    machine=preflight(doc,action)
    if action!='Pause':
        doc.validate_operation_id();doc.validate_sequence_id();doc.validate_work_order();doc.validate_job_card_qty()
    # Run the exact scoped timer validator in memory; no timer action or Save.
    timestamp=now_datetime()
    with action_context(doc,action,timestamp):validate_physical_timers(doc)
    return {'allowed':True,'action':action,'job_card':job_card,'machine':machine,'warnings':execution_warnings(doc),
            'message':'Hard gates passed. No timer action has been executed.'}
