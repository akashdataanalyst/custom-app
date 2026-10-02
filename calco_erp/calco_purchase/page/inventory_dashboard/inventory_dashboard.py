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


@frappe.whitelist()
def get_dashboard_data(report_date: str | None = None) -> dict[str, object]:
    if report_date:
        report_date = frappe.utils.getdate(report_date)
    else:
        report_date = resolve_report_date(
            ["select max(posting_date) as report_date from `tabStock Ledger Entry` where is_cancelled = 0"]
        )

    rm_bins = frappe.db.sql(
        """
        select
            b.item_code,
            round(sum(b.actual_qty), 3) as qty
        from `tabBin` b
        inner join `tabItem` i on i.name = b.item_code
        where i.item_group = 'Raw Material'
          and b.actual_qty > 0
        group by b.item_code
        order by qty desc, b.item_code asc
        limit 10
        """,
        as_dict=True,
    )
    fg_bins = frappe.db.sql(
        """
        select
            b.item_code,
            round(sum(b.actual_qty), 3) as qty
        from `tabBin` b
        inner join `tabItem` i on i.name = b.item_code
        where i.item_group = 'Finished Goods'
          and b.actual_qty > 0
        group by b.item_code
        order by qty desc, b.item_code asc
        limit 10
        """,
        as_dict=True,
    )
    movement = frappe.db.sql(
        """
        select
            voucher_type,
            round(sum(case when actual_qty > 0 then actual_qty else 0 end), 3) as inward_qty,
            round(sum(case when actual_qty < 0 then abs(actual_qty) else 0 end), 3) as outward_qty
        from `tabStock Ledger Entry`
        where is_cancelled = 0
          and posting_date = %(report_date)s
        group by voucher_type
        order by voucher_type asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )
    recent_vouchers = frappe.db.sql(
        """
        select voucher_type, voucher_no, max(posting_date) as posting_date
        from `tabStock Ledger Entry`
        where is_cancelled = 0
        group by voucher_type, voucher_no
        order by posting_date desc, voucher_no desc
        limit 8
        """,
        as_dict=True,
    )
    live_batches = frappe.db.sql(
        """
        select
            batch_no,
            item_code,
            warehouse,
            round(sum(actual_qty), 3) as qty
        from `tabStock Ledger Entry`
        where is_cancelled = 0
          and ifnull(batch_no, '') != ''
        group by batch_no, item_code, warehouse
        having qty > 0
        order by qty desc, batch_no asc
        limit 8
        """,
        as_dict=True,
    )

    rm_qty = flt(
        frappe.db.sql(
            """
            select coalesce(sum(b.actual_qty), 0)
            from `tabBin` b
            inner join `tabItem` i on i.name = b.item_code
            where i.item_group = 'Raw Material'
            """
        )[0][0]
    )
    fg_qty = flt(
        frappe.db.sql(
            """
            select coalesce(sum(b.actual_qty), 0)
            from `tabBin` b
            inner join `tabItem` i on i.name = b.item_code
            where i.item_group = 'Finished Goods'
            """
        )[0][0]
    )
    rm_value = flt(
        frappe.db.sql(
            """
            select coalesce(sum(b.stock_value), 0)
            from `tabBin` b
            inner join `tabItem` i on i.name = b.item_code
            where i.item_group = 'Raw Material'
            """
        )[0][0]
    )
    fg_value = flt(
        frappe.db.sql(
            """
            select coalesce(sum(b.stock_value), 0)
            from `tabBin` b
            inner join `tabItem` i on i.name = b.item_code
            where i.item_group = 'Finished Goods'
            """
        )[0][0]
    )

    cards = [
        make_card("RM On Hand", round(rm_qty, 3), suffix=" Kg", route=get_list_route("Item")),
        make_card("FG On Hand", round(fg_qty, 3), suffix=" Kg", route=get_list_route("Item")),
        make_card("RM Stock Value", round(rm_value, 2), suffix=" Rs", route=get_list_route("Stock Ledger Entry")),
        make_card("FG Stock Value", round(fg_value, 2), suffix=" Rs", route=get_list_route("Stock Ledger Entry")),
    ]

    charts = [
        make_chart(
            "rm-stock",
            "Top RM Stock",
            [row.item_code for row in rm_bins],
            [{"name": "Qty", "values": [flt(row.qty) for row in rm_bins]}],
            colors=["#2e7d32"],
            suffix=" Kg",
        ),
        make_chart(
            "fg-stock",
            "Top FG Stock",
            [row.item_code for row in fg_bins],
            [{"name": "Qty", "values": [flt(row.qty) for row in fg_bins]}],
            colors=["#1565c0"],
            suffix=" Kg",
        ),
        make_chart(
            "stock-movement",
            "Daily Stock Movement",
            [row.voucher_type for row in movement],
            [
                {"name": "Inward", "values": [flt(row.inward_qty) for row in movement]},
                {"name": "Outward", "values": [flt(row.outward_qty) for row in movement]},
            ],
            chart_type="bar",
            colors=["#00897b", "#c62828"],
            suffix=" Kg",
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Recent Stock Vouchers",
            [
                make_drilldown_row(
                    row.voucher_type,
                    row.voucher_no,
                    meta=f"{row.voucher_type} | {row.posting_date}",
                )
                for row in recent_vouchers
            ],
        ),
        make_drilldown(
            "Live FG / RM Batches",
            [
                make_drilldown_row(
                    "Batch",
                    row.batch_no,
                    meta=f"{row.item_code} | {row.warehouse} | {flt(row.qty)} Kg",
                )
                for row in live_batches
            ],
        ),
    ]

    return {
        "report_date": str(report_date),
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }
