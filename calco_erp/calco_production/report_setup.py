import frappe


REPORT_NAME = "RM Batch Consumption Cost"
STOCK_WORKSPACE = "Stock"


def ensure_rm_batch_consumption_cost_report_link():
    """Expose the Calco report in the standard Stock workspace without editing ERPNext core."""
    if not frappe.db.exists("Report", REPORT_NAME) or not frappe.db.exists("Workspace", STOCK_WORKSPACE):
        return

    workspace = frappe.get_doc("Workspace", STOCK_WORKSPACE)
    if any(link.link_type == "Report" and link.link_to == REPORT_NAME for link in workspace.get("links") or []):
        return

    workspace.append(
        "links",
        {
            "hidden": 0,
            "is_query_report": 1,
            "label": REPORT_NAME,
            "link_count": 0,
            "link_to": REPORT_NAME,
            "link_type": "Report",
            "onboard": 0,
            "type": "Link",
        },
    )
    workspace.save(ignore_permissions=True)
    frappe.clear_cache()
