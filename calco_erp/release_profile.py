"""Production distribution policy; copied site configuration cannot enable UAT helpers."""
PRODUCTION_DISTRIBUTION = True

def require_recovery_distribution():
    if PRODUCTION_DISTRIBUTION:
        import frappe
        frappe.throw("Recovery-only administrative helper is disabled in the production distribution.", frappe.PermissionError)
