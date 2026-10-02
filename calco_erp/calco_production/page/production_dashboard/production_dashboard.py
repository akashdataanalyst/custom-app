from __future__ import annotations

import frappe
from frappe.utils import flt

from calco_erp.dashboard_utils import (
    get_list_route,
    make_card,
    make_chart,
    make_drilldown,
    make_drilldown_row,
    resolve_report_date,
)
from calco_erp.machine_setup import get_output_by_machine_rows, get_output_by_shift_rows


@frappe.whitelist()
def get_dashboard_data(report_date: str | None = None) -> dict[str, object]:
    if report_date:
        report_date = frappe.utils.getdate(report_date)
    else:
        report_date = resolve_report_date(
            [
                """
                select max(se.posting_date) as report_date
                from `tabStock Entry` se
                where se.docstatus = 1
                  and ifnull(se.stock_entry_type, se.purpose) = 'Manufacture'
                """,
                """
                select max(date(wo.planned_start_date)) as report_date
                from `tabWork Order` wo
                where wo.docstatus < 2
                """,
            ]
        )

    daily_production = frappe.db.sql(
        """
        select
            bpr.item_code,
            round(sum(bpr.produced_qty), 3) as qty
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        where bpr.docstatus = 1
          and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        group by bpr.item_code
        order by qty desc, bpr.item_code asc
        limit 10
        """,
        {"report_date": report_date},
        as_dict=True,
    )
    work_order_status = frappe.db.sql(
        """
        select coalesce(status, 'Not Set') as status, count(*) as count
        from `tabWork Order`
        where docstatus < 2
        group by status
        order by count desc, status asc
        """,
        as_dict=True,
    )
    machine_output = get_output_by_machine_rows(report_date)
    shift_output = get_output_by_shift_rows(report_date)
    recent_work_orders = frappe.get_all(
        "Work Order",
        fields=["name", "production_item", "status", "qty"],
        order_by="modified desc",
        limit_page_length=8,
    )
    recent_stock_entries = frappe.get_all(
        "Stock Entry",
        filters={"docstatus": 1, "stock_entry_type": ("in", ["Manufacture"])},
        fields=["name", "posting_date", "work_order"],
        order_by="posting_date desc, modified desc",
        limit_page_length=8,
    )
    recent_batches = frappe.get_all(
        "Batch Production Record",
        fields=["name", "item_code", "fg_batch_no", "produced_qty"],
        order_by="modified desc",
        limit_page_length=8,
    )

    cards = [
        make_card(
            "Open Work Orders",
            frappe.db.count(
                "Work Order",
                filters={"docstatus": ("<", 2), "status": ("not in", ["Completed", "Stopped", "Closed"])},
            ),
            route=get_list_route("Work Order"),
        ),
        make_card(
            "Planned Qty",
            flt(
                frappe.db.sql(
                    """
                    select coalesce(sum(qty), 0)
                    from `tabWork Order`
                    where docstatus < 2
                      and date(planned_start_date) = %s
                    """,
                    report_date,
                )[0][0]
            ),
            suffix=" Kg",
            route=get_list_route("Work Order"),
        ),
        make_card(
            "Produced Qty",
            round(sum(flt(row.qty) for row in daily_production), 3),
            suffix=" Kg",
            route=get_list_route("Batch Production Record"),
        ),
        make_card(
            "Manufacture Entries",
            frappe.db.count(
                "Stock Entry",
                filters={"docstatus": 1, "posting_date": report_date, "stock_entry_type": "Manufacture"},
            ),
            route=get_list_route("Stock Entry"),
        ),
    ]

    charts = [
        make_chart(
            "production-by-fg",
            "Daily Production by FG",
            [row.item_code for row in daily_production],
            [{"name": "Produced Qty", "values": [flt(row.qty) for row in daily_production]}],
            colors=["#c62828"],
            suffix=" Kg",
        ),
        make_chart(
            "work-order-status",
            "Work Order Status",
            [row.status for row in work_order_status],
            [{"name": "Count", "values": [int(row.count) for row in work_order_status]}],
            chart_type="donut",
            colors=["#1565c0", "#2e7d32", "#ef6c00", "#6a1b9a", "#ad1457"],
        ),
        make_chart(
            "machine-output",
            "Output per Machine",
            [row.machine for row in machine_output],
            [{"name": "Produced Qty", "values": [flt(row.produced_qty) for row in machine_output]}],
            colors=["#ef6c00"],
            suffix=" Kg",
        ),
        make_chart(
            "shift-output",
            "Output per Shift",
            [row.shift_type for row in shift_output],
            [{"name": "Produced Qty", "values": [flt(row.produced_qty) for row in shift_output]}],
            colors=["#5e35b1"],
            suffix=" Kg",
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Recent Work Orders",
            [
                make_drilldown_row(
                    "Work Order",
                    row.name,
                    meta=f"{row.production_item} | {row.status} | {flt(row.qty)} Kg",
                )
                for row in recent_work_orders
            ],
        ),
        make_drilldown(
            "Recent Manufacture Entries",
            [
                make_drilldown_row(
                    "Stock Entry",
                    row.name,
                    meta=f"{row.posting_date} | Work Order {row.work_order or '-'}",
                )
                for row in recent_stock_entries
            ],
        ),
        make_drilldown(
            "Recent Batch Production Records",
            [
                make_drilldown_row(
                    "Batch Production Record",
                    row.name,
                    meta=f"{row.item_code} | {row.fg_batch_no} | {flt(row.produced_qty)} Kg",
                )
                for row in recent_batches
            ],
        ),
    ]

    return {
        "report_date": str(report_date),
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }
