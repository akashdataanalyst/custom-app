import frappe

from calco_erp.workspace_setup import sync_workspace_ui


def execute():
    sync_workspace_ui()
    frappe.clear_cache(doctype="Workspace")

