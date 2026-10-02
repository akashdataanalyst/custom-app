"""Object-scoped native bundle permission for an authorized automatic RM release.

No role permissions or global permission flags change. Context is private,
request-local, and expires before returning to the caller.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import frappe
from frappe.utils import flt

_scope = ContextVar('calco_rm_release_bundle_authority', default=None)


@contextmanager
def release_bundle_authority(release):
    from calco_erp.calco_quality import automatic_rm_release as service
    from calco_erp.calco_quality.rm_warehouse_flow import get_rm_flow_warehouses
    if not release.flags.calco_automatic_release:
        frappe.throw('Automatic RM release authority required.')
    qi=frappe.get_doc('Quality Inspection',release.custom_quality_inspection)
    if release.get('custom_rm_deviation_approval'):
        source=frappe.get_doc('RM Deviation Approval',release.custom_rm_deviation_approval)
        source.check_permission('submit')
        if source.docstatus!=1 or source.approval_status!='Approved':
            frappe.throw('Submitted approved deviation required.')
        row=service.get_exact_deviation_purchase_receipt_row(source)
        permitted=flt(source.approved_qty)
    else:
        qi.check_permission('submit')
        if qi.docstatus!=1 or not service.is_incoming_purchase_receipt_qi(qi) or service.normalize_result(qi)!='ACCEPTED':
            frappe.throw('Submitted Accepted Incoming QI required.')
        row=service.get_exact_purchase_receipt_row(qi)
        permitted=flt(qi.custom_accepted_qty)
    pr=service.validate_purchase_receipt(row.parent)
    service.validate_supplier_documents_and_rm_storage(pr)
    service.validate_release_quantity(row,release.release_qty)
    qty=flt(release.release_qty)
    if not math.isfinite(qty) or qty<=0 or qty>permitted+1e-9:
        frappe.throw('Automatic release exceeds its authorized quantity.')
    if (release.item_code,release.batch_no,release.custom_purchase_receipt,release.custom_purchase_receipt_item)!=(row.item_code,row.batch_no,pr.name,row.name):
        frappe.throw('Automatic release source lineage mismatch.')
    warehouses=get_rm_flow_warehouses(pr.company)
    if release.get('release_warehouse') and release.release_warehouse!=warehouses['released']:
        frappe.throw('Automatic release destination must be the configured released warehouse.')
    point='rm_bundle_'+frappe.generate_hash(length=10)
    frappe.db.savepoint(point)
    token=_scope.set(dict(release=release,user=frappe.session.user,company=pr.company,item=row.item_code,batch=row.batch_no,qty=qty,warehouses=warehouses))
    try:
        yield
    except Exception:
        frappe.db.rollback(save_point=point)
        raise
    finally:
        _scope.reset(token)


def matches_authorized_bundle(doc,permission_type):
    scope=_scope.get()
    if not scope or permission_type not in ('create','write','submit') or scope['user']!=frappe.session.user:
        return False
    release=scope['release']
    if not release.name or release.docstatus!=1 or release.get('custom_generated_stock_entry'):
        return False
    if doc.company!=scope['company'] or doc.item_code!=scope['item'] or doc.voucher_type!='Stock Entry':
        return False
    warehouses=scope['warehouses']
    allowed=([warehouses['released']] if doc.type_of_transaction=='Inward' else
             [warehouses[k] for k in ('quarantine','hold','rejected')] if doc.type_of_transaction=='Outward' else [])
    if doc.warehouse not in allowed or not doc.entries:
        return False
    sign=1 if doc.type_of_transaction=='Inward' else -1
    if any(row.batch_no!=scope['batch'] or row.serial_no or not math.isfinite(flt(row.qty)) or flt(row.qty)*sign<=0 for row in doc.entries):
        return False
    if abs(sum(flt(row.qty)*sign for row in doc.entries)-scope['qty'])>1e-9:
        return False
    if doc.voucher_no:
        entry=frappe.get_doc('Stock Entry',doc.voucher_no)
        if entry.get('custom_rm_release_note')!=release.name or entry.purpose!='Material Transfer':
            return False
    return True


class ReleaseBundleMixin:
    def check_permission(self,permtype='read',permlevel=None):
        if matches_authorized_bundle(self,permtype):
            return self
        return super().check_permission(permtype,permlevel=permlevel)
