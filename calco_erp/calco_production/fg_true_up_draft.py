"""Explicit Recovery Draft LCV preparation. Submission remains standard/manual."""
import hashlib,json
import frappe
from calco_erp.production_site import allowed as production_site_allowed
from frappe.utils import flt,now_datetime,nowdate
from calco_erp.calco_production import partial_lot_reconciliation as audit,partial_fg_lots as lots,fg_confirmation as signing,physical_completion

ROLES={'Production Head','Manufacturing Manager','Accounts Manager'}
FIELDS={'custom_fg_work_order':'Work Order','custom_fg_partial_lot':'Partial FG Lot','custom_fg_manufacture':'Stock Entry'}
PROTECTED=tuple(FIELDS)+('custom_fg_reconciliation_version','custom_fg_reconciliation_snapshot')
_CREATE=object()

def setup():
    if not production_site_allowed():return
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    fields=[{'fieldname':name,'fieldtype':'Link','options':dt,'label':dt,'read_only':1,'no_copy':1} for name,dt in FIELDS.items()]
    fields += [{'fieldname':'custom_fg_reconciliation_version','fieldtype':'Data','label':'FG Reconciliation Version','read_only':1,'no_copy':1},
        {'fieldname':'custom_fg_reconciliation_snapshot','fieldtype':'Long Text','label':'FG Reconciliation Evidence','read_only':1,'no_copy':1}]
    create_custom_fields({'Landed Cost Voucher':fields},update=True)

def authority():
    if not production_site_allowed():frappe.throw('Draft true-up preparation requires the configured manufacturing site.')
    if not ROLES.intersection(frappe.get_roles()):frappe.throw('Authorized Production/Accounts manager required.',frappe.PermissionError)
    frappe.has_permission('Landed Cost Voucher','create',throw=True)

def encode(value):return json.dumps(value,sort_keys=True,default=str,separators=(',',':'))
def version(value):return hashlib.sha256(encode(value).encode()).hexdigest()

def native_period_check(entry):
    doc=frappe.get_doc({'doctype':'Repost Item Valuation','based_on':'Transaction','company':entry.company,
        'voucher_type':'Stock Entry','voucher_no':entry.name,'posting_date':entry.posting_date,'posting_time':entry.posting_time})
    doc.validate_period_closing_voucher();doc.validate_accounts_freeze()

def validate_measurements(measurements):
    """Entry completeness only; leave quantity reconciliation arithmetic untouched."""
    if not isinstance(measurements, list) or not measurements:
        frappe.throw('Explicit final measurements are required; accounted consumption is not a default measurement.')
    for row in measurements:
        if not isinstance(row, dict) or row.get('qty') is None or not str(row.get('qty')).strip():
            frappe.throw('Enter Final Measured Qty for every RM row. Enter 0 explicitly for a measured zero.')
        if not str(row.get('measurement_reference') or '').strip():
            frappe.throw('Measurement Reference is required for every measured RM row.')
        lots.cost.number(row['qty'])


def can_change_account():
    return 'Accounts Manager' in frappe.get_roles()


def validate_account_choice(company, account):
    if not can_change_account() and account != frappe.get_cached_value('Company', company, 'stock_adjustment_account'):
        frappe.throw('Only an authorized Accounts Manager may change the default clearing account.', frappe.PermissionError)


def existing_draft(data):
    # Retain the existing stronger one-pending-Draft-per-lot creation guard.
    name = frappe.db.get_value('Landed Cost Voucher', {'custom_fg_partial_lot':data['lot'], 'docstatus':0}, 'name')
    if not name:
        return None
    doc = frappe.get_doc('Landed Cost Voucher', name)
    doc.check_permission('read')
    return {'name':doc.name, 'same_version':doc.custom_fg_reconciliation_version == data['version'],
        'manufacture':doc.custom_fg_manufacture}


def state(lot_name,measurements,target,account,reason):
    if isinstance(measurements,dict) and 'settlement_preparation' in measurements:
        from calco_erp.calco_production.production_settlement import draft_state
        return draft_state(measurements,lot_name,target,account,reason)
    lot=frappe.get_doc('Partial FG Lot',lot_name);lot.check_permission('read');lots.lock(lot.work_order)
    if not (reason or '').strip():frappe.throw('Reconciliation evidence/reason is required.')
    validate_measurements(measurements)
    validate_account_choice(lot.company, account)
    result=audit.audit_true_up(lot.work_order,measurements,{lot.name:target})
    values=[v for v in result['valuation_reconciliation'] if v['lot']==lot.name]
    if len(values)!=1:frappe.throw('Exactly one valid Manufacture receipt is required for this lot.')
    value=values[0];entry=frappe.get_doc('Stock Entry',value['stock_entry']);entry.check_permission('read')
    native_period_check(entry)
    acct=frappe.get_doc('Account',account);acct.check_permission('read')
    if acct.company!=lot.company or acct.is_group or acct.disabled:frappe.throw('Select a valid same-company valuation expense account.')
    if acct.account_currency!=frappe.get_cached_value('Company',lot.company,'default_currency'):frappe.throw('Use a company-currency valuation expense account for this controlled UAT.')
    sources=lots.cost.actual_sources(lot.work_order,now_datetime())
    return {'work_order':lot.work_order,'lot':lot.name,'manufacture':entry.name,'company':lot.company,
        'measurements':measurements,'target':str(lots.cost.number(target)),'expense_account':account,'reason':reason.strip(),
        'sources':[{k:v for k,v in row.items() if k!='cutoff'} for row in sources],
        'quantity':result['quantity_reconciliation'],'valuation':value}

@frappe.whitelist()
def context(job_card):
    authority();card=frappe.get_doc('Job Card',job_card);card.check_permission('read')
    data=audit.audit_true_up(card.work_order)
    sources=lots.cost.actual_sources(card.work_order,now_datetime());group={}
    for row in sources:
        key=(row['item_code'],row['batch_no']);group[key]=group.get(key,0)+flt(row['qty'])
    return {'work_order':card.work_order,'lots':data['valuation_reconciliation'],
        'quantities':[{'item_code':k[0],'batch_no':k[1],'already_accounted_qty':v} for k,v in group.items()],
        'expense_account':frappe.get_cached_value('Company',card.company,'stock_adjustment_account'),
        'currency':frappe.get_cached_value('Company',card.company,'default_currency'),
        'can_change_account':can_change_account()}

@frappe.whitelist()
def preview(lot_name,measurements,target,account,reason):
    authority();data=state(lot_name,frappe.parse_json(measurements),target,account,reason)
    from datetime import timedelta
    data['version']=version(data)
    blockers=[]
    if any(abs(audit.signed_amount(row['quantity_delta']))>lots.cost.TOL for row in data['quantity']):
        blockers.append('Resolve measured quantity differences through controlled consumption/restoration first. LCV cannot restore RM.')
    if not data['valuation']['ledger_reconciled']:blockers.append('Complete native valuation reposting first.')
    if abs(audit.signed_amount(data['valuation']['residual_value_delta']))<=lots.cost.TOL:blockers.append('No residual valuation adjustment; no LCV can be created.')
    token=None if blockers else signing.sign({'user':frappe.session.user,'expires':str(now_datetime()+timedelta(minutes=30)),'purpose':'draft-fg-lcv-v1','data':data})
    return {'data':data,'blockers':blockers,'token':token,'automatic_submit':False,
        'existing_draft':existing_draft(data),
        'currency':frappe.get_cached_value('Company',data['company'],'default_currency')}

@frappe.whitelist()
@physical_completion.atomic
def create_draft(token):
    authority();signed=signing.decode(token)
    if signed.get('purpose')!='draft-fg-lcv-v1':frappe.throw('Open Final Reconciliation again.')
    data=signed['data'];lots.lock(data['work_order'])
    existing=frappe.db.get_value('Landed Cost Voucher',{'custom_fg_reconciliation_version':data['version'],'docstatus':('!=',2)},'name')
    if existing:
        doc=frappe.get_doc('Landed Cost Voucher',existing);doc.check_permission('read');return {'name':doc.name,'docstatus':doc.docstatus,'reused':True}
    fresh=preview(data['lot'],data['measurements'],data['target'],data['expense_account'],data['reason'])
    if fresh['blockers'] or fresh['data']['version']!=data['version']:frappe.throw('Reconciliation changed or is blocked. Recalculate before preparing a Draft LCV.')
    pending=frappe.db.get_value('Landed Cost Voucher',{'custom_fg_partial_lot':data['lot'],'docstatus':0},'name')
    if pending:frappe.throw('Resolve existing Draft LCV '+pending+' before preparing another valuation correction.')
    doc=frappe.get_doc({'doctype':'Landed Cost Voucher','company':data['company'],'posting_date':nowdate(),
        'distribute_charges_based_on':'Qty','purchase_receipts':[{'receipt_document_type':'Stock Entry','receipt_document':data['manufacture']}],
        'taxes':[{'expense_account':data['expense_account'],'description':data['reason'],'amount':data['valuation']['residual_value_delta']}],
        'custom_fg_work_order':data['work_order'],'custom_fg_partial_lot':data['lot'],'custom_fg_manufacture':data['manufacture'],
        'custom_fg_reconciliation_version':data['version'],'custom_fg_reconciliation_snapshot':encode(data)})
    doc.get_items_from_purchase_receipts();doc.flags.fg_draft_creation=_CREATE;doc.insert()
    return {'name':doc.name,'docstatus':doc.docstatus,'reused':False}

def validate(doc,method=None):
    if not any(doc.get(f) for f in PROTECTED):return
    if doc.is_new() and getattr(doc.flags,'fg_draft_creation',None) is not _CREATE:frappe.throw('Create this controlled LCV through Final Reconciliation.')
    old=doc.get_doc_before_save()
    if old and any(doc.get(f)!=old.get(f) for f in PROTECTED):frappe.throw('Reconciliation identity and evidence are immutable.')
    data=frappe.parse_json(doc.custom_fg_reconciliation_snapshot)
    original=dict(data);original.pop('version',None)
    if version(original)!=data['version'] or doc.custom_fg_reconciliation_version!=data['version']:frappe.throw('Invalid reconciliation evidence fingerprint.')
    if (doc.custom_fg_work_order,doc.custom_fg_partial_lot,doc.custom_fg_manufacture)!=(data['work_order'],data['lot'],data['manufacture']):frappe.throw('Reconciliation genealogy cannot change.')
    if len(doc.purchase_receipts)!=1 or doc.purchase_receipts[0].receipt_document_type!='Stock Entry' or doc.purchase_receipts[0].receipt_document!=data['manufacture']:frappe.throw('LCV must reference its exact Manufacture receipt.')
    entry=frappe.get_doc('Stock Entry',data['manufacture']);finished=[r for r in entry.items if r.is_finished_item]
    delta=audit.signed_amount(data['valuation']['residual_value_delta'])
    if abs(delta)<=lots.cost.TOL:frappe.throw('Zero residual cannot create an LCV.')
    if len(doc.items)!=1 or len(finished)!=1 or doc.items[0].stock_entry_item!=finished[0].name or abs(flt(doc.items[0].qty)-flt(finished[0].qty))>1e-6:frappe.throw('LCV finished-item identity/quantity cannot change.')
    if len(doc.taxes)!=1 or doc.taxes[0].expense_account!=data['expense_account'] or abs(audit.signed_amount(doc.taxes[0].base_amount)-delta)>lots.cost.TOL or abs(audit.signed_amount(doc.items[0].applicable_charges)-delta)>lots.cost.TOL:frappe.throw('LCV must retain the reconciled residual adjustment.')

def before_submit(doc,method=None):
    if not doc.get('custom_fg_reconciliation_version'):return
    authority();validate(doc);data=frappe.parse_json(doc.custom_fg_reconciliation_snapshot)
    fresh=preview(data['lot'],data['measurements'],data['target'],data['expense_account'],data['reason'])
    if fresh['blockers'] or fresh['data']['version']!=data['version']:frappe.throw('Reconciliation evidence changed. Do not submit this stale LCV; recalculate through controlled review.')

def prevent_delete(doc,method=None):
    if doc.get('custom_fg_reconciliation_version'):frappe.throw('Retain controlled reconciliation evidence; use the normal cancellation/review lifecycle.')
