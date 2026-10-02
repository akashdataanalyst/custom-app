"""Read-only, permission-aware Enterprise Home. No workflow/state mutations."""
import copy
import frappe
from frappe import _
ROLE = 'Calco New UI'
OPEN_WO = {'docstatus':['<',2], 'status':['not in',['Completed','Stopped','Closed','Cancelled']]}
METRICS = (
 ('purchase','Purchase','commercial','Commercial approvals pending','Purchase Commercial Approval',{'docstatus':0,'approval_status':['in',['Draft','Reopened']]}),
 ('purchase','Purchase','supplier','Supplier requests in purchase review','New Supplier Request',{'docstatus':0,'status':'Purchase Review'}),
 ('incoming','Incoming QC','incoming','Incoming inspections pending','Quality Inspection',{'inspection_type':'Incoming','docstatus':0}),
 ('incoming','Incoming QC','deviation','RM deviation approvals pending','RM Deviation Approval',{'docstatus':0,'approval_status':['in',['Draft','Pending Operations Approval']]}),
 ('production','Production','jobs','Open Compounding Job Cards','Job Card',{'docstatus':0,'status':['in',['Open','Work In Progress','Partially Transferred','Material Transferred','On Hold']]}),
 ('production','Production','orders','Open Work Orders','Work Order',OPEN_WO),
 ('fg','FG QC','release','Final QC releases pending','Final QC Release',{'docstatus':0,'status':'Pending'}),
 ('fg','FG QC','final','Final FG inspections open','Quality Inspection',{'docstatus':0,'custom_work_order_qc_stage':'Final QC'}),
 ('dispatch','Dispatch / Handover','dispatch','Dispatch clearances pending','Dispatch Clearance',{'docstatus':0,'status':'Pending'}),
 ('dispatch','Dispatch / Handover','handover','FG handovers in progress','FG Delivery Note',{'docstatus':['<',2],'status':['in',['Draft','Production Submitted','FG Quarantine','QA Accepted','Reversal Pending']]}),
)
CREATE = ('Material Request','New RM Request','New Supplier Request','Work Order','FG Delivery Note')
DASHBOARDS = (('production-dashboard','Production Dashboard'),('plant-production-dashboard','Plant Production Dashboard'),('quality-dashboard','Quality Dashboard'),('purchase-performance-dashboard','Purchase Performance Dashboard'),('fg-planning-dashboard','FG Planning Dashboard'),('rm-planning-dashboard','RM Planning Dashboard'),('inventory-dashboard','Inventory Dashboard'),('maintenance-dashboard','Maintenance Dashboard'),('fg-standard-review','FG Standard Review'),('rm-consumption-history','RM Consumption History'))
QUEUE_FILTERS = {'inspection_type':'Incoming','docstatus':0}
QUEUE_LIMIT = 8


def require_access():
    if frappe.session.user == 'Guest' or ROLE not in frappe.get_roles():
        frappe.throw(_('Calco New UI role required.'),frappe.PermissionError)


def readable(doctype):
    return bool(frappe.db.exists('DocType',doctype) and frappe.has_permission(doctype,'read'))


def names(doctype,filters):
    # Exhaustive native permission-aware list; preview limits never affect counts.
    return frappe.get_list(doctype,filters=filters,pluck='name',limit_page_length=0)


def tile(key,label,doctype,filters):
    if not readable(doctype):return None
    try:count=len(set(names(doctype,filters)))
    except frappe.PermissionError:return None
    return dict(key=key,label=_(label),doctype=doctype,filters=filters,count=count)


@frappe.whitelist()
def get_navigation():
    require_access()
    from frappe.desk.desktop import get_workspaces
    from frappe.desk.desk_views import DeskViews
    # Native workspace access includes module/domain, role and private ownership.
    workspaces=[dict(name=w.name,title=w.title or w.name) for w in get_workspaces()['pages']
                if not w.get('is_hidden') and w.get('type') not in ('Link',)]
    allowed=DeskViews.get_allowed_pages(cache=True)
    dashboards=[dict(route=route,label=_(label)) for route,label in DASHBOARDS if route in allowed]
    return dict(workspaces=workspaces,dashboards=dashboards)


@frappe.whitelist()
def get_home_data():
    require_access()
    stages={}
    for group,title,key,label,doctype,raw in METRICS:
        filters=copy.deepcopy(raw)
        if key=='jobs':
            # A paused historical JC on a stopped/completed WO is not actionable.
            if not readable('Work Order') or not readable('Job Card'):continue
            parents=names('Work Order',dict(OPEN_WO,docstatus=1))
            filters['work_order']=['in',parents or ['']]
            # Existing controlled operation authority, not obsolete Packing cards.
            from calco_erp.calco_production.job_card_execution import COMPOUNDING_OPERATION
            filters['operation']=COMPOUNDING_OPERATION
        entry=tile(key,label,doctype,filters)
        if entry is not None:stages.setdefault(group,dict(key=group,title=_(title),tiles=[]))['tiles'].append(entry)
    queue=None
    if readable('Quality Inspection'):
        queue=dict(doctype='Quality Inspection',filters=copy.deepcopy(QUEUE_FILTERS),total=len(set(names('Quality Inspection',QUEUE_FILTERS))),rows=frappe.get_list('Quality Inspection',filters=QUEUE_FILTERS,fields=['name','reference_name','item_code','batch_no','creation'],order_by='creation desc',limit_page_length=QUEUE_LIMIT))
    inbox=tile('tasks','My open tasks','ToDo',{'status':'Open','allocated_to':frappe.session.user})
    creatable=[dt for dt in CREATE if readable(dt) and frappe.has_permission(dt,'create')]
    return dict(stages=list(stages.values()),queue=queue,inbox=inbox,create=creatable,dashboards=get_navigation()['dashboards'])
