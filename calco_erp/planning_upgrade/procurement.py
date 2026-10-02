"""Draft procurement only, using unconverted MR plus exact outstanding PO.

No MR is physical supply. A converted MR quantity is counted in PO supply once,
not both MR and PO. Overdue POs remain commitments with unknown usable ETA.
"""
from contextvars import ContextVar

_creating = ContextVar("calco_planning_procurement_creation", default=False)

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
import frappe
from frappe.utils import getdate
from calco_erp.planning_upgrade.allocation import number


def unresolved_requests(items,company):
    if not items:return []
    return frappe.db.sql("""SELECT mr.name,mri.name detail,mri.item_code,mri.stock_uom,
        GREATEST(mri.stock_qty-COALESCE(ordered.quantity,0),0) outstanding
        FROM `tabMaterial Request` mr JOIN `tabMaterial Request Item` mri ON mri.parent=mr.name
        LEFT JOIN (SELECT poi.material_request_item,SUM(poi.stock_qty) quantity
            FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON po.name=poi.parent
            WHERE po.docstatus=1 GROUP BY poi.material_request_item) ordered
            ON ordered.material_request_item=mri.name
        WHERE mr.company=%s AND mr.material_request_type='Purchase' AND mr.docstatus<2
        AND mr.status NOT IN ('Stopped','Cancelled')
        AND COALESCE(mr.custom_calco_permanent_closure,0)=0
        AND mri.item_code IN %s""",(company,tuple(items)),as_dict=True)


def proposal(check):
    from calco_erp.planning_upgrade.planning import future_supply
    needs=defaultdict(Decimal);physical={};fgs=defaultdict(list)
    for row in check['rows']:
        for material in row['materials']:
            item=material['item_code'];needs[item]+=number(material['required'])
            physical[item]=max(number(material['released_physical'])-number(material['existing_commitments']),Decimal(0))
            fgs[item].append(dict(item_code=row['item_code'],key=row['key'],required=material['required']))
    mr=unresolved_requests(list(needs),check['company'])
    po=future_supply(list(needs),check['company'])
    output=[]
    for item in sorted(needs):
        mr_rows=[dict(r) for r in mr if r.item_code==item and number(r.outstanding)>0]
        unconverted=sum((number(r['outstanding']) for r in mr_rows),Decimal(0))
        expected=sum((number(r['outstanding']) for r in po.get(item,[])),Decimal(0))
        shortage=max(needs[item]-physical[item],Decimal(0))
        new=max(shortage-unconverted-expected,Decimal(0))
        output.append(dict(item_code=item,required=str(needs[item]),released_net=str(physical[item]),
            current_shortage=str(shortage),unconverted_mr=str(unconverted),outstanding_po=str(expected),
            new_procurement=str(new),material_requests=mr_rows,purchase_orders=po.get(item,[]),
            fg_lines=fgs[item],stock_uom=frappe.db.get_value('Item',item,'stock_uom')))
    return output


@frappe.whitelist()
def prepare_draft(from_date=None,to_date=None,request_token=''):
    from calco_erp.calco_production import planning_release
    from calco_erp.planning_upgrade.planning import check_month
    from calco_erp.planning_upgrade.fresh_read import fresh_committed_read
    from calco_erp.inventory.availability import PRODUCTION_SOURCE_WAREHOUSE
    planning_release.ensure_planning_access()
    if not frappe.has_permission('Material Request','create'):
        frappe.throw('Material Request create permission required',frappe.PermissionError)
    token=str(request_token or '').strip()
    if not token or len(token)>100:frappe.throw('A valid procurement request token is required.')
    initial=check_month(from_date,to_date)
    company=initial['company'];frappe.get_doc('Company',company).check_permission('read')
    frappe.db.sql('SELECT name FROM tabCompany WHERE name=%s FOR UPDATE',company)
    key=hashlib.sha256((company+'|'+token).encode()).hexdigest()
    existing=frappe.db.sql('SELECT name FROM `tabMaterial Request` WHERE custom_rm_planning_key=%s FOR UPDATE',key)
    if existing:
        doc=frappe.get_doc('Material Request',existing[0][0]);doc.check_permission('read')
        return dict(name=doc.name,existing=True,docstatus=doc.docstatus)
    with fresh_committed_read():
        check=check_month(from_date,to_date)
        if check['blockers']:frappe.throw('Resolve the RM Check blockers before preparing procurement.')
        rows=proposal(check)
    to_buy=[r for r in rows if number(r['new_procurement'])>0]
    if not to_buy:frappe.throw('No uncovered procurement shortfall remains after existing MR and PO commitments.')
    evidence=dict(version='rm-planning-procurement-v1',company=company,
        from_date=check['from_date'],to_date=check['to_date'],rows=rows)
    doc=frappe.new_doc('Material Request');doc.company=company;doc.material_request_type='Purchase'
    doc.custom_rm_planning_key=key
    doc.custom_rm_planning_evidence=json.dumps(evidence,sort_keys=True,default=str)
    due=max(getdate(check['from_date']),getdate())
    for row in to_buy:
        doc.append('items',dict(item_code=row['item_code'],qty=float(number(row['new_procurement'])),
            uom=row['stock_uom'],stock_uom=row['stock_uom'],conversion_factor=1,
            warehouse=PRODUCTION_SOURCE_WAREHOUSE,schedule_date=due))
    creation = _creating.set(True)
    try:
        doc.insert()
    finally:
        _creating.reset(creation)
    return dict(name=doc.name,existing=False,docstatus=doc.docstatus,proposal=rows)


def protect_provenance(doc, method=None):
    """Protect origin evidence without preventing normal Draft MR planning edits."""
    fields = ('custom_rm_planning_key', 'custom_rm_planning_evidence')
    old = doc.get_doc_before_save()
    if old and any(old.get(field) for field in fields):
        if any(doc.get(field) != old.get(field) for field in fields):
            frappe.throw('RM planning procurement provenance is immutable.')
        if doc.company != old.company or doc.material_request_type != old.material_request_type:
            frappe.throw('Planning procurement company and request type cannot be changed.')
    elif any(doc.get(field) for field in fields) and not _creating.get():
        frappe.throw('RM planning procurement provenance requires the controlled preparation action.')
