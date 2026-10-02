from __future__ import annotations

import frappe
from frappe import _

from calco_erp.calco_production.consumption_reporting import (
    build_common_conditions,
    build_consumption_row_source,
)


def execute(filters=None):
    conditions, params = build_common_conditions(filters)
    row_source = build_consumption_row_source()
    columns = [
        {"label": _("Production Line"), "fieldname": "production_line", "fieldtype": "Link", "options": "Workstation", "width": 170},
        {"label": _("Line Description"), "fieldname": "line_description", "fieldtype": "Data", "width": 200},
        {"label": _("Total RM Qty"), "fieldname": "total_rm_qty", "fieldtype": "Float", "width": 140},
        {"label": _("Entries"), "fieldname": "entry_count", "fieldtype": "Int", "width": 100},
        {"label": _("Distinct RM Codes"), "fieldname": "rm_code_count", "fieldtype": "Int", "width": 130},
        {"label": _("Distinct FG Batches"), "fieldname": "fg_batch_count", "fieldtype": "Int", "width": 140},
        {"label": _("Last Posting"), "fieldname": "last_posting", "fieldtype": "Datetime", "width": 170},
    ]
    data = frappe.db.sql(
        f"""
        select
            pce.production_line,
            ws.description as line_description,
            round(sum(pci.rm_qty), 3) as total_rm_qty,
            count(distinct pce.name) as entry_count,
            count(distinct pci.rm_code) as rm_code_count,
            count(distinct pce.fg_batch_no) as fg_batch_count,
            max(pce.posting_datetime) as last_posting
        from `tabProduction Consumption Entry` pce
        inner join ({row_source}) pci on pci.parent = pce.name
        left join `tabWorkstation` ws on ws.name = pce.production_line
        where {conditions}
        group by pce.production_line, ws.description
        order by total_rm_qty desc, pce.production_line asc
        """,
        params,
        as_dict=True,
    )
    return columns, data
