"""Final Confirmation mode on the familiar Production Consumption Entry."""
from decimal import Decimal
import frappe
from frappe.utils import get_datetime, now_datetime
from calco_erp.calco_production import production_batch_closure as closure
from calco_erp.calco_production import receipt_policy_infrastructure as infra
from calco_erp.calco_production.final_consumption_quantities import plan, FinalConsumptionError

MODE = 'Final Confirmation'
VERSION = 'final-pce-v1'
TOKEN = object()
DT = 'Production Consumption Entry'
HEADER = ('user','company','work_order','job_card','production_line','fg_code','fg_batch_no','warehouse','final_release','posting_datetime','supersedes')
AUDIT = ('final_status','final_result','batch_closure','final_fingerprint','confirmed_by','confirmed_on','stock_entry')


def is_final(doc):
    return doc.get('consumption_mode') == MODE


def guard_mode(doc):
    old = doc.get_doc_before_save()
    if old and (old.get('consumption_mode') or '') != (doc.get('consumption_mode') or ''):
        frappe.throw('Consumption mode cannot be changed on an existing entry.')


def latest(job_card, exclude=None):
    filters = dict(job_card=job_card, consumption_mode=MODE, docstatus=1)
    if exclude: filters['name'] = ['!=', exclude]
    name = frappe.db.get_value(DT, filters, 'name', order_by='creation desc, name desc')
    return frappe.get_doc(DT, name) if name else None


def ledger(wo, card):
    rows, entries = closure.ledger_snapshot(wo, card)
    evidence = dict(materials=rows, ledger=entries)
    return rows, infra.fingerprint(evidence), evidence


@frappe.whitelist()
@closure.physical.atomic

def open_entry(final_release, supersedes=None):
    closure.authority(manager=bool(supersedes))
    if not frappe.has_permission(DT, 'create'):
        frappe.throw('Production Consumption Entry create permission required.', frappe.PermissionError)
    release, lot, wo, card, run_lots = closure.context(final_release)
    old = latest(card.name)
    if old and not supersedes:
        old.check_permission('read')
        return old.name
    if supersedes and old and old.get('supersedes') == supersedes:
        old.check_permission('read')
        return old.name  # retry after the correction was already accepted
    if supersedes and (not old or old.name != supersedes):
        frappe.throw('A correction must reference the latest Final Confirmation.')
    if not old and closure.latest(card.name):
        frappe.throw('Historical closure evidence already exists. Do not create a retrospective PCE.')
    existing = frappe.db.get_value(DT, dict(job_card=card.name, consumption_mode=MODE,
        docstatus=0, supersedes=supersedes or ''), 'name')
    if existing:
        frappe.get_doc(DT, existing).check_permission('read')
        return existing
    rows, fingerprint, evidence = ledger(wo, card)
    previous_rows = {(r.rm_code,r.rm_batch_no):r for r in old.items} if old else {}
    header = dict(user=frappe.session.user,company=wo.company,work_order=wo.name,job_card=card.name,
        production_line=card.workstation,fg_code=wo.production_item,fg_batch_no=wo.custom_fg_batch_no,
        warehouse=wo.wip_warehouse,final_release=release.name,posting_datetime=str(now_datetime()),supersedes=supersedes or '')
    items = []
    for row in rows:
        key = row['item_code'],row['batch_no']
        prior = previous_rows[key].prior_actual_qty if key in previous_rows else row['actual_accounted_qty']
        items.append(dict(rm_code=key[0],rm_batch_no=key[1],issued_qty=str(row['issued_qty']),
            prior_actual_qty=str(prior),provisional_qty=str(row['provisional_qty']),
            remaining_wip_qty=str(row['remaining_qty']),accounted_qty=str(row['accounted_qty']),
            additional_actual_qty='',final_actual_qty='',stock_delta='',rm_qty_consumed=0))
    source = dict(version=VERSION,header=header,rows=items,ledger_fingerprint=fingerprint,ledger_evidence=evidence,
        previous_closure=old.batch_closure if old else None)
    doc = frappe.get_doc(dict(doctype=DT,consumption_mode=MODE,**header,
        final_source=infra.canonical(source),items=items))
    doc.flags.final_pce_token = TOKEN
    doc.insert()
    return doc.name


def validate(doc):
    closure.authority(manager=bool(doc.get('supersedes')))
    old = doc.get_doc_before_save()
    if doc.is_new() and doc.flags.get('final_pce_token') is not TOKEN:
        frappe.throw('Open Final RM Consumption from the production run.')
    if old and old.docstatus == 1:
        frappe.throw('Submitted final consumption is immutable. Use a controlled correction.')
    if old and old.final_source != doc.final_source:
        frappe.throw('Final consumption source evidence is immutable.')
    source = infra.payload(doc.final_source)
    if source.get('version') != VERSION: frappe.throw('Final consumption source is missing.')
    for field in HEADER:
        actual, expected = doc.get(field) or '', source['header'].get(field) or ''
        if field == 'posting_datetime': actual,expected = get_datetime(actual),get_datetime(expected)
        if actual != expected: frappe.throw('Final production context is protected: '+field)
    for field in AUDIT:
        if (doc.get(field) or '') != ((old.get(field) if old else None) or ''):
            frappe.throw('Final consumption audit references are server-controlled.')
    expected = {(r['rm_code'],r['rm_batch_no']):r for r in source['rows']}
    seen = set()
    materials, measurements = [], []
    for row in doc.items:
        key = row.rm_code,row.rm_batch_no
        if key not in expected or key in seen: frappe.throw('Retain each authoritative RM batch exactly once.')
        seen.add(key)
        base = expected[key]
        for field in ('issued_qty','prior_actual_qty','provisional_qty','remaining_wip_qty','accounted_qty'):
            if str(row.get(field)) != str(base[field]): frappe.throw('Material context quantities are read-only.')
        material = dict(item_code=key[0],batch_no=key[1],prior_actual_qty=base['prior_actual_qty'],
            provisional_qty=base['provisional_qty'],accounted_qty=base['accounted_qty'],
            issued_qty=base['issued_qty'],remaining_qty=base['remaining_wip_qty'])
        supplied = dict(item_code=key[0],batch_no=key[1],additional_actual_qty=row.additional_actual_qty)
        if row.additional_actual_qty is None or not str(row.additional_actual_qty).strip():
            if doc.docstatus == 1: frappe.throw('Enter Consumed Qty for every RM batch, including an explicit 0.')
            row.final_actual_qty = row.stock_delta = ''
            row.rm_qty_consumed = 0
        else:
            try: calculated = plan([material],[supplied])[0]
            except FinalConsumptionError as exc: frappe.throw(str(exc))
            row.final_actual_qty = calculated['final_actual_qty']
            row.stock_delta = calculated['stock_delta']
            row.rm_qty_consumed = calculated['additional_actual_qty']
        materials.append(material); measurements.append(supplied)
    if seen != set(expected): frappe.throw('Every authoritative RM batch is required.')
    if doc.docstatus == 1:
        if not doc.final_confirmation: frappe.throw('Explicitly confirm final additional actual consumption.')
        if doc.supersedes and not (doc.correction_reason or '').strip(): frappe.throw('Correction Reason is mandatory.')


def submit(doc):
    release,lot,wo,card,run_lots = closure.context(doc.final_release)  # shared WO lock
    source = infra.payload(doc.final_source)
    old = latest(card.name,exclude=doc.name)
    if (old.name if old else '') != (doc.supersedes or ''):
        frappe.throw('Another Final Confirmation exists. Open the latest evidence.')
    if old and old.batch_closure != source.get('previous_closure'):
        frappe.throw('Correction closure lineage changed.')
    if not old and closure.latest(card.name): frappe.throw('Final closure already exists; no retrospective PCE is permitted.')
    rows,fingerprint,_ = ledger(wo,card)
    if fingerprint != source['ledger_fingerprint']:
        frappe.throw('WIP evidence changed. Delete this new Draft and reopen Final RM Consumption before submitting.')
    stock_entry = None
    if not any(Decimal(r.stock_delta)<0 for r in doc.items):
        stock_entry = post_positive_delta(doc)
    result = closure.confirm(doc.final_release,
        [dict(item_code=r.rm_code,batch_no=r.rm_batch_no,final_actual_qty=r.final_actual_qty) for r in doc.items],
        production_complete=1,supersedes=source.get('previous_closure'),correction_reason=doc.correction_reason)
    evidence = dict(source=source,measurements=[dict(item=r.rm_code,batch=r.rm_batch_no,
        additional=r.additional_actual_qty,final=r.final_actual_qty,delta=r.stock_delta) for r in doc.items],
        closure=result['name'],stock_entry=stock_entry,actor=frappe.session.user,at=str(now_datetime()))
    doc.db_set(dict(batch_closure=result['name'],stock_entry=stock_entry,final_status=result['status'],
        final_result='\n'.join(result.get('reasons',[])),
        final_fingerprint=infra.fingerprint(evidence),confirmed_by=evidence['actor'],confirmed_on=evidence['at']),update_modified=False)


def post_positive_delta(doc):
    from calco_erp.calco_production.doctype.production_consumption_entry.production_consumption_entry import create_material_issue_stock_entry
    positive = [r for r in doc.items if Decimal(r.stock_delta)>0]
    if not positive: return None
    posting = frappe._dict(doc.as_dict())
    posting['items'] = [frappe._dict(dict(r.as_dict(),rm_qty_consumed=r.stock_delta)) for r in positive]
    posting['final_pce_posting_token'] = TOKEN
    return create_material_issue_stock_entry(posting).name


def prevent_cancel(doc):
    frappe.throw('Use controlled Final Confirmation correction and referenced restoration. Do not cancel final consumption evidence.')


def stock_entries(work_order,cutoff=None):
    if not frappe.get_meta(DT).has_field('consumption_mode'): return []
    condition = ' and timestamp(se.posting_date,se.posting_time)<=%(cutoff)s and se.creation<=%(cutoff)s' if cutoff else ''
    return frappe.db.sql('''select se.name from `tabStock Entry` se
        inner join `tabProduction Consumption Entry` p on p.name=se.custom_production_consumption_entry
        where p.docstatus=1 and se.docstatus=1 and se.purpose='Material Issue'
        and ifnull(se.custom_provisional_work_order,'')=''
        and ((p.consumption_mode=%(mode)s and p.work_order=%(wo)s)
             or (ifnull(p.consumption_mode,'')!=%(mode)s and se.work_order=%(wo)s)) '''+condition+
        ' order by se.posting_date,se.posting_time,se.name',dict(mode=MODE,wo=work_order,cutoff=cutoff),as_dict=True)


def add_posted_wip_consumption(work_order, totals, item_code=None):
    references = stock_entries(work_order)
    if not references:
        return
    wo = frappe.get_doc('Work Order',work_order)
    for ref in references:
        entry = frappe.get_doc('Stock Entry',ref.name)
        amounts = {}
        for row in entry.items:
            if item_code and item_code != row.item_code: continue
            if row.s_warehouse != wo.wip_warehouse or row.t_warehouse or entry.company != wo.company:
                frappe.throw('Consumption stock lineage is invalid.')
            if row.serial_and_batch_bundle:
                bundle = frappe.get_doc('Serial and Batch Bundle',row.serial_and_batch_bundle)
                if bundle.item_code != row.item_code or bundle.warehouse != wo.wip_warehouse:
                    frappe.throw('Consumption batch bundle lineage is invalid.')
                batches = [(r.batch_no,-Decimal(str(r.qty))) for r in bundle.entries]
            else:
                batches = [(row.batch_no,Decimal(str(row.transfer_qty)))]
            if sum((qty for batch,qty in batches),Decimal(0)) != Decimal(str(row.transfer_qty)):
                frappe.throw('Consumption batch quantity differs from native stock quantity.')
            for batch,qty in batches:
                if not batch or qty <= 0: frappe.throw('Consumption requires exact outgoing RM batches.')
                key = row.item_code,batch
                amounts[key] = amounts.get(key,Decimal(0)) + qty
        for key,qty in amounts.items():
            if key not in totals:
                frappe.throw('Consumption references material not issued to this Work Order.')
            if entry.name not in totals[key]['consumption_entries']:
                totals[key]['consumed_qty'] += qty if isinstance(totals[key]['consumed_qty'], Decimal) else float(qty)
                totals[key]['consumption_entries'].append(entry.name)


def validate_stock(doc, method=None):
    name = doc.get('custom_production_consumption_entry')
    if not name: return
    pce = frappe.get_doc(DT,name)
    if not is_final(pce): return
    if doc.is_new() and doc.flags.get('final_pce_posting_token') is not TOKEN:
        frappe.throw('Create final consumption Stock Entries through Final Confirmation only.')
    if pce.docstatus != 1 or doc.purpose != 'Material Issue' or doc.company != pce.company:
        frappe.throw('Final consumption Stock Entry authority is invalid.')
    if pce.stock_entry and pce.stock_entry != doc.name:
        frappe.throw('Final consumption already has a linked Stock Entry.')
    existing = frappe.db.exists('Stock Entry',{'custom_production_consumption_entry':pce.name,
        'docstatus':['!=',2],'name':['!=',doc.name]})
    if existing: frappe.throw('A final-consumption Stock Entry already exists.')
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no
    expected = {(r.rm_code,r.rm_batch_no):Decimal(r.stock_delta) for r in pce.items if Decimal(r.stock_delta)>0}
    actual = {}
    for row in doc.items:
        key = row.item_code,get_stock_entry_row_batch_no(row)
        if key in actual or row.s_warehouse != pce.warehouse or row.t_warehouse:
            frappe.throw('Final consumption must retain exact outgoing WIP item/batch lineage.')
        actual[key] = Decimal(str(row.transfer_qty))
        if Decimal(str(row.qty)) != actual[key] or Decimal(str(row.conversion_factor)) != 1:
            frappe.throw('Final consumption uses the recorded stock UOM quantity without conversion.')
    if actual != expected:
        frappe.throw('Posted quantity must exactly equal the frozen unresolved final-consumption delta.')


def before_cancel_stock(doc, method=None):
    name = doc.get('custom_production_consumption_entry')
    if name and is_final(frappe.get_doc(DT,name)):
        prevent_cancel(doc)
