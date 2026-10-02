from frappe.model.document import Document

from calco_erp.calco_production.premix_execution import protect_completed_child_evidence


class PremixRunMaterial(Document):
    def validate(self):
        protect_completed_child_evidence(
            self,
            (
                "sequence", "item_code", "item_name", "batch_no", "uom",
                "specification_percent", "planned_qty", "actual_qty",
                "wip_available_qty", "observation",
            ),
        )
