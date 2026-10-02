"""Permission-aware, bounded result paging; COUNT uses the same predicate."""
import frappe
from frappe.utils import cint
from frappe.desk.reportview import get_match_cond

SEARCHES={'Any','MR','PO','PR','Batch No','Supplier','Item Code'}


def scope(query='', search_by='Any'):
    if not frappe.has_permission('Material Request','read'):
        frappe.throw('Material Request read permission required',frappe.PermissionError)
    if search_by not in SEARCHES:frappe.throw('Invalid traceability search type')
    query=(query or '').strip()
    predicates=[];args=dict(term='%'+query+'%')
    mr='`tabMaterial Request`'
    po="EXISTS (SELECT 1 FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON po.name=poi.parent WHERE poi.material_request="+mr+".name AND po.docstatus<2 AND ({condition}))"
    pr="EXISTS (SELECT 1 FROM `tabPurchase Receipt Item` pri JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent LEFT JOIN `tabPurchase Order Item` p ON p.name=pri.purchase_order_item WHERE (pri.material_request="+mr+".name OR p.material_request="+mr+".name) AND pr.docstatus<2 AND ({condition}))"
    if query:
        if search_by in ('Any','MR'):predicates.append(mr+'.name LIKE %(term)s')
        if search_by in ('Any','PO'):predicates.append(po.format(condition='po.name LIKE %(term)s'))
        if search_by in ('Any','PR'):predicates.append(pr.format(condition='pr.name LIKE %(term)s'))
        if search_by in ('Any','Supplier'):
            predicates.extend([po.format(condition='po.supplier LIKE %(term)s'),pr.format(condition='pr.supplier LIKE %(term)s')])
        if search_by in ('Any','Item Code'):
            predicates.append('EXISTS (SELECT 1 FROM `tabMaterial Request Item` mri WHERE mri.parent='+mr+'.name AND mri.item_code LIKE %(term)s)')
        if search_by in ('Any','Batch No'):
            predicates.append(pr.format(condition="pri.batch_no LIKE %(term)s OR EXISTS (SELECT 1 FROM `tabSerial and Batch Entry` sbe WHERE sbe.parent=pri.serial_and_batch_bundle AND sbe.batch_no LIKE %(term)s)"))
    where=mr+".material_request_type='Purchase' AND "+mr+'.docstatus<2'+get_match_cond('Material Request')
    if predicates:where+=' AND ('+' OR '.join(predicates)+')'
    return where,args


def page(query='',search_by='Any',limit=20,offset=0):
    where,args=scope(query,search_by)
    limit=max(1,min(cint(limit) or 20,50));offset=max(cint(offset),0)
    args.update(limit=limit,offset=offset)
    mr='`tabMaterial Request`'
    total=frappe.db.sql('SELECT COUNT(*) FROM '+mr+' WHERE '+where,args)[0][0]
    names=frappe.db.sql('SELECT '+mr+'.name FROM '+mr+' WHERE '+where+
        ' ORDER BY '+mr+'.modified DESC, '+mr+'.name DESC LIMIT %(limit)s OFFSET %(offset)s',args,pluck=True)
    return dict(names=names,total_count=total,offset=offset,page_length=limit,has_more=offset+len(names)<total,quantities=quantities(where,args))


def quantities(where,args):
    """Aggregate the complete permission-filtered population without journey hydration.

    Native stock-UOM factors govern physical quantities. Legacy Calco QC custom
    quantities label stock UOM but were initialized from receipt UOM. Non-unit
    conversion evidence is therefore flagged instead of inventing a QC total.
    """
    rows=frappe.db.sql("""WITH matching AS (
      SELECT name FROM `tabMaterial Request` WHERE """+where+"""
    ), po_lines AS (
      SELECT i.*,p.status po_status FROM `tabPurchase Order Item` i
      JOIN `tabPurchase Order` p ON p.name=i.parent
      WHERE p.docstatus=1 AND i.material_request IN (SELECT name FROM matching)
    ), pr_lines AS (
      SELECT i.*,COALESCE(NULLIF(i.material_request,''),po.material_request) mr FROM `tabPurchase Receipt Item` i
      JOIN `tabPurchase Receipt` p ON p.name=i.parent
      LEFT JOIN po_lines po ON po.name=i.purchase_order_item
      WHERE p.docstatus=1 AND p.is_return=0
        AND (i.material_request IN (SELECT name FROM matching) OR po.name IS NOT NULL)
    ), measures AS (
      SELECT i.stock_uom uom,'requested' kind,SUM(i.stock_qty) qty,0 review
      FROM `tabMaterial Request Item` i JOIN matching m ON m.name=i.parent GROUP BY i.stock_uom
      UNION ALL
      SELECT stock_uom,'ordered',SUM(stock_qty),0 FROM po_lines GROUP BY stock_uom
      UNION ALL
      SELECT stock_uom,'received',SUM(COALESCE(NULLIF(received_qty,0),qty)*conversion_factor),0
      FROM pr_lines GROUP BY stock_uom
      UNION ALL
      SELECT stock_uom,'qc_accepted',SUM(CASE WHEN conversion_factor=1 THEN custom_accepted_qty ELSE 0 END),
        SUM(CASE WHEN conversion_factor<>1 THEN 1 ELSE 0 END) FROM pr_lines GROUP BY stock_uom
      UNION ALL
      SELECT stock_uom,'rejected',SUM(CASE WHEN conversion_factor=1 THEN custom_rejected_qty ELSE 0 END),
        SUM(CASE WHEN conversion_factor<>1 THEN 1 ELSE 0 END) FROM pr_lines GROUP BY stock_uom
      UNION ALL
      SELECT i.stock_uom,'returned',SUM(ABS(i.stock_qty)),0 FROM `tabPurchase Receipt Item` i
      JOIN `tabPurchase Receipt` p ON p.name=i.parent
      LEFT JOIN po_lines po ON po.name=i.purchase_order_item
      WHERE p.docstatus=1 AND p.is_return=1
        AND (i.material_request IN (SELECT name FROM matching) OR po.name IS NOT NULL) GROUP BY i.stock_uom
      UNION ALL
      SELECT po.stock_uom,'outstanding_po',SUM(CASE WHEN po.po_status IN ('Closed','Completed','Cancelled','Stopped','On Hold') THEN 0
        ELSE GREATEST(po.stock_qty-COALESCE(r.received,0),0) END),0 FROM po_lines po
      LEFT JOIN (SELECT purchase_order_item,SUM(COALESCE(NULLIF(received_qty,0),qty)*conversion_factor) received
        FROM pr_lines GROUP BY purchase_order_item) r ON r.purchase_order_item=po.name GROUP BY po.stock_uom
      UNION ALL
      SELECT d.stock_uom,'released',SUM(d.transfer_qty),0 FROM `RM_RELEASE_PLACEHOLDER` rn
      JOIN `tabStock Entry` se ON se.name=rn.custom_generated_stock_entry AND se.docstatus=1
      JOIN `tabStock Entry Detail` d ON d.parent=se.name AND d.item_code=rn.item_code
      WHERE rn.docstatus=1 AND rn.status='Released' AND d.t_warehouse IS NOT NULL AND d.t_warehouse<>''
        AND rn.custom_purchase_receipt_item IN (SELECT name FROM pr_lines)
      GROUP BY d.stock_uom
    ) SELECT uom,kind,COALESCE(qty,0) qty,review FROM measures""".replace('`RM_RELEASE_PLACEHOLDER`','`tabRM Release Note`'),args,as_dict=True)
    output={}
    for row in rows:
        uom=row.uom or 'Unknown UOM'
        values=output.setdefault(uom,dict(stock_uom=uom,qc_quantity_review_required=False))
        values[row.kind]=None if row.review else float(row.qty)
        if row.review:values['qc_quantity_review_required']=True
    for values in output.values():
        values['qc_pending']=None if values['qc_quantity_review_required'] else max(
            values.get('received',0)-values.get('qc_accepted',0)-values.get('rejected',0),0)
    return [output[k] for k in sorted(output)]
