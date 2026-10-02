from __future__ import annotations

from datetime import timedelta

from frappe.utils import flt

from calco_erp.calco_quality.page.quality_dashboard.quality_dashboard import (
    get_daily_rm_counts,
    resolve_date_range,
)


def execute(filters=None):
    filters = filters or {}
    from_date, to_date = resolve_date_range(
        filters.get("report_date"),
        filters.get("from_date"),
        filters.get("to_date"),
    )
    supplier = filters.get("supplier")
    item_code = filters.get("item_code")

    rejected_map = get_daily_rm_counts("Rejected", from_date, to_date, supplier=supplier, item_code=item_code)
    inspected_map = get_daily_rm_counts(
        ["Accepted", "Hold", "Rejected"],
        from_date,
        to_date,
        supplier=supplier,
        item_code=item_code,
    )

    columns = [
        {"label": "Date", "fieldname": "report_date", "fieldtype": "Date", "width": 120},
        {"label": "Inspected RM", "fieldname": "inspected_count", "fieldtype": "Int", "width": 140},
        {"label": "Rejected RM", "fieldname": "rejected_count", "fieldtype": "Int", "width": 140},
        {"label": "RM Rejection %", "fieldname": "rejection_percentage", "fieldtype": "Percent", "width": 140},
    ]

    data = []
    current_date = from_date
    while current_date <= to_date:
        key = str(current_date)
        rejected = flt(rejected_map.get(key))
        inspected = flt(inspected_map.get(key))
        data.append(
            {
                "report_date": current_date,
                "inspected_count": inspected,
                "rejected_count": rejected,
                "rejection_percentage": round((rejected * 100.0 / inspected), 2) if inspected else 0,
            }
        )
        current_date = current_date + timedelta(days=1)

    chart = {
        "data": {
            "labels": [row["report_date"].strftime("%d %b") for row in data],
            "datasets": [{"name": "Rejection %", "values": [row["rejection_percentage"] for row in data]}],
        },
        "type": "line",
        "colors": ["#dc2626"],
    }

    return columns, data, None, chart
