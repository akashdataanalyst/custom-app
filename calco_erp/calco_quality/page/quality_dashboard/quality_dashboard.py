from __future__ import annotations

from collections import Counter

import frappe
from frappe.utils import add_days, cint, flt, formatdate, get_datetime, getdate, nowdate

from calco_erp.dashboard_utils import (
    get_list_route,
    get_report_route,
    make_card,
    make_chart,
    make_drilldown,
    make_drilldown_row,
)


DRILLDOWN_LIMIT = 8
DEFAULT_RANGE_DAYS = 30
TOP_LIMIT = 8

SUPPLIER_REJECTION_REPORT = "Supplier-wise RM Rejection Report"
PARAMETER_FAILURE_REPORT = "RM Parameter Failure Report"
PROBLEMATIC_ITEMS_REPORT = "Top Problematic RM Items Report"
REJECTION_TREND_REPORT = "RM Rejection Trend Report"


@frappe.whitelist()
def get_dashboard_data(
    report_date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    supplier: str | None = None,
    item_code: str | None = None,
) -> dict[str, object]:
    from_date, to_date = resolve_date_range(report_date, from_date, to_date)
    report_date = to_date
    day_start, day_end = get_day_bounds(report_date)
    range_start, range_end = get_range_bounds(from_date, to_date)
    report_filters = build_report_filters(from_date, to_date, supplier=supplier, item_code=item_code)

    rm_pending_route_options = build_pending_rm_quality_inspection_route_options(item_code=item_code)
    rm_accepted_day_filters = build_rm_inward_filters(
        status="Accepted",
        supplier=supplier,
        item_code=item_code,
        modified_between=(day_start, day_end),
    )
    rm_rejected_day_filters = build_rm_inward_filters(
        status="Rejected",
        supplier=supplier,
        item_code=item_code,
        modified_between=(day_start, day_end),
    )
    rm_inspected_range_filters = build_rm_inward_filters(
        statuses=["Accepted", "Hold", "Rejected"],
        supplier=supplier,
        item_code=item_code,
        modified_between=(range_start, range_end),
    )
    rm_rejected_range_filters = build_rm_inward_filters(
        status="Rejected",
        supplier=supplier,
        item_code=item_code,
        modified_between=(range_start, range_end),
    )

    rm_pending_rows = get_pending_rm_quality_inspection_rows(
        supplier=supplier,
        item_code=item_code,
        limit=DRILLDOWN_LIMIT,
    )
    rm_recent_rejection_rows = frappe.get_all(
        "RM Inward Validation",
        filters=rm_rejected_range_filters,
        fields=["name", "purchase_receipt", "supplier", "item_code", "batch_no", "status", "modified"],
        order_by="modified desc",
        limit_page_length=DRILLDOWN_LIMIT,
    )

    rm_pending_count = count_pending_rm_quality_inspections(supplier=supplier, item_code=item_code)
    rm_accepted_today_count = count_doctype("RM Inward Validation", rm_accepted_day_filters)
    rm_rejected_today_count = count_doctype("RM Inward Validation", rm_rejected_day_filters)
    rm_inspected_count = count_doctype("RM Inward Validation", rm_inspected_range_filters)
    rm_rejected_count = count_doctype("RM Inward Validation", rm_rejected_range_filters)
    rm_rejection_pct = round((rm_rejected_count * 100.0 / rm_inspected_count), 2) if rm_inspected_count else 0

    supplier_failures = get_supplier_rejection_counts(range_start, range_end, supplier=supplier, item_code=item_code)
    parameter_failures = get_parameter_failure_counts(range_start, range_end, supplier=supplier, item_code=item_code)
    problematic_items = get_problematic_rm_items(range_start, range_end, supplier=supplier, item_code=item_code)
    rejection_trend = get_rm_rejection_trend(range_start, range_end, supplier=supplier, item_code=item_code)

    rm_status_counts = Counter(
        {
            "Pending QC": rm_pending_count,
            "Accepted on Date": rm_accepted_today_count,
            "Rejected on Date": rm_rejected_today_count,
            "Rejected in Range": rm_rejected_count,
        }
    )

    cards = [
        make_card(
            "RM Pending QC",
            rm_pending_count,
            route=get_list_route("Quality Inspection"),
            route_doctype="Quality Inspection",
            route_options=rm_pending_route_options,
        ),
        make_card(
            "RM Accepted Today",
            rm_accepted_today_count,
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {"status": "Accepted", "modified": [[">=", day_start], ["<=", day_end]]},
                supplier=supplier,
                item_code=item_code,
            ),
        ),
        make_card(
            "RM Rejected Today",
            rm_rejected_today_count,
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {"status": "Rejected", "modified": [[">=", day_start], ["<=", day_end]]},
                supplier=supplier,
                item_code=item_code,
            ),
        ),
        make_card(
            "RM Inspected",
            rm_inspected_count,
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {
                    "status": ["in", ["Accepted", "Hold", "Rejected"]],
                    "modified": [[">=", range_start], ["<=", range_end]],
                },
                supplier=supplier,
                item_code=item_code,
            ),
        ),
        make_card(
            "Rejected RM",
            rm_rejected_count,
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {"status": "Rejected", "modified": [[">=", range_start], ["<=", range_end]]},
                supplier=supplier,
                item_code=item_code,
            ),
        ),
        make_card(
            "RM Rejection %",
            rm_rejection_pct,
            suffix="%",
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {"status": "Rejected", "modified": [[">=", range_start], ["<=", range_end]]},
                supplier=supplier,
                item_code=item_code,
            ),
        ),
    ]

    charts = [
        make_chart(
            "rm-quality-status",
            "Raw Material Quality",
            list(rm_status_counts.keys()),
            [{"name": "Count", "values": list(rm_status_counts.values())}],
            chart_type="bar",
            colors=["#f59e0b"],
            route=get_list_route("RM Inward Validation"),
            route_doctype="RM Inward Validation",
            route_options=build_route_options(
                {"modified": [[">=", range_start], ["<=", range_end]]},
                supplier=supplier,
                item_code=item_code,
            ),
        ),
        make_chart(
            "rm-rejection-rate",
            "RM Rejection Trend",
            rejection_trend["labels"],
            [{"name": "Rejection %", "values": rejection_trend["values"]}],
            chart_type="line",
            colors=["#dc2626"],
            suffix="%",
            route=get_report_route(REJECTION_TREND_REPORT),
            route_report=REJECTION_TREND_REPORT,
            route_options=report_filters,
        ),
        make_chart(
            "supplier-rejection",
            "Supplier-wise RM Rejection",
            [row["label"] for row in supplier_failures],
            [{"name": "Rejected RM", "values": [row["value"] for row in supplier_failures]}],
            chart_type="bar",
            colors=["#ea580c"],
            route=get_report_route(SUPPLIER_REJECTION_REPORT),
            route_report=SUPPLIER_REJECTION_REPORT,
            route_options=report_filters,
        ),
        make_chart(
            "parameter-failure",
            "RM Parameter Failure Analysis",
            [row["label"] for row in parameter_failures],
            [{"name": "Failures", "values": [row["value"] for row in parameter_failures]}],
            chart_type="bar",
            colors=["#dc2626"],
            route=get_report_route(PARAMETER_FAILURE_REPORT),
            route_report=PARAMETER_FAILURE_REPORT,
            route_options=report_filters,
        ),
        make_chart(
            "problematic-rm-items",
            "Top Problematic RM Items",
            [row["label"] for row in problematic_items],
            [{"name": "Rejected RM", "values": [row["value"] for row in problematic_items]}],
            chart_type="bar",
            colors=["#b91c1c"],
            route=get_report_route(PROBLEMATIC_ITEMS_REPORT),
            route_report=PROBLEMATIC_ITEMS_REPORT,
            route_options=report_filters,
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Recent RM Rejections",
            [
                make_drilldown_row(
                    "RM Inward Validation",
                    row.name,
                    meta=f"{row.supplier or 'No Supplier'} | {row.item_code} | {row.batch_no or 'No Batch'} | {format_datetime_value(row.modified)}",
                )
                for row in rm_recent_rejection_rows
            ],
        ),
        make_drilldown(
            "RM QC Pending",
            [
                make_drilldown_row(
                    "Quality Inspection",
                    row.name,
                    meta=f"{row.purchase_receipt} | {row.supplier or 'No Supplier'} | {row.item_code} | {row.batch_no or 'No Batch'}",
                )
                for row in rm_pending_rows
            ],
        ),
    ]

    return {
        "report_date": str(report_date),
        "from_date": str(from_date),
        "to_date": str(to_date),
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }


def count_doctype(doctype: str, filters: dict[str, object]) -> int:
    return frappe.db.count(doctype, filters=filters)


def resolve_date_range(report_date=None, from_date=None, to_date=None):
    if from_date or to_date:
        start = getdate(from_date or to_date or nowdate())
        end = getdate(to_date or from_date or nowdate())
    elif report_date:
        start = end = getdate(report_date)
    else:
        end = getdate(nowdate())
        start = add_days(end, -(DEFAULT_RANGE_DAYS - 1))

    if start > end:
        start, end = end, start

    return start, end


def get_day_bounds(report_date) -> tuple[str, str]:
    report_date = getdate(report_date)
    return f"{report_date} 00:00:00", f"{report_date} 23:59:59"


def get_range_bounds(from_date, to_date) -> tuple[str, str]:
    from_date = getdate(from_date)
    to_date = getdate(to_date)
    return f"{from_date} 00:00:00", f"{to_date} 23:59:59"


def build_report_filters(from_date, to_date, supplier: str | None = None, item_code: str | None = None):
    filters = {
        "from_date": str(getdate(from_date)),
        "to_date": str(getdate(to_date)),
    }
    if supplier:
        filters["supplier"] = supplier
    if item_code:
        filters["item_code"] = item_code
    return filters


def build_rm_inward_filters(
    status: str | None = None,
    statuses: list[str] | None = None,
    supplier: str | None = None,
    item_code: str | None = None,
    modified_between: tuple[str, str] | None = None,
) -> dict[str, object]:
    filters: dict[str, object] = {}
    if status:
        filters["status"] = status
    elif statuses:
        filters["status"] = ("in", statuses)
    if supplier:
        filters["supplier"] = supplier
    if item_code:
        filters["item_code"] = item_code
    if modified_between:
        filters["modified"] = ("between", list(modified_between))
    return filters


def build_pending_rm_quality_inspection_route_options(item_code: str | None = None):
    options = {
        "inspection_type": "Incoming",
        "reference_type": "Purchase Receipt",
        "docstatus": ["in", [0, 1]],
        "status": ["!=", "Accepted"],
    }
    if item_code:
        options["item_code"] = item_code
    return options


def build_route_options(base: dict[str, object], supplier: str | None = None, item_code: str | None = None):
    options = dict(base)
    if supplier:
        options["supplier"] = supplier
    if item_code:
        options["item_code"] = item_code
    return options


def count_pending_rm_quality_inspections(supplier: str | None = None, item_code: str | None = None) -> int:
    conditions = [
        "qi.inspection_type = 'Incoming'",
        "qi.reference_type = 'Purchase Receipt'",
        "qi.docstatus in (0, 1)",
        "ifnull(qi.status, '') != 'Accepted'",
    ]
    params = {}

    if supplier:
        conditions.append("pr.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("qi.item_code = %(item_code)s")
        params["item_code"] = item_code

    return cint(
        frappe.db.sql(
            f"""
            select count(*) as count
            from `tabQuality Inspection` qi
            left join `tabPurchase Receipt` pr on pr.name = qi.reference_name
            where {" and ".join(conditions)}
            """,
            params,
        )[0][0]
        or 0
    )


def get_pending_rm_quality_inspection_rows(
    supplier: str | None = None,
    item_code: str | None = None,
    limit: int = DRILLDOWN_LIMIT,
):
    conditions = [
        "qi.inspection_type = 'Incoming'",
        "qi.reference_type = 'Purchase Receipt'",
        "qi.docstatus in (0, 1)",
        "ifnull(qi.status, '') != 'Accepted'",
    ]
    params = {}

    if supplier:
        conditions.append("pr.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("qi.item_code = %(item_code)s")
        params["item_code"] = item_code

    return frappe.db.sql(
        f"""
        select
            qi.name,
            qi.reference_name as purchase_receipt,
            pr.supplier,
            qi.item_code,
            qi.batch_no,
            qi.status,
            qi.modified
        from `tabQuality Inspection` qi
        left join `tabPurchase Receipt` pr on pr.name = qi.reference_name
        where {" and ".join(conditions)}
        order by qi.docstatus asc, qi.modified desc, qi.name desc
        limit {cint(limit) or DRILLDOWN_LIMIT}
        """,
        params,
        as_dict=True,
    )


def get_supplier_rejection_counts(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "riv.status = 'Rejected'",
        "riv.modified between %(range_start)s and %(range_end)s",
    ]
    params = {"range_start": range_start, "range_end": range_end}
    if supplier:
        conditions.append("riv.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("riv.item_code = %(item_code)s")
        params["item_code"] = item_code

    return frappe.db.sql(
        f"""
        select
            coalesce(s.supplier_name, riv.supplier) as label,
            count(*) as value
        from `tabRM Inward Validation` riv
        left join `tabSupplier` s on s.name = riv.supplier
        where {" and ".join(conditions)}
        group by riv.supplier, s.supplier_name
        order by value desc, label asc
        limit {TOP_LIMIT}
        """,
        params,
        as_dict=True,
    )


def get_problematic_rm_items(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "riv.status = 'Rejected'",
        "riv.modified between %(range_start)s and %(range_end)s",
    ]
    params = {"range_start": range_start, "range_end": range_end}
    if supplier:
        conditions.append("riv.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("riv.item_code = %(item_code)s")
        params["item_code"] = item_code

    return frappe.db.sql(
        f"""
        select
            riv.item_code as label,
            count(*) as value
        from `tabRM Inward Validation` riv
        where {" and ".join(conditions)}
        group by riv.item_code
        order by value desc, label asc
        limit {TOP_LIMIT}
        """,
        params,
        as_dict=True,
    )


def get_parameter_failure_counts(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "qi.docstatus = 1",
        "qi.inspection_type = 'Incoming'",
        "qir.status = 'Rejected'",
        "qi.report_date between %(from_date)s and %(to_date)s",
    ]
    params = {"from_date": str(getdate(range_start.split()[0])), "to_date": str(getdate(range_end.split()[0]))}
    if item_code:
        conditions.append("qi.item_code = %(item_code)s")
        params["item_code"] = item_code
    if supplier:
        conditions.append("qi.reference_type = 'Purchase Receipt'")
        conditions.append("pr.supplier = %(supplier)s")
        params["supplier"] = supplier

    return frappe.db.sql(
        f"""
        select
            qir.specification as label,
            count(*) as value
        from `tabQuality Inspection Reading` qir
        inner join `tabQuality Inspection` qi on qi.name = qir.parent
        left join `tabPurchase Receipt` pr on qi.reference_type = 'Purchase Receipt' and qi.reference_name = pr.name
        where {" and ".join(conditions)}
        group by qir.specification
        order by value desc, label asc
        limit {TOP_LIMIT}
        """,
        params,
        as_dict=True,
    )


def get_rm_rejection_trend(range_start: str, range_end: str, supplier: str | None = None, item_code: str | None = None):
    labels = []
    values = []
    from_date = getdate(range_start.split()[0])
    to_date = getdate(range_end.split()[0])

    rejected_map = get_daily_rm_counts("Rejected", from_date, to_date, supplier=supplier, item_code=item_code)
    inspected_map = get_daily_rm_counts(
        ["Accepted", "Hold", "Rejected"],
        from_date,
        to_date,
        supplier=supplier,
        item_code=item_code,
    )

    current_date = from_date
    while current_date <= to_date:
        key = str(current_date)
        rejected = flt(rejected_map.get(key))
        inspected = flt(inspected_map.get(key))
        rejection_pct = round((rejected * 100.0 / inspected), 2) if inspected else 0
        labels.append(formatdate(current_date, "dd MMM"))
        values.append(rejection_pct)
        current_date = add_days(current_date, 1)

    return {"labels": labels, "values": values}


def get_daily_rm_counts(status, from_date, to_date, supplier: str | None = None, item_code: str | None = None):
    conditions = [
        "date(riv.modified) between %(from_date)s and %(to_date)s",
    ]
    params = {"from_date": str(from_date), "to_date": str(to_date)}
    if isinstance(status, list):
        quoted_statuses = ", ".join(f"'{entry}'" for entry in status)
        conditions.append(f"riv.status in ({quoted_statuses})")
    else:
        conditions.append("riv.status = %(status)s")
        params["status"] = status
    if supplier:
        conditions.append("riv.supplier = %(supplier)s")
        params["supplier"] = supplier
    if item_code:
        conditions.append("riv.item_code = %(item_code)s")
        params["item_code"] = item_code

    rows = frappe.db.sql(
        f"""
        select
            date(riv.modified) as report_date,
            count(*) as value
        from `tabRM Inward Validation` riv
        where {" and ".join(conditions)}
        group by date(riv.modified)
        order by report_date asc
        """,
        params,
        as_dict=True,
    )
    return {str(row.report_date): row.value for row in rows}


def format_datetime_value(value) -> str:
    if not value:
        return ""
    return get_datetime(value).strftime("%d %b %Y %H:%M")
