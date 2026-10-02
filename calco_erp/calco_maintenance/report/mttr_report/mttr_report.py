from __future__ import annotations

from frappe import _

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

    ticket_rows = get_ticket_rows(from_date, to_date, machine, order_by="machine_name asc, raised_on asc")
    machine_labels = get_machine_labels(ticket_rows)

    aggregates = {}
    total_hours = 0
    total_count = 0

    for row in ticket_rows:
        duration_hours = get_ticket_duration_hours(row)
        if duration_hours is None:
            continue

        machine_label = get_machine_label(row.machine_name, machine_labels)
        machine_row = aggregates.setdefault(
            machine_label,
            {"machine": machine_label, "ticket_count": 0, "total_repair_hours": 0.0, "average_repair_hours": 0.0},
        )
        machine_row["ticket_count"] += 1
        machine_row["total_repair_hours"] += duration_hours
        total_hours += duration_hours
        total_count += 1

    data = []
    for machine_label in sorted(aggregates):
        row = aggregates[machine_label]
        row["total_repair_hours"] = round(row["total_repair_hours"], 2)
        row["average_repair_hours"] = round(
            row["total_repair_hours"] / row["ticket_count"], 2
        ) if row["ticket_count"] else 0
        data.append(row)

    data.append(
        {
            "machine": _("Overall"),
            "ticket_count": total_count,
            "total_repair_hours": round(total_hours, 2),
            "average_repair_hours": round(total_hours / total_count, 2) if total_count else 0,
        }
    )

    columns = [
        {"label": _("Machine"), "fieldname": "machine", "fieldtype": "Data", "width": 240},
        {"label": _("Tickets with Repair Time"), "fieldname": "ticket_count", "fieldtype": "Int", "width": 160},
        {"label": _("Total Repair Hours"), "fieldname": "total_repair_hours", "fieldtype": "Float", "width": 150},
        {"label": _("Average Repair Hours"), "fieldname": "average_repair_hours", "fieldtype": "Float", "width": 160},
    ]

    return columns, data
