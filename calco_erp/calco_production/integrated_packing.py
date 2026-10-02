"""Recovery policy: packing is integrated in Compounding, with audited unused legacy retirement."""
import json
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.utils import flt, now_datetime
from calco_erp.calco_production import physical_completion as physical
from calco_erp.calco_production import receipt_policy_infrastructure as infra

POLICY = 'compounding-integrated-packing-v1'
FIELD = 'custom_integrated_operation_audit'
TOKEN = object()
REASON = 'Obsolete UAT operation — Packing is part of Compounding / Extrusion and is not a separate Calco manufacturing operation.'


def recovery():
    from calco_erp.release_profile import PRODUCTION_DISTRIBUTION
    return not PRODUCTION_DISTRIBUTION and frappe.local.site == infra.SITE


def setup():
    if not production_site_allowed():frappe.throw('Explicit manufacturing site binding required.')
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Work Order':[dict(fieldname=FIELD,label='Integrated Operation Retirement Audit',
        fieldtype='Long Text',read_only=1,allow_on_submit=1,no_copy=1,insert_after='operations')]},update=True)


def validate_routing(doc,method=None):
    if not production_site_allowed():return
    operations={r.operation for r in doc.get('operations') or []}
    if {'Compounding / Extrusion','Packing'} <= operations:
        frappe.throw('Packing is integrated in Compounding / Extrusion. Select/create the corrected single-operation BOM; separate Packing must not be generated.')


def validate_new_work_order(doc,method=None):
    if not production_site_allowed():return
    validate_routing(doc)
    if doc.get('bom_no'):
        validate_routing(frappe.get_doc('BOM',doc.bom_no))


def validate_new_bom(doc,method=None):
    if doc.docstatus==0:validate_routing(doc)


def history(wo):
    return json.loads(wo.get(FIELD) or '[]')


def protect(wo,method=None):
    old=wo.get_doc_before_save()
    previous=old.get(FIELD) if old else None
    if wo.get(FIELD)==previous:return
    if wo.flags.get('integrated_operation_token') is not TOKEN or wo.get(FIELD)!=wo.flags.get('integrated_operation_expected'):
        frappe.throw('Integrated operation retirement evidence is immutable and server-controlled.')
    before=json.loads(previous or '[]');after=history(wo)
    if after[:len(before)]!=before or len(after)!=len(before)+1:
        frappe.throw('Operation retirement must append one controlled event.')


def assert_unused(card):
    if card.docstatus!=0 or card.status!='Open' or card.operation!='Packing':
        frappe.throw('Management Review Required: only never-used Draft/Open Packing may be retired.')
    fields=('total_completed_qty','process_loss_qty','manufactured_qty','transferred_qty','total_time_in_mins','requested_qty')
    evidence=('actual_start_date','actual_end_date','quality_inspection','custom_operator','custom_execution_policy_audit',
        'custom_physical_completion_audit','custom_stopped_execution_closure','custom_shift_output_activation','custom_shift_unique_activation')
    if any(flt(card.get(f)) for f in fields) or any(card.get(f) for f in evidence):
        frappe.throw('Management Review Required: Packing contains execution/quantity/quality evidence.')
    if any(card.get(f) for f in ('employee','time_logs','items','sub_operations','secondary_items','custom_shift_reports','custom_rm_loading_details','custom_grade_change_checklist')):
        frappe.throw('Management Review Required: Packing contains operational evidence.')
    from frappe.model.delete_doc import check_if_doc_is_linked,check_if_doc_is_dynamically_linked
    check_if_doc_is_linked(card)
    check_if_doc_is_dynamically_linked(card)


def completed_run(wo):
    from calco_erp.calco_production import physical_closure_state as bridge
    names=frappe.get_all('Job Card',filters={'work_order':wo.name,'operation':'Compounding / Extrusion','docstatus':['!=',2]},pluck='name')
    if len(names)!=1:frappe.throw('Management Review Required: one completed controlled Compounding run is required.')
    card=frappe.get_doc('Job Card',names[0])
    if card.docstatus!=1 or card.status!='Completed' or abs(flt(card.total_completed_qty)-flt(wo.qty))>1e-6:
        frappe.throw('Completed full-quantity Compounding is required before obsolete operation retirement.')
    from calco_erp.calco_production import partial_fg_lots as lots, in_process_quality as qc
    material=bridge.material_state(card)
    output=lots.fg_state(card)
    state=dict(closure=material,cumulative_fg_qty=output['fg_qty'],received_qty=lots.received_fg(wo.name))
    if not physical.ended(card) or not material or not material['reconciled']:
        frappe.throw('Resolved physical end, closed batch, final consumption and zero WIP are required.')
    if any(r.from_time and not r.to_time for r in card.time_logs) or not qc.state_for_work_order(wo).get('ready') or physical.reporting_blockers(card):
        frappe.throw('Unresolved execution, reporting or Quality obligations remain.')
    if not any(e.get('action')=='Finalize' for e in physical.events(card)):
        frappe.throw('Controlled Job Card finalization evidence is required.')
    if abs(state['cumulative_fg_qty']-flt(wo.qty))>1e-6 or abs(state['received_qty']-flt(wo.qty))>1e-6:
        frappe.throw('Controlled FG and receipts must equal Work Order quantity.')
    return card,state


def operation_fingerprint(operation):
    fields=('name','parent','operation','bom','workstation','sequence_id','time_in_mins','completed_qty','process_loss_qty','actual_operation_time','actual_start_time','actual_end_time')
    return infra.fingerprint({f:operation.get(f) for f in fields})


def excluded(wo,operation,matching):
    if not recovery() or operation.operation!='Packing' or matching:return False
    events=[e for e in history(wo) if e.get('policy')==POLICY and e.get('operation_id')==operation.name]
    if len(events)!=1:return False
    e=events[0]
    if e['operation_fingerprint']!=operation_fingerprint(operation):return False
    if frappe.db.exists('Job Card',e['job_card']):return False
    archive=frappe.get_doc('Deleted Document',e['deleted_document'])
    if archive.restored or archive.deleted_doctype!='Job Card' or archive.deleted_name!=e['job_card'] or infra.fingerprint(json.loads(archive.data))!=e['job_card_fingerprint']:
        frappe.throw('Retired operation archive differs from its controlled evidence.')
    completed_run(wo)
    return True


@frappe.whitelist()
@physical.atomic
def retire_unused_packing(job_card,reason):
    if not recovery():frappe.throw('Recovery only.')
    if not {'Production Head','Manufacturing Manager'}.intersection(frappe.get_roles()):
        frappe.throw('Production management authority required.',frappe.PermissionError)
    if not (reason or '').strip():frappe.throw('Retirement reason is required.')
    card=frappe.get_doc('Job Card',job_card);card.check_permission('delete')
    infra.lock_run(card.work_order)
    frappe.db.sql('select name from `tabJob Card` where name=%s for update',card.name)
    card.reload();assert_unused(card)
    wo=frappe.get_doc('Work Order',card.work_order);wo.check_permission('write')
    if wo.docstatus!=1 or wo.status in ('Stopped','Closed','Cancelled'):frappe.throw('Submitted active Work Order required.')
    operation=next((r for r in wo.operations if r.name==card.operation_id and r.operation=='Packing'),None)
    if not operation:frappe.throw('Packing Work Order operation identity is missing.')
    siblings=frappe.get_all('Job Card',filters={'work_order':wo.name,'operation_id':operation.name},pluck='name')
    if siblings!=[card.name]:frappe.throw('Management Review Required: other Job Cards reference this operation.')
    completed,state=completed_run(wo)
    if flt(operation.completed_qty) or flt(operation.actual_operation_time) or operation.actual_start_time or operation.actual_end_time:
        frappe.throw('Management Review Required: operation has execution evidence.')
    name=card.name
    frappe.delete_doc('Job Card',name,delete_permanently=False)
    archive_name=frappe.db.get_value('Deleted Document',{'deleted_doctype':'Job Card','deleted_name':name},'name',order_by='creation desc')
    if not archive_name:frappe.throw('Native deleted-document audit was not retained.')
    archive=frappe.get_doc('Deleted Document',archive_name)
    event=dict(policy=POLICY,classification='Integrated / not separate',operation_id=operation.name,
        operation_fingerprint=operation_fingerprint(operation),job_card=name,deleted_document=archive_name,
        job_card_fingerprint=infra.fingerprint(json.loads(archive.data)),reason=reason.strip(),by=frappe.session.user,
        at=str(now_datetime()),compounding_job_card=completed.name,closure=state['closure']['closure'],
        closure_revision=state['closure']['revision'],closure_fingerprint=state['closure']['fingerprint'],source_bom=wo.bom_no)
    wo.set(FIELD,json.dumps(history(wo)+[event],sort_keys=True))
    wo.flags.integrated_operation_token=TOKEN;wo.flags.integrated_operation_expected=wo.get(FIELD)
    wo.save()
    wo.update_status()
    return dict(job_card=name,disposition='Retired/obsolete — no execution',deleted_document=archive_name,work_order=wo.name,status=wo.status)


def revise_bom(source_name):
    """New submitted master revision; historical BOM/WO operations are never edited."""
    if not recovery():frappe.throw('Recovery only.')
    source=frappe.get_doc('BOM',source_name);source.check_permission('read')
    if source.docstatus!=1 or not {'Packing','Compounding / Extrusion'} <= {r.operation for r in source.operations}:
        frappe.throw('A submitted former Compounding/Packing BOM is required.')
    # Do not discard any genuinely different operation or semi-finished output.
    packing=[r for r in source.operations if r.operation=='Packing']
    if any(r.get('finished_good') or r.get('is_subcontracted') or r.get('bom_no') for r in packing):
        frappe.throw('Management Review Required: Packing has a separate manufacturing dependency.')
    note=f'{POLICY}: revised from {source.name}. Packing is included in Compounding / Extrusion.'
    for name in frappe.get_all('BOM',filters={'item':source.item,'docstatus':1},pluck='name'):
        candidate=frappe.get_doc('BOM',name)
        if frappe.db.exists('Comment',{'reference_doctype':'BOM','reference_name':name,'content':note}):
            validate_routing(candidate)
            return name
    revision=frappe.copy_doc(source);revision.docstatus=0;revision.is_default=source.is_default
    revision.set('operations',[r.as_dict() for r in source.operations if r.operation!='Packing'])
    for i,r in enumerate(revision.operations,1):
        r.name=None;r.idx=i;r.sequence_id=i
    revision.insert();revision.submit()
    from calco_erp.calco_production.operation_master import validate_bom_preservation
    validate_bom_preservation(source,revision)
    revision.add_comment('Info',note)
    return revision.name
