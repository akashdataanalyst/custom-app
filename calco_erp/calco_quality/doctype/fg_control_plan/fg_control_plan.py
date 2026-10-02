from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from calco_erp.calco_quality.fg_quality_setup import has_positive_fg_control_requirement
from calco_erp.calco_quality.rm_testing_utils import normalize_text, parse_float


class FGControlPlan(Document):
    def validate(self):
        self.size = get_numeric_value(self.size)
        self.frequency = normalize_text(self.frequency)
        from calco_erp.calco_production.in_process_quality import validate_plan_row, STAGE
        validate_plan_row(self)
        if self.get("custom_inspection_scope") == STAGE:
            self.applicable = cint(self.get("applicable"))
        else:
            self.applicable = 1 if has_positive_fg_control_requirement(self.size, self.frequency) else 0
        self.critical_test = cint(self.critical_test)
        self.is_active = 1 if self.is_active is None else cint(self.is_active)
        self.version = normalize_text(self.version) or "1.0"
        self.test_type = normalize_text(self.test_type) or "Manual"

        if self.test_type not in {"Numeric", "Manual"}:
            frappe.throw(_("Test Type must be Numeric or Manual."))

        if self.test_type == "Manual":
            self.minimum_value = None
            self.maximum_value = None

        duplicate_names = frappe.get_all(
            "FG Control Plan",
            filters={
                "fg_item_code": self.fg_item_code,
                "parameter": self.parameter,
                "is_active": 1,
                **({"custom_inspection_scope": self.get("custom_inspection_scope") or "Final QC"} if self.meta.has_field("custom_inspection_scope") else {}),
                "name": ["!=", self.name or ""],
            },
            pluck="name",
            limit_page_length=1,
        )
        if duplicate_names and self.is_active:
            frappe.throw(
                _("An active FG Control Plan already exists for item {0} and parameter {1}.").format(
                    self.fg_item_code,
                    self.parameter,
                )
            )


def get_numeric_value(value):
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    return parse_float(value)
