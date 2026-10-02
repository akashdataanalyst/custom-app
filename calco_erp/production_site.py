"""Explicit deployment-site binding; does not enable provisional routing or auto posting."""
import frappe
from calco_erp.release_profile import PRODUCTION_DISTRIBUTION

def allowed():
    expected = str(frappe.conf.get('calco_manufacturing_site') or '').strip()
    site = str(getattr(frappe.local, 'site', '') or '')
    if expected:
        return bool(site and expected == site)
    return not PRODUCTION_DISTRIBUTION and site == 'recovery120120.localhost'
