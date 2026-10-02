import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class MaterialReadinessCheck(Document):
    def validate(self):
        if self.work_order:
            work_order = frappe.get_doc("Work Order", self.work_order)
            self.production_item = work_order.production_item
            self.bom_no = work_order.bom_no
            self.planned_qty = work_order.qty

        if not self.checked_by:
            self.checked_by = frappe.session.user
        if not self.checked_on:
            self.checked_on = now_datetime()

        shortages = self.get_shortages()
        self.shortage_summary = "\n".join(shortages)

        if self.status == "Ready" and shortages:
            frappe.throw("Material Readiness Check cannot be Ready while BOM items are missing released RM.")

        if shortages and self.status == "Draft":
            self.status = "Blocked"
        elif not shortages and self.status in ("Draft", "Blocked"):
            self.status = "Ready"

    def get_shortages(self):
        """Use execution's existing stock/batch/WIP authority, not lifetime release totals.

        No readiness refresh is persisted here. Consumed WO-attributed material
        remains accounted evidence through the established hybrid/WIP evaluator.
        """
        if not self.bom_no or not self.planned_qty:
            return []
        from calco_erp.calco_production import production_readiness as readiness
        if self.work_order:
            work_order=frappe.get_doc('Work Order',self.work_order)
        else:
            bom=frappe.get_doc('BOM',self.bom_no)
            work_order=frappe._dict(name=None,company=bom.company,bom_no=bom.name,
                qty=self.planned_qty,production_item=bom.item,use_multi_level_bom=1)
        qty,_=readiness._effective_quantity(work_order)
        requirements=readiness._get_bom_requirements(work_order,self.bom_no,qty)
        if not requirements:
            return ['Submitted BOM material requirements could not be resolved.']
        source=(work_order.get('source_warehouse') or '').strip()
        if source.startswith('Rework'):
            # Preserve the existing explicit rework warehouse route; use the
            # same normalized stock-UOM BOM requirements rather than row qty.
            from erpnext.stock.utils import get_stock_balance
            return [f"{item}: required {r['qty']}, available in {source} {get_stock_balance(item,source) or 0}"
                for item,r in requirements.items()
                if float(get_stock_balance(item,source) or 0)<float(r['qty'])]
        transferred=float(work_order.get('material_transferred_for_manufacturing') or 0)
        role='wip' if transferred>=qty else 'hybrid' if transferred>0 else 'source'
        return readiness._evaluate_material(work_order,requirements,role)['blockers']
