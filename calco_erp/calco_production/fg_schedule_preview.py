"""Detached ERPNext scheduling. No insert/save/db_set or execution records."""
from types import MethodType
import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime, date_diff, getdate
from erpnext.manufacturing.doctype.job_card.job_card import JobCard
from erpnext.manufacturing.doctype.work_order.work_order import WorkOrder

REQUESTED = 'custom_fg_requested_start'
VERSION = 'erpnext-feasible-preview-v1'


def _calculate_once(values):
    requested = values.get(REQUESTED) or values.get('planned_start_date')
    if not requested or flt(values.get('qty')) <= 0 or not values.get('bom_no'):
        frappe.throw('Resolve quantity, BOM, production line and requested start before scheduling.')
    line = values.get('custom_machine') or values.get('custom_production_line')
    if not line:
        frappe.throw('Select Production Line/Machine before scheduling.')
    bom = frappe.get_doc('BOM', values.get('bom_no'))
    if bom.docstatus != 1 or not bom.is_active or bom.item != values.get('production_item') or bom.company != values.get('company'):
        frappe.throw('Select an active submitted BOM matching this company and FG.')
    # Use the same operation loader and quantity/fixed-time scaling as standard WO.
    wo = WorkOrder({'doctype':'Work Order', 'company':values.get('company'),
        'production_item':values.get('production_item'), 'bom_no':bom.name, 'qty':flt(values.get('qty')),
        'planned_start_date':str(get_datetime(requested)),
        'use_multi_level_bom':cint(values.get('use_multi_level_bom')), 'track_semi_finished_goods':0})
    if values.get('track_semi_finished_goods'):
        frappe.throw('Feasible preview for semi-finished routing is not supported; resolve routing before planning approval.')
    wo.set_work_order_operations()
    if not wo.operations:
        frappe.throw('BOM operations are required for feasible scheduling.')
    settings = frappe.get_single('Manufacturing Settings')
    capacity = not cint(settings.disable_capacity_planning)
    limit = cint(settings.capacity_planning_for_days) or 30
    ephemeral, result = [], []
    for idx, row in enumerate(wo.operations):
        # Selected production line must match the BOM route, never silently override another workstation.
        if row.workstation and row.workstation != line:
            frappe.throw('Selected production line is incompatible with BOM operation workstation.')
        row.workstation = line
        batch_enabled = cint(frappe.db.get_value('Operation',row.operation,'create_job_card_based_on_batch_size'))
        batch_size = flt(row.batch_size) if batch_enabled else wo.qty
        if batch_size <= 0 or flt(row.time_in_mins) <= 0:
            frappe.throw('Positive operation duration and batch size are required.')
        remaining = wo.qty
        batches = 0
        while remaining > 1e-9:
            batches += 1
            if batches > 1000:
                frappe.throw('Scheduling preview exceeds 1000 operation batches.')
            job_qty = min(batch_size, remaining)
            remaining -= job_qty
            wo.set_operation_start_end_time(row,idx)
            card = JobCard({'doctype':'Job Card','workstation':line,'operation':row.operation,
                           'for_quantity':job_qty,'scheduled_time_logs':[]})
            def time_logs(self,args,doctype,open_job_cards=None):
                logs = JobCard.get_time_logs(self,args,doctype,open_job_cards)
                if doctype == 'Job Card Scheduled Time':
                    logs += [r for r in ephemeral if r.workstation == self.workstation
                             and r.from_time < get_datetime(args.to_time) and r.to_time > get_datetime(args.from_time)]
                return sorted(logs,key=lambda r:r.to_time)
            card.get_time_logs = MethodType(time_logs,card)
            # Bound recursive slot searching and the standard calendar loop without changing its rules.
            original_check = card.check_workstation_time
            original_overlap = card.validate_overlap_for_workstation
            counter = [0]
            def guard():
                counter[0] += 1
                if counter[0] > 500 or date_diff(row.planned_start_time,requested) > limit:
                    frappe.throw('No feasible slot inside the standard capacity-planning horizon.')
            def checked(self,current):
                guard();return original_check(current)
            def overlap(self,args,current):
                guard();return original_overlap(args,current)
            card.check_workstation_time = MethodType(checked,card)
            card.validate_overlap_for_workstation = MethodType(overlap,card)
            if capacity:
                card.schedule_time_logs(row)
                slots = card.scheduled_time_logs
                row.planned_start_time = slots[-1].from_time
                row.planned_end_time = slots[-1].to_time
            else:
                slots = [frappe._dict(from_time=row.planned_start_time,to_time=row.planned_end_time)]
            if date_diff(row.planned_end_time,requested) > limit and capacity:
                frappe.throw('No feasible slot inside the standard capacity-planning horizon.')
            for slot in slots:
                entry = frappe._dict(name=f'preview-{len(ephemeral)}',workstation=line,
                    from_time=get_datetime(slot.from_time),to_time=get_datetime(slot.to_time))
                ephemeral.append(entry)
            result.append({'operation':row.operation,'sequence_id':row.sequence_id,'workstation':line,
                'quantity':job_qty,'time_in_mins':row.time_in_mins,
                'start':str(get_datetime(slots[0].from_time)),'end':str(get_datetime(slots[-1].to_time))})
    workstation = frappe.get_doc('Workstation',line)
    holiday = bool(capacity and not cint(settings.allow_production_on_holidays) and workstation.holiday_list and frappe.db.exists('Holiday', {'parent':workstation.holiday_list,'holiday_date':getdate(requested)}))
    start = min(r['start'] for r in result)
    end = str(get_datetime(wo.operations[-1].planned_end_time))
    return {'version':VERSION,'calculated_on':str(now_datetime()),'requested_start':str(get_datetime(requested)),
        'start':start,'end':end,'operations':result,
        'operation_rows':[r.as_dict() for r in wo.operations],
        'calendar':{'workstation':line,'holiday_list':workstation.holiday_list,
                    'capacity':workstation.production_capacity,'capacity_planning':capacity,
                    'allow_overtime':settings.allow_overtime,'allow_production_on_holidays':settings.allow_production_on_holidays,
                    'configured_gap_minutes':settings.mins_between_operations,
                    'effective_gap_minutes':cint(settings.mins_between_operations) or 10},
        'notice':(f'{getdate(requested)} is a holiday/non-production day. ' if holiday else '') + ('Requested start moved by ERPNext working-calendar/capacity rules.' if start != str(get_datetime(requested)) else '')}


def calculate(values):
    # Re-evaluate from the resolved first slot: standard Submit starts there too.
    # This also checks capacity on a date reached through holiday adjustment.
    values = dict(values)
    requested = str(get_datetime(values.get(REQUESTED) or values.get('planned_start_date')))
    first_notice = ''
    for attempt in range(8):
        result = _calculate_once(values)
        first_notice = first_notice or result['notice']
        if result['start'] == result['requested_start']:
            result['requested_start'] = requested
            result['notice'] = first_notice
            return result
        values[REQUESTED] = result['start']
    frappe.throw('Capacity changed repeatedly during preview. Refresh scheduling and try again.')

@frappe.whitelist()
def preview(work_order):
    values = frappe.parse_json(work_order)
    if values.get('name') and frappe.db.exists('Work Order',values['name']):
        stored = frappe.get_doc('Work Order',values['name']);stored.check_permission('read')
        if stored.docstatus != 0:
            frappe.throw('Use scheduling preview on Draft Work Orders only.')
    else:
        frappe.has_permission('Work Order','create',throw=True)
    frappe.get_doc('BOM',values.get('bom_no')).check_permission('read')
    from calco_erp.calco_production.fg_planning_authority import compatible_boms
    allowed = compatible_boms(values.get('production_item'),values.get('company'))
    if not any(r['bom_no']==values.get('bom_no') and r['production_line']==(values.get('custom_machine') or values.get('custom_production_line')) for r in allowed['candidates']):
        frappe.throw('Select a compatible BOM and production line for this FG.')
    result = calculate(values)
    if values.get('name') and frappe.db.exists('Work Order', values['name']):
        from calco_erp.calco_production import fg_planning_authority as authority
        if authority.is_dashboard(stored):
            content = authority.verify(stored)
            last = next((e for e in reversed(content['events']) if e['event'] == 'Revision'), None)
            approved = next((e for e in reversed(content['events']) if e['event'] == 'Approved'), None)
            projected = frappe.get_doc(stored.as_dict())
            for key in authority.revision(stored):
                if key in values:
                    projected.set(key, values[key])
            projected.set(REQUESTED, result['requested_start'])
            projected.planned_start_date = result['start']
            projected.planned_end_date = result['end']
            result['reason_required'] = bool(last and approved and not authority.revision_matches(projected, last, content))
            if approved:
                result['approved_schedule'] = approved.get('values') or next((e.get('values', {}) for e in reversed(content['events']) if e['event'] == 'Revision' and e['revision'] == approved['revision']), {})
    return result


def resolve(doc, check_only=False):
    preview = calculate(doc.as_dict())
    same = (get_datetime(doc.planned_start_date)==get_datetime(preview['start'])
            and doc.planned_end_date and get_datetime(doc.planned_end_date)==get_datetime(preview['end']))
    if check_only:
        expected = [(r['operation'],r['workstation'],flt(r['time_in_mins'])) for r in preview['operation_rows']]
        actual = [(r.operation,r.workstation,flt(r.time_in_mins)) for r in doc.operations]
        if expected != actual:
            frappe.throw('BOM operation schedule changed. Preview and save the Work Order before approval/Submit.')
        if not same:
            frappe.throw('Feasible schedule changed. Preview and save a schedule revision, then obtain planning approval before Submit.')
        return preview
    doc.set(REQUESTED,preview['requested_start'])
    doc.planned_start_date=preview['start'];doc.planned_end_date=preview['end']
    old={(r.idx,r.operation):r.name for r in doc.operations}
    rows=[]
    for r in preview['operation_rows']:
        r=dict(r)
        r['name']=old.get((r['idx'],r['operation']))
        for key in ('parent','parentfield','parenttype','creation','modified','owner','modified_by'):
            r.pop(key,None)
        # Execution scheduling rows are created only by standard Submit, not this preview.
        r['planned_start_time']=None;r['planned_end_time']=None
        rows.append(r)
    doc.set('operations',rows)
    return preview


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Work Order':[{'fieldname':REQUESTED,'label':'Requested Production Start',
        'fieldtype':'Datetime','insert_after':'planned_start_date','allow_on_submit':0,
        'depends_on':'eval:doc.custom_fg_planning_origin == "fg-dashboard-v1" || doc.__fg_dashboard_context'}]},update=True)
