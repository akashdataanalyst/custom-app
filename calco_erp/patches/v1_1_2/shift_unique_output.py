def execute():
    import frappe
    from calco_erp.calco_production.shift_unique_output import setup
    setup()
    for dt in ("shift_report", "shift_production_output_reading"):
        frappe.reload_doc("calco_production", "doctype", dt, force=True)
    # No activation or backfill of existing runs.
