from __future__ import annotations

import frappe
from frappe.model.document import Document

from calco_erp.calco_quality.rm_testing_utils import derive_approval_rule, derive_target_value, normalize_text


class RMTestingStandard(Document):
    def validate(self):
        self.is_active = 1 if self.is_active is None else int(bool(self.is_active))

        if self.rm_item:
            self.rm_code = frappe.db.get_value("Item", self.rm_item, "item_code") or self.rm_code
            self.item_group = frappe.db.get_value("Item", self.rm_item, "item_group") or self.item_group

        if self.testing_type:
            parameter_meta = frappe.db.get_value(
                "Quality Inspection Parameter",
                self.testing_type,
                [
                    "custom_unit",
                    "custom_test_standard",
                    "custom_cppl_method",
                    "custom_test_condition",
                ],
                as_dict=True,
            ) or {}
            self.unit = self.unit or parameter_meta.get("custom_unit")
            self.test_standard = self.test_standard or parameter_meta.get("custom_test_standard")
            self.cppl_method = self.cppl_method or parameter_meta.get("custom_cppl_method")
            self.test_condition = self.test_condition or parameter_meta.get("custom_test_condition")

        self.acceptable_min = normalize_text(self.acceptable_min)
        self.acceptable_max = normalize_text(self.acceptable_max)
        self.target_value = normalize_text(self.target_value) or derive_target_value(
            self.acceptable_min,
            self.acceptable_max,
        )
        self.approval_rule = self.approval_rule or derive_approval_rule(
            self.acceptable_min,
            self.acceptable_max,
            self.target_value,
        )

