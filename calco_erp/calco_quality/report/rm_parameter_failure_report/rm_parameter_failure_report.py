from __future__ import annotations

import frappe

from calco_erp.calco_quality.page.quality_dashboard.quality_dashboard import (
    get_parameter_failure_counts,
    get_range_bounds,
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

    failure_rows = get_parameter_failure_counts(range_start, range_end, supplier=supplier, item_code=item_code)
    affected_inspections = get_parameter_inspection_counts(range_start, range_end, supplier=supplier, item_code=item_code)

    columns = [
        {"label": "Parameter", "fieldname": "specification", "fieldtype": "Data", "width": 240},
        {"label": "Failed Readings", "fieldname": "failure_count", "fieldtype": "Int", "width": 130},
        {"label": "Affected Inspections", "fieldname": "inspection_count", "fieldtype": "Int", "width": 150},
    ]

    data = [
        {
            "specification": row["label"],
            "failure_count": row["value"],
            "inspection_count": affected_inspections.get(row["label"], 0),
        }
        for row in failure_rows
    ]

    chart = {
        "data": {
            "labels": [row["specification"] for row in data],
            "datasets": [{"name": "Failures", "values": [row["failure_count"] for row in data]}],
        },
        "type": "bar",
        "colors": ["#dc2626"],
    }

    return columns, data, None, chart


def get_parameter_inspection_counts(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "qi.docstatus = 1",
        "qi.inspection_type = 'Incoming'",
        "qir.status = 'Rejected'",
        "qi.report_date between %(from_date)s and %(to_date)s",
    ]
    params = {"from_date": range_start.split()[0], "to_date": range_end.split()[0]}
    if item_code:
        conditions.append("qi.item_code = %(item_code)s")
        params["item_code"] = item_code
    if supplier:
        conditions.append("qi.reference_type = 'Purchase Receipt'")
        conditions.append("pr.supplier = %(supplier)s")
        params["supplier"] = supplier

    rows = frappe.db.sql(
        f"""
        select
            qir.specification as specification,
            count(distinct qi.name) as inspection_count
        from `tabQuality Inspection Reading` qir
        inner join `tabQuality Inspection` qi on qi.name = qir.parent
        left join `tabPurchase Receipt` pr on qi.reference_type = 'Purchase Receipt' and qi.reference_name = pr.name
        where {" and ".join(conditions)}
        group by qir.specification
        """,
        params,
        as_dict=True,
    )
    return {row["specification"]: row["inspection_count"] for row in rows}
