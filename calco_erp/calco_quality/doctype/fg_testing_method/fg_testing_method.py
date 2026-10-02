from __future__ import annotations

from frappe.model.document import Document

from calco_erp.calco_quality.rm_testing_utils import normalize_text


class FGTestingMethod(Document):
    def validate(self):
        self.method_name = normalize_text(self.method_name)
        self.description = normalize_text(self.description)
