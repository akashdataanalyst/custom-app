from __future__ import annotations

from frappe import _
from frappe.utils import get_datetime

from calco_erp.calco_maintenance.analytics import (
    get_machine_failure_gaps,
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
    gap_map = get_machine_failure_gaps(ticket_rows)

    breakdown_counts = {}
    last_breakdown = {}
    for row in ticket_rows:
        machine_key = row.machine_name or "Not Set"
        breakdown_counts[machine_key] = breakdown_counts.get(machine_key, 0) + 1
        if row.raised_on:
            last_breakdown[machine_key] = get_datetime(row.raised_on)

    data = []
    all_gaps = []
    total_breakdowns = 0
    total_intervals = 0

    for machine_key in sorted(breakdown_counts):
        gaps = gap_map.get(machine_key, [])
        all_gaps.extend(gaps)
        total_breakdowns += breakdown_counts[machine_key]
        total_intervals += len(gaps)

        data.append(
            {
                "machine": get_machine_label(machine_key, machine_labels),
                "breakdown_count": breakdown_counts[machine_key],
                "interval_count": len(gaps),
                "average_mtbf_hours": round(sum(gaps) / len(gaps), 2) if gaps else 0,
                "last_breakdown": last_breakdown.get(machine_key),
            }
        )

    data.append(
        {
            "machine": _("Overall"),
            "breakdown_count": total_breakdowns,
            "interval_count": total_intervals,
            "average_mtbf_hours": round(sum(all_gaps) / len(all_gaps), 2) if all_gaps else 0,
            "last_breakdown": None,
        }
    )

    columns = [
        {"label": _("Machine"), "fieldname": "machine", "fieldtype": "Data", "width": 240},
        {"label": _("Breakdown Count"), "fieldname": "breakdown_count", "fieldtype": "Int", "width": 140},
        {"label": _("Intervals Considered"), "fieldname": "interval_count", "fieldtype": "Int", "width": 150},
        {"label": _("Average MTBF Hours"), "fieldname": "average_mtbf_hours", "fieldtype": "Float", "width": 160},
        {"label": _("Last Breakdown"), "fieldname": "last_breakdown", "fieldtype": "Datetime", "width": 180},
    ]

    return columns, data
