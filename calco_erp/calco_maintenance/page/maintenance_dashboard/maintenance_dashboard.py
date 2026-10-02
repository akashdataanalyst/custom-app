from __future__ import annotations

from collections import Counter

import frappe
from frappe.utils import cint, formatdate, get_datetime

from calco_erp.calco_maintenance.analytics import (
    build_pm_filters,
    get_machine_failure_gaps,
    get_machine_label,
    get_machine_labels,
    get_pm_doctype,
    get_quality_rows,
    get_ticket_duration_hours,
    get_ticket_rows,
    month_bucket_labels,
    normalize_date_range,
    normalize_machine,
)
from calco_erp.dashboard_utils import (
    get_list_route,
    get_report_route,
    make_card,
    make_chart,
    make_drilldown,
    make_drilldown_row,
)


TOP_MACHINE_LIMIT = 10
RECENT_ROW_LIMIT = 8


@frappe.whitelist()
def get_dashboard_data(
    from_date: str | None = None,
    to_date: str | None = None,
    machine: str | None = None,
) -> dict[str, object]:
    from_date, to_date = normalize_date_range(from_date, to_date)
    machine = normalize_machine(machine)

    ticket_rows = get_ticket_rows(from_date, to_date, machine)
    machine_labels = get_machine_labels(ticket_rows)
    quality_rows = get_quality_rows(from_date, to_date)
    pm_doctype = get_pm_doctype()

    durations = [get_ticket_duration_hours(row) for row in ticket_rows]
    duration_values = [value for value in durations if value is not None]

    mttr_hours = round(sum(duration_values) / len(duration_values), 2) if duration_values else 0
    downtime_hours = round(sum(duration_values), 2) if duration_values else 0
    gap_values = [gap for gaps in get_machine_failure_gaps(ticket_rows).values() for gap in gaps]
    mtbf_hours = round(sum(gap_values) / len(gap_values), 2) if gap_values else 0
    breakdown_count = len(ticket_rows)
    open_tickets = sum(1 for row in ticket_rows if (row.status or "") != "Closed")
    overdue_tickets = sum(1 for row in ticket_rows if cint(row.is_overdue) and (row.status or "") != "Closed")
    qpcr_count = len(quality_rows)
    pm_due_today, pm_overdue, pm_completed_today = get_pm_card_counts(pm_doctype, machine)

    machine_counts = Counter(get_machine_label(row.machine_name, machine_labels) for row in ticket_rows)
    status_counts = Counter((row.status or "Not Set") for row in ticket_rows)
    monthly_counts = build_monthly_counts(ticket_rows, from_date, to_date)

    cards = [
        make_card("MTTR", mttr_hours, suffix=" h", route=get_report_route("MTTR Report")),
        make_card("MTBF", mtbf_hours, suffix=" h", route=get_report_route("MTBF Report")),
        make_card("Breakdown Count", breakdown_count, route=get_report_route("Machine-wise Breakdown Report")),
        make_card("Downtime", downtime_hours, suffix=" h", route=get_report_route("Downtime Report")),
        make_card("Open Tickets", open_tickets, route=get_list_route("Maintenance Ticket")),
        make_card("Overdue Tickets", overdue_tickets, route=get_list_route("Maintenance Ticket")),
        make_card(
            "PM Due Today",
            pm_due_today,
            route=get_list_route(pm_doctype) if pm_doctype else None,
            route_doctype=pm_doctype,
            route_options=build_pm_route_options(pm_doctype, "due_today", machine),
        ),
        make_card(
            "PM Overdue",
            pm_overdue,
            route=get_list_route(pm_doctype) if pm_doctype else None,
            route_doctype=pm_doctype,
            route_options=build_pm_route_options(pm_doctype, "overdue", machine),
        ),
        make_card(
            "PM Completed Today",
            pm_completed_today,
            route=get_list_route(pm_doctype) if pm_doctype else None,
            route_doctype=pm_doctype,
            route_options=build_pm_route_options(pm_doctype, "completed_today", machine),
        ),
        make_card(
            "QPCR / Complaints",
            qpcr_count,
            route=get_list_route("Technical Assistance Ticket")
            if frappe.db.exists("DocType", "Technical Assistance Ticket")
            else None,
        ),
    ]

    charts = [
        make_chart(
            "machine-breakdowns",
            "Breakdown by Machine",
            [label for label, _count in machine_counts.most_common(TOP_MACHINE_LIMIT)],
            [
                {
                    "name": "Tickets",
                    "values": [count for _label, count in machine_counts.most_common(TOP_MACHINE_LIMIT)],
                }
            ],
            colors=["#f97316"],
            route=get_report_route("Machine-wise Breakdown Report"),
        ),
        make_chart(
            "ticket-status",
            "Tickets by Status",
            list(status_counts.keys()),
            [{"name": "Tickets", "values": list(status_counts.values())}],
            chart_type="donut",
            colors=["#2563eb", "#f59e0b", "#7c3aed", "#14b8a6", "#16a34a", "#64748b", "#dc2626"],
        ),
        make_chart(
            "monthly-trend",
            "Monthly Trend",
            list(monthly_counts.keys()),
            [{"name": "Breakdowns", "values": list(monthly_counts.values())}],
            chart_type="line",
            colors=["#0f766e"],
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Recent Maintenance Tickets",
            [
                make_drilldown_row(
                    "Maintenance Ticket",
                    row.name,
                    meta=f"{get_machine_label(row.machine_name, machine_labels)} | {row.status or 'Not Set'} | {format_datetime_value(row.raised_on)}",
                )
                for row in ticket_rows[:RECENT_ROW_LIMIT]
            ],
        ),
        make_drilldown(
            "Open / Overdue Tickets",
            [
                make_drilldown_row(
                    "Maintenance Ticket",
                    row.name,
                    meta=f"{get_machine_label(row.machine_name, machine_labels)} | {row.status or 'Not Set'} | Overdue: {'Yes' if cint(row.is_overdue) else 'No'}",
                )
                for row in ticket_rows
                if (row.status or "") != "Closed" or cint(row.is_overdue)
            ][:RECENT_ROW_LIMIT],
        ),
        make_drilldown(
            "Recent Quality Complaints",
            [
                make_drilldown_row(
                    "Technical Assistance Ticket",
                    row.name,
                    meta=f"{row.customer or 'No Customer'} | {row.status or 'Not Set'} | {formatdate(row.complaint_date)}",
                )
                for row in quality_rows[:RECENT_ROW_LIMIT]
            ],
        ),
    ]

    return {
        "from_date": str(from_date),
        "to_date": str(to_date),
        "machine": machine or "",
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }


def build_monthly_counts(ticket_rows, from_date, to_date) -> dict[str, int]:
    counts = month_bucket_labels(from_date, to_date)

    for row in ticket_rows:
        if not row.raised_on:
            continue
        counts[get_datetime(row.raised_on).strftime("%b %Y")] += 1

    return dict(counts)


def format_datetime_value(value) -> str:
    if not value:
        return ""
    return get_datetime(value).strftime("%d %b %Y %H:%M")


def get_pm_card_counts(pm_doctype: str | None, machine: str | None) -> tuple[int, int, int]:
    if not pm_doctype:
        return 0, 0, 0

    today_date = frappe.utils.getdate(frappe.utils.today())
    active_filters = build_pm_filters(machine)
    completed_filters = {"equipment": machine} if machine else {}

    pm_due_today = frappe.db.count(
        pm_doctype,
        filters={**active_filters, "next_due_date": str(today_date)},
    )
    pm_overdue = frappe.db.count(
        pm_doctype,
        filters={**active_filters, "next_due_date": ("<", str(today_date))},
    )
    pm_completed_today = frappe.db.count(
        pm_doctype,
        filters={**completed_filters, "last_completed_on": str(today_date)},
    )

    return pm_due_today, pm_overdue, pm_completed_today


def build_pm_route_options(pm_doctype: str | None, bucket: str, machine: str | None) -> dict[str, object] | None:
    if not pm_doctype:
        return None

    today_date = frappe.utils.getdate(frappe.utils.today())
    route_options: dict[str, object] = {}

    if bucket == "due_today":
        route_options["next_due_date"] = str(today_date)
    elif bucket == "overdue":
        route_options["next_due_date"] = ["<", str(today_date)]
    elif bucket == "completed_today":
        route_options["last_completed_on"] = str(today_date)
    else:
        return None

    if machine:
        route_options["equipment"] = machine

    if bucket != "completed_today":
        route_options["is_active"] = 1

    return route_options
