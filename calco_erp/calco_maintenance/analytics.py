from __future__ import annotations

from collections import defaultdict
from datetime import date

import frappe
from frappe.utils import add_days, flt, get_datetime, getdate, nowdate, time_diff_in_hours


DEFAULT_RANGE_DAYS = 180


def normalize_date_range(from_date: str | None, to_date: str | None) -> tuple[date, date]:
    resolved_to_date = getdate(to_date or nowdate())
    resolved_from_date = getdate(from_date or add_days(resolved_to_date, -(DEFAULT_RANGE_DAYS - 1)))

    if resolved_from_date > resolved_to_date:
        resolved_from_date, resolved_to_date = resolved_to_date, resolved_from_date

    return resolved_from_date, resolved_to_date


def normalize_machine(machine: str | None) -> str | None:
    machine = (machine or "").strip()
    if machine and frappe.db.exists("Maintenance Equipment", machine):
        return machine
    return None


def get_ticket_rows(from_date: date, to_date: date, machine: str | None = None, order_by: str = "raised_on desc"):
    filters = {
        "raised_on": ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"]),
    }
    if machine:
        filters["machine_name"] = machine

    return frappe.get_all(
        "Maintenance Ticket",
        filters=filters,
        fields=[
            "name",
            "machine_name",
            "status",
            "raised_on",
            "actual_start_time",
            "actual_end_time",
            "is_overdue",
        ],
        order_by=order_by,
        limit_page_length=0,
    )


def get_machine_labels(ticket_rows) -> dict[str, str]:
    machine_names = sorted({row.machine_name for row in ticket_rows if row.machine_name})
    if not machine_names:
        return {}

    rows = frappe.get_all(
        "Maintenance Equipment",
        filters={"name": ("in", machine_names)},
        fields=["name", "machine_code", "machine_name", "equipment_name"],
        limit_page_length=len(machine_names),
    )

    labels = {}
    for row in rows:
        labels[row.name] = row.machine_code or row.machine_name or row.equipment_name or row.name
    return labels


def get_machine_label(machine_name: str | None, labels: dict[str, str]) -> str:
    if not machine_name:
        return "Not Set"
    return labels.get(machine_name, machine_name)


def get_ticket_duration_hours(row) -> float | None:
    if not row.actual_start_time or not row.actual_end_time:
        return None

    start_time = get_datetime(row.actual_start_time)
    end_time = get_datetime(row.actual_end_time)
    if end_time <= start_time:
        return 0

    return round(flt(time_diff_in_hours(end_time, start_time)), 2)


def get_machine_failure_gaps(ticket_rows) -> dict[str, list[float]]:
    machine_failures = defaultdict(list)

    for row in ticket_rows:
        if row.machine_name and row.raised_on:
            machine_failures[row.machine_name].append(get_datetime(row.raised_on))

    gaps = {}
    for machine_name, timestamps in machine_failures.items():
        ordered = sorted(timestamps)
        machine_gaps = []
        for previous, current in zip(ordered, ordered[1:]):
            if current > previous:
                machine_gaps.append(round(flt(time_diff_in_hours(current, previous)), 2))
        gaps[machine_name] = machine_gaps

    return gaps


def get_quality_rows(from_date: date, to_date: date):
    if not frappe.db.exists("DocType", "Technical Assistance Ticket"):
        return []

    return frappe.get_all(
        "Technical Assistance Ticket",
        filters={"complaint_date": ("between", [str(from_date), str(to_date)])},
        fields=["name", "customer", "status", "complaint_date", "fg_batch_no"],
        order_by="complaint_date desc, modified desc",
        limit_page_length=0,
    )


def get_pm_doctype() -> str | None:
    if frappe.db.exists("DocType", "Preventive Maintenance Plan"):
        return "Preventive Maintenance Plan"
    return None


def build_pm_filters(machine: str | None = None) -> dict[str, object]:
    filters: dict[str, object] = {"is_active": 1}
    if machine:
        filters["equipment"] = machine
    return filters


def month_bucket_labels(from_date: date, to_date: date) -> dict[str, int]:
    counts = defaultdict(int)

    cursor = date(from_date.year, from_date.month, 1)
    limit = date(to_date.year, to_date.month, 1)
    while cursor <= limit:
        counts[cursor.strftime("%b %Y")] = 0
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    return counts
