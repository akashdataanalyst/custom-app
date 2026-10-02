"""Gate 1 only: immutable evidence schemas. No receipt, closure or accounting route."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime, cint
from calco_erp.calco_production import record_fingerprints as records

SITE = 'recovery120120.localhost'
FLAG = 'calco_provisional_receipts_enabled'
ADAPTER_IMPLEMENTED = True
WRITE_TOKEN = object()
LOT_FIELDS = ('custom_receipt_policy_version','custom_receipt_policy_boundary','custom_receipt_policy_snapshot','custom_receipt_policy_fingerprint')
STOCK_FIELDS = ('custom_provisional_work_order','custom_provisional_job_card','custom_provisional_lot','custom_provisional_manufacture','custom_provisional_wip_entry','custom_provisional_fingerprint')
RECORDS = ('Production Receipt Policy Boundary','Production Batch Closure','Production Settlement Preparation')

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=str)
def fingerprint(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def payload(value):
    try:result=frappe.parse_json(value)
    except Exception:frappe.throw('Invalid controlled evidence JSON.')
    if not isinstance(result,dict):frappe.throw('Controlled evidence must be a JSON object.')
    return result

def quantity(value):
    if value is None or not str(value).strip():frappe.throw('Explicit physical quantity required; enter zero explicitly.')
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError):frappe.throw('Invalid physical quantity.')
    if not number.is_finite() or number<0:frappe.throw('Physical quantity must be finite and non-negative.')
    return number

def enabled():
    # This remains false throughout Gate 1, even with a copied/enabled config flag.
    return bool(ADAPTER_IMPLEMENTED and production_site_allowed() and cint(frappe.conf.get(FLAG,0)))

def activation_context(work_order,at=None):
    if not enabled():return {'enabled':False,'reason':'Provisional receipt adapter is not activated.'}
    at=get_datetime(at or now_datetime())
    name=frappe.db.get_value('Production Receipt Policy Boundary',{'work_order':work_order,'effective_from':('<=',at)},'name',order_by='effective_from desc')
    return {'enabled':bool(name),'boundary':name}

def controlled(doc):return doc.flags.get('receipt_infrastructure_token') is WRITE_TOKEN

def lock_run(work_order):
    if not work_order:frappe.throw('Work Order lineage is required.')
    frappe.db.sql('select name from `tabWork Order` where name=%s for update',work_order)

def check_run(doc):
    lock_run(doc.work_order)
    wo=frappe.get_doc('Work Order',doc.work_order);jc=frappe.get_doc('Job Card',doc.job_card)
    if doc.company!=wo.company or jc.work_order!=wo.name or jc.operation!='Compounding / Extrusion' or doc.parent_batch!=wo.get('custom_fg_batch_no'):
        frappe.throw('Controlled production run genealogy mismatch.')

def field_value(df,value):
    if df.fieldtype in ('Float','Currency','Percent'):return str(Decimal(str(value or 0)).normalize())
    if df.fieldtype in ('Int','Check'):return cint(value)
    if df.fieldtype=='Datetime' and value:return str(get_datetime(value))
    return value

def record_body(doc):
    result={}
    for df in doc.meta.fields:
        if df.fieldname=='fingerprint' or not df.fieldname or df.fieldtype in ('Section Break','Column Break','Tab Break','HTML','Button'):continue
        value=doc.get(df.fieldname)
        if df.fieldtype=='Table':
            value=[{f.fieldname:field_value(f,r.get(f.fieldname)) for f in frappe.get_meta(df.options).fields if f.fieldname} for r in value or []]
        result[df.fieldname]=field_value(df,value)
    return result

class InfrastructureRecord(Document):
    def validate(self):
        if self.docstatus:frappe.throw('Gate 1 evidence records cannot be submitted or cancelled.')
        if not production_site_allowed():frappe.throw('Manufacturing site has not been explicitly bound for this deployment.')
        if not self.is_new():
            old=frappe.get_doc(self.doctype,self.name)
            if not records.verified(old) or not records.verified(self) or records.body(old)!=records.body(self) or self.fingerprint!=old.fingerprint:
                frappe.throw('Controlled evidence is immutable; create a referenced correction version.')
            return
        if not controlled(self):frappe.throw('Use the controlled server workflow; Gate 1 does not expose record creation.')
        allowed={'Production Batch Closure':{'Production Engineer','Production Head','Manufacturing Manager'},
            'Production Settlement Preparation':{'Manufacturing Manager','Accounts Manager'},
            'Production Receipt Policy Boundary':{'Manufacturing Manager','Accounts Manager'}}[self.doctype]
        if not allowed.intersection(frappe.get_roles()):frappe.throw('Controlled record authority required.',frappe.PermissionError)
        check_run(self)
        previous=None
        if self.supersedes:
            previous=frappe.get_doc(self.doctype,self.supersedes)
            if (previous.work_order,previous.job_card)!=(self.work_order,self.job_card):frappe.throw('Correction must belong to the same production run.')
            if not (self.correction_reason or '').strip():frappe.throw('Correction reason is required.')
            if frappe.db.exists(self.doctype,{'supersedes':previous.name}):frappe.throw('A successor version already exists.')
        elif frappe.db.exists(self.doctype,{'job_card':self.job_card}):
            frappe.throw('Run evidence already exists. Reuse it or create a controlled correction.')
        self.revision=cint(previous.revision)+1 if previous else 1
        self.recorded_by=frappe.session.user;self.recorded_on=now_datetime()
        if self.doctype=='Production Batch Closure':
            from calco_erp.calco_production.physical_completion import end_event
            event=end_event(frappe.get_doc('Job Card',self.job_card))
            if not event or not self.final_closure_confirmed:frappe.throw('Physical end and explicit final production closure are required.')
            self.physical_end_event=canonical(event)
            release=frappe.get_doc('Final QC Release',self.final_release)
            lot=frappe.db.get_value('Partial FG Lot',{'lot_batch':release.batch_no,'job_card':self.job_card},'name')
            if release.docstatus!=1 or not lot:frappe.throw('A submitted release belonging to this run is required.')
            if not self.materials:frappe.throw('Final physical material evidence is required.')
            seen=set()
            for row in self.materials:
                key=(row.item_code,row.batch_no)
                if not all(key) or key in seen:frappe.throw('Unique RM item and batch required.')
                seen.add(key)
                if frappe.db.get_value('Batch',row.batch_no,'item')!=row.item_code:frappe.throw('RM batch/item mismatch.')
                for field in ('issued_qty','accounted_qty','final_actual_qty'):quantity(row.get(field))
                payload(row.source_evidence)
            self.status='Final Consumption Confirmed'
            self.measurement_reference=self.name
            payload(self.source_snapshot)
        elif self.doctype=='Production Settlement Preparation':
            closure=frappe.get_doc('Production Batch Closure',self.closure)
            if (closure.work_order,closure.job_card)!=(self.work_order,self.job_card) or cint(self.closure_version)!=closure.revision:
                frappe.throw('Settlement must reference its exact physical confirmation version.')
            if previous and previous.closure==self.closure and previous.closure_version==self.closure_version:
                # Subsequent preparation may change only with explicit correction evidence.
                if not self.correction_reason:frappe.throw('Settlement correction reason is required.')
            for f in ('quantity_evidence','lot_targets','value_evidence'):payload(self.get(f))
            self.status='Prepared'
        else:
            if not self.policy_version or not (self.authorization_reason or '').strip():frappe.throw('Approved policy version and authorization reason required.')
            approved=payload(self.policy_snapshot)
            self.policy_fingerprint=fingerprint(approved)
            self.effective_from=self.recorded_on;self.authorized_by=frappe.session.user
        self.fingerprint=records.fingerprint(self)
    def on_trash(self):frappe.throw('Controlled evidence cannot be deleted.')
    def before_cancel(self):frappe.throw('Controlled evidence requires a referenced correction.')


def protect_lot(doc,method=None):
    old=doc.get_doc_before_save()
    if old and any(doc.get(f)!=old.get(f) for f in LOT_FIELDS):frappe.throw('Receipt policy snapshot cannot be changed or backfilled.')
    if not any(doc.get(f) for f in LOT_FIELDS):return
    if doc.is_new() and not controlled(doc):frappe.throw('Receipt policy snapshot requires controlled server authority.')
    if not all(doc.get(f) for f in LOT_FIELDS):frappe.throw('Complete frozen receipt policy identity required.')
    data=payload(doc.custom_receipt_policy_snapshot)
    required=('policy_version','company','work_order','job_card','parent_batch','interval_start','cutoff','formulation_version','formulation_fingerprint','output_evidence','materials','classification')
    if any(data.get(k) in (None,'',[]) for k in required):frappe.throw('Incomplete provisional receipt evidence.')
    for f in ('company','work_order','job_card','parent_batch'):
        if data[f]!=doc.get(f):frappe.throw('Receipt policy production genealogy mismatch.')
    if data['policy_version']!=doc.custom_receipt_policy_version or fingerprint(data)!=doc.custom_receipt_policy_fingerprint:
        frappe.throw('Receipt policy fingerprint/version mismatch.')
    boundary=frappe.get_doc('Production Receipt Policy Boundary',doc.custom_receipt_policy_boundary)
    if boundary.work_order!=doc.work_order or boundary.job_card!=doc.job_card or boundary.policy_version!=data['policy_version']:
        frappe.throw('Receipt policy boundary belongs to another run or policy.')
    if not (get_datetime(boundary.effective_from)<=get_datetime(data['interval_start'])<=get_datetime(data['cutoff'])):
        frappe.throw('Receipt interval precedes its prospective activation boundary.')
    if get_datetime(data['cutoff'])!=get_datetime(doc.cutoff):frappe.throw('Receipt policy cutoff must match the lot.')
    actual=frappe.parse_json(doc.get('consumption_evidence') or '[]')
    if actual!=data.get('actual_attribution',[]):frappe.throw('Actual-backed attribution must remain separate and match the frozen mixed snapshot.')
    if data['classification']!=('Mixed' if actual else 'Provisional'):frappe.throw('Provisional evidence cannot be represented as measured actual consumption.')
    seen=set()
    for row in data['materials']:
        required=('item_code','batch_no','stock_uom','provisional_qty','valuation_basis','evidence_source')
        if any(row.get(k) in (None,'') for k in required):frappe.throw('Incomplete provisional material attribution.')
        key=(row['item_code'],row['batch_no'])
        if key in seen:frappe.throw('Duplicate provisional material attribution.')
        seen.add(key);quantity(row['provisional_qty'])
        if frappe.db.get_value('Batch',row['batch_no'],'item')!=row['item_code']:frappe.throw('Provisional RM batch/item mismatch.')

def protect_stock(doc,method=None):
    old=doc.get_doc_before_save()
    if old and any(doc.get(f)!=old.get(f) for f in STOCK_FIELDS):frappe.throw('Protected provisional stock lineage cannot change or be backfilled.')
    if not any(doc.get(f) for f in STOCK_FIELDS):return
    if doc.is_new() and not controlled(doc):frappe.throw('Provisional stock lineage requires controlled server authority.')
    lot=frappe.get_doc('Partial FG Lot',doc.custom_provisional_lot)
    if not lot.custom_receipt_policy_fingerprint or (doc.custom_provisional_work_order,doc.custom_provisional_job_card,doc.company)!=(lot.work_order,lot.job_card,lot.company):
        frappe.throw('Provisional stock run/lot lineage mismatch.')
    if doc.custom_provisional_fingerprint!=lot.custom_receipt_policy_fingerprint:frappe.throw('Provisional stock fingerprint mismatch.')
    for f,purpose in [('custom_provisional_manufacture','Manufacture'),('custom_provisional_wip_entry','Material Issue')]:
        if doc.get(f):
            entry=frappe.get_doc('Stock Entry',doc.get(f))
            if entry.company!=doc.company or entry.purpose!=purpose or entry.custom_provisional_lot!=lot.name:frappe.throw('Provisional paired stock entry lineage mismatch.')
    if doc.purpose=='Manufacture' and (doc.work_order!=lot.work_order or doc.get('custom_partial_fg_lot')!=lot.name):frappe.throw('Manufacture must retain native WO and partial-lot lineage.')
    if doc.purpose not in ('Material Issue','Manufacture'):frappe.throw('Invalid provisional posting purpose.')

def setup():
    if not production_site_allowed():return
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    def field(name,kind,options=None):
        d={'fieldname':name,'label':name.replace('custom_','').replace('_',' ').title(),'fieldtype':kind,'read_only':1,'hidden':1,'no_copy':1}
        if options:d['options']=options
        return d
    for dt in RECORDS:frappe.db.add_unique(dt,['job_card','revision'],constraint_name='unique_run_revision')
    create_custom_fields({'Partial FG Lot':[field(LOT_FIELDS[0],'Data'),field(LOT_FIELDS[1],'Link','Production Receipt Policy Boundary'),field(LOT_FIELDS[2],'Long Text'),field(LOT_FIELDS[3],'Data')],
        'Stock Entry':[field(name,'Link',dt) for name,dt in zip(STOCK_FIELDS[:-1],['Work Order','Job Card','Partial FG Lot','Stock Entry','Stock Entry'])]+[field(STOCK_FIELDS[-1],'Data')]},update=True)
