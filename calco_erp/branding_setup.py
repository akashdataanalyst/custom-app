from __future__ import annotations

import frappe


COMPANY_NAME = "Calco PolyTechnik Pvt Ltd"
ERP_TITLE = "Calco PolyTechnik Pvt Ltd ERP"
LOGIN_TITLE = "Calco PolyTechnik Pvt Ltd Manufacturing ERP"
ADDRESS_PLACEHOLDER = "Address: [Company Address Placeholder]"
LOGO_URL = "/assets/calco_erp/images/calco-logo-official.svg"
BANNER_HTML = (
    '<span class="calco-brand-inline">'
    f'<img src="{LOGO_URL}" alt="{COMPANY_NAME}">'
    f"<span>{ERP_TITLE}</span>"
    "</span>"
)
LETTER_HEAD_NAME = COMPANY_NAME
LETTER_HEAD_FOOTER = (
    f"<div style='font-size:11px;color:#666;border-top:1px solid #d1d5db;padding-top:8px;'>"
    f"{COMPANY_NAME} | {ADDRESS_PLACEHOLDER}</div>"
)


def ensure_branding_setup():
    ensure_system_identity()
    ensure_navbar_and_login_branding()
    ensure_letter_head()
    ensure_company_defaults()
    frappe.clear_cache()


def ensure_system_identity():
    frappe.db.set_single_value("System Settings", "app_name", ERP_TITLE, update_modified=False)
    frappe.db.set_single_value("Website Settings", "app_name", LOGIN_TITLE, update_modified=False)


def ensure_navbar_and_login_branding():
    frappe.db.set_single_value("Navbar Settings", "app_logo", LOGO_URL, update_modified=False)
    frappe.db.set_single_value("Website Settings", "app_logo", LOGO_URL, update_modified=False)
    frappe.db.set_single_value("Website Settings", "banner_image", LOGO_URL, update_modified=False)
    frappe.db.set_single_value("Website Settings", "brand_html", BANNER_HTML, update_modified=False)


def ensure_letter_head():
    if frappe.db.exists("Letter Head", LETTER_HEAD_NAME):
        letter_head = frappe.get_doc("Letter Head", LETTER_HEAD_NAME)
    else:
        letter_head = frappe.new_doc("Letter Head")
        letter_head.letter_head_name = LETTER_HEAD_NAME

    letter_head.source = "HTML"
    letter_head.footer_source = "HTML"
    letter_head.disabled = 0
    letter_head.is_default = 1
    letter_head.content = build_letter_head_html()
    letter_head.footer = LETTER_HEAD_FOOTER

    if letter_head.is_new():
        letter_head.insert(ignore_permissions=True)
    else:
        letter_head.save(ignore_permissions=True)

    frappe.db.sql(
        """
        update `tabLetter Head`
        set is_default = 0
        where name != %s and ifnull(disabled, 0) = 0
        """,
        (LETTER_HEAD_NAME,),
    )
    frappe.db.set_value("Letter Head", LETTER_HEAD_NAME, "is_default", 1, update_modified=False)


def build_letter_head_html() -> str:
    return f"""
<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #b32025;padding-bottom:12px;margin-bottom:12px;">
  <div style="display:flex;align-items:center;gap:14px;">
    <img src="{LOGO_URL}" alt="{COMPANY_NAME}" style="height:56px;width:auto;">
    <div>
      <div style="font-size:24px;font-weight:700;color:#27272a;line-height:1.2;">{COMPANY_NAME}</div>
      <div style="font-size:12px;color:#666;">Manufacturing ERP</div>
    </div>
  </div>
  <div style="font-size:11px;color:#666;text-align:right;">{ADDRESS_PLACEHOLDER}</div>
</div>
"""


def ensure_company_defaults():
    companies = frappe.get_all("Company", fields=["name", "default_letter_head"], limit_page_length=0)
    for company in companies:
        if company.default_letter_head != LETTER_HEAD_NAME:
            frappe.db.set_value("Company", company.name, "default_letter_head", LETTER_HEAD_NAME, update_modified=False)
