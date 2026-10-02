from __future__ import annotations

import frappe
from frappe.utils import flt, nowdate

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
                "select max(posting_date) as report_date from `tabSales Invoice` where docstatus = 1",
                "select max(posting_date) as report_date from `tabPurchase Invoice` where docstatus = 1",
            ]
        )

    receivable = flt(
        frappe.db.sql(
            """
            select coalesce(sum(outstanding_amount), 0)
            from `tabSales Invoice`
            where docstatus = 1
              and outstanding_amount > 0
            """
        )[0][0]
    )
    payable = flt(
        frappe.db.sql(
            """
            select coalesce(sum(outstanding_amount), 0)
            from `tabPurchase Invoice`
            where docstatus = 1
              and outstanding_amount > 0
            """
        )[0][0]
    )
    sales_today = flt(
        frappe.db.sql(
            "select coalesce(sum(base_grand_total), 0) from `tabSales Invoice` where docstatus = 1 and posting_date = %s",
            report_date,
        )[0][0]
    )
    purchases_today = flt(
        frappe.db.sql(
            "select coalesce(sum(base_grand_total), 0) from `tabPurchase Invoice` where docstatus = 1 and posting_date = %s",
            report_date,
        )[0][0]
    )

    last_dates = last_n_dates(report_date, 7)
    sales_trend = frappe.db.sql(
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
    purchase_trend = frappe.db.sql(
        """
        select posting_date, round(sum(base_grand_total), 2) as amount
        from `tabPurchase Invoice`
        where docstatus = 1
          and posting_date between %(start)s and %(end)s
        group by posting_date
        order by posting_date asc
        """,
        {"start": last_dates[0], "end": last_dates[-1]},
        as_dict=True,
    )
    sales_map = {str(row.posting_date): flt(row.amount) for row in sales_trend}
    purchase_map = {str(row.posting_date): flt(row.amount) for row in purchase_trend}

    top_customers = frappe.db.sql(
        """
        select customer, round(sum(outstanding_amount), 2) as outstanding
        from `tabSales Invoice`
        where docstatus = 1
          and outstanding_amount > 0
        group by customer
        order by outstanding desc, customer asc
        limit 10
        """,
        as_dict=True,
    )
    top_suppliers = frappe.db.sql(
        """
        select supplier, round(sum(outstanding_amount), 2) as outstanding
        from `tabPurchase Invoice`
        where docstatus = 1
          and outstanding_amount > 0
        group by supplier
        order by outstanding desc, supplier asc
        limit 10
        """,
        as_dict=True,
    )
    overdue_sales = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": (">", 0), "due_date": ("<", nowdate())},
        fields=["name", "customer", "outstanding_amount", "due_date"],
        order_by="due_date asc",
        limit_page_length=8,
    )
    overdue_purchases = frappe.get_all(
        "Purchase Invoice",
        filters={"docstatus": 1, "outstanding_amount": (">", 0), "due_date": ("<", nowdate())},
        fields=["name", "supplier", "outstanding_amount", "due_date"],
        order_by="due_date asc",
        limit_page_length=8,
    )

    cards = [
        make_card("Receivables", round(receivable, 2), suffix=" Rs", route=get_list_route("Sales Invoice")),
        make_card("Payables", round(payable, 2), suffix=" Rs", route=get_list_route("Purchase Invoice")),
        make_card("Sales Billed Today", round(sales_today, 2), suffix=" Rs", route=get_list_route("Sales Invoice")),
        make_card(
            "Purchases Billed Today",
            round(purchases_today, 2),
            suffix=" Rs",
            route=get_list_route("Purchase Invoice"),
        ),
    ]

    charts = [
        make_chart(
            "invoice-trend",
            "Sales vs Purchase Invoice Trend",
            last_dates,
            [
                {"name": "Sales", "values": [flt(sales_map.get(date, 0)) for date in last_dates]},
                {"name": "Purchase", "values": [flt(purchase_map.get(date, 0)) for date in last_dates]},
            ],
            chart_type="line",
            colors=["#2e7d32", "#c62828"],
            suffix=" Rs",
        ),
        make_chart(
            "customer-receivables",
            "Top Customer Receivables",
            [row.customer for row in top_customers],
            [{"name": "Outstanding", "values": [flt(row.outstanding) for row in top_customers]}],
            colors=["#1565c0"],
            suffix=" Rs",
        ),
        make_chart(
            "supplier-payables",
            "Top Supplier Payables",
            [row.supplier for row in top_suppliers],
            [{"name": "Outstanding", "values": [flt(row.outstanding) for row in top_suppliers]}],
            colors=["#ef6c00"],
            suffix=" Rs",
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Overdue Sales Invoices",
            [
                make_drilldown_row(
                    "Sales Invoice",
                    row.name,
                    meta=f"{row.customer} | Due {row.due_date} | Rs {flt(row.outstanding_amount)}",
                )
                for row in overdue_sales
            ],
        ),
        make_drilldown(
            "Overdue Purchase Invoices",
            [
                make_drilldown_row(
                    "Purchase Invoice",
                    row.name,
                    meta=f"{row.supplier} | Due {row.due_date} | Rs {flt(row.outstanding_amount)}",
                )
                for row in overdue_purchases
            ],
        ),
    ]

    return {
        "report_date": str(report_date),
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }
