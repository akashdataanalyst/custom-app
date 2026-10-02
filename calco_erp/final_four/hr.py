"""Server parity for approved HR UI calculations and new-leave validation."""
import frappe
from frappe.utils import flt, getdate, today

CATEGORIES=('Earning','Employee Deduction','Employer Contribution','Annual Benefit')

def job_offer_validate(doc,method=None):
    if doc.docstatus != 0:return
    totals={k:0 for k in CATEGORIES}
    for row in doc.get('custom_job_offer_salary_component') or []:
        category=frappe.db.get_value('Salary Component',row.salary_component,'custom_component_category') if row.salary_component else ''
        row.component_category=category or ''
        row.amount_annual=flt(row.amount_monthly)*12
        if category in totals:totals[category]+=flt(row.amount_monthly)
    doc.custom_monthly_gross_salary=totals['Earning']
    doc.custom_monthly_net_in_hand=totals['Earning']-totals['Employee Deduction']
    doc.custom_monthly_ctc_=totals['Earning']+totals['Employer Contribution']+totals['Annual Benefit']
    doc.custom_annual_ctc_=doc.custom_monthly_ctc_*12

def leave_before_insert(doc,method=None):
    if frappe.session.user=='Administrator':return
    if doc.from_date and getdate(doc.from_date)<getdate(today()) and doc.leave_type not in ('Sick Leave','Mispunch','On Duty','Short Leave','Privilege Leave'):
        frappe.throw('Past dated leave is allowed only for Sick Leave, Mispunch, On Duty, Short Leave, and Privilege Leave.')
