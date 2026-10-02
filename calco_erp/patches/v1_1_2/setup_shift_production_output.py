import frappe

def execute():
    frappe.reload_doc('calco_production', 'doctype', 'shift_production_output_reading', force=True)
    frappe.reload_doc('calco_production', 'doctype', 'shift_report', force=True)
