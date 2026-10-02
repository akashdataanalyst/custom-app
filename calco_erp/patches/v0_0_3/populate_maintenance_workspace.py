import json

import frappe


MODULE = "Calco Maintenance"
WORKSPACE = "Maintenance"

NUMBER_CARDS = [
    {"label": "Open Tickets", "document_type": "Maintenance Ticket", "filters": [["Maintenance Ticket", "status", "=", "Open"]]},
    {
        "label": "Inspection Pending",
        "document_type": "Maintenance Ticket",
        "filters": [["Maintenance Ticket", "status", "=", "Inspection"]],
    },
    {
        "label": "Spare Pending",
        "document_type": "Maintenance Ticket",
        "filters": [["Maintenance Ticket", "status", "in", ["Spare Required", "Spare Available"]]],
    },
    {"label": "In Progress", "document_type": "Maintenance Ticket", "filters": [["Maintenance Ticket", "status", "=", "In Progress"]]},
    {"label": "Completed", "document_type": "Maintenance Ticket", "filters": [["Maintenance Ticket", "status", "=", "Completed"]]},
    {"label": "Overdue Tickets", "document_type": "Maintenance Ticket", "filters": [["Maintenance Ticket", "is_overdue", "=", 1]]},
    {
        "label": "PM Due Today",
        "document_type": "Preventive Maintenance Plan",
        "filters": [["Preventive Maintenance Plan", "is_active", "=", 1]],
        "dynamic_filters": [["Preventive Maintenance Plan", "next_due_date", "=", "frappe.datetime.get_today()"]],
    },
    {
        "label": "PM Overdue",
        "document_type": "Preventive Maintenance Plan",
        "filters": [["Preventive Maintenance Plan", "is_active", "=", 1]],
        "dynamic_filters": [["Preventive Maintenance Plan", "next_due_date", "<", "frappe.datetime.get_today()"]],
    },
    {
        "label": "PM Completed Today",
        "document_type": "Preventive Maintenance Plan",
        "filters": [],
        "dynamic_filters": [["Preventive Maintenance Plan", "last_completed_on", "=", "frappe.datetime.get_today()"]],
    },
]


def build_workspace_content():
    return [
        {
            "id": "maintenance-quick-access-header",
            "type": "header",
            "data": {"text": '<span class="h4"><b>Quick Access</b></span>', "col": 12},
        },
        {"id": "maintenance-ticket-shortcut", "type": "shortcut", "data": {"shortcut_name": "Maintenance Ticket", "col": 3}},
        {"id": "maintenance-inspection-shortcut", "type": "shortcut", "data": {"shortcut_name": "Maintenance Inspection", "col": 3}},
        {"id": "maintenance-job-card-shortcut", "type": "shortcut", "data": {"shortcut_name": "Maintenance Job Card", "col": 3}},
        {"id": "material-request-shortcut", "type": "shortcut", "data": {"shortcut_name": "Material Request", "col": 3}},
        {"id": "qpcr-shortcut", "type": "shortcut", "data": {"shortcut_name": "QPCR", "col": 3}},
        {"id": "maintenance-spacer-1", "type": "spacer", "data": {"col": 12}},
        {
            "id": "maintenance-status-header",
            "type": "header",
            "data": {"text": '<span class="h4"><b>Breakdown Status Overview</b></span>', "col": 12},
        },
        {"id": "open-tickets-card", "type": "number_card", "data": {"number_card_name": "Open Tickets", "col": 4}},
        {"id": "inspection-pending-card", "type": "number_card", "data": {"number_card_name": "Inspection Pending", "col": 4}},
        {"id": "spare-pending-card", "type": "number_card", "data": {"number_card_name": "Spare Pending", "col": 4}},
        {"id": "in-progress-card", "type": "number_card", "data": {"number_card_name": "In Progress", "col": 4}},
        {"id": "completed-card", "type": "number_card", "data": {"number_card_name": "Completed", "col": 4}},
        {"id": "overdue-tickets-card", "type": "number_card", "data": {"number_card_name": "Overdue Tickets", "col": 4}},
        {"id": "maintenance-spacer-2", "type": "spacer", "data": {"col": 12}},
        {
            "id": "maintenance-pm-header",
            "type": "header",
            "data": {"text": '<span class="h4"><b>Preventive Maintenance</b></span>', "col": 12},
        },
        {"id": "pm-due-today-card", "type": "number_card", "data": {"number_card_name": "PM Due Today", "col": 4}},
        {"id": "pm-overdue-card", "type": "number_card", "data": {"number_card_name": "PM Overdue", "col": 4}},
        {"id": "pm-completed-today-card", "type": "number_card", "data": {"number_card_name": "PM Completed Today", "col": 4}},
        {"id": "maintenance-spacer-3", "type": "spacer", "data": {"col": 12}},
        {
            "id": "maintenance-views-header",
            "type": "header",
            "data": {"text": '<span class="h4"><b>Useful Views</b></span>', "col": 12},
        },
        {
            "id": "machine-wise-breakdown-shortcut",
            "type": "shortcut",
            "data": {"shortcut_name": "Machine-wise Breakdown", "col": 4},
        },
        {"id": "ticket-aging-shortcut", "type": "shortcut", "data": {"shortcut_name": "Ticket Aging", "col": 4}},
        {"id": "downtime-report-shortcut", "type": "shortcut", "data": {"shortcut_name": "Downtime Report", "col": 4}},
        {"id": "mttr-shortcut", "type": "shortcut", "data": {"shortcut_name": "MTTR", "col": 4}},
        {"id": "mtbf-shortcut", "type": "shortcut", "data": {"shortcut_name": "MTBF", "col": 4}},
    ]


def execute():
    ensure_number_cards()
    ensure_workspace_layout()
    ensure_workspace_visibility()
    frappe.clear_cache(doctype="Number Card")
    frappe.clear_cache(doctype="Workspace")


def ensure_number_cards():
    for card in NUMBER_CARDS:
        if frappe.db.exists("Number Card", card["label"]):
            doc = frappe.get_doc("Number Card", card["label"])
        else:
            doc = frappe.new_doc("Number Card")
            doc.label = card["label"]

        doc.module = MODULE
        doc.type = "Document Type"
        doc.document_type = card["document_type"]
        doc.function = "Count"
        doc.is_public = 1
        doc.show_full_number = 1
        doc.filters_json = json.dumps(card["filters"])
        doc.dynamic_filters_json = json.dumps(card.get("dynamic_filters") or [])
        doc.save(ignore_permissions=True)


def ensure_workspace_layout():
    if not frappe.db.exists("Workspace", WORKSPACE):
        return

    workspace = frappe.get_doc("Workspace", WORKSPACE)
    workspace.content = json.dumps(build_workspace_content())
    workspace.set(
        "number_cards",
        [{"label": card["label"], "number_card_name": card["label"]} for card in NUMBER_CARDS],
    )
    workspace.save(ignore_permissions=True)


def ensure_workspace_visibility():
    if not frappe.db.exists("Workspace", WORKSPACE):
        return

    workspace = frappe.get_doc("Workspace", WORKSPACE)
    workspace.public = 1
    workspace.is_hidden = 0
    workspace.module = MODULE
    workspace.save(ignore_permissions=True)
