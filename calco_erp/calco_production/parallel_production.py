"""Explicit parallel PCE mode and audited pre-evidence FG batch correction."""
import hashlib
import frappe
from frappe.utils import cstr, now_datetime
from calco_erp.calco_production.physical_completion import atomic

MANUAL = 'Manual / Parallel Production'
FINAL = 'Final Confirmation'
AUDIT = 'Production Batch Change'
TOKEN = object()
ROLES = {'Production Head', 'Manufacturing Manager'}
BATCH_FIELDS = {'batch_no', 'fg_batch_no', 'custom_fg_batch_no', 'parent_production_batch',
                'production_batch', 'fg_batch', 'batch'}
RUN_EVIDENCE = ('Partial FG Lot', 'FG Delivery Note', 'Production Batch Closure',
                'Production Settlement Preparation', 'Batch Production Record',
                'Shift Report', 'Premix Run', 'Feeder Run', 'Process Parameter Monitor',
                'Quality Inspection', 'Final QC Release', 'Dispatch Clearance')


def lock_identity():
    # Shared by automatic allocation, manual posting and controlled correction.
    key = 'calco_fg_identity:' + hashlib.sha256(frappe.local.site.encode()).hexdigest()[:32]
    if getattr(frappe.local, 'calco_fg_identity_locked', False): return
    if frappe.db.sql('SELECT GET_LOCK(%s, 10)', key)[0][0] != 1:
        frappe.throw('FG batch identity is being updated. Please retry.')
    frappe.local.calco_fg_identity_locked = True
    def release():
        frappe.db.sql('SELECT RELEASE_LOCK(%s)', key)
        frappe.local.calco_fg_identity_locked = False
    frappe.db.after_commit.add(release)
    frappe.db.after_rollback.add(release)


def matches(dt, field, value):
    if not value or not frappe.db.exists('DocType', dt): return []
    meta = frappe.get_meta(dt)
    if meta.issingle or meta.is_virtual or not meta.has_field(field): return []
    # Identifiers come only from installed DocType metadata, values parameterized.
    table = ('tab'+dt).replace('`','``'); column=field.replace('`','``')
    fields = 'name, parent, parenttype' if meta.istable else 'name'
    return frappe.db.sql(f'SELECT {fields} FROM `{table}` WHERE `{column}`=%s LOCK IN SHARE MODE', value, as_dict=True)


def batch_owners(batch):
    owners=set()
    for dt,field in [('Work Order','custom_fg_batch_no'), ('Job Card','custom_fg_batch_no'),
                     ('Partial FG Lot','parent_batch'), ('Partial FG Lot','lot_batch'),
                     ('Production Batch Closure','parent_batch'),
                     ('Production Receipt Policy Boundary','parent_batch'),
                     (AUDIT,'old_batch'), (AUDIT,'new_batch')]:
        for r in matches(dt,field,batch):
            if dt=='Work Order': owners.add((dt,r.name))
            else:
                wo=frappe.db.get_value(dt,r.name,'work_order') if frappe.get_meta(dt).has_field('work_order') else None
                owners.add(('Work Order',wo) if wo else (dt,r.name))
    if frappe.db.exists('Batch',batch):
        b=frappe.get_doc('Batch',batch)
        for f in b.meta.fields:
            if f.fieldtype=='Link' and f.options in ('Work Order','Job Card','Partial FG Lot','Production Batch Closure') and b.get(f.fieldname):
                ref=b.get(f.fieldname)
                wo=ref if f.options=='Work Order' else frappe.db.get_value(f.options,ref,'work_order')
                owners.add(('Work Order',wo) if wo else (f.options,ref))
        if b.get('reference_doctype') and b.get('reference_name'):
            dt,name=b.reference_doctype,b.reference_name
            if dt=='Job Card':
                wo=frappe.db.get_value(dt,name,'work_order')
                owners.add(('Work Order',wo) if wo else (dt,name))
            elif dt=='Work Order': owners.add((dt,name))
            elif dt=='Stock Entry':
                wo=frappe.db.get_value(dt,name,'work_order')
                if wo: owners.add(('Work Order',wo))
            elif dt in ('Partial FG Lot','Production Batch Closure'):
                wo=frappe.db.get_value(dt,name,'work_order')
                owners.add(('Work Order',wo) if wo else (dt,name))
    return sorted(owners)


def validate_manual(doc):
    if doc.get('consumption_mode')==FINAL: return
    batch=cstr(doc.get('fg_batch_no')).strip()
    if not batch: return
    lock_identity()
    if frappe.db.exists('Batch',batch):
        item=frappe.db.get_value('Batch',batch,'item')
        if item != doc.get('fg_code'):
            frappe.throw(f'FG Batch {batch} belongs to {item}, not {doc.get("fg_code")}.')
    owners=batch_owners(batch)
    # Final PCE also carries an authoritative batch/run even if a source WO was retired.
    for r in matches('Production Consumption Entry','fg_batch_no',batch):
        if r.name != doc.name and frappe.db.get_value('Production Consumption Entry',r.name,'consumption_mode')==FINAL:
            owners.append(('Production Consumption Entry',r.name))
    if owners:
        frappe.throw('This FG Batch is already managed through ERP Manufacturing. '
            'Record consumption through the controlled production/final-consumption workflow instead. '
            + '; '.join(f'{dt}: {name}' for dt,name in owners))


def prepare_manual(doc):
    old=doc.get_doc_before_save()
    if doc.is_new() and not doc.get('consumption_mode'): doc.consumption_mode=MANUAL
    if doc.get('consumption_mode') not in ('',None,MANUAL,FINAL):
        frappe.throw('Unsupported consumption mode.')
    if doc.get('consumption_mode')!=FINAL:
        if any(doc.get(k) for k in ('work_order','job_card','batch_closure','final_source','final_fingerprint','supersedes')):
            frappe.throw('Manual consumption cannot supply controlled final-consumption lineage.')
        if doc.is_new(): doc.user=frappe.session.user
        elif old and old.get('user') != doc.get('user'):
            frappe.throw('Consumption actor cannot be changed.')


def batch_evidence(batch, work_order=None, cards=()):
    """Exact installed Batch links plus explicit legacy Data identities; never fuzzy text."""
    found=set()
    fields=frappe.get_all('DocField',filters={'fieldtype':['in',['Link','Data','Dynamic Link']]},fields=['parent','fieldname','fieldtype','options'],limit_page_length=0)
    fields += [frappe._dict(parent=r.dt,fieldname=r.fieldname,fieldtype=r.fieldtype,options=r.options)
               for r in frappe.get_all('Custom Field',filters={'fieldtype':['in',['Link','Data','Dynamic Link']]},fields=['dt','fieldname','fieldtype','options'],limit_page_length=0)]
    for f in fields:
        if f.parent in ('Batch','Work Order','Job Card',AUDIT): continue
        if (f.fieldtype=='Link' and f.options=='Batch') or f.fieldname in BATCH_FIELDS:
            for row in matches(f.parent,f.fieldname,batch):
                found.add((row.get('parenttype') or f.parent,row.get('parent') or row.name))
        elif f.fieldtype=='Dynamic Link' and f.options:
            for row in matches(f.parent,f.fieldname,batch):
                if frappe.db.get_value(f.parent,row.name,f.options)=='Batch':
                    found.add((row.get('parenttype') or f.parent,row.get('parent') or row.name))
    if work_order:
        for dt in RUN_EVIDENCE:
            for field,value in [('work_order',work_order)]+[(f,c) for c in cards for f in ('job_card','production_run')]:
                for row in matches(dt,field,value): found.add((dt,row.name))
    return sorted(found)


def authority():
    if not ROLES.intersection(frappe.get_roles()):
        frappe.throw('Production Head or Manufacturing Manager authority required.',frappe.PermissionError)


@frappe.whitelist()
@atomic
def correct_batch(job_card, batch_no, reason, expected_batch=None):
    authority()
    reason=cstr(reason).strip(); batch_no=cstr(batch_no).strip()
    if not reason: frappe.throw('Reason for FG batch correction is mandatory.')
    if not batch_no or len(batch_no)>140: frappe.throw('Enter a valid physical FG Batch identity.')
    lock_identity()
    card=frappe.get_doc('Job Card',job_card); card.check_permission('write')
    from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION
    from calco_erp.calco_production.stopped_execution import lock_work_order
    if card.operation != COMPOUNDING_OPERATION or card.docstatus != 0 or not card.work_order:
        frappe.throw('Use a Draft controlled Compounding Job Card.')
    from calco_erp.calco_production.manufacture_entry import is_controlled_work_order
    from calco_erp.calco_production.physical_completion import ended
    if not is_controlled_work_order(card.work_order):
        frappe.throw('Job Card must belong to a controlled Manufacturing Work Order.')
    if ended(card): frappe.throw('Physical End evidence exists. Controlled Batch Correction required.')
    lock_work_order(card.work_order)
    wo=frappe.get_doc('Work Order',card.work_order)
    if wo.docstatus != 1 or wo.status in ('Stopped','Closed','Cancelled','Completed'):
        frappe.throw('Work Order must be active and submitted.')
    cards=frappe.db.sql('SELECT name, custom_fg_batch_no FROM `tabJob Card` WHERE work_order=%s AND docstatus<2 FOR UPDATE',wo.name,as_dict=True)
    old=cstr(wo.get('custom_fg_batch_no')).strip()
    if any(c.custom_fg_batch_no and c.custom_fg_batch_no!=old for c in cards):
        frappe.throw('Existing WO/Job Card batch identities conflict. Management Review Required.')
    if expected_batch is None or cstr(expected_batch)!=old:
        frappe.throw('FG batch changed. Reload the Job Card before correcting it.')
    if batch_no==old: return dict(batch_no=old,changed=False)
    conflicts=[f'{dt}: {n}' for dt,n in batch_owners(batch_no) if (dt,n)!=('Work Order',wo.name)]
    if conflicts: frappe.throw('Batch belongs to another production event: '+'; '.join(conflicts))
    blockers=batch_evidence(old,wo.name,[r.name for r in cards]) if old else batch_evidence('',wo.name,[r.name for r in cards])
    blockers += batch_evidence(batch_no)
    if blockers:
        frappe.throw('Controlled Batch Correction required; existing genealogy cannot be rewritten. Blocking records: '
                     + '; '.join(f'{dt}: {n}' for dt,n in sorted(set(blockers))))
    if frappe.db.exists('Batch',batch_no):
        b=frappe.get_doc('Batch',batch_no)
        if b.item!=wo.production_item or b.get('disabled'):
            frappe.throw('FG Batch must be enabled and belong to the same FG Item/Grade.')
        from frappe.utils import getdate, today
        if b.get('expiry_date') and getdate(b.expiry_date)<getdate(today()): frappe.throw('FG Batch is expired.')
    else:
        # Narrow trusted creation after Production authority, item and genealogy checks.
        b=frappe.get_doc(dict(doctype='Batch',batch_id=batch_no,item=wo.production_item))
        b.insert(ignore_permissions=True)
        if b.name!=batch_no: frappe.throw('Native Batch identity differs from requested identity. No change saved.')
    history=frappe.get_all(AUDIT,filters={'work_order':wo.name},fields=['name','original_batch'],order_by='creation asc',limit_page_length=1)
    audit=frappe.get_doc(dict(doctype=AUDIT,work_order=wo.name,job_card=card.name,
        original_batch=history[0].original_batch if history else old,old_batch=old,new_batch=batch_no,
        reason=reason,changed_by=frappe.session.user,changed_on=now_datetime()))
    audit.flags.parallel_batch_token=TOKEN; audit.insert(ignore_permissions=True)
    wo.db_set('custom_fg_batch_no',batch_no)
    for r in cards: frappe.get_doc('Job Card',r.name).db_set('custom_fg_batch_no',batch_no)
    return dict(batch_no=batch_no,changed=True,audit=audit.name)


def protect_batch(doc,method=None):
    old=doc.get_doc_before_save()
    if old and cstr(old.get('custom_fg_batch_no'))!=cstr(doc.get('custom_fg_batch_no')):
        frappe.throw('Use Set / Correct FG Batch. Production batch identity is controlled.')
