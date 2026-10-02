from frappe.model.document import Document

from calco_erp.calco_production.premix_execution import protect_completed_child_evidence


class PremixRunOnlineChange(Document):
    def validate(self):
        protect_completed_child_evidence(
            self,
            (
                "changed_on", "item_code", "batch_no", "original_percent",
                "revised_percent", "reason", "changed_by",
            ),
        )
