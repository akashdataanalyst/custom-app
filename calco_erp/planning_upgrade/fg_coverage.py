"""PPC coverage from the controlled FG Released warehouse, in stock UOM.

Firm SO rows already represented in this month's demand are not subtracted a
second time. Other outstanding warehouse commitments retain first claim.
"""
from collections import defaultdict
import frappe
from frappe.utils import flt
from calco_erp.calco_quality.doctype.final_qc_release.final_qc_release import FG_RELEASED_WAREHOUSE


def coverage(items, company, demands):
    if not items:return {},{}
    warehouse=FG_RELEASED_WAREHOUSE
    if frappe.db.get_value('Warehouse',warehouse,'company')!=company:
        frappe.throw('Controlled FG Released warehouse is not configured for this company.')
    included={r['source_row'] for r in demands if r['source_type']=='Sales Order'}
    physical={r.item_code:flt(r.qty) for r in frappe.db.sql(
        'SELECT item_code,SUM(actual_qty) qty FROM tabBin WHERE warehouse=%s AND item_code IN %s GROUP BY item_code',
        (warehouse,tuple(items)),as_dict=True)}
    so_rows=frappe.db.sql("""SELECT i.name,i.item_code,
        GREATEST(i.stock_qty-i.delivered_qty*i.conversion_factor,0) quantity
        FROM `tabSales Order Item` i JOIN `tabSales Order` p ON p.name=i.parent
        WHERE p.company=%s AND p.docstatus=1 AND p.status NOT IN ('Closed','Completed','Cancelled','Stopped')
        AND i.warehouse=%s AND i.item_code IN %s""",(company,warehouse,tuple(items)),as_dict=True)
    external=defaultdict(float)
    for row in so_rows:
        if row.name not in included:external[row.item_code]+=flt(row.quantity)
    reservations=frappe.get_all('Stock Reservation Entry',filters={'warehouse':warehouse,
        'item_code':['in',items],'docstatus':1,'status':['not in',['Closed','Delivered']]},
        fields=['item_code','voucher_type','voucher_detail_no','reserved_qty','delivered_qty','transferred_qty','consumed_qty'])
    sre_so=defaultdict(float);sre_other=defaultdict(float)
    for row in reservations:
        quantity=max(flt(row.reserved_qty)-flt(row.delivered_qty)-flt(row.transferred_qty)-flt(row.consumed_qty),0)
        if row.voucher_type=='Sales Order':
            if row.voucher_detail_no not in included:sre_so[row.item_code]+=quantity
        else:sre_other[row.item_code]+=quantity
    evidence={};net={}
    for item in items:
        committed=max(external[item],sre_so[item])+sre_other[item]
        net[item]=max(physical.get(item,0)-committed,0)
        evidence[item]=dict(warehouse=warehouse,released_physical=physical.get(item,0),
            external_commitments=committed,net_released=net[item])
    return net,evidence
