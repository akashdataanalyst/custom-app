from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

from calco_erp.calco_purchase.import_shipment import (
    LC_IMPORT_SHIPMENT_DOCTYPE,
    get_import_shipment_context_from_purchase_order,
)


class LCImportShipment(Document):
    def validate(self):
        self.sync_from_purchase_order()
        self.validate_overseas_supplier()
        self.validate_duplicate_active_record()
        self.validate_dates()
        if not self.status:
            self.status = "Draft"

    def before_submit(self):
        self.sync_from_purchase_order()
        self.validate_overseas_supplier()
        self.validate_duplicate_active_record()
        self.validate_dates()
        self.status = "Completed"

    def on_cancel(self):
        if self.meta.has_field("status"):
            self.db_set("status", "Cancelled", update_modified=False)

    def sync_from_purchase_order(self):
        if not self.purchase_order:
            frappe.throw(_("Purchase Order is required for LC Import Shipment."))

        context = get_import_shipment_context_from_purchase_order(self.purchase_order)
        self.material_request = context.get("material_request") or self.material_request
        self.request_for_quotation = context.get("request_for_quotation") or self.request_for_quotation
        self.supplier_quotation = context.get("supplier_quotation") or self.supplier_quotation
        self.supplier = context.get("supplier") or self.supplier
        self.item_code = context.get("item_code") or self.item_code
        if self.meta.has_field("item_name"):
            self.item_name = context.get("item_name") or self.item_name
        if self.meta.has_field("uom"):
            self.uom = context.get("uom") or self.uom
        self.qty = context.get("qty") or self.qty
        if self.meta.has_field("po_date"):
            self.po_date = context.get("po_date") or self.po_date
        if self.meta.has_field("required_by"):
            self.required_by = context.get("required_by") or self.required_by
        if self.meta.has_field("payment_terms"):
            self.payment_terms = context.get("payment_terms") or self.payment_terms
        if self.meta.has_field("currency"):
            self.currency = context.get("currency") or self.currency
        self.overseas_supplier = cint(context.get("overseas_supplier"))

    def validate_overseas_supplier(self):
        if not cint(self.overseas_supplier):
            frappe.throw(_("LC Import Shipment is applicable only for overseas suppliers."))

    def validate_duplicate_active_record(self):
        existing = frappe.get_all(
            LC_IMPORT_SHIPMENT_DOCTYPE,
            filters={
                "purchase_order": self.purchase_order,
                "docstatus": ("<", 2),
                "name": ("!=", self.name or ""),
            },
            fields=["name", "status"],
            limit_page_length=1,
        )
        if existing:
            frappe.throw(
                _("LC Import Shipment {0} already exists for Purchase Order {1}.").format(
                    existing[0]["name"],
                    self.purchase_order,
                )
            )

    def validate_dates(self):
        if self.etd and self.eta and getdate(self.eta) < getdate(self.etd):
            frappe.throw(_("ETA cannot be earlier than ETD."))
        if cint(self.no_of_containers or 0) <= 0:
            frappe.throw(_("No of Containers must be greater than zero."))
        if cint(self.detention_days or 0) < 0:
            frappe.throw(_("Detention Days cannot be negative."))
