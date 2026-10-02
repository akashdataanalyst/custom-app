from __future__ import annotations

from frappe import _
from frappe.utils import get_datetime

from calco_erp.calco_maintenance.analytics import (
    get_machine_label,
    get_machine_labels,
    get_ticket_duration_hours,
    get_ticket_rows,
    normalize_date_range,
    normalize_machine,
)


def execute(filters=None):
    filters = filters or {}
    from_date, to_date = normalize_date_range(filters.get("from_date"), filters.get("to_date"))
    machine = normalize_machine(filters.get("machine"))
    group_by = (filters.get("group_by") or "Machine").strip()

    ticket_rows = get_ticket_rows(from_date, to_date, machine, order_by="actual_start_time asc, raised_on asc")
    machine_labels = get_machine_labels(ticket_rows)
    aggregates = {}

    for row in ticket_rows:
        duration_hours = get_ticket_duration_hours(row)
        if duration_hours is None:
            continue

        key = get_group_key(group_by, row, machine_labels)
        group_row = aggregates.setdefault(
            key,
            {"group_label": key, "ticket_count": 0, "downtime_hours": 0.0, "average_downtime_hours": 0.0},
        )
        group_row["ticket_count"] += 1
        group_row["downtime_hours"] += duration_hours

    data = []
    total_hours = 0
    total_count = 0

    for key in sorted(aggregates):
        row = aggregates[key]
        row["downtime_hours"] = round(row["downtime_hours"], 2)
        row["average_downtime_hours"] = round(
            row["downtime_hours"] / row["ticket_count"], 2
        ) if row["ticket_count"] else 0
        data.append(row)
        total_hours += row["downtime_hours"]
        total_count += row["ticket_count"]

    data.append(
        {
            "group_label": _("Overall"),
            "ticket_count": total_count,
            "downtime_hours": round(total_hours, 2),
            "average_downtime_hours": round(total_hours / total_count, 2) if total_count else 0,
        }
    )

    columns = [
        {"label": _(group_by), "fieldname": "group_label", "fieldtype": "Data", "width": 240},
        {"label": _("Tickets"), "fieldname": "ticket_count", "fieldtype": "Int", "width": 120},
        {"label": _("Downtime Hours"), "fieldname": "downtime_hours", "fieldtype": "Float", "width": 150},
        {"label": _("Average Downtime Hours"), "fieldname": "average_downtime_hours", "fieldtype": "Float", "width": 170},
    ]

    return columns, data


def get_group_key(group_by, row, machine_labels):
    if group_by == "Date":
        base_value = row.actual_start_time or row.raised_on
        return get_datetime(base_value).strftime("%Y-%m-%d") if base_value else "Not Set"

    if group_by == "Month":
        base_value = row.actual_start_time or row.raised_on
        return get_datetime(base_value).strftime("%b %Y") if base_value else "Not Set"

    return get_machine_label(row.machine_name, machine_labels)
