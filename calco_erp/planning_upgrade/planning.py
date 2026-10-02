"""Pre-release planning using existing released-batch and commitment authority.

No PO or MR quantity is physical supply. Unknown lineage blocks release rather
than guessing a BOM or cancelling someone else's commitment.
"""
import hashlib
import json
from decimal import Decimal
from collections import defaultdict
import frappe
from frappe.utils import flt, getdate, now_datetime
from calco_erp.inventory import availability
from calco_erp.calco_production import planning_release
from calco_erp.planning_upgrade.allocation import allocate, number

VERSION='released-rm-planning-v1'
SNAPSHOT='custom_rm_planning_snapshot'


def bom_components(bom, cache=None, chain=()):
    cache=cache if cache is not None else {}
    if bom in chain:frappe.throw('Cyclic BOM requires master review')
    if bom in cache:return cache[bom]
    doc=frappe.get_doc('BOM',bom)
    if doc.docstatus!=1 or not doc.is_active or number(doc.quantity)<=0:
        frappe.throw('Submitted active BOM with positive base quantity required: '+bom)
    # Match the existing WO/readiness native expansion, including stock UOM,
    # phantom/semi-finished semantics and the submitted explosion table.
    from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict
    native=get_bom_items_as_dict(bom,doc.company,qty=1,fetch_exploded=1)
    result=defaultdict(Decimal)
    for row in native.values():
        factor=number(row.qty)
        if factor<=0:frappe.throw('BOM contains non-positive material quantity: '+bom)
        result[row.item_code]+=factor
    if not result:frappe.throw('BOM has no manufacturing stock requirements: '+bom)
    cache[bom]=dict(result)
    return cache[bom]


def selected_bom(item):
    bom=frappe.db.get_value('Item',item,'default_bom')
    if not bom:frappe.throw('No explicit default BOM: '+item)
    if frappe.db.get_value('BOM',bom,'item')!=item:frappe.throw('Default BOM Item mismatch')
    from calco_erp.calco_production.operation_master import COMPOUNDING_OPERATION
    if not frappe.db.get_value('BOM',bom,'with_operations') or not frappe.db.exists(
            'BOM Operation', {'parent':bom,'operation':COMPOUNDING_OPERATION,'workstation':['!=','']}):
        frappe.throw('Default BOM requires manufacturing-master review for this production route.')
    return bom


def extra_commitments(company, cache):
    """Commit released demand not yet represented by submitted native WO claims.

    Submitted WO reservations are already supplied by availability.py. Never
    add their planned quantity again. Draft WOs belonging to these releases
    remain represented by the release until submission.
    """
    claims=defaultdict(Decimal);evidence=[]
    releases=frappe.db.sql("""SELECT pr.name,pr.custom_rm_planning_snapshot snapshot,
        pri.item_code,pri.requested_qty,pp.name plan,ppi.bom_no,
        COALESCE((SELECT SUM(wo.qty) FROM `tabWork Order` wo WHERE wo.production_plan=pp.name
          AND wo.production_item=pri.item_code AND wo.docstatus=1 AND wo.status NOT IN ('Stopped','Closed','Cancelled')),0) wo_qty
        FROM `tabProduction Requirement` pr JOIN `tabProduction Requirement Item` pri ON pri.parent=pr.name
        LEFT JOIN `tabProduction Plan` pp ON pp.custom_release_authority=pr.name AND pp.docstatus<2
        LEFT JOIN `tabProduction Plan Item` ppi ON ppi.parent=pp.name AND ppi.item_code=pri.item_code
        WHERE pr.custom_calco_release_to_production=1 AND pr.docstatus<2
        AND COALESCE(pr.status,'') NOT IN ('Closed','Cancelled') AND pr.custom_release_company=%s""",company,as_dict=True)
    seen=set()
    for row in releases:
        if row.name in seen:frappe.throw('Multiple plans for release require commitment review: '+row.name)
        seen.add(row.name);qty=max(number(row.requested_qty)-number(row.wo_qty),Decimal(0))
        if not qty:continue
        frozen=json.loads(row.snapshot) if row.snapshot else None
        if frozen:components={k:number(v) for k,v in frozen['components'].items()}
        elif row.bom_no:components=bom_components(row.bom_no,cache)
        else:frappe.throw('Management Review Required: historical release has no frozen BOM authority: '+row.name)
        for item,factor in components.items():claims[item]+=qty*factor
        evidence.append(dict(release=row.name,remaining_fg=str(qty),bom=(frozen or {}).get('bom') or row.bom_no))
    # Non-Calco plans and standalone Draft WOs still commit stock.
    plans=frappe.db.sql("""SELECT p.name,i.item_code,i.bom_no,i.planned_qty,
        COALESCE((SELECT SUM(w.qty) FROM `tabWork Order` w WHERE w.production_plan=p.name
           AND w.production_item=i.item_code AND w.docstatus=1 AND w.status NOT IN ('Stopped','Closed','Cancelled')),0) wo_qty
        FROM `tabProduction Plan` p JOIN `tabProduction Plan Item` i ON i.parent=p.name
        WHERE p.company=%s AND p.docstatus<2 AND COALESCE(p.status,'') NOT IN ('Closed','Completed','Cancelled')
        AND COALESCE(p.custom_release_authority,'')=''""",company,as_dict=True)
    drafts=frappe.get_all('Work Order',filters={'company':company,'docstatus':0,'production_plan':['in',['',None]]},
        fields=['name','production_item as item_code','bom_no','qty as planned_qty'])
    for row in plans+drafts:
        qty=max(number(row.planned_qty)-number(row.get('wo_qty')),Decimal(0))
        if not qty:continue
        if not row.bom_no:frappe.throw('Management Review Required: commitment lacks BOM: '+row.name)
        for item,factor in bom_components(row.bom_no,cache).items():claims[item]+=qty*factor
        evidence.append(dict(source=row.name,remaining_fg=str(qty),bom=row.bom_no))
    return claims,evidence


def released_material(item, additional=0):
    snapshot=availability.get_item_availability(item,warehouse=availability.PRODUCTION_SOURCE_WAREHOUSE)
    if not snapshot.get('has_batch_no'):
        frappe.throw('Management Review Required: non-batch material release authority: '+item)
    # Never use UAT override quantities. Normal released eligibility is computed
    # by the same service used by WO readiness, including batch expiry/disabled.
    physical=sum(number(r['normal_released_eligible_quantity']) for r in snapshot['batches'])
    committed=number(snapshot['reservation_snapshot']['external_claim_quantity'])+number(additional)
    return dict(released=str(physical),committed=str(committed),expected_po='0',
        expected_receipt_date=None,eligible_eta=None,evidence=dict(batches=snapshot['batches'],
        reservations=snapshot['reservation_snapshot']))


def future_supply(items,company):
    if not items:return {}
    rows=frappe.db.sql("""SELECT po.name,poi.name detail,poi.item_code,poi.schedule_date,
       GREATEST(poi.stock_qty-COALESCE(receipts.received,0),0) outstanding
       FROM `tabPurchase Order` po JOIN `tabPurchase Order Item` poi ON poi.parent=po.name
       LEFT JOIN (SELECT pri.purchase_order_item,SUM(GREATEST(COALESCE(NULLIF(pri.received_qty,0),pri.qty)*pri.conversion_factor,0)) received
          FROM `tabPurchase Receipt Item` pri JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent
          WHERE pr.docstatus=1 AND pr.is_return=0 GROUP BY pri.purchase_order_item) receipts
          ON receipts.purchase_order_item=poi.name
       WHERE po.company=%s AND po.docstatus=1 AND po.status NOT IN ('Closed','Completed','Cancelled','Stopped','On Hold')
       AND poi.item_code IN %s ORDER BY poi.schedule_date,po.name,poi.idx""",(company,tuple(items)),as_dict=True)
    result=defaultdict(list)
    for r in rows:
        if number(r.outstanding)>0:result[r.item_code].append(dict(r))
    return dict(result)


@frappe.whitelist()
def check_month(from_date=None,to_date=None):
    planning_release.ensure_planning_access()
    data=planning_release.build_planning_data(from_date=from_date,to_date=to_date)
    frappe.get_doc('Company',data['company']).check_permission('read')
    cache={};requirements=[];errors=[]
    for row in data['rows']:
        if row['review_status']!='Confirmed' or row.get('review_stale') or flt(row['remaining_to_release'])<=0:continue
        try:
            bom=selected_bom(row['item_code']);components=bom_components(bom,cache)
            requirements.append(dict(key=row['recommendation_key'],item_code=row['item_code'],bom=bom,
                remaining=row['remaining_to_release'],sales_order_qty=row['sales_order_qty'],
                required_date=row['required_date'],priority=row['priority'],components=components))
        except frappe.ValidationError as exc:errors.append(dict(key=row['recommendation_key'],reason=str(exc)))
    if not requirements:return dict(rows=[],blockers=errors,company=data['company'],version=VERSION)
    claims,evidence=extra_commitments(data['company'],cache)
    materials={item:released_material(item,claims.get(item,0)) for item in sorted({i for r in requirements for i in r['components']})}
    supply=future_supply(list(materials),data['company'])
    for item,material in materials.items():
        valid=[r for r in supply.get(item,[]) if r.get('schedule_date') and getdate(r['schedule_date'])>=getdate()]
        material['expected_po']=str(sum((number(r['outstanding']) for r in valid),Decimal(0)))
        material['expected_receipt_date']=str(valid[0]['schedule_date']) if valid else None
        material['evidence']['expected_po']=supply.get(item,[])
        # Required QC/RM release duration has no approved universal constant.
        # Receipt ETA is not a promise of production eligibility or completion.
    result=allocate(requirements,materials)
    for row in result:
        row['production_eligible_eta']=None
        row['expected_completion']=None
        row['month_risk']='Arrival/production eligibility unknown' if number(row['waiting_for_rm'])>0 else 'Schedule unknown'
        if number(row['buildable_now'])>0:
            bom=frappe.get_doc('BOM',row['bom'])
            lines={r.workstation for r in bom.operations if r.workstation}
            if len(lines)==1:
                from calco_erp.calco_production.fg_schedule_preview import calculate
                try:
                    preview=calculate(dict(company=data['company'],production_item=row['item_code'],bom_no=row['bom'],
                        qty=row['buildable_now'],custom_machine=next(iter(lines)),use_multi_level_bom=1,
                        custom_fg_requested_start=str(max(getdate(data['from_date']),getdate()))+' 00:00:00'))
                    row['buildable_completion_estimate']=preview['end']
                    row['schedule_evidence']=preview
                    if number(row['waiting_for_rm'])==0:
                        row['expected_completion']=preview['end']
                        row['month_risk']='Will miss month' if getdate(preview['end'])>getdate(data['to_date']) else 'Estimated within month'
                except frappe.ValidationError as exc:
                    row['schedule_blocker']=str(exc)
    return dict(version=VERSION,company=data['company'],from_date=data['from_date'],to_date=data['to_date'],
        calculated_on=str(now_datetime()),rows=result,blockers=errors,commitment_evidence=evidence,
        components={r['key']:{k:str(v) for k,v in r['components'].items()} for r in requirements})


def release_snapshot(key,qty,from_date,to_date):
    # Shared Company row serializes month-wide releases, including distinct FGs.
    # Existing reservation service uses the same sorted Item/Warehouse Bin locks.
    row,data=planning_release._get_requirement(key,from_date,to_date)
    frappe.db.sql('SELECT name FROM tabCompany WHERE name=%s FOR UPDATE',data['company'])
    bom=selected_bom(row['item_code']);components=bom_components(bom)
    from calco_erp.calco_production.material_reservation_submission import _lock_inventory_keys
    _lock_inventory_keys([(i,availability.PRODUCTION_SOURCE_WAREHOUSE) for i in sorted(components)])
    from calco_erp.planning_upgrade.fresh_read import fresh_committed_read
    with fresh_committed_read():
        fresh_row,fresh_data=planning_release._get_requirement(key,from_date,to_date)
        fresh_bom=selected_bom(fresh_row['item_code'])
        fresh_components=bom_components(fresh_bom)
        if fresh_bom!=bom or fresh_components!=components:
            frappe.throw('BOM authority changed while locking materials; run RM Check again.')
        result=check_month(from_date,to_date)
    chosen=next((r for r in result['rows'] if r['key']==key),None)
    if not chosen or number(qty)>number(chosen['buildable_now']):
        frappe.throw('Release exceeds current Released RM allocation; run RM Check again.')
    payload=dict(version=VERSION,key=key,bom=bom,components={k:str(v) for k,v in components.items()},
        quantity=str(number(qty)),calculated_on=result['calculated_on'],actor=frappe.session.user,
        allocation=chosen,commitment_evidence=result['commitment_evidence'],requirement=fresh_row)
    payload['fingerprint']=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    return json.dumps(payload,sort_keys=True,default=str)


from contextvars import ContextVar
from contextlib import contextmanager
_creating = ContextVar('calco_rm_release_creation',default=False)

@contextmanager
def authorized_snapshot_creation():
    token=_creating.set(True)
    try:yield
    finally:_creating.reset(token)


def protect_snapshot(doc,method=None):
    old=doc.get_doc_before_save()
    prior=old.get(SNAPSHOT) if old else None
    current=doc.get(SNAPSHOT)
    if prior:
        if current!=prior:frappe.throw('Released RM planning evidence is immutable')
        identity = ('custom_calco_release_to_production','custom_release_key',
            'custom_release_requirement_key','custom_release_company',
            'custom_release_source_details','week_start_date','week_end_date')
        if any(str(doc.get(f) or '') != str(old.get(f) or '') for f in identity):
            frappe.throw('Released planning identity and source lineage are immutable')
        before=[(r.item_code,number(r.requested_qty),number(r.net_required_qty)) for r in old.items]
        after=[(r.item_code,number(r.requested_qty),number(r.net_required_qty)) for r in doc.items]
        if before!=after:frappe.throw('Released requirement quantity requires controlled revision')
    elif current and not _creating.get():
        frappe.throw('RM planning evidence may only be created by controlled release')
