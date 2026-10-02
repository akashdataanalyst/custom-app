"""Canonical Desk entry without changing guest or portal routing."""
import frappe
from frappe.website.path_resolver import resolve_path as native_resolve_path


def resolve_path(path):
    """Use a temporary native redirect for authenticated System User GET / only.

    Frappe otherwise renders the Desk template at / without canonicalizing the
    URL. Classic Desk presentation depends on its /desk pathname. All other
    website paths retain Frappe's native resolution (including guest home).
    """
    request = getattr(frappe.local, "request", None)
    session = getattr(frappe, "session", None)
    if (
        path == ""
        and request
        and request.path == "/"
        and request.method in ("GET", "HEAD")
        and not frappe.form_dict.get("cmd")
        and session
        and session.user != "Guest"
        and session.data.get("user_type") == "System User"
    ):
        frappe.flags.redirect_location = "/desk"
        raise frappe.Redirect(http_status_code=302)
    return native_resolve_path(path)
