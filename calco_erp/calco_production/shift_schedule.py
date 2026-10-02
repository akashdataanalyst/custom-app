"""Resolve operational occurrences from the site/server timestamp, never browser date."""
import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, cstr, now_datetime
from datetime import timedelta


def active_occurrences(shifts, timestamp):
    from calco_erp.calco_production.shift_reporting import _combine
    current = get_datetime(timestamp)
    result = []
    for row in shifts:
        if row.get('start_time') is None or row.get('end_time') is None:
            continue
        for day in (current.date()-timedelta(days=1), current.date()):
            start = _combine(day,row['start_time']);end = _combine(day,row['end_time'])
            if end <= start:end += timedelta(days=1)
            if start <= current < end:
                result.append({'shift':row['name'],'operational_shift_date':str(day),'shift_start':str(start),'shift_end':str(end)})
    return result


def current_options(card, timestamp=None):
    timestamp = get_datetime(timestamp or now_datetime())
    shifts = frappe.get_all('Shift Type',fields=['name','start_time','end_time'],order_by='name asc')
    choices = active_occurrences(shifts,timestamp)
    preferred = cstr(card.get('custom_shift_type')).strip()
    selected = preferred if any(r['shift']==preferred for r in choices) else (choices[0]['shift'] if len(choices)==1 else '')
    return {'server_time':str(timestamp),'choices':choices,'selected_shift':selected,
            'message':_('No configured shift is active at the current server time.') if not choices else ''}


def resolve(card, shift='', operational_date=None, timestamp=None):
    options = current_options(card,timestamp)
    if not options['choices']:frappe.throw(options['message'])
    selected = cstr(shift).strip() or options['selected_shift']
    if not selected:frappe.throw(_('Multiple shifts are active. Select the operational shift for this run.'))
    occurrence = next((r for r in options['choices'] if r['shift']==selected),None)
    if not occurrence:frappe.throw(_('Selected shift is not active at the current server time. Refresh the active shift choices.'))
    if operational_date and getdate(operational_date)!=getdate(occurrence['operational_shift_date']):
        frappe.throw(_('Live Shift Reports must use the current operational shift date resolved by the server.'))
    return occurrence


@frappe.whitelist()
def get_active_shift_options(job_card):
    from calco_erp.calco_production import shift_reporting as sr
    card,wo = sr._get_controlled_context(job_card)
    card.check_permission('read')
    sr._validate_creation_authority(card,wo)
    if not frappe.has_permission('Shift Report','create'):
        frappe.throw(_('Shift Report creation permission is required.'),frappe.PermissionError)
    return report_options(card)


def matching_report(reports, occurrence):
    """Resolve only this operational identity; preserve correction chains."""
    matches=[r for r in reports if r.get('status') not in {'Superseded','Abandoned'}
             and str(getdate(r.get('operational_shift_date')))==occurrence['operational_shift_date']
             and r.get('shift')==occurrence['shift']]
    ancestors={r.get('correction_of') for r in matches if r.get('correction_of')}
    matches=[r for r in matches if r['name'] not in ancestors]
    if len(matches)>1:
        frappe.throw(_('Multiple current Shift Reports exist for this exact operational identity. Resolve the duplicate/correction evidence.'))
    if not matches:return None
    report=matches[0]
    if any(get_datetime(report.get(f))!=get_datetime(occurrence[f]) for f in ('shift_start','shift_end')):
        frappe.throw(_('Existing Shift Report window differs from the current Shift Type schedule. Review the schedule; do not retime historical evidence.'))
    return report


def report_options(card, reports=None):
    options=current_options(card)
    if reports is None:
        reports=frappe.get_all('Shift Report',filters={'job_card':card.name},
            fields=['name','status','shift','operational_shift_date','shift_start','shift_end','correction_of'])
    for occurrence in options['choices']:
        report=matching_report(reports,occurrence)
        occurrence['current_report']=report['name'] if report else ''
        occurrence['report_status']=report['status'] if report else ''
    existing=[r for r in options['choices'] if r['current_report']]
    if not options['selected_shift'] and len(existing)==1:
        options['selected_shift']=existing[0]['shift']
    return options


def current_report_resolution(card, shift='', reports=None):
    options=report_options(card,reports)
    selected=cstr(shift).strip() or options['selected_shift']
    occurrence=next((r for r in options['choices'] if r['shift']==selected),None)
    if shift and not occurrence:
        frappe.throw(_('Selected shift is no longer active. Refresh the current shift choices.'))
    return {**options,'selected_shift':selected,'choice_required':bool(options['choices'] and not occurrence),
            'occurrence':occurrence,'current_report':occurrence['current_report'] if occurrence else '',
            'action':'Open Current Shift Report' if occurrence and occurrence['current_report'] else 'New Current Shift Report'}


@frappe.whitelist()
def get_current_shift_report(job_card, shift=''):
    from calco_erp.calco_production import shift_reporting as sr
    card,wo=sr._get_controlled_context(job_card)
    card.check_permission('read')
    result=current_report_resolution(card,shift)
    if result['current_report']:
        frappe.get_doc('Shift Report',result['current_report']).check_permission('read')
    return result
