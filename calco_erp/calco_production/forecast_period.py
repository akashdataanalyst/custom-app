"""Server-signed forecast periods; legacy submitted dates retain their old meaning."""
import hashlib
import hmac
import json
from collections import defaultdict

import frappe
from frappe.utils import add_to_date, cint, getdate, get_first_day

PERIOD = "custom_forecast_period_start"
TOKEN = "custom_forecast_period_token"
LEGACY_NOTICE = "Legacy forecast period missing: Delivery Date retained as compatibility authority; review intended period before replacement."


def period_sql(alias="sfi"):
    return f"coalesce({alias}.{PERIOD}, {alias}.delivery_date)"


def effective_period(row):
    return row.get(PERIOD) or row.get("delivery_date")


def period_start(doc, index):
    if not doc.from_date or doc.frequency not in ("Monthly", "Weekly") or cint(doc.demand_number) < 1:
        frappe.throw("Set From Date, Weekly/Monthly frequency and a positive period count before generating demand.")
    if index < 0 or index >= cint(doc.demand_number):
        frappe.throw("Forecast period sequence is outside the configured horizon.")
    start = get_first_day(doc.from_date) if doc.frequency == "Monthly" else getdate(doc.from_date)
    return getdate(add_to_date(start, **({"months": index} if doc.frequency == "Monthly" else {"weeks": index})))


def signature(doc, row, index):
    payload = json.dumps(["forecast-period-v1", doc.company, str(getdate(doc.from_date)), doc.frequency,
                          cint(doc.demand_number), row.item_code, index, str(period_start(doc, index))], separators=(",", ":"))
    return hmac.new(frappe.local.conf.encryption_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def assign_period(doc, row, index):
    row.set(PERIOD, period_start(doc, index))
    row.set(TOKEN, f"{index}:{signature(doc, row, index)}")


class ForecastPeriodMixin:
    @frappe.whitelist()
    def generate_demand(self):
        if self.docstatus != 0:
            frappe.throw("Generate Demand is allowed only on Draft Sales Forecasts.")
        self.check_permission("create" if self.is_new() else "write")
        period_start(self, 0)
        super().generate_demand()
        # The selector reuses Sales Forecast Item, whose demand_qty is mandatory.
        # Selector quantities are not consumed by planning (parentfield must be items).
        for selection in self.selected_items:
            if not selection.demand_qty:
                selection.demand_qty = 1
        indices = defaultdict(int)
        for row in self.items:
            index = indices[row.item_code]
            assign_period(self, row, index)
            indices[row.item_code] += 1
        # Response document is synchronized by frm.call; normal Save remains authoritative.


def validate_periods(doc, method=None):
    old = doc.get_doc_before_save()
    old_rows = {r.name: r for r in old.items} if old else {}
    seen = set()
    for row in doc.items:
        previous = old_rows.get(row.name)
        if previous and old.docstatus == 1:
            if row.get(PERIOD) != previous.get(PERIOD) or row.get(TOKEN) != previous.get(TOKEN):
                frappe.throw("Submitted forecast period identity is immutable.")
            continue
        if not row.get(PERIOD) or not row.get(TOKEN):
            if previous and not previous.get(PERIOD) and not previous.get(TOKEN) and doc.docstatus == 0:
                continue  # Existing draft may be saved, but cannot be submitted without regeneration.
            frappe.throw("Generate Demand or import the forecast to establish server-controlled forecast periods before saving/submitting.")
        try:
            index_text, digest = row.get(TOKEN).split(":", 1)
            index = int(index_text)
            valid = hmac.compare_digest(digest, signature(doc, row, index)) and getdate(row.get(PERIOD)) == period_start(doc, index)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            frappe.throw("Forecast period context changed or is invalid. Regenerate demand or import again.")
        identity = (row.item_code, str(getdate(row.get(PERIOD))))
        if identity in seen:
            frappe.throw("Duplicate item and forecast period. Combine the demand quantity into one row.")
        seen.add(identity)


def setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({"Sales Forecast Item": [
        {"fieldname": PERIOD, "label": "Forecast Period Start", "fieldtype": "Date", "insert_after": "delivery_date", "read_only": 1, "in_list_view": 1},
        {"fieldname": TOKEN, "label": "Forecast Period Authority", "fieldtype": "Small Text", "insert_after": PERIOD, "read_only": 1, "hidden": 1},
    ]}, update=True)
