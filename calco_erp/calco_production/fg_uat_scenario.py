"""Opt-in Recovery-only demand; state is outside images and database backups."""
import fcntl
import hmac
import json
import os
from pathlib import Path
from contextlib import contextmanager
import frappe
from frappe.utils import now_datetime
from calco_erp.calco_production.fg_planning_authority import sign
SITE='recovery120120.localhost'
COMPANY='Calco PolyTechnik Pvt Ltd'
ITEM='UAT-MFG-FG-01'
UOM='Kg'
FLAG='calco_uat_fg_planning_enabled'
ROOT=Path('/home/frappe/frappe-bench/logs/calco-recovery-uat')
MARKER='CALCO-RECOVERY-FG-UAT-ONLY-v1'
LABEL='Recovery UAT Scenario'

def mounted_marker():
    try:
        mounted=any(line.split()[4]==str(ROOT.parent) for line in Path('/proc/self/mountinfo').read_text().splitlines())
        return mounted and (ROOT/'marker').read_text().strip()==MARKER
    except (OSError,IndexError):
        return False

def enabled():
    from calco_erp.release_profile import PRODUCTION_DISTRIBUTION
    if PRODUCTION_DISTRIBUTION:return False
    flag=frappe.conf.get(FLAG)
    return frappe.local.site==SITE and type(flag) is int and flag==1 and mounted_marker()

def read_state():
    path=ROOT/'scenario.json'
    if not path.exists():return None
    envelope=json.loads(path.read_text());state=envelope['state']
    if not hmac.compare_digest(envelope['signature'],sign(state)):
        frappe.throw('Recovery UAT Scenario state signature is invalid.')
    if (state['company'],state['item_code'],state['uom'],state['demand_qty'])!=(COMPANY,ITEM,UOM,20):
        frappe.throw('Recovery UAT Scenario scope is invalid.')
    return state

def active(company,item,uom):
    if (company,item,uom)!=(COMPANY,ITEM,UOM) or not enabled():return None
    state=read_state()
    return state if state and state['active'] else None

@contextmanager
def locked_state():
    with (ROOT/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield

def write_state(state):
    temporary=ROOT/'scenario.tmp'
    temporary.write_text(json.dumps({'state':state,'signature':sign(state)},sort_keys=True))
    os.replace(temporary,ROOT/'scenario.json')

def admin_guard():
    if frappe.session.user!='Administrator' or not enabled():
        frappe.throw('Administrator, exact Recovery site, dedicated flag and mounted marker are required.')

@frappe.whitelist()
def activate(month,confirmation):
    admin_guard()
    from calco_erp.calco_production.fg_dashboard import months_at
    if confirmation!='ACTIVATE-RECOVERY-FG-UAT-20KG' or month not in [m['key'] for m in months_at(now_datetime())]:
        frappe.throw('Explicit confirmation and a month in the current planning horizon are required.')
    with locked_state():
        previous=read_state()
        if previous:
            if previous['active'] and previous['month']==month:return previous
            frappe.throw('A frozen scenario already exists. It cannot be reset or silently replaced.')
        state={'id':f'RECOVERY-FG-UAT-{month}-20KG-v1','label':LABEL,'company':COMPANY,'item_code':ITEM,
               'uom':UOM,'month':month,'demand_qty':20,'active':True,'activated_by':frappe.session.user,
               'activated_on':str(now_datetime()),'events':[]}
        state['events'].append({'action':'Activated','user':frappe.session.user,'on':state['activated_on']})
        write_state(state)
        return state

@frappe.whitelist()
def deactivate(confirmation):
    admin_guard()
    if confirmation!='DEACTIVATE-RECOVERY-FG-UAT':frappe.throw('Explicit deactivation confirmation is required.')
    with locked_state():
        state=read_state()
        if state and state['active']:
            state['active']=False
            state['events'].append({'action':'Deactivated','user':frappe.session.user,'on':str(now_datetime())})
            write_state(state)
        return state
