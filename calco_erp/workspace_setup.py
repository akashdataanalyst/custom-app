import json
from pathlib import Path

import frappe

from calco_erp.branding_setup import ERP_TITLE, LOGO_URL


WORKSPACES = [
    {
        "name": "Production",
        "module": "Calco Production",
        "icon": "factory",
        "sequence_id": 1.0,
        "slug": "production",
    },
    {
        "name": "Quality",
        "module": "Calco Quality",
        "icon": "check-circle",
        "sequence_id": 2.0,
        "slug": "quality",
    },
    {
        "name": "Purchase",
        "module": "Calco Purchase",
        "icon": "shopping-cart",
        "sequence_id": 3.0,
        "slug": "purchase",
    },
    {
        "name": "Dispatch",
        "module": "Calco Dispatch",
        "icon": "truck",
        "sequence_id": 4.0,
        "slug": "dispatch",
    },
    {
        "name": "Maintenance",
        "module": "Calco Maintenance",
        "icon": "tool",
        "sequence_id": 5.0,
        "slug": "maintenance",
    },
    {
        "name": "Complaint CAPA",
        "module": "Calco Complaint CAPA",
        "icon": "alert-triangle",
        "sequence_id": 6.0,
        "slug": "complaint-capa",
    },
    {
        "name": "Vendor Approval",
        "module": "Calco Vendor Approval",
        "icon": "users",
        "sequence_id": 7.0,
        "slug": "vendor-approval",
    },
    {
        "name": "Customer Approval",
        "module": "Calco Customer Approval",
        "icon": "user-check",
        "sequence_id": 8.0,
        "slug": "customer-approval",
    },
    {
        "name": "Management Review",
        "module": "Calco Management Review",
        "icon": "bar-chart",
        "sequence_id": 9.0,
        "slug": "management-review",
    },
    {
        "name": "NPD",
        "module": "Calco NPD",
        "icon": "flask",
        "sequence_id": 10.0,
        "slug": "npd",
    },
]

WORKSPACE_PAGE_LINKS = {
    "Production": [
        {"label": "Production Dashboard", "page": "production-dashboard"},
        {"label": "Plant Production Dashboard", "page": "plant-production-dashboard"},
    ],
    "Maintenance": [
        {"label": "Maintenance Dashboard", "page": "maintenance-dashboard"},
    ],
    "Quality": [
        {"label": "Quality Dashboard", "page": "quality-dashboard"},
    ],
    "Purchase": [
        {"label": "Master Data Governance Center", "page": "master-data-governance"},
        {"label": "Purchase Performance Dashboard", "page": "purchase-performance-dashboard"},
        {"label": "Inventory Dashboard", "page": "inventory-dashboard"},
    ],
    "Customer Approval": [
        {"label": "Sales Dashboard", "page": "sales-dashboard"},
    ],
    "Management Review": [
        {"label": "Finance Dashboard", "page": "finance-dashboard"},
    ],
}

APP_ROOT = Path(__file__).resolve().parent
QUALITY_WORKSPACE_NAME = "Quality"
QUALITY_WORKSPACE_ROUTE = "/app/quality"
QUALITY_WORKSPACE_JSON_PATH = APP_ROOT / "calco_quality" / "workspace" / "quality" / "quality.json"
QUALITY_WORKSPACE_ROLE_FALLBACK = (
    "Administrator",
    "System Manager",
    "Quality Manager",
    "Quality User",
)
QUALITY_WORKSPACE_FIELD_NAMES = (
    "app",
    "title",
    "label",
    "module",
    "icon",
    "indicator_color",
    "public",
    "is_hidden",
    "hide_custom",
    "sequence_id",
    "for_user",
    "parent_page",
    "type",
)
QUALITY_WORKSPACE_CHILD_TABLES = (
    "charts",
    "custom_blocks",
    "links",
    "number_cards",
    "quick_lists",
    "shortcuts",
)

MAINTENANCE_NUMBER_CARDS = [
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

MASTER_DATA_GOVERNANCE_NUMBER_CARDS = [
    {
        "label": "New RM Requests - Pending Technical Review",
        "module": "Calco Purchase",
        "document_type": "New RM Request",
        "filters": [["New RM Request", "status", "=", "Technical Review"]],
    },
    {
        "label": "New RM Requests - Pending Document & Sample Verification",
        "module": "Calco Purchase",
        "document_type": "New RM Request",
        "filters": [["New RM Request", "status", "=", "Document & Sample Readiness"]],
    },
    {
        "label": "New RM Requests - Pending Quality Review",
        "module": "Calco Purchase",
        "document_type": "New RM Request",
        "filters": [["New RM Request", "status", "=", "Quality Review"]],
    },
    {
        "label": "New RM Requests - Pending Purchase Review",
        "module": "Calco Purchase",
        "document_type": "New RM Request",
        "filters": [["New RM Request", "status", "=", "Purchase Review"]],
    },
    {
        "label": "New Supplier Requests - Pending Quality Review",
        "module": "Calco Purchase",
        "document_type": "New Supplier Request",
        "filters": [["New Supplier Request", "status", "=", "Quality Review"]],
    },
    {
        "label": "New Supplier Requests - Pending Purchase Review",
        "module": "Calco Purchase",
        "document_type": "New Supplier Request",
        "filters": [["New Supplier Request", "status", "=", "Purchase Review"]],
    },
    {
        "label": "New Supplier Requests - Pending Management Review",
        "module": "Calco Purchase",
        "document_type": "New Supplier Request",
        "filters": [["New Supplier Request", "status", "=", "Management Review"]],
    },
    {
        "label": "Commercial Approvals Pending",
        "module": "Calco Purchase",
        "document_type": "Purchase Commercial Approval",
        "filters": [["Purchase Commercial Approval", "approval_status", "=", "Draft"]],
    },
]

ALL_NUMBER_CARDS = MAINTENANCE_NUMBER_CARDS + MASTER_DATA_GOVERNANCE_NUMBER_CARDS
NUMBER_CARD_BUILDERS = {card["label"]: card for card in ALL_NUMBER_CARDS}


def sync_workspace_ui():
    ensure_module_defs()
    ensure_workspace_dependencies()

    for config in WORKSPACES:
        ensure_workspace(config)
        ensure_workspace_sidebar(config)
        ensure_desktop_icon(config)

    ensure_workspace_page_links()
    ensure_workspace_layout_definitions()
    ensure_welcome_workspace()
    frappe.clear_cache()


def after_install_setup():
    sync_workspace_ui()
    from calco_erp.calco_customer_approval.sales_order_journey import ensure_sales_order_journey_setup

    ensure_sales_order_journey_setup()


def ensure_module_defs():
    if not frappe.db.exists("DocType", "Module Def"):
        return

    for config in WORKSPACES:
        if frappe.db.exists("Module Def", config["module"]):
            continue

        frappe.get_doc(
            {
                "doctype": "Module Def",
                "module_name": config["module"],
                "app_name": "calco_erp",
            }
        ).insert(ignore_permissions=True)


def ensure_workspace(config):
    changed = False

    if frappe.db.exists("Workspace", config["name"]):
        workspace = frappe.get_doc("Workspace", config["name"])
    else:
        workspace = frappe.new_doc("Workspace")
        workspace.title = config["name"]
        workspace.label = config["name"]
        workspace.name = config["name"]
        workspace.public = 1
        workspace.type = "Workspace"
        changed = True

    updates = {
        "title": config["name"],
        "label": config["name"],
        "module": config["module"],
        "icon": config["icon"],
        "public": 1,
        "is_hidden": 0,
        "type": "Workspace",
        "sequence_id": config["sequence_id"],
        "for_user": "",
    }

    for fieldname, value in updates.items():
        if getattr(workspace, fieldname) != value:
            setattr(workspace, fieldname, value)
            changed = True

    if changed:
        if workspace.is_new():
            workspace.insert(ignore_permissions=True)
        else:
            workspace.save(ignore_permissions=True)


def ensure_workspace_sidebar(config):
    if frappe.db.exists("Workspace Sidebar", config["name"]):
        sidebar = frappe.get_doc("Workspace Sidebar", config["name"])
    else:
        sidebar = frappe.new_doc("Workspace Sidebar")
        sidebar.title = config["name"]

    sidebar.title = config["name"]
    sidebar.header_icon = config["icon"]
    sidebar.module = config["module"]
    sidebar.set("items", [])
    sidebar.append(
        "items",
        {
            "label": "Home",
            "type": "Link",
            "link_to": config["name"],
            "link_type": "Workspace",
        },
    )
    for link in WORKSPACE_PAGE_LINKS.get(config["name"], []):
        if not ui_record_exists("Page", link["page"]):
            continue
        sidebar.append(
            "items",
            {
                "label": link["label"],
                "type": "Link",
                "link_to": link["page"],
                "link_type": "Page",
            },
        )
    sidebar.save(ignore_permissions=True)


def ensure_desktop_icon(config):
    if frappe.db.exists("Desktop Icon", config["name"]):
        icon = frappe.get_doc("Desktop Icon", config["name"])
    else:
        icon = frappe.new_doc("Desktop Icon")
        icon.label = config["name"]

    icon.label = config["name"]
    icon.icon_type = "Link"
    icon.link_type = "Workspace Sidebar"
    icon.link_to = config["name"]
    icon.icon = config["icon"]
    icon.hidden = 0
    icon.standard = 0
    icon.idx = int(config["sequence_id"])
    icon.app = "calco_erp"
    icon.parent_icon = None
    icon.save(ignore_permissions=True)


def ensure_workspace_page_links():
    for workspace_name, links in WORKSPACE_PAGE_LINKS.items():
        if not frappe.db.exists("Workspace", workspace_name):
            continue

        workspace = frappe.get_doc("Workspace", workspace_name)
        sanitize_workspace_doc(workspace)
        existing = {(link.link_type, link.link_to) for link in workspace.links}
        changed = False

        for link in links:
            if not ui_record_exists("Page", link["page"]):
                continue
            key = ("Page", link["page"])
            if key in existing:
                continue

            workspace.append(
                "links",
                {
                    "hidden": 0,
                    "is_query_report": 0,
                    "label": link["label"],
                    "link_count": 0,
                    "link_to": link["page"],
                    "link_type": "Page",
                    "onboard": 0,
                    "type": "Link",
                },
            )
            changed = True

        if changed:
            workspace.save(ignore_permissions=True)


def ensure_quality_workspace_layout():
    ensure_workspace_layout_definitions((QUALITY_WORKSPACE_NAME,))


def load_quality_workspace_definition():
    with QUALITY_WORKSPACE_JSON_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def ensure_workspace_dependencies():
    ensure_maintenance_number_cards()
    ensure_master_data_governance_number_cards()


def ensure_maintenance_number_cards():
    if not frappe.db.exists("DocType", "Number Card"):
        return

    for card in MAINTENANCE_NUMBER_CARDS:
        ensure_number_card(card)


def ensure_master_data_governance_number_cards():
    if not frappe.db.exists("DocType", "Number Card"):
        return

    for card in MASTER_DATA_GOVERNANCE_NUMBER_CARDS:
        ensure_number_card(card)


def ensure_number_card(card_config):
    label = card_config["label"]

    if frappe.db.exists("Number Card", label):
        doc = frappe.get_doc("Number Card", label)
    else:
        doc = frappe.new_doc("Number Card")
        doc.label = label

    doc.module = card_config.get("module") or "Calco Maintenance"
    doc.type = "Document Type"
    doc.document_type = card_config["document_type"]
    doc.function = "Count"
    doc.is_public = 1
    doc.show_full_number = 1
    doc.filters_json = json.dumps(card_config.get("filters") or [])
    doc.dynamic_filters_json = json.dumps(card_config.get("dynamic_filters") or [])
    doc.save(ignore_permissions=True)


def discover_workspace_definition_paths():
    mapping = {}
    for json_path in APP_ROOT.glob("**/workspace/*/*.json"):
        try:
            with json_path.open(encoding="utf-8") as handle:
                definition = json.load(handle)
        except Exception:
            continue

        workspace_name = definition.get("name") or definition.get("title") or definition.get("label")
        if workspace_name:
            existing = mapping.get(workspace_name)
            if existing is not None and len(json_path.parts) >= len(existing.parts):
                continue

            mapping[workspace_name] = json_path

    return mapping


def ensure_workspace_layout_definitions(workspace_names=None):
    definition_paths = discover_workspace_definition_paths()
    selected_names = set(workspace_names or definition_paths.keys())

    for workspace_name in selected_names:
        json_path = definition_paths.get(workspace_name)
        if not json_path:
            continue
        ensure_workspace_layout_from_definition(workspace_name, json_path)


def force_sync_workspace_from_definition(workspace_name):
    definition_paths = discover_workspace_definition_paths()
    json_path = definition_paths.get(workspace_name)
    if not json_path:
        frappe.throw(f"Workspace definition not found for {workspace_name}.")

    ensure_workspace_layout_from_definition(workspace_name, json_path)
    frappe.clear_cache()


def ensure_workspace_layout_from_definition(workspace_name, json_path):
    with json_path.open(encoding="utf-8") as handle:
        definition = json.load(handle)

    if frappe.db.exists("Workspace", workspace_name):
        workspace = frappe.get_doc("Workspace", workspace_name)
    else:
        workspace = frappe.new_doc("Workspace")
        workspace.name = workspace_name

    for fieldname in QUALITY_WORKSPACE_FIELD_NAMES:
        if fieldname in definition:
            setattr(workspace, fieldname, definition[fieldname])

    workspace.name = workspace_name
    workspace.set("roles", [])
    roles = [row.get("role") for row in definition.get("roles", []) if row.get("role")] or (
        list(QUALITY_WORKSPACE_ROLE_FALLBACK) if workspace_name == QUALITY_WORKSPACE_NAME else []
    )
    for role in roles:
        if frappe.db.exists("Role", role):
            workspace.append("roles", {"role": role})

    sanitized_shortcuts = []
    for child_table in QUALITY_WORKSPACE_CHILD_TABLES:
        rows = [row.copy() for row in definition.get(child_table, [])]
        sanitized_rows = sanitize_workspace_rows(workspace_name, child_table, rows)
        workspace.set(child_table, [])
        for row in sanitized_rows:
            workspace.append(child_table, row)
        if child_table == "shortcuts":
            sanitized_shortcuts = sanitized_rows

    workspace.content = sanitize_workspace_content(definition.get("content") or "[]", sanitized_shortcuts)

    if workspace.is_new():
        workspace.insert(ignore_permissions=True)
    else:
        workspace.save(ignore_permissions=True)


def sanitize_workspace_doc(workspace):
    sanitized_shortcuts = []
    for child_table in QUALITY_WORKSPACE_CHILD_TABLES:
        rows = [row.as_dict() if hasattr(row, "as_dict") else dict(row) for row in getattr(workspace, child_table, [])]
        sanitized_rows = sanitize_workspace_rows(workspace.name, child_table, rows)
        workspace.set(child_table, [])
        for row in sanitized_rows:
            workspace.append(child_table, row)
        if child_table == "shortcuts":
            sanitized_shortcuts = sanitized_rows

    workspace.content = sanitize_workspace_content(workspace.content or "[]", sanitized_shortcuts)


def sanitize_workspace_rows(workspace_name, child_table, rows):
    sanitized = []
    for row in rows:
        if should_keep_workspace_row(workspace_name, child_table, row):
            sanitized.append(row)
    return sanitized


def should_keep_workspace_row(workspace_name, child_table, row):
    if child_table == "number_cards":
        card_name = row.get("number_card_name")
        if ensure_number_card_if_possible(card_name):
            return True
        return not card_name

    if child_table == "charts":
        chart_name = row.get("chart_name")
        return ui_record_exists("Dashboard Chart", chart_name) if chart_name else True

    if child_table == "links":
        return ui_target_exists(row.get("link_type"), row.get("link_to"), row)

    if child_table == "shortcuts":
        return ui_target_exists(row.get("type"), row.get("link_to"), row)

    if child_table == "quick_lists":
        document_type = row.get("document_type")
        return ui_record_exists("DocType", document_type) if document_type else True

    if child_table == "custom_blocks":
        block_name = row.get("custom_block_name")
        return ui_record_exists("Custom Block", block_name) if block_name else True

    return True


def ensure_number_card_if_possible(card_name):
    if not card_name:
        return True
    if ui_record_exists("Number Card", card_name):
        return True
    card_config = NUMBER_CARD_BUILDERS.get(card_name)
    if not card_config:
        return False
    ensure_number_card(card_config)
    return ui_record_exists("Number Card", card_name)


def ui_target_exists(target_type, target_name, row=None):
    if not target_type or not target_name:
        return True

    target_type = str(target_type)
    if target_type in {"URL", "URL List"}:
        return True
    if target_type == "DocType":
        return ui_record_exists("DocType", target_name)
    if target_type == "Page":
        return ui_record_exists("Page", target_name)
    if target_type == "Report":
        return ui_record_exists("Report", target_name)
    if target_type == "Workspace":
        return ui_record_exists("Workspace", target_name)
    if target_type == "Dashboard Chart":
        return ui_record_exists("Dashboard Chart", target_name)
    if target_type == "Number Card":
        return ensure_number_card_if_possible(target_name)
    return True


def ui_record_exists(doctype, name):
    if not name:
        return False
    if not frappe.db.exists("DocType", doctype):
        return False
    return bool(frappe.db.exists(doctype, name))


def sanitize_workspace_content(content_json, shortcut_rows):
    try:
        content = json.loads(content_json or "[]")
    except Exception:
        return "[]"

    shortcut_names = {row.get("label") for row in shortcut_rows if row.get("label")}
    sanitized = []
    for block in content:
        block_type = block.get("type")
        data = block.get("data") or {}
        if block_type == "number_card":
            if not ensure_number_card_if_possible(data.get("number_card_name")):
                continue
        elif block_type == "shortcut":
            shortcut_name = data.get("shortcut_name")
            if shortcut_name and shortcut_name not in shortcut_names:
                continue
        sanitized.append(block)

    return json.dumps(sanitized)


def ensure_welcome_workspace():
    if not frappe.db.exists("Workspace", "Welcome Workspace"):
        return

    workspace = frappe.get_doc("Workspace", "Welcome Workspace")
    workspace.public = 1
    workspace.is_hidden = 0
    workspace.type = "Workspace"
    workspace.set("shortcuts", [])

    content = [
        {
            "id": "welcome-calco-header",
            "type": "header",
            "data": {
                "text": f'<div class="calco-workspace-hero"><img src="{LOGO_URL}" alt="{ERP_TITLE}" class="calco-workspace-logo"><div><div class="h2">{ERP_TITLE}</div><div class="text-muted">Integrated manufacturing, quality, dispatch, finance and HR for Calco PolyTechnik Pvt Ltd.</div></div></div>',
                "col": 12,
            },
        },
        {
            "id": "welcome-calco-paragraph",
            "type": "paragraph",
            "data": {
                "text": "Open the business workspaces below to run Calco PolyTechnik Pvt Ltd Manufacturing ERP.",
                "col": 12,
            },
        },
    ]

    for config in WORKSPACES:
        target_url = f"/app/{config['slug']}"
        if config["name"] == QUALITY_WORKSPACE_NAME:
            target_url = QUALITY_WORKSPACE_ROUTE

        workspace.append(
            "shortcuts",
            {
                "type": "URL",
                "label": config["name"],
                "url": target_url,
                "icon": config["icon"],
                "color": "blue",
            },
        )
        content.append(
            {
                "id": f"welcome-{config['slug']}-shortcut",
                "type": "shortcut",
                "data": {"shortcut_name": config["name"], "col": 3},
            }
        )

    workspace.content = json.dumps(content)
    workspace.save(ignore_permissions=True)
