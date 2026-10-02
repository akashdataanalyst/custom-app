"""Captured in-place Item revision semantics with stale-save serialization."""
import frappe
from frappe.utils import cint, get_datetime, today
SKIP = set('modified modified_by creation owner name idx docstatus parent parentfield parenttype doctype custom_revision_no custom_revision_date _user_tags _comments _assign _liked_by _seen __islocal __unsaved __last_sync_on'.split())

def normalized(v):
    if v is None or v == '' or v == 0: return ''
    return str(float(v)) if isinstance(v, (int,float)) else str(v)

def changed(before, after):
    for k in set(before) | set(after):
        if k in SKIP: continue
        a,b=before.get(k),after.get(k)
        if isinstance(a,list) or isinstance(b,list):
            a,b=a or [],b or []
            if len(a)!=len(b) or any(changed(x,y) for x,y in zip(a,b)): return True
        elif normalized(a)!=normalized(b): return True
    return False

def before_save(doc, method=None):
    if doc.is_new() or doc.item_group not in ('Raw Material','Finished Goods'): return
    old=doc.get_doc_before_save()
    if not old: return
    current=frappe.db.sql('select modified, custom_revision_no from `tabItem` where name=%s for update',(doc.name,),as_dict=True)[0]
    if get_datetime(current.modified)!=get_datetime(old.modified) or cint(current.custom_revision_no)!=cint(old.get('custom_revision_no')):
        frappe.throw('Item changed concurrently. Reload before saving its next revision.',frappe.TimestampMismatchError)
    doc.custom_revision_no=cint(old.get('custom_revision_no'))
    doc.custom_revision_date=old.get('custom_revision_date')
    if changed(old.as_dict(),doc.as_dict()):
        doc.custom_revision_no+=1
        doc.custom_revision_date=today()
