from __future__ import annotations

from collections.abc import Iterable

import frappe
from erpnext.manufacturing.report.quality_inspection_summary import (
    quality_inspection_summary as erpnext_quality_inspection_summary,
)


SUPPORTED_STATUSES = ("Rejected", "Review Required", "Accepted", "Cancelled")
REPORT_FIELDS = (
    "name",
    "status",
    "report_date",
    "item_code",
    "item_name",
    "sample_size",
    "inspection_type",
    "reference_type",
    "reference_name",
    "inspected_by",
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    data = get_data(filters)
    columns = erpnext_quality_inspection_summary.get_columns(filters)
    chart = get_chart_data(data)
    return columns, data, None, chart


def get_data(filters):
    data = list(erpnext_quality_inspection_summary.get_data(filters))
    data.extend(get_cancelled_data(filters))
    return sorted(data, key=lambda row: (row.get("report_date") or "", row.get("name") or ""))


def get_cancelled_data(filters):
    selected_statuses = normalize_selected_statuses(filters.get("status"))
    if selected_statuses and "Cancelled" not in selected_statuses:
        return []

    query_filters = {
        "docstatus": 2,
        "report_date": ["between", [filters.get("from_date"), filters.get("to_date")]],
    }
    for fieldname in ("item_code", "inspected_by"):
        if filters.get(fieldname):
            query_filters[fieldname] = ("in", filters.get(fieldname))

    rows = frappe.get_all(
        "Quality Inspection",
        fields=list(REPORT_FIELDS),
        filters=query_filters,
        order_by="report_date asc",
    )
    for row in rows:
        row.status = "Cancelled"
    return rows


def get_chart_data(data):
    status_counts = {status: 0 for status in SUPPORTED_STATUSES}
    unexpected_count = 0

    for row in data:
        status = (row.get("status") or "").strip()
        if status in status_counts:
            status_counts[status] += 1
        else:
            unexpected_count += 1

    labels = list(SUPPORTED_STATUSES)
    values = [status_counts[status] for status in labels]
    if unexpected_count:
        labels.append("Unexpected Status")
        values.append(unexpected_count)

    return {
        "data": {
            "labels": [frappe._(label) for label in labels],
            "datasets": [{"name": frappe._("Qty Wise Chart"), "values": values}],
        },
        "type": "donut",
        "height": 300,
    }


def normalize_selected_statuses(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Iterable):
        return {status for status in value if status}
    return {str(value)}
