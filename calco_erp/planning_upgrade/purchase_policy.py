"""Shared Purchase SLA authority; assignment activation is explicit and site-bound."""
from datetime import date, timedelta
from copy import deepcopy
import frappe
from frappe.utils import getdate

AUTOMATIC_ASSIGNMENTS_ENABLED = False
VERSION = 'purchase-responsibility-sla-v1'
POLICY = {
 'mr-open':dict(owner='Tirthankar Bhowmick',backup='Poonam Devi',kind='MR',days=7,
                timeline='RFQ in 2 days; PO in 7 days of MR',rfq_days=2),
 'pca':dict(owner='Varun Gupta',backup='Harsh Gupta',kind='PCA',days=1,working_days=True,
            timeline='Decision in 1 working day'),
 'po-open':dict(owner='Poonam Devi',backup='Tirthankar Bhowmick',kind='PO',required_date=True,
                timeline='By required-by date; chase 3 days before',chase_days=3),
 'qc-open':dict(owner='Mohan Lal',backup='Pooja',kind='QC',days=2,
                timeline='QC decision in 2 days of receipt'),
 'grn-bill':dict(owner='Parteek',backup='Dinesh',kind='GRN',days=7,
                 timeline='Invoice in 7 days of GRN; escalate at 15 days',escalation_days=15),
}


def due_date(stage, start, required=None, holidays=None):
    policy=POLICY[stage]
    if policy.get('required_date'):return getdate(required) if required else None
    if not start:return None
    day=getdate(start)
    if policy.get('working_days'):
        if holidays is None:return None  # Missing calendar is not a weekday guess.
        holidays={getdate(x) for x in holidays}
        left=policy['days']
        for _ in range(370):
            day+=timedelta(days=1)
            if day not in holidays:left-=1
            if not left:return day
        raise ValueError('No working day within calendar horizon')
    return day+timedelta(days=policy['days'])


def assignment_proposal(stage, reference_type, reference, owner, start, as_of,
                        required=None, holidays=None):
    """Design contract only: never creates ToDo, sends notification or changes a doc."""
    due=due_date(stage,start,required,holidays)
    return dict(policy_version=VERSION,automatic_creation=False,
        duplicate_key=[VERSION,stage,reference_type,reference,owner],
        due_date=str(max(due,getdate(as_of))) if due else None,
        priority='High' if due and due<getdate(as_of) else 'Medium',
        blocker=None if due else 'SLA calendar/date requires review')


@frappe.whitelist()
def display_policy(company=None):
    if not {'Purchase Manager','Purchase User','System Manager'}.intersection(frappe.get_roles()):
        frappe.throw('Purchase MIS permission required',frappe.PermissionError)
    company=company or frappe.defaults.get_user_default('Company')
    holidays=None;calendar=None;calendar_from=None;calendar_to=None
    if company:
        doc=frappe.get_doc('Company',company);doc.check_permission('read')
        calendar=doc.get('default_holiday_list')
        if calendar:
            calendar_from,calendar_to=frappe.db.get_value('Holiday List',calendar,['from_date','to_date'])
            holidays=[str(x) for x in frappe.get_all('Holiday',filters={'parent':calendar},pluck='holiday_date')]
    from calco_erp.planning_upgrade.purchase_assignments import configuration
    active = configuration()
    if active and (active.get("holiday_lists") or {}).get(company):
        calendar = active["holiday_lists"][company]
        calendar_from,calendar_to=frappe.db.get_value("Holiday List",calendar,["from_date","to_date"])
        holidays=[str(x) for x in frappe.get_all("Holiday",filters={"parent":calendar},pluck="holiday_date")]
    policies = deepcopy(POLICY)
    if active:
        for stage, user in (active.get("owners") or {}).items():
            if stage in policies:
                policies[stage]["owner"] = frappe.db.get_value("User",user,"full_name") or policies[stage]["owner"]
    return dict(version=VERSION,policies=policies,calendar=calendar,holidays=holidays,
                calendar_from=str(calendar_from or ""),calendar_to=str(calendar_to or ""),
                automatic_assignments_enabled=bool(active))
