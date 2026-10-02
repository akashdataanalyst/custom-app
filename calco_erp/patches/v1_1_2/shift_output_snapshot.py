def execute():
    import frappe
    from calco_erp.calco_production.shift_output_snapshot import setup
    setup()
    for doctype in ("shift_production_output_reading", "shift_report"):
        frappe.reload_doc("calco_production", "doctype", doctype, force=True)
    # Deliberately no run activation or historical backfill.
