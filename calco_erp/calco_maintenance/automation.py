from __future__ import annotations
import re
from collections import defaultdict

import frappe
import requests
from frappe import _
from frappe.utils import cstr, formatdate, get_datetime, get_url, getdate, now_datetime, nowdate

from calco_erp.calco_maintenance.pm_schedule_sync import refresh_pm_schedule_tracking


OPEN_TICKET_STATUSES = [
    "Open",
    "Inspection",
    "Spare Required",
    "Spare Available",
    "In Progress",
    "Completed",
]


def run_daily_maintenance_automation():
    refresh_pm_schedule_tracking()
    created = create_due_pm_tickets()
    email_result = send_daily_summary_email()
    whatsapp_result = send_daily_whatsapp_summary()
    update_automation_run_timestamp()
    return {
        "created_tickets": created,
        "email_result": email_result,
        "whatsapp_result": whatsapp_result,
    }


@frappe.whitelist()
def run_due_pm_ticket_generation_now():
    return {
        "created_tickets": create_due_pm_tickets(),
    }


@frappe.whitelist()
def send_daily_summary_now():
    return send_daily_summary_email(force=True)


@frappe.whitelist()
def get_whatsapp_summary_preview():
    summary = build_daily_summary()
    return {
        "message": build_whatsapp_summary_message(summary),
        "settings": get_whatsapp_config_snapshot(),
    }


def create_due_pm_tickets(run_date=None) -> list[str]:
    settings = get_automation_settings()
    if settings and not settings.auto_create_pm_tickets:
        return []

    due_date = getdate(run_date or nowdate())
    created_tickets = []

    for plan_name in get_due_pm_plan_names(due_date):
        result = create_or_get_pm_ticket(plan_name, due_date=due_date)
        if result["created"]:
            created_tickets.append(result["ticket"])

    return created_tickets


def get_due_pm_plan_names(due_date) -> list[str]:
    if not frappe.db.exists("DocType", "Preventive Maintenance Plan"):
        return []

    rows = frappe.get_all(
        "Preventive Maintenance Plan",
        filters={
            "is_active": 1,
            "next_due_date": ("<=", str(getdate(due_date))),
        },
        fields=["name"],
        order_by="next_due_date asc, modified asc",
        limit_page_length=0,
    )
    return [row.name for row in rows]


def create_or_get_pm_ticket(plan_name: str, due_date=None, ignore_permissions: bool = True) -> dict[str, object]:
    plan = frappe.get_doc("Preventive Maintenance Plan", plan_name)
    existing_ticket = get_open_ticket_for_pm_plan(plan.name)
    if existing_ticket:
        return {"created": False, "ticket": existing_ticket}

    settings = get_automation_settings()
    equipment = frappe.get_doc("Maintenance Equipment", plan.equipment)
    raised_by = frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Administrator"
    ticket_owner = plan.responsible_person or (settings.default_ticket_owner if settings else None) or "Administrator"
    priority = resolve_pm_ticket_priority(plan, settings)
    summary = build_pm_activity_summary(plan)

    ticket = frappe.get_doc(
        {
            "doctype": "Maintenance Ticket",
            "raised_on": now_datetime(),
            "raised_by": raised_by,
            "location": equipment.location or "Maintenance",
            "equipment": equipment.name,
            "machine_name": equipment.name,
            "pm_plan": plan.name,
            "maintenance_type": "Preventive",
            "problem_description": summary,
            "priority": priority,
            "status": "Open",
            "ticket_owner": ticket_owner,
            "responsible_role": cstr(plan.responsible_role),
        }
    )
    ticket.insert(ignore_permissions=ignore_permissions)

    frappe.db.set_value(
        "Preventive Maintenance Plan",
        plan.name,
        "last_generated_ticket",
        ticket.name,
        update_modified=False,
    )
    frappe.db.commit()

    return {"created": True, "ticket": ticket.name}


def get_open_ticket_for_pm_plan(plan_name: str) -> str | None:
    rows = frappe.get_all(
        "Maintenance Ticket",
        filters={
            "pm_plan": plan_name,
            "status": ("in", OPEN_TICKET_STATUSES),
        },
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return rows[0].name if rows else None


def resolve_pm_ticket_priority(plan, settings) -> str:
    if cstr(plan.ticket_priority) in {"Red", "Yellow", "Green"}:
        return plan.ticket_priority

    if plan.next_due_date:
        next_due_date = getdate(plan.next_due_date)
        today_date = getdate(nowdate())
        if next_due_date < today_date:
            return "Red"
        if next_due_date == today_date:
            return "Yellow"

    if settings and cstr(settings.default_pm_ticket_priority) in {"Red", "Yellow", "Green"}:
        return settings.default_pm_ticket_priority

    return "Green"


def build_pm_activity_summary(plan) -> str:
    lines = [
        _("Preventive Maintenance"),
        _("Frequency: {0}").format(cstr(plan.frequency) or _("Not Set")),
        _("Checklist: {0}").format(cstr(plan.activity_checklist) or _("Not Set")),
    ]

    if plan.method:
        lines.append(_("Method: {0}").format(cstr(plan.method)))

    if plan.criteria:
        lines.append(_("Criteria: {0}").format(cstr(plan.criteria)))

    if plan.next_due_date:
        lines.append(_("PM Due Date: {0}").format(formatdate(plan.next_due_date)))

    if plan.responsible_role:
        lines.append(_("Responsible Role: {0}").format(cstr(plan.responsible_role)))

    return "\n".join(lines)


def sync_pm_plan_completion_from_ticket(doc, method=None):
    if not doc.pm_plan or doc.status not in {"Completed", "Closed"}:
        return

    if not frappe.db.exists("Preventive Maintenance Plan", doc.pm_plan):
        return

    completion_source = doc.actual_end_time or now_datetime()
    completion_date = getdate(get_datetime(completion_source))

    plan = frappe.get_doc("Preventive Maintenance Plan", doc.pm_plan)
    current_completed_on = getdate(plan.last_completed_on) if plan.last_completed_on else None

    if current_completed_on == completion_date and plan.last_generated_ticket == doc.name:
        return

    plan.last_completed_on = completion_date
    plan.last_generated_ticket = doc.name
    plan.save(ignore_permissions=True)


def send_daily_summary_email(force: bool = False) -> dict[str, object]:
    settings = get_automation_settings()
    if not settings or not settings.send_daily_summary_email:
        return {"status": "skipped", "reason": "disabled"}

    recipients = parse_recipients(settings.summary_email_recipients)
    if not recipients:
        return {"status": "skipped", "reason": "no_recipients"}

    today_date = getdate(nowdate())
    if not force and settings.last_daily_summary_sent_on and getdate(settings.last_daily_summary_sent_on) == today_date:
        return {"status": "skipped", "reason": "already_sent"}

    summary = build_daily_summary(today_date)
    subject = _("[Maintenance] Daily Summary - {0}").format(formatdate(today_date))
    message = build_daily_summary_email(summary)

    frappe.sendmail(
        recipients=recipients,
        subject=subject,
        message=message,
        delayed=False,
    )

    settings.last_daily_summary_sent_on = today_date
    settings.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "sent",
        "recipients": recipients,
        "subject": subject,
    }


def build_daily_summary(report_date=None) -> dict[str, object]:
    report_date = getdate(report_date or nowdate())

    pm_due_today = len(
        frappe.get_all(
            "Preventive Maintenance Plan",
            filters={"is_active": 1, "next_due_date": str(report_date)},
            pluck="name",
            limit_page_length=0,
        )
    )
    pm_overdue = len(
        frappe.get_all(
            "Preventive Maintenance Plan",
            filters={"is_active": 1, "next_due_date": ("<", str(report_date))},
            pluck="name",
            limit_page_length=0,
        )
    )
    open_tickets = len(
        frappe.get_all(
            "Maintenance Ticket",
            filters={"status": ("!=", "Closed")},
            pluck="name",
            limit_page_length=0,
        )
    )
    overdue_tickets = len(
        frappe.get_all(
            "Maintenance Ticket",
            filters={"status": ("!=", "Closed"), "is_overdue": 1},
            pluck="name",
            limit_page_length=0,
        )
    )

    overdue_plans = frappe.get_all(
        "Preventive Maintenance Plan",
        filters={"is_active": 1, "next_due_date": ("<", str(report_date))},
        fields=["equipment", "equipment_code", "equipment_name", "next_due_date"],
        order_by="next_due_date asc, equipment_code asc",
        limit_page_length=0,
    )

    overdue_by_machine = defaultdict(lambda: {"count": 0, "oldest_due_date": None, "label": ""})
    for row in overdue_plans:
        machine_key = row.equipment or row.equipment_code or row.equipment_name or _("Unknown")
        bucket = overdue_by_machine[machine_key]
        bucket["count"] += 1
        bucket["label"] = row.equipment_code or row.equipment_name or machine_key
        row_due_date = getdate(row.next_due_date) if row.next_due_date else None
        if row_due_date and (bucket["oldest_due_date"] is None or row_due_date < bucket["oldest_due_date"]):
            bucket["oldest_due_date"] = row_due_date

    overdue_machine_rows = sorted(
        overdue_by_machine.values(),
        key=lambda row: (row["oldest_due_date"] or report_date, row["label"]),
    )

    return {
        "report_date": report_date,
        "pm_due_today": pm_due_today,
        "pm_overdue": pm_overdue,
        "open_tickets": open_tickets,
        "overdue_tickets": overdue_tickets,
        "overdue_machine_rows": overdue_machine_rows,
    }


def build_daily_summary_email(summary: dict[str, object]) -> str:
    overdue_rows = summary.get("overdue_machine_rows") or []
    overdue_html = ""

    if overdue_rows:
        overdue_html = "".join(
            f"<li><b>{frappe.utils.escape_html(cstr(row['label']))}</b>: {row['count']} overdue PM task(s)"
            f"{' | Oldest due ' + formatdate(row['oldest_due_date']) if row['oldest_due_date'] else ''}</li>"
            for row in overdue_rows[:20]
        )
    else:
        overdue_html = "<li>No overdue PM records.</li>"

    return f"""
        <p>Daily maintenance summary for <b>{formatdate(summary['report_date'])}</b>.</p>
        <ul>
            <li><b>PM Due Today:</b> {summary['pm_due_today']}</li>
            <li><b>PM Overdue:</b> {summary['pm_overdue']}</li>
            <li><b>Open Maintenance Tickets:</b> {summary['open_tickets']}</li>
            <li><b>Overdue Tickets:</b> {summary['overdue_tickets']}</li>
        </ul>
        <p><b>Machine-wise Overdue PM Summary</b></p>
        <ul>{overdue_html}</ul>
        <p>Open the system here: <a href="{get_url('/app/maintenance')}">{get_url('/app/maintenance')}</a></p>
    """


def build_whatsapp_summary_message(summary: dict[str, object]) -> str:
    lines = [
        f"Maintenance summary for {formatdate(summary['report_date'])}",
        f"PM Due Today: {summary['pm_due_today']}",
        f"PM Overdue: {summary['pm_overdue']}",
        f"Open Maintenance Tickets: {summary['open_tickets']}",
        f"Overdue Tickets: {summary['overdue_tickets']}",
    ]

    overdue_rows = summary.get("overdue_machine_rows") or []
    if overdue_rows:
        lines.append("Overdue PM by Machine:")
        for row in overdue_rows[:10]:
            oldest_due = f" (oldest {formatdate(row['oldest_due_date'])})" if row["oldest_due_date"] else ""
            lines.append(f"- {row['label']}: {row['count']}{oldest_due}")

    return "\n".join(lines)


def send_daily_whatsapp_summary(force: bool = False) -> dict[str, object]:
    settings = get_automation_settings()
    if not settings or not settings.enable_whatsapp_alerts:
        return {"status": "prepared_only", "reason": "disabled"}

    if not settings.whatsapp_webhook_url or not settings.whatsapp_provider:
        return {"status": "prepared_only", "reason": "provider_not_configured"}

    recipients = parse_recipients(settings.whatsapp_recipients)
    if not recipients:
        return {"status": "prepared_only", "reason": "no_recipients"}

    today_date = getdate(nowdate())
    if not force and settings.last_whatsapp_summary_sent_on and getdate(settings.last_whatsapp_summary_sent_on) == today_date:
        return {"status": "prepared_only", "reason": "already_sent"}

    summary = build_daily_summary(today_date)
    payload = {
        "provider": settings.whatsapp_provider,
        "recipients": recipients,
        "message": build_whatsapp_summary_message(summary),
    }
    headers = {"Content-Type": "application/json"}
    if settings.whatsapp_auth_token:
        headers["Authorization"] = f"Bearer {settings.whatsapp_auth_token}"

    response = requests.post(
        settings.whatsapp_webhook_url,
        json=payload,
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()

    settings.last_whatsapp_summary_sent_on = today_date
    settings.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "sent",
        "provider": settings.whatsapp_provider,
        "recipients": recipients,
    }


def update_automation_run_timestamp():
    settings = get_automation_settings()
    if not settings:
        return
    settings.last_automation_run_on = now_datetime()
    settings.save(ignore_permissions=True)


def get_automation_settings():
    if not frappe.db.exists("DocType", "Maintenance Automation Settings"):
        return None
    return frappe.get_single("Maintenance Automation Settings")


def parse_recipients(value: str | None) -> list[str]:
    tokens = re.split(r"[\n,;]+", cstr(value))
    return [token.strip() for token in tokens if token and token.strip()]


def get_whatsapp_config_snapshot() -> dict[str, object]:
    settings = get_automation_settings()
    if not settings:
        return {}
    return {
        "enabled": bool(settings.enable_whatsapp_alerts),
        "provider": cstr(settings.whatsapp_provider),
        "webhook_configured": bool(settings.whatsapp_webhook_url),
        "recipients": parse_recipients(settings.whatsapp_recipients),
    }
