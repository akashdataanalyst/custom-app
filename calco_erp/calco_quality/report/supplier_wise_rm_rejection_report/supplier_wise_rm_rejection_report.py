from __future__ import annotations

import frappe

from calco_erp.calco_quality.page.quality_dashboard.quality_dashboard import (
    get_range_bounds,
    get_supplier_rejection_counts,
    resolve_date_range,
)


def execute(filters=None):
    filters = filters or {}
    from_date, to_date = resolve_date_range(
        filters.get("report_date"),
        filters.get("from_date"),
        filters.get("to_date"),
    )
    range_start, range_end = get_range_bounds(from_date, to_date)
    supplier = filters.get("supplier")
    item_code = filters.get("item_code")

    rejected_rows = get_supplier_rejection_counts(range_start, range_end, supplier=supplier, item_code=item_code)
    inspected_totals = get_supplier_inspection_totals(range_start, range_end, supplier=supplier, item_code=item_code)

    columns = [
        {"label": "Supplier", "fieldname": "supplier", "fieldtype": "Data", "width": 220},
        {"label": "Inspected RM", "fieldname": "inspected_count", "fieldtype": "Int", "width": 130},
        {"label": "Rejected RM", "fieldname": "rejected_count", "fieldtype": "Int", "width": 130},
        {"label": "RM Rejection %", "fieldname": "rejection_percentage", "fieldtype": "Percent", "width": 140},
    ]

    data = []
    for row in rejected_rows:
        inspected_count = inspected_totals.get(row["label"], 0)
        data.append(
            {
                "supplier": row["label"],
                "inspected_count": inspected_count,
                "rejected_count": row["value"],
                "rejection_percentage": round((row["value"] * 100.0 / inspected_count), 2) if inspected_count else 0,
            }
        )

    chart = {
        "data": {
            "labels": [row["supplier"] for row in data],
            "datasets": [{"name": "Rejected RM", "values": [row["rejected_count"] for row in data]}],
        },
        "type": "bar",
        "colors": ["#ea580c"],
    }

    return columns, data, None, chart


def get_supplier_inspection_totals(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "riv.status in ('Accepted', 'Hold', 'Rejected')",
        "riv.modified between %(range_start)s and %(range_end)s",
    ]
    params = {"range_start": range_start, "range_end": range_end}
    if supplier:
        conditions.append("riv.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("riv.item_code = %(item_code)s")
        params["item_code"] = item_code

    rows = frappe.db.sql(
        f"""
        select
            coalesce(s.supplier_name, riv.supplier) as supplier,
            count(*) as inspected_count
        from `tabRM Inward Validation` riv
        left join `tabSupplier` s on s.name = riv.supplier
        where {" and ".join(conditions)}
        group by riv.supplier, s.supplier_name
        """,
        params,
        as_dict=True,
    )
    return {row["supplier"]: row["inspected_count"] for row in rows}
