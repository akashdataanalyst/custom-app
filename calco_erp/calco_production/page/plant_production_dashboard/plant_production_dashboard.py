from __future__ import annotations

from collections import OrderedDict

import frappe
from frappe.utils import flt, getdate, nowdate

from calco_erp.machine_setup import (
    PLANT_MACHINES,
    get_output_by_machine_rows,
    get_output_by_operator_rows,
    get_output_by_shift_rows,
)


@frappe.whitelist()
def get_dashboard_data(report_date: str | None = None) -> dict[str, object]:
    report_date = resolve_report_date(report_date)

    daily_production = get_daily_production_by_fg(report_date)
    extruder_utilization = get_extruder_utilization(report_date)
    output_by_machine = get_output_by_machine_rows(report_date)
    output_by_operator = get_output_by_operator_rows(report_date)
    output_by_shift = get_output_by_shift_rows(report_date)
    cost_by_product = get_cost_by_product(report_date)
    profit_by_product = get_profit_by_product(report_date, cost_by_product)
    rm_consumption = get_rm_consumption(report_date)
    qc_failures = get_qc_failures(report_date)
    dispatch_quantity = get_dispatch_quantity(report_date)

    return {
        "report_date": str(report_date),
        "cards": {
            "production_qty": round(sum(row["qty"] for row in daily_production), 3),
            "dispatch_qty": round(sum(row["qty"] for row in dispatch_quantity), 3),
            "rm_consumption_qty": round(sum(row["qty"] for row in rm_consumption), 3),
            "qc_failure_count": int(sum(row["count"] for row in qc_failures)),
            "machine_count": int(len(output_by_machine)),
            "operator_count": int(len(output_by_operator)),
            "shift_count": int(len(output_by_shift)),
            "avg_rm_cost_per_kg": round(
                sum(row["rm_cost_per_kg"] for row in cost_by_product) / len(cost_by_product), 4
            )
            if cost_by_product
            else 0,
            "avg_manufacturing_cost_per_kg": round(
                sum(row["manufacturing_cost_per_kg"] for row in cost_by_product) / len(cost_by_product), 4
            )
            if cost_by_product
            else 0,
            "gross_profit_total": round(sum(row["gross_profit_total"] for row in profit_by_product), 2),
            "avg_extruder_utilization": round(
                sum(row["utilization_pct"] for row in extruder_utilization) / len(extruder_utilization), 2
            )
            if extruder_utilization
            else 0,
        },
        "daily_production_by_fg": daily_production,
        "extruder_utilization": extruder_utilization,
        "output_by_machine": output_by_machine,
        "output_by_operator": output_by_operator,
        "output_by_shift": output_by_shift,
        "cost_by_product": cost_by_product,
        "profit_by_product": profit_by_product,
        "rm_consumption": rm_consumption,
        "qc_failures": qc_failures,
        "dispatch_quantity": dispatch_quantity,
    }


def resolve_report_date(report_date):
    if report_date:
        return getdate(report_date)

    latest_production_date = frappe.db.sql(
        """
        select max(ifnull(se.posting_date, date(bpr.modified))) as report_date
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        where bpr.docstatus = 1
        """,
        as_dict=True,
    )
    if latest_production_date and latest_production_date[0].report_date:
        return getdate(latest_production_date[0].report_date)

    return getdate(nowdate())


def get_daily_production_by_fg(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        """
        select
            bpr.item_code,
            coalesce(i.item_name, bpr.item_code) as item_name,
            round(sum(bpr.produced_qty), 3) as qty
        from `tabBatch Production Record` bpr
        left join `tabStock Entry` se on se.name = bpr.stock_entry
        left join `tabItem` i on i.name = bpr.item_code
        where bpr.docstatus = 1
          and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        group by bpr.item_code, item_name
        order by qty desc, bpr.item_code asc
        limit 10
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_extruder_utilization(report_date) -> list[dict[str, object]]:
    job_card_columns = set(frappe.db.get_table_columns("Job Card") or [])
    if "workstation" not in job_card_columns:
        return []

    time_fields = [field for field in ("total_time_in_mins", "time_required") if field in job_card_columns]
    if not time_fields:
        return []

    machine_names = {machine["name"] for machine in PLANT_MACHINES}
    machine_aliases = {machine["alias"] for machine in PLANT_MACHINES}
    time_expr = "coalesce(" + ", ".join(f"jc.{field}" for field in time_fields) + ", 0)"
    date_field = "jc.posting_date" if "posting_date" in job_card_columns else "date(jc.modified)"

    rows = frappe.db.sql(
        f"""
        select
            coalesce(ws.workstation_name, jc.workstation) as workstation,
            round(sum({time_expr}), 2) as runtime_minutes
        from `tabJob Card` jc
        left join `tabWorkstation` ws on ws.name = jc.workstation
        where jc.docstatus < 2
          and {date_field} = %(report_date)s
        group by jc.workstation, workstation
        order by workstation asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )

    available_minutes = 24 * 60
    output = []
    for row in rows:
        workstation_name = row.workstation
        if workstation_name not in machine_names and workstation_name not in machine_aliases:
            continue
        runtime_minutes = flt(row.runtime_minutes)
        output.append(
            {
                "workstation": workstation_name,
                "runtime_minutes": runtime_minutes,
                "available_minutes": available_minutes,
                "utilization_pct": round(min((runtime_minutes / available_minutes) * 100, 100), 2),
            }
        )
    return output


def get_rm_consumption(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        """
        select
            sed.item_code,
            coalesce(i.item_name, sed.item_code) as item_name,
            round(sum(sed.qty), 3) as qty
        from `tabStock Entry Detail` sed
        inner join `tabStock Entry` se on se.name = sed.parent
        left join `tabItem` i on i.name = sed.item_code
        where se.docstatus = 1
          and ifnull(se.stock_entry_type, se.purpose) = 'Manufacture'
          and ifnull(sed.is_finished_item, 0) = 0
          and se.posting_date = %(report_date)s
        group by sed.item_code, item_name
        order by qty desc, sed.item_code asc
        limit 10
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_qc_failures(report_date) -> list[dict[str, object]]:
    output = OrderedDict(
        {
            "RM Hold": 0,
            "RM Rejected": 0,
            "Final QC Hold": 0,
            "Final QC Rejected": 0,
            "Quality Inspection Rejected": 0,
        }
    )

    rm_rows = frappe.db.sql(
        """
        select decision, count(*) as count
        from `tabRM QC Decision`
        where docstatus = 1
          and decision in ('Hold', 'Rejected')
          and date(modified) = %(report_date)s
        group by decision
        """,
        {"report_date": report_date},
        as_dict=True,
    )
    for row in rm_rows:
        output[f"RM {row.decision}"] = int(row.count)

    final_rows = frappe.db.sql(
        """
        select status, count(*) as count
        from `tabFinal QC Release`
        where docstatus = 1
          and status in ('Hold', 'Rejected')
          and date(ifnull(released_on, modified)) = %(report_date)s
        group by status
        """,
        {"report_date": report_date},
        as_dict=True,
    )
    for row in final_rows:
        output[f"Final QC {row.status}"] = int(row.count)

    inspection_count = frappe.db.count(
        "Quality Inspection",
        filters={
            "docstatus": 1,
            "status": "Rejected",
            "modified": ("between", [f"{report_date} 00:00:00", f"{report_date} 23:59:59"]),
        },
    )
    output["Quality Inspection Rejected"] = int(inspection_count)

    return [{"label": label, "count": count} for label, count in output.items()]


def get_dispatch_quantity(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        """
        select
            dni.item_code,
            coalesce(i.item_name, dni.item_code) as item_name,
            round(sum(dni.qty), 3) as qty
        from `tabDelivery Note Item` dni
        inner join `tabDelivery Note` dn on dn.name = dni.parent
        left join `tabItem` i on i.name = dni.item_code
        where dn.docstatus = 1
          and dn.posting_date = %(report_date)s
        group by dni.item_code, item_name
        order by qty desc, dni.item_code asc
        limit 10
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_cost_by_product(report_date) -> list[dict[str, object]]:
    return frappe.db.sql(
        """
        select
            prod.item_code,
            prod.item_name,
            round(sum(prod.produced_qty), 3) as produced_qty,
            round(sum(coalesce(rm.rm_cost_total, 0)), 2) as rm_cost_total,
            round(
                case when sum(prod.produced_qty) > 0
                    then sum(coalesce(rm.rm_cost_total, 0)) / sum(prod.produced_qty)
                    else 0
                end,
                4
            ) as rm_cost_per_kg,
            round(sum(coalesce(fg.fg_cost_total, coalesce(rm.rm_cost_total, 0))), 2) as manufacturing_cost_total,
            round(
                case when sum(prod.produced_qty) > 0
                    then sum(coalesce(fg.fg_cost_total, coalesce(rm.rm_cost_total, 0))) / sum(prod.produced_qty)
                    else 0
                end,
                4
            ) as manufacturing_cost_per_kg
        from (
            select
                bpr.stock_entry,
                bpr.item_code,
                coalesce(i.item_name, bpr.item_code) as item_name,
                bpr.produced_qty
            from `tabBatch Production Record` bpr
            left join `tabStock Entry` se on se.name = bpr.stock_entry
            left join `tabItem` i on i.name = bpr.item_code
            where bpr.docstatus = 1
              and ifnull(se.posting_date, date(bpr.modified)) = %(report_date)s
        ) prod
        left join (
            select
                sed.parent as stock_entry,
                sum(
                    abs(
                        coalesce(
                            sed.basic_amount,
                            sed.amount,
                            abs(sed.qty) * coalesce(sed.basic_rate, sed.valuation_rate, i.valuation_rate, 0)
                        )
                    )
                ) as rm_cost_total
            from `tabStock Entry Detail` sed
            inner join `tabStock Entry` se on se.name = sed.parent
            left join `tabItem` i on i.name = sed.item_code
            where se.docstatus = 1
              and ifnull(se.stock_entry_type, se.purpose) = 'Manufacture'
              and ifnull(sed.is_finished_item, 0) = 0
              and se.posting_date = %(report_date)s
            group by sed.parent
        ) rm on rm.stock_entry = prod.stock_entry
        left join (
            select
                sed.parent as stock_entry,
                sum(
                    abs(
                        coalesce(
                            sed.basic_amount,
                            sed.amount,
                            abs(sed.qty) * coalesce(sed.basic_rate, sed.valuation_rate, i.valuation_rate, 0)
                        )
                    )
                ) as fg_cost_total
            from `tabStock Entry Detail` sed
            inner join `tabStock Entry` se on se.name = sed.parent
            left join `tabItem` i on i.name = sed.item_code
            where se.docstatus = 1
              and ifnull(se.stock_entry_type, se.purpose) = 'Manufacture'
              and ifnull(sed.is_finished_item, 0) = 1
              and se.posting_date = %(report_date)s
            group by sed.parent
        ) fg on fg.stock_entry = prod.stock_entry
        group by prod.item_code, prod.item_name
        order by produced_qty desc, prod.item_code asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )


def get_profit_by_product(report_date, cost_by_product: list[dict[str, object]]) -> list[dict[str, object]]:
    if not cost_by_product:
        return []

    cost_map = {row["item_code"]: row for row in cost_by_product}
    dispatch_rows = frappe.db.sql(
        """
        select
            dni.item_code,
            coalesce(i.item_name, dni.item_code) as item_name,
            round(sum(dni.qty), 3) as dispatched_qty,
            round(sum(coalesce(dni.base_amount, dni.amount, dni.qty * dni.rate)), 2) as sales_amount_total,
            round(
                case when sum(dni.qty) > 0
                    then sum(coalesce(dni.base_amount, dni.amount, dni.qty * dni.rate)) / sum(dni.qty)
                    else 0
                end,
                4
            ) as selling_price_per_kg
        from `tabDelivery Note Item` dni
        inner join `tabDelivery Note` dn on dn.name = dni.parent
        left join `tabItem` i on i.name = dni.item_code
        where dn.docstatus = 1
          and dn.posting_date = %(report_date)s
        group by dni.item_code, item_name
        order by dispatched_qty desc, dni.item_code asc
        """,
        {"report_date": report_date},
        as_dict=True,
    )

    output = []
    for row in dispatch_rows:
        cost_row = cost_map.get(row["item_code"])
        if not cost_row:
            continue

        manufacturing_cost_per_kg = flt(cost_row.get("manufacturing_cost_per_kg"))
        dispatched_qty = flt(row.get("dispatched_qty"))
        selling_price_per_kg = flt(row.get("selling_price_per_kg"))
        gross_profit_per_kg = round(selling_price_per_kg - manufacturing_cost_per_kg, 4)
        gross_profit_total = round(gross_profit_per_kg * dispatched_qty, 2)

        output.append(
            {
                "item_code": row["item_code"],
                "item_name": row["item_name"],
                "dispatched_qty": dispatched_qty,
                "sales_amount_total": flt(row.get("sales_amount_total")),
                "selling_price_per_kg": selling_price_per_kg,
                "manufacturing_cost_per_kg": manufacturing_cost_per_kg,
                "gross_profit_per_kg": gross_profit_per_kg,
                "gross_profit_total": gross_profit_total,
            }
        )

    output.sort(key=lambda row: (-row["gross_profit_total"], row["item_code"]))
    return output
