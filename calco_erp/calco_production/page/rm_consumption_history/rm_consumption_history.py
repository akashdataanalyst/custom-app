"""One permission-aware PCE history for legacy and final confirmation evidence."""
import frappe
from frappe.utils import cint

@frappe.whitelist()
def get_history(company=None, source='All', search='', page=0):
    if frappe.session.user == 'Guest': frappe.throw('Sign in to view consumption history.',frappe.PermissionError)
    if source not in ('All','Final','Historical','Manual'): frappe.throw('Invalid history filter.')
    if not frappe.has_permission('Production Consumption Entry','read'): return dict(rows=[],has_more=False,page=0,visible_sources=[])
    page=max(cint(page),0)
    if page>100: frappe.throw('Narrow the search to view more records.')
    dt='Production Consumption Entry'
    filters={'company':company} if company else {}
    if source=='Final': filters['consumption_mode']='Final Confirmation'
    if source=='Historical': filters['consumption_mode']=['not in',['Final Confirmation','Manual / Parallel Production']]
    if source=='Manual': filters['consumption_mode']='Manual / Parallel Production'
    fields=['name','company','work_order','job_card','fg_code','fg_batch_no','production_line','posting_datetime','docstatus','modified','consumption_mode']
    search_fields=['name','work_order','fg_code','fg_batch_no','production_line']
    or_filters=[[dt,f,'like','%'+str(search).strip()+'%'] for f in search_fields] if str(search).strip() else None
    docs=frappe.get_list(dt,filters=filters,or_filters=or_filters,fields=fields,order_by='modified desc, name desc',limit_start=page*50,limit_page_length=51)
    rows=[dict(doctype=dt,name=d.name,source='Final' if d.consumption_mode=='Final Confirmation' else ('Manual' if d.consumption_mode=='Manual / Parallel Production' else 'Historical'),company=d.company,
        date=d.posting_datetime,modified=d.modified,work_order=d.work_order or '',fg_code=d.fg_code,batch=d.fg_batch_no,
        status={0:'Draft',1:'Submitted',2:'Cancelled'}[d.docstatus]) for d in docs]
    return dict(rows=rows[:50],has_more=len(rows)>50,page=page,visible_sources=['Final','Manual','Historical'])
