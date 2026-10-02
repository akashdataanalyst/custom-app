"""Durable prospective return events; one native communication and queue.
SMTP cannot guarantee exactly-once transport; logical queue creation can.
"""
import hashlib
import json
from html import escape
import frappe
from frappe.utils import cint
DOCTYPE='Calco Return Communication'
INTERNAL=('mis.1@calco.in','varun.gupta@calco.in')

def event_name(receipt):
    return 'PRR-'+hashlib.sha256((receipt+':submitted:v1').encode()).hexdigest()[:40]

def _enqueue(name):
    try:
        frappe.enqueue('calco_erp.final_four.return_communication.deliver',name=name,enqueue_after_commit=True)
    except Exception:
        frappe.log_error(title='Return communication queue unavailable',message=frappe.get_traceback())

def on_submit(doc,method=None):
    if cint(doc.docstatus)!=1 or not cint(doc.is_return): return
    name=event_name(doc.name)
    frappe.db.savepoint('calco_return_event')
    try:
        frappe.db.sql('select name from `tabPurchase Receipt` where name=%s for update',(doc.name,))
        if not frappe.db.exists(DOCTYPE,name):
            payload=dict(receipt=doc.name,supplier=doc.supplier,posting_date=str(doc.posting_date),supplier_email=frappe.db.get_value('Supplier',doc.supplier,'email_id'),internal=list(INTERNAL),actor=frappe.session.user,qis=sorted({r.quality_inspection for r in doc.items if r.quality_inspection}))
            prior=frappe.flags.calco_return_event_write
            try:
                frappe.flags.calco_return_event_write=True
                frappe.get_doc(dict(doctype=DOCTYPE,purchase_receipt=doc.name,state='Pending',payload=json.dumps(payload),attempts=0)).insert(ignore_permissions=True,set_name=name)
            finally:
                frappe.flags.calco_return_event_write=prior
        _enqueue(name)
    except Exception:
        frappe.db.rollback(save_point='calco_return_event')
        frappe.log_error(title='Return communication scheduling failed',message=frappe.get_traceback())

def body(p):
    return ('<p>Dear Team,</p><p>Please find attached the <b>RM TC</b> document related to the below-mentioned Purchase Return and Quality Inspection for your reference and further necessary action.</p>'
        f"<p><b>Purchase Return:</b> {escape(p['receipt'])}<br><b>Supplier:</b> {escape(p['supplier'])}<br><b>Posting Date:</b> {escape(p['posting_date'])}<br><b>Quality Inspection:</b> {escape(', '.join(p['qis']))}</p>"
        '<p>Kindly review the attached RM TC document and proceed with the necessary action accordingly.</p><p>Should you require any further information or clarification, please feel free to contact us.</p><p>Regards,<br>Quality / Purchase Team<br>Calco</p>')

def deliver(name):
    receipt=frappe.db.get_value(DOCTYPE,name,'purchase_receipt')
    if not receipt:return
    # Same lock order as submit/cancel: parent first, event second.
    state=frappe.db.sql('select docstatus,is_return from `tabPurchase Receipt` where name=%s for update',(receipt,),as_dict=True)
    frappe.db.sql(f'select name from `tab{DOCTYPE}` where name=%s for update',(name,))
    event=frappe.get_doc(DOCTYPE,name)
    if event.state in ('Queued','Cancelled'):return event.state
    if not state or state[0].docstatus!=1 or not state[0].is_return:
        event.db_set('state','Cancelled');return 'Cancelled'
    p=json.loads(event.payload);attempts=cint(event.attempts)+1
    frappe.db.savepoint('return_delivery')
    try:
        if not p.get('supplier_email'):raise ValueError('Supplier email missing in submission evidence; authorized review required')
        if not p['qis']:raise ValueError('No linked Quality Inspection in submission evidence')
        recipients=list(dict.fromkeys([*p['internal'],p['supplier_email']]))
        attachments=[dict(fname=q+'-RM-TC.pdf',fcontent=frappe.get_print('Quality Inspection',q,print_format='RM TC',as_pdf=True)) for q in p['qis']]
        from frappe.core.doctype.communication.email import _make
        result=_make(doctype='Purchase Receipt',name=p['receipt'],subject='RM TC - Purchase Return '+p['receipt'],content=body(p),recipients=recipients,send_email=False,communication_type='Communication',add_signature=False)
        queue=frappe.sendmail(recipients=recipients,subject='RM TC - Purchase Return '+p['receipt'],message=body(p),attachments=attachments,reference_doctype='Purchase Receipt',reference_name=p['receipt'],communication=result['name'],delayed=True,now=False)
        if not queue:raise ValueError('Native Email Queue was not created')
        event.db_set(dict(state='Queued',communication=result['name'],email_queue=queue.name,attempts=attempts,last_error=''))
        return 'Queued'
    except Exception as exc:
        frappe.db.rollback(save_point='return_delivery')
        event.db_set(dict(state='Failed',attempts=attempts,last_error=str(exc)[:4000]))
        return 'Failed'

@frappe.whitelist()
def retry(name):
    event=frappe.get_doc(DOCTYPE,name)
    frappe.get_doc('Purchase Receipt',event.purchase_receipt).check_permission('email')
    if event.state not in ('Queued','Cancelled'):_enqueue(name)
    return {'event':name,'state':event.state}


def protect_event(doc,method=None):
    if not frappe.flags.calco_return_event_write:
        frappe.throw('Return communication evidence is controlled; use the authorized retry action.')
