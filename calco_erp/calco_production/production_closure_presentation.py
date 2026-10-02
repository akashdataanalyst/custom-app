"""Read-only closure presentation over the authoritative existing transaction engine."""
import frappe
from frappe.utils import now_datetime
from calco_erp.calco_production import production_batch_closure as closure
from calco_erp.calco_production import physical_completion as physical, partial_fg_lots as lots


@frappe.whitelist()
def preview(final_release):
    # Delegate eligibility, permissions and material quantities to the current engine.
    result = closure.preview(final_release)
    if not result.get('available'):
        return result
    release = frappe.get_doc('Final QC Release', final_release)
    lot_name = frappe.db.get_value('Partial FG Lot', {'lot_batch': release.batch_no, 'docstatus': 1}, 'name')
    lot = frappe.get_doc('Partial FG Lot', lot_name)
    wo = frappe.get_doc('Work Order', lot.work_order)
    card = frappe.get_doc('Job Card', lot.job_card)
    end = physical.end_event(card) or {}
    result.update(final_release=release.name, work_order=wo.name, job_card=card.name,
        production_context=dict(work_order=wo.name, job_card=card.name,
            production_line=card.get('workstation'), fg_item=wo.production_item,
            fg_grade=wo.get('item_name') or wo.production_item,
            parent_batch=wo.get('custom_fg_batch_no'),
            stock_uom=frappe.db.get_value('Item', wo.production_item, 'stock_uom'),
            produced_qty=lots.fg_state(card)['fg_qty'], received_qty=lots.received_fg(wo.name),
            physical_end_timestamp=end.get('at'),
            operators=[r.employee for r in card.get('employee', []) if r.employee],
            confirmed_by=frappe.session.user, posting_datetime=str(now_datetime())))
    return result


@frappe.whitelist()
def preview_for_job_card(job_card):
    closure.authority()
    card = frappe.get_doc('Job Card', job_card)
    card.check_permission('read')
    if not physical.controlled(card) or not physical.end_event(card):
        return dict(available=False, reason='Confirm Physical End before final consumption.')
    lot = frappe.db.get_value('Partial FG Lot',
        {'job_card': card.name, 'work_order': card.work_order, 'docstatus': 1},
        ['name', 'lot_batch'], as_dict=True, order_by='cutoff desc, creation desc, name desc')
    release = frappe.db.get_value('Final QC Release',
        {'batch_no': lot.lot_batch, 'docstatus': 1, 'status': 'Released'}, 'name') if lot else None
    if not release:
        return dict(available=False, reason='Receive and release the final FG lot before closing this run.')
    return preview(release)
