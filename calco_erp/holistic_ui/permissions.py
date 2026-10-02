"""Deny-only visibility for the approved Management presentation."""
import frappe

DASHBOARD = "CEO Bird's Eye View"
ROLES = {"System Manager", "Management Reviewer"}

def allowed(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or bool(ROLES.intersection(frappe.get_roles(user)))

def dashboard_permission(doc, ptype=None, user=None, **kwargs):
    if doc.name == DASHBOARD and not allowed(user):
        return False
    return None  # Native permissions remain authoritative; never grant access.

def dashboard_query(user=None):
    if not allowed(user):
        return "`tabDashboard`.`name` != " + frappe.db.escape(DASHBOARD)
    return ""
