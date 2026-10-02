import json

import frappe

from calco_erp.branding_setup import ERP_TITLE, LOGO_URL


HOME_SHORTCUT_LABEL = "Production"
HOME_SHORTCUT_URL = "/app/production"


def execute():
    ensure_production_workspace()
    ensure_welcome_shortcut()


def ensure_production_workspace():
    if not frappe.db.exists("Workspace", "Production"):
        return

    workspace = frappe.get_doc("Workspace", "Production")
    changed = False

    if workspace.public != 1:
        workspace.public = 1
        changed = True

    if workspace.is_hidden != 0:
        workspace.is_hidden = 0
        changed = True

    if workspace.module != "Calco Production":
        workspace.module = "Calco Production"
        changed = True

    if not workspace.title:
        workspace.title = "Production"
        changed = True

    if not workspace.label:
        workspace.label = "Production"
        changed = True

    if changed:
        workspace.save(ignore_permissions=True)


def ensure_welcome_shortcut():
    if not frappe.db.exists("Workspace", "Welcome Workspace"):
        return

    workspace = frappe.get_doc("Workspace", "Welcome Workspace")

    shortcut = None
    for row in workspace.shortcuts:
        if row.label == HOME_SHORTCUT_LABEL or row.url == HOME_SHORTCUT_URL:
            shortcut = row
            break

    if not shortcut:
        shortcut = workspace.append("shortcuts", {})

    shortcut.type = "URL"
    shortcut.label = HOME_SHORTCUT_LABEL
    shortcut.url = HOME_SHORTCUT_URL
    shortcut.icon = "factory"
    shortcut.color = "blue"

    workspace.public = 1
    workspace.is_hidden = 0
    workspace.type = "Workspace"
    workspace.content = json.dumps(
        [
            {
                "id": "welcome-production-header",
                "type": "header",
                "data": {
                    "text": f'<div class="calco-workspace-hero"><img src="{LOGO_URL}" alt="{ERP_TITLE}" class="calco-workspace-logo"><div><div class="h2">{ERP_TITLE}</div><div class="text-muted">Calco PolyTechnik Pvt Ltd Manufacturing ERP</div></div></div>',
                    "col": 12,
                },
            },
            {
                "id": "welcome-production-paragraph",
                "type": "paragraph",
                "data": {
                    "text": "Open the Production workspace to start using Calco PolyTechnik Pvt Ltd Manufacturing ERP.",
                    "col": 12,
                },
            },
            {
                "id": "welcome-production-shortcut",
                "type": "shortcut",
                "data": {"shortcut_name": HOME_SHORTCUT_LABEL, "col": 3},
            },
        ]
    )
    workspace.save(ignore_permissions=True)
