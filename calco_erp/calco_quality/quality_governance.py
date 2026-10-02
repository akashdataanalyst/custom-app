import frappe
from frappe import _
from frappe.utils import cstr


QUALITY_GOVERNANCE_DOCTYPES = (
    "Quality Procedure",
    "Non Conformance",
    "Quality Action",
    "Quality Goal",
)

PERMISSION_TYPES = (
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "print",
    "email",
    "share",
    "select",
)

QUALITY_GOVERNANCE_PERMISSIONS = {
    "Quality Procedure": {
        "System Manager": ("read", "write", "create", "delete", "report", "export", "print", "email", "share"),
        "Quality Manager": ("read", "write", "create", "report", "export", "print", "email", "share"),
        "Quality User": ("read", "report", "export", "print"),
    },
    "Non Conformance": {
        "System Manager": ("read", "write", "create", "delete", "report", "export", "print", "email", "share"),
        "Quality Manager": ("read", "write", "create", "report", "export", "print", "email", "share"),
        "Quality User": ("read", "write", "create", "report", "print", "email"),
    },
    "Quality Action": {
        "System Manager": ("read", "write", "create", "delete", "report", "export", "print", "email", "share"),
        "Quality Manager": ("read", "write", "create", "report", "export", "print", "email", "share"),
        "Quality User": ("read", "write", "report", "print", "email"),
    },
    "Quality Goal": {
        "System Manager": ("read", "write", "create", "delete", "report", "export", "print", "email", "share"),
        "Quality Manager": ("read", "write", "create", "report", "export", "print", "email", "share"),
        "Quality User": ("read", "report", "export", "print"),
    },
}


def validate_quality_goal_phase(doc, method=None):
    if cstr(doc.get("frequency")).strip() not in {"", "None"}:
        frappe.throw(
            _(
                "Automated Quality Reviews are not active in this phase. "
                "Set Monitoring Frequency to None."
            )
        )


def install_quality_governance_permissions():
    for doctype, role_permissions in QUALITY_GOVERNANCE_PERMISSIONS.items():
        if not frappe.db.exists("DocType", doctype):
            continue

        for name in frappe.get_all(
            "Custom DocPerm",
            filters={"parent": doctype},
            pluck="name",
        ):
            frappe.delete_doc(
                "Custom DocPerm",
                name,
                ignore_permissions=True,
                force=True,
            )

        for role, permission_types in role_permissions.items():
            if not frappe.db.exists("Role", role):
                continue

            row = {
                "doctype": "Custom DocPerm",
                "parent": doctype,
                "parenttype": "DocType",
                "parentfield": "permissions",
                "role": role,
                "permlevel": 0,
                "if_owner": 0,
            }
            row.update({permission_type: 0 for permission_type in PERMISSION_TYPES})
            row.update({permission_type: 1 for permission_type in permission_types})
            frappe.get_doc(row).insert(ignore_permissions=True)

        frappe.clear_cache(doctype=doctype)
