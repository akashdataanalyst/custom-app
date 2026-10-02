import frappe
from frappe.model.document import Document


class RMInwardValidation(Document):
    def validate(self):
        if not self.status:
            self.status = "Pending QC"

        required_fields = ["purchase_receipt", "item_code", "batch_no", "received_qty"]
        missing = [field for field in required_fields if not self.get(field)]
        if missing:
            frappe.throw("Purchase Receipt, Item Code, Batch No, and Received Qty are mandatory.")


def create_from_purchase_receipt(doc, method=None):
    if not frappe.db.exists("DocType", "RM Inward Validation"):
        return

    for row in doc.get("items", []):
        if not row.get("item_code") or not row.get("batch_no"):
            continue

        if frappe.db.exists(
            "RM Inward Validation",
            {
                "purchase_receipt": doc.name,
                "purchase_receipt_item": row.name,
                "item_code": row.item_code,
                "batch_no": row.batch_no,
            },
        ):
            continue

        inward = frappe.get_doc(
            {
                "doctype": "RM Inward Validation",
                "purchase_receipt": doc.name,
                "purchase_receipt_item": row.name,
                "supplier": doc.supplier,
                "item_code": row.item_code,
                "item_name": row.item_name,
                "batch_no": row.batch_no,
                "received_qty": row.received_qty or row.qty,
                "warehouse": row.warehouse,
                "status": "Pending QC",
            }
        )
        inward.insert(ignore_permissions=True)
