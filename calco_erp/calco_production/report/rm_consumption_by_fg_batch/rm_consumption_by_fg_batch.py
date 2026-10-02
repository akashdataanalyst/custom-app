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
        {"label": _("FG Batch No"), "fieldname": "fg_batch_no", "fieldtype": "Data", "width": 160},
        {"label": _("FG Code"), "fieldname": "fg_code", "fieldtype": "Link", "options": "Item", "width": 170},
        {"label": _("FG Name"), "fieldname": "fg_name", "fieldtype": "Data", "width": 220},
        {"label": _("Production Line"), "fieldname": "production_line", "fieldtype": "Link", "options": "Workstation", "width": 150},
        {"label": _("Total RM Qty"), "fieldname": "total_rm_qty", "fieldtype": "Float", "width": 140},
        {"label": _("Entries"), "fieldname": "entry_count", "fieldtype": "Int", "width": 100},
        {"label": _("Distinct RM Batches"), "fieldname": "rm_batch_count", "fieldtype": "Int", "width": 140},
        {"label": _("Last Posting"), "fieldname": "last_posting", "fieldtype": "Datetime", "width": 170},
    ]
    data = frappe.db.sql(
        f"""
        select
            pce.fg_batch_no,
            pce.fg_code,
            i.item_name as fg_name,
            pce.production_line,
            round(sum(pci.rm_qty), 3) as total_rm_qty,
            count(distinct pce.name) as entry_count,
            count(distinct pci.rm_batch_no) as rm_batch_count,
            max(pce.posting_datetime) as last_posting
        from `tabProduction Consumption Entry` pce
        inner join ({row_source}) pci on pci.parent = pce.name
        left join `tabItem` i on i.name = pce.fg_code
        where {conditions}
        group by pce.fg_batch_no, pce.fg_code, i.item_name, pce.production_line
        order by last_posting desc, pce.fg_batch_no asc
        """,
        params,
        as_dict=True,
    )
    return columns, data
