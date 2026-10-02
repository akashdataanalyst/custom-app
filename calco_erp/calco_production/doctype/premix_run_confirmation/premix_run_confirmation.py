from frappe.model.document import Document

from calco_erp.calco_production.premix_execution import protect_completed_child_evidence


class PremixRunConfirmation(Document):
    def validate(self):
        protect_completed_child_evidence(
            self,
            (
                "confirmation_key", "confirmation", "confirmed", "observation",
                "checked_by", "checked_on",
            ),
        )
