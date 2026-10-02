from __future__ import annotations

import frappe
from frappe.utils import flt

from calco_erp.dashboard_utils import (
    get_list_route,
    last_n_dates,
    make_card,
    make_chart,
    make_drilldown,
    make_drilldown_row,
    resolve_report_date,
)


@frappe.whitelist()
def get_dashboard_data(report_date: str | None = None) -> dict[str, object]:
    if report_date:
        report_date = frappe.utils.getdate(report_date)
    else:
        report_date = resolve_report_date(
            [
                "select max(transaction_date) as report_date from `tabSales Order` where docstatus = 1",
                "select max(posting_date) as report_date from `tabSales Invoice` where docstatus = 1",
            ]
        )

    open_order_rows = frappe.db.sql(
        """
        select count(*) as order_count, coalesce(sum(base_grand_total), 0) as order_value
        from `tabSales Order`
        where docstatus = 1
          and status not in ('Completed', 'Closed', 'Cancelled')
        """,
        as_dict=True,
    )[0]
    sales_order_status = frappe.db.sql(
        """
        select coalesce(status, 'Not Set') as status, count(*) as count
        from `tabSales Order`
        where docstatus = 1
        group by status
        order by count desc, status asc
        """,
        as_dict=True,
    )
    top_sold_items = frappe.db.sql(
        """
        select sii.item_code, round(sum(sii.qty), 3) as qty
        from `tabSales Invoice Item` sii
        inner join `tabSales Invoice` si on si.name = sii.parent
        where si.docstatus = 1
          and si.posting_date = %(report_date)s
        group by sii.item_code
        order by qty desc, sii.item_code asc
        limit 10
        """,
        {"report_date": report_date},
        as_dict=True,
    )

    last_dates = last_n_dates(report_date, 7)
    invoice_totals = frappe.db.sql(
        """
        select posting_date, round(sum(base_grand_total), 2) as amount
        from `tabSales Invoice`
        where docstatus = 1
          and posting_date between %(start)s and %(end)s
        group by posting_date
        order by posting_date asc
        """,
        {"start": last_dates[0], "end": last_dates[-1]},
        as_dict=True,
    )
    invoice_map = {str(row.posting_date): flt(row.amount) for row in invoice_totals}
    recent_sales_orders = frappe.get_all(
        "Sales Order",
        fields=["name", "customer", "status", "grand_total"],
        order_by="modified desc",
        limit_page_length=8,
    )
    recent_sales_invoices = frappe.get_all(
        "Sales Invoice",
        fields=["name", "customer", "status", "grand_total"],
        order_by="posting_date desc, modified desc",
        limit_page_length=8,
    )
    pending_sales_orders = frappe.get_all(
        "Sales Order",
        filters={"docstatus": 1, "status": ("not in", ["Completed", "Closed", "Cancelled"])},
        fields=["name", "customer", "status", "base_grand_total"],
        order_by="modified desc",
        limit_page_length=8,
    )

    cards = [
        make_card("Open Sales Orders", int(open_order_rows.order_count or 0), route=get_list_route("Sales Order")),
        make_card(
            "Open Order Value",
            round(flt(open_order_rows.order_value), 2),
            suffix=" Rs",
            route=get_list_route("Sales Order"),
        ),
        make_card(
            "Invoices Today",
            frappe.db.count("Sales Invoice", filters={"docstatus": 1, "posting_date": report_date}),
            route=get_list_route("Sales Invoice"),
        ),
        make_card(
            "Billed Today",
            round(
                flt(
                    frappe.db.sql(
                        "select coalesce(sum(base_grand_total), 0) from `tabSales Invoice` where docstatus = 1 and posting_date = %s",
                        report_date,
                    )[0][0]
                ),
                2,
            ),
            suffix=" Rs",
            route=get_list_route("Sales Invoice"),
        ),
    ]

    charts = [
        make_chart(
            "sales-order-status",
            "Sales Order Status",
            [row.status for row in sales_order_status],
            [{"name": "Count", "values": [int(row.count) for row in sales_order_status]}],
            chart_type="donut",
            colors=["#1565c0", "#2e7d32", "#ef6c00", "#c62828", "#6a1b9a"],
        ),
        make_chart(
            "top-sold-items",
            "Top Sold Items",
            [row.item_code for row in top_sold_items],
            [{"name": "Qty", "values": [flt(row.qty) for row in top_sold_items]}],
            colors=["#00897b"],
            suffix=" Kg",
        ),
        make_chart(
            "invoice-trend",
            "Sales Invoice Trend",
            last_dates,
            [{"name": "Invoice Amount", "values": [flt(invoice_map.get(date, 0)) for date in last_dates]}],
            chart_type="line",
            colors=["#c62828"],
            suffix=" Rs",
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Recent Sales Orders",
            [
                make_drilldown_row(
                    "Sales Order",
                    row.name,
                    meta=f"{row.customer} | {row.status} | Rs {flt(row.grand_total)}",
                )
                for row in recent_sales_orders
            ],
        ),
        make_drilldown(
            "Recent Sales Invoices",
            [
                make_drilldown_row(
                    "Sales Invoice",
                    row.name,
                    meta=f"{row.customer} | {row.status} | Rs {flt(row.grand_total)}",
                )
                for row in recent_sales_invoices
            ],
        ),
        make_drilldown(
            "Pending Sales Orders",
            [
                make_drilldown_row(
                    "Sales Order",
                    row.name,
                    meta=f"{row.customer} | {row.status} | Rs {flt(row.base_grand_total)}",
                )
                for row in pending_sales_orders
            ],
        ),
    ]

    return {
        "report_date": str(report_date),
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }
