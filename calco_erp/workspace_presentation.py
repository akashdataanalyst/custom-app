"""Presentation-only workspace sync; preserves document permissions and business records."""
import json
from pathlib import Path
import frappe

WORKSPACES = ("Production", "Purchase", "Quality")

def definition(name):
    slug=name.lower()
    path=Path(__file__).parent / f"calco_{slug}/workspace/{slug}/{slug}.json"
    return json.loads(path.read_text(encoding="utf-8"))

SIDEBAR_LABELS = {
    "Production": {"Work Order", "Production Job Card", "FG Delivery Note", "Final RM Consumption", "FG Planning Dashboard", "Production Dashboard", "Plant Production Dashboard"},
    "Purchase": {"RM Planning Dashboard", "New RM Request", "New Supplier Request", "Material Request", "Request for Quotation", "Supplier Quotation / Acknowledgement", "Purchase Order", "Purchase Receipt", "Purchase Journey / Material Traceability", "Purchase Performance Dashboard"},
    "Quality": {"Incoming Quality Inspections", "RM Release Note", "In-Process QC / Checkpoints", "In-Process Quality Inspections", "Final FG Quality Inspection", "Final QC Release", "Dispatch Clearance", "Quality Dashboard"},
}

def sidebar_items(name, config):
    rows = [{"type": "Link", "label": "Home", "link_type": "Workspace", "link_to": name}]
    for r in config["shortcuts"]:
        if name == "Production" or r["label"] in SIDEBAR_LABELS[name]:
            rows.append({"type": "Link", "label": r["label"], "link_type": r["type"],
                         "link_to": r["link_to"], "filters": r.get("stats_filter", "[]")})
    return rows

def sync():
    for name in WORKSPACES:
        if not frappe.db.exists("Workspace",name):
            continue
        config=definition(name)
        workspace=frappe.get_doc("Workspace",name)
        # Keep existing roles, ownership, permissions, number-card definitions and all business data.
        for field in ("shortcuts","links"):
            workspace.set(field,config[field])
        workspace.content=config["content"]
        if "custom_blocks" in config:
            workspace.set("custom_blocks", config["custom_blocks"])
        # Explicitly approved workspace visibility only; no DocPerm changes.
        if name == "Purchase" and not any(r.role == "Purchase User" for r in workspace.roles):
            workspace.append("roles", {"role": "Purchase User"})
        workspace.save(ignore_permissions=True)
        if frappe.db.exists("Workspace Sidebar",name):
            sidebar=frappe.get_doc("Workspace Sidebar",name)
            rows=sidebar_items(name,config)
            sidebar.set("items",rows);sidebar.save(ignore_permissions=True)
    frappe.clear_cache()


def sync_purchase_material_request_context(apply=False):
    """Target only the Purchase MR entry-point filters; no broad workspace sync.

    PLAN by default. Refuse unexpected filters rather than replacing user intent.
    Uses native document saves, preserves child identities and all other fields.
    Caller owns the transaction/commit.
    """
    frappe.only_for("System Manager")
    expected = [["Material Request", "material_request_type", "=", "Purchase"]]
    changes = []
    documents = []
    for doctype, table, field in (
        ("Workspace", "shortcuts", "stats_filter"),
        ("Workspace Sidebar", "items", "filters"),
    ):
        if not frappe.db.exists(doctype, "Purchase"):
            frappe.throw(f"Missing Purchase {doctype}; review navigation before applying")
        doc = frappe.get_doc(doctype, "Purchase")
        rows = [r for r in doc.get(table) if r.link_to == "Material Request"]
        if len(rows) != 1:
            frappe.throw(f"Expected exactly one Purchase Material Request entry in {doctype}")
        row = rows[0]
        current = frappe.parse_json(row.get(field) or "[]")
        if current not in ([], expected):
            frappe.throw(f"Unexpected existing Material Request filters in {doctype}; review required")
        changed = current != expected
        changes.append(dict(doctype=doctype, name=doc.name, row=row.name,
                            field=field, before=current, after=expected, changed=changed))
        documents.append((doc, row, field, changed))
    if apply:
        for doc, row, field, changed in documents:
            if changed:
                row.set(field, frappe.as_json(expected))
                doc.save()
        frappe.clear_cache()
    return changes
