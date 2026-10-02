from frappe.model.document import Document

from calco_erp.calco_production.blending_execution import protect_completed_child_evidence


class BlendingRunMaterial(Document):
    def validate(self):
        protect_completed_child_evidence(
            self,
            (
                "sequence",
                "item_code",
                "item_name",
                "batch_no",
                "uom",
                "wip_available_qty",
                "planned_qty",
                "actual_qty",
                "variance_qty",
                "observation",
            ),
        )
