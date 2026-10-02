from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


VALID_SEASONS = {"Low", "Normal", "Peak"}


class RMPlanningParameter(Document):
    def validate(self):
        self.validate_item()
        self.validate_non_negative_fields()
        self.validate_current_season()
        self.normalize_defaults()

    def validate_item(self):
        if not self.item_code:
            frappe.throw(_("Item Code is required."))
        if not frappe.db.exists("Item", self.item_code):
            frappe.throw(_("Item {0} does not exist.").format(self.item_code))

    def validate_non_negative_fields(self):
        checks = {
            "daily_avg_consumption_low": _("Daily Avg Consumption - Low"),
            "daily_avg_consumption_peak": _("Daily Avg Consumption - Peak"),
            "manual_lead_time_days": _("Manual Lead Time Days"),
            "safety_days": _("Safety Days"),
            "review_period_days": _("Review Period Days"),
            "minimum_order_qty": _("Minimum Order Qty"),
            "purchase_pack_size": _("Purchase Pack Size"),
        }
        for fieldname, label in checks.items():
            if flt(self.get(fieldname) or 0) < 0:
                frappe.throw(_("{0} cannot be negative.").format(label))

    def validate_current_season(self):
        season = (self.current_season or "Normal").strip().title()
        if season not in VALID_SEASONS:
            frappe.throw(_("Current Season must be one of: {0}.").format(", ".join(sorted(VALID_SEASONS))))
        self.current_season = season

    def normalize_defaults(self):
        if self.minimum_order_qty and self.purchase_pack_size and flt(self.purchase_pack_size) < 0:
            self.purchase_pack_size = 0
        self.is_active = 1 if cint_bool(self.is_active) else 0


def cint_bool(value) -> int:
    return 1 if str(value).strip() in {"1", "True", "true", "Yes", "yes"} else 0

