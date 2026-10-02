from __future__ import annotations

from frappe import _
from frappe.utils import cint

from calco_erp.calco_maintenance.analytics import (
    get_machine_label,
    get_machine_labels,
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

    for row in ticket_rows:
        machine_label = get_machine_label(row.machine_name, machine_labels)
        machine_row = aggregates.setdefault(
            machine_label,
            {
                "machine": machine_label,
                "breakdown_count": 0,
                "open_tickets": 0,
                "closed_tickets": 0,
                "overdue_tickets": 0,
            },
        )
        machine_row["breakdown_count"] += 1
        if (row.status or "") == "Closed":
            machine_row["closed_tickets"] += 1
        else:
            machine_row["open_tickets"] += 1
        if cint(row.is_overdue):
            machine_row["overdue_tickets"] += 1

    data = [aggregates[key] for key in sorted(aggregates)]
    data.append(
        {
            "machine": _("Overall"),
            "breakdown_count": sum(row["breakdown_count"] for row in data),
            "open_tickets": sum(row["open_tickets"] for row in data),
            "closed_tickets": sum(row["closed_tickets"] for row in data),
            "overdue_tickets": sum(row["overdue_tickets"] for row in data),
        }
    )

    columns = [
        {"label": _("Machine"), "fieldname": "machine", "fieldtype": "Data", "width": 240},
        {"label": _("Breakdown Count"), "fieldname": "breakdown_count", "fieldtype": "Int", "width": 140},
        {"label": _("Open Tickets"), "fieldname": "open_tickets", "fieldtype": "Int", "width": 120},
        {"label": _("Closed Tickets"), "fieldname": "closed_tickets", "fieldtype": "Int", "width": 120},
        {"label": _("Overdue Tickets"), "fieldname": "overdue_tickets", "fieldtype": "Int", "width": 130},
    ]

    return columns, data
