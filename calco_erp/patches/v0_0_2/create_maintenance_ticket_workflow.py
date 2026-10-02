import frappe


WORKFLOW_NAME = "Maintenance Ticket Workflow"
DOCUMENT_TYPE = "Maintenance Ticket"
EDIT_ROLE = "System Manager"

WORKFLOW_STATES = [
    {"workflow_state_name": "Open", "style": "Primary"},
    {"workflow_state_name": "Inspection", "style": "Warning"},
    {"workflow_state_name": "Spare Required", "style": "Danger"},
    {"workflow_state_name": "Spare Available", "style": "Info"},
    {"workflow_state_name": "In Progress", "style": "Primary"},
    {"workflow_state_name": "Completed", "style": "Success"},
    {"workflow_state_name": "Closed", "style": "Info"},
]

DOC_STATES = [
    {"state": "Open", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "Inspection", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "Spare Required", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "Spare Available", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "In Progress", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "Completed", "doc_status": 0, "allow_edit": EDIT_ROLE},
    {"state": "Closed", "doc_status": 0, "allow_edit": EDIT_ROLE},
]

TRANSITIONS = [
    {
        "state": "Open",
        "action": "Start Inspection",
        "next_state": "Inspection",
        "allowed": EDIT_ROLE,
    },
    {
        "state": "Inspection",
        "action": "Mark Spare Required",
        "next_state": "Spare Required",
        "allowed": EDIT_ROLE,
        "condition": "doc.spare_required",
    },
    {
        "state": "Inspection",
        "action": "Start Work",
        "next_state": "In Progress",
        "allowed": EDIT_ROLE,
        "condition": "not doc.spare_required",
    },
    {
        "state": "Spare Required",
        "action": "Mark Spare Available",
        "next_state": "Spare Available",
        "allowed": EDIT_ROLE,
    },
    {
        "state": "Spare Available",
        "action": "Start Work",
        "next_state": "In Progress",
        "allowed": EDIT_ROLE,
    },
    {
        "state": "In Progress",
        "action": "Complete",
        "next_state": "Completed",
        "allowed": EDIT_ROLE,
    },
    {
        "state": "Completed",
        "action": "Close",
        "next_state": "Closed",
        "allowed": EDIT_ROLE,
    },
]


def execute():
    ensure_workflow_states()
    ensure_workflow_action_masters()
    ensure_workflow()
    frappe.clear_cache(doctype=DOCUMENT_TYPE)
    frappe.cache.hdel("workflow", DOCUMENT_TYPE)


def ensure_workflow_states():
    for state in WORKFLOW_STATES:
        name = frappe.db.get_value(
            "Workflow State", {"workflow_state_name": state["workflow_state_name"]}, "name"
        )

        if name:
            doc = frappe.get_doc("Workflow State", name)
        else:
            doc = frappe.new_doc("Workflow State")
            doc.workflow_state_name = state["workflow_state_name"]

        doc.style = state["style"]
        doc.save(ignore_permissions=True)


def ensure_workflow_action_masters():
    action_names = {transition["action"] for transition in TRANSITIONS}

    for action_name in action_names:
        if frappe.db.exists("Workflow Action Master", action_name):
            continue

        doc = frappe.new_doc("Workflow Action Master")
        doc.workflow_action_name = action_name
        doc.save(ignore_permissions=True)


def ensure_workflow():
    existing_name = frappe.db.get_value("Workflow", {"document_type": DOCUMENT_TYPE}, "name")
    if existing_name:
        workflow = frappe.get_doc("Workflow", existing_name)
    else:
        workflow = frappe.new_doc("Workflow")

    workflow.workflow_name = WORKFLOW_NAME
    workflow.document_type = DOCUMENT_TYPE
    workflow.workflow_state_field = "status"
    workflow.override_status = 0
    workflow.send_email_alert = 0
    workflow.enable_action_confirmation = 1
    workflow.is_active = 0

    workflow.set("states", [])
    for state in DOC_STATES:
        workflow.append("states", state)

    workflow.set("transitions", [])
    for transition in TRANSITIONS:
        workflow.append("transitions", transition)

    workflow.is_active = 1
    workflow.save(ignore_permissions=True)
