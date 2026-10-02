from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate, today

from calco_erp.dashboard_utils import get_doc_route, get_list_route
from calco_erp.calco_production.production_execution import get_default_bom


FG_ITEM_GROUPS = ("Finished Goods", "Finished Good")
FG_WAREHOUSE_PATTERN = "%Finished Good%"
HEALTH_META = {
    "Critical": {"color": "red", "rank": 0},
    "Watch": {"color": "yellow", "rank": 1},
    "Healthy": {"color": "green", "rank": 2},
    "Surplus": {"color": "purple", "rank": 3},
}


def _clean_planning_kwargs(kwargs: dict[str, object] | None) -> dict[str, object]:
    cleaned = dict(kwargs or {})
    cleaned.pop("cmd", None)
    cleaned.pop("method", None)
    return cleaned


@frappe.whitelist()
def get_dashboard_data(*args, **kwargs) -> dict[str, object]:
    kwargs = _clean_planning_kwargs(kwargs)
    item_code = (kwargs.get("item_code") or "").strip()
    inventory_health = (kwargs.get("inventory_health") or "").strip()
    only_items_requiring_production = cint(kwargs.get("only_items_requiring_production") or 0)
    report_date = getdate(kwargs.get("report_date") or nowdate())
    month_start = report_date.replace(day=1)
    month_end = report_date.replace(day=calendar.monthrange(report_date.year, report_date.month)[1])

    filters = {
        "item_code": item_code,
        "inventory_health": inventory_health,
        "only_items_requiring_production": only_items_requiring_production,
    }

    items = get_fg_items(filters)
    item_codes = [row["item_code"] for row in items]
    stock_map = get_fg_stock_map(item_codes)
    forecast_map = get_sales_forecast_map(item_codes, month_start, month_end)
    sales_order_map = get_open_sales_order_map(item_codes, month_start, month_end)
    planned_production_map = get_planned_production_map(item_codes, month_start, month_end)

    rows = []
    for item in items:
        code = item["item_code"]
        row = build_dashboard_row(
            item=item,
            current_stock=flt(stock_map.get(code) or 0),
            forecast_qty=flt(forecast_map.get(code) or 0),
            open_sales_order_qty=flt(sales_order_map.get(code) or 0),
            planned_production_qty=flt(planned_production_map.get(code) or 0),
        )
        if not dashboard_row_matches_filters(row, filters):
            continue
        rows.append(row)

    rows.sort(key=lambda row: (HEALTH_META.get(row["inventory_health"], {}).get("rank", 9), row["item_code"]))
    return {
        "report_date": str(report_date),
        "period_start": str(month_start),
        "period_end": str(month_end),
        "warehouse_scope": "Finished Goods warehouses",
        "cards": build_dashboard_cards(rows),
        "rows": rows,
        "filters": filters,
        "formulas": {
            "planning_demand": "Sales Forecast Qty + Open Sales Order Qty",
            "available_qty": "Current FG Stock + Planned Production Qty",
            "available_percentage": "Available Qty / Planning Demand * 100",
            "production_requirement": "MAX(0, Planning Demand - Current FG Stock - Planned Production Qty)",
        },
    }


@frappe.whitelist()
def search_fg_items(txt: str | None = None, term: str | None = None, limit: int = 12, *args, **kwargs) -> list[dict[str, str]]:
    kwargs = _clean_planning_kwargs(kwargs)
    search_term = (txt or term or kwargs.get("txt") or kwargs.get("term") or "").strip()
    if not search_term:
        return []

    rows = frappe.get_all(
        "Item",
        filters={"disabled": 0, "item_group": ("in", get_fg_item_groups())},
        or_filters=[
            ["Item", "name", "like", f"%{search_term}%"],
            ["Item", "item_name", "like", f"%{search_term}%"],
        ],
        fields=["name as item_code", "item_name"],
        order_by="name asc",
        limit_page_length=max(1, min(cint(limit or 12), 20)),
    )
    return [
        {
            "value": row["item_code"],
            "label": f'{row["item_code"]} - {row.get("item_name") or ""}'.strip(" -"),
            "item_code": row["item_code"],
            "item_name": row.get("item_name") or "",
        }
        for row in rows
    ]


@frappe.whitelist()
def create_draft_work_orders_from_basket(rows_json: str | None = None, *args, **kwargs) -> dict[str, object]:
    kwargs = _clean_planning_kwargs(kwargs)
    rows_json = rows_json or kwargs.get("rows_json")
    basket_rows = json.loads(rows_json or "[]")
    valid_rows = [row for row in basket_rows if flt(row.get("production_requirement") or row.get("suggested_work_order_qty") or 0) > 0]
    if not valid_rows:
        frappe.throw(_("No basket rows with a positive Production Requirement were selected."))

    work_orders = []
    for row in valid_rows:
        item_code = (row.get("item_code") or "").strip()
        qty = flt(row.get("production_requirement") or row.get("suggested_work_order_qty") or 0)
        if not item_code or qty <= 0:
            continue
        work_orders.append(create_draft_work_order(item_code=item_code, qty=qty))

    if not work_orders:
        frappe.throw(_("No draft Work Orders were created."))

    return {
        "work_orders": work_orders,
        "count": len(work_orders),
        "route": ["Form", "Work Order", work_orders[0]["name"]] if len(work_orders) == 1 else ["List", "Work Order"],
    }


def get_fg_items(filters: dict[str, object]) -> list[dict[str, object]]:
    query_args = {
        "doctype": "Item",
        "filters": {"disabled": 0, "item_group": ("in", get_fg_item_groups())},
        "fields": ["name as item_code", "item_name", "item_group as category", "stock_uom"],
        "order_by": "name asc",
    }
    if filters.get("item_code"):
        search_term = str(filters["item_code"]).strip()
        query_args["or_filters"] = [
            ["Item", "name", "like", f"%{search_term}%"],
            ["Item", "item_name", "like", f"%{search_term}%"],
        ]
    return frappe.get_all(**query_args)


def get_fg_item_groups() -> list[str]:
    groups = set()
    for group in FG_ITEM_GROUPS:
        if frappe.db.exists("Item Group", group):
            groups.add(group)
            group_bounds = frappe.db.get_value("Item Group", group, ["lft", "rgt"], as_dict=True)
            if group_bounds:
                child_groups = frappe.get_all(
                    "Item Group",
                    filters={"lft": (">=", group_bounds.lft), "rgt": ("<=", group_bounds.rgt)},
                    pluck="name",
                )
                groups.update(child_groups)
    if not groups:
        groups.update(FG_ITEM_GROUPS)
    return sorted(groups)


def get_fg_warehouses() -> list[str]:
    if not frappe.db.exists("DocType", "Warehouse"):
        return []
    warehouses = frappe.get_all("Warehouse", filters={"name": ("like", FG_WAREHOUSE_PATTERN), "is_group": 0}, pluck="name")
    if not warehouses:
        warehouses = frappe.get_all("Warehouse", filters={"name": ("like", FG_WAREHOUSE_PATTERN)}, pluck="name")
    return warehouses


def get_fg_stock_map(item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    warehouses = get_fg_warehouses()
    if not warehouses:
        return {}
    rows = frappe.db.sql(
        """
        select item_code, round(sum(actual_qty), 3) as qty
        from `tabBin`
        where item_code in %(item_codes)s
          and warehouse in %(warehouses)s
        group by item_code
        """,
        {"item_codes": tuple(item_codes), "warehouses": tuple(warehouses)},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def get_sales_forecast_map(item_codes: list[str], month_start: date, month_end: date) -> dict[str, float]:
    if not item_codes or not frappe.db.exists("DocType", "Production Requirement Item"):
        return {}
    rows = frappe.db.sql(
        """
        select pri.item_code, round(sum(coalesce(pri.requested_qty, 0)), 3) as qty
        from `tabProduction Requirement Item` pri
        inner join `tabProduction Requirement` pr on pr.name = pri.parent
        where pr.docstatus < 2
          and coalesce(pr.status, '') not in ('Closed', 'Cancelled')
          and pri.source_type = 'Forecast'
          and pri.item_code in %(item_codes)s
          and date(coalesce(pri.target_date, pr.week_start_date)) between %(month_start)s and %(month_end)s
        group by pri.item_code
        """,
        {"item_codes": tuple(item_codes), "month_start": month_start, "month_end": month_end},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def get_open_sales_order_map(item_codes: list[str], month_start: date, month_end: date) -> dict[str, float]:
    if not item_codes or not frappe.db.exists("DocType", "Sales Order Item"):
        return {}
    rows = frappe.db.sql(
        """
        select
            soi.item_code,
            round(sum(greatest(coalesce(soi.qty, 0) - coalesce(soi.delivered_qty, 0), 0)), 3) as qty
        from `tabSales Order Item` soi
        inner join `tabSales Order` so on so.name = soi.parent
        where so.docstatus = 1
          and coalesce(so.status, '') not in ('Closed', 'Completed', 'Cancelled')
          and soi.item_code in %(item_codes)s
          and greatest(coalesce(soi.qty, 0) - coalesce(soi.delivered_qty, 0), 0) > 0
          and date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date)) between %(month_start)s and %(month_end)s
        group by soi.item_code
        """,
        {"item_codes": tuple(item_codes), "month_start": month_start, "month_end": month_end},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def get_planned_production_map(item_codes: list[str], month_start: date, month_end: date) -> dict[str, float]:
    planned = defaultdict(float)
    for item_code, qty in get_open_work_order_map(item_codes).items():
        planned[item_code] += qty
    for item_code, qty in get_production_plan_map(item_codes, month_start, month_end).items():
        planned[item_code] += qty
    return {item_code: round(qty, 3) for item_code, qty in planned.items()}


def get_open_work_order_map(item_codes: list[str]) -> dict[str, float]:
    if not item_codes or not frappe.db.exists("DocType", "Work Order"):
        return {}
    rows = frappe.db.sql(
        """
        select
            production_item as item_code,
            round(sum(greatest(coalesce(qty, 0) - coalesce(produced_qty, 0), 0)), 3) as qty
        from `tabWork Order`
        where docstatus < 2
          and production_item in %(item_codes)s
          and coalesce(status, '') not in ('Stopped', 'Completed', 'Closed', 'Cancelled')
          and greatest(coalesce(qty, 0) - coalesce(produced_qty, 0), 0) > 0
        group by production_item
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def get_production_plan_map(item_codes: list[str], month_start: date, month_end: date) -> dict[str, float]:
    if not item_codes or not frappe.db.exists("DocType", "Production Plan Item"):
        return {}
    rows = frappe.db.sql(
        """
        select ppi.item_code, round(sum(coalesce(ppi.planned_qty, 0)), 3) as qty
        from `tabProduction Plan Item` ppi
        inner join `tabProduction Plan` pp on pp.name = ppi.parent
        where pp.docstatus < 2
          and ppi.item_code in %(item_codes)s
          and date(coalesce(ppi.planned_start_date, pp.from_date, pp.posting_date)) between %(month_start)s and %(month_end)s
        group by ppi.item_code
        """,
        {"item_codes": tuple(item_codes), "month_start": month_start, "month_end": month_end},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def build_dashboard_row(item: dict[str, object], current_stock: float, forecast_qty: float, open_sales_order_qty: float, planned_production_qty: float) -> dict[str, object]:
    planning_demand = round(forecast_qty + open_sales_order_qty, 3)
    available_qty = round(current_stock + planned_production_qty, 3)
    available_percentage = round((available_qty / planning_demand) * 100, 2) if planning_demand > 0 else 100.0
    production_requirement = round(max(0, planning_demand - current_stock - planned_production_qty), 3)
    inventory_health = classify_inventory_health(available_percentage)
    planning_reason = get_planning_reason(
        production_requirement=production_requirement,
        current_stock=current_stock,
        planned_production_qty=planned_production_qty,
        forecast_qty=forecast_qty,
        open_sales_order_qty=open_sales_order_qty,
        planning_demand=planning_demand,
    )

    return {
        "item_code": item["item_code"],
        "item_name": item.get("item_name") or "",
        "category": item.get("category") or "",
        "stock_uom": item.get("stock_uom") or "",
        "current_fg_stock": round(current_stock, 3),
        "planning_demand": planning_demand,
        "sales_forecast_qty": round(forecast_qty, 3),
        "open_sales_order_qty": round(open_sales_order_qty, 3),
        "planned_production_qty": round(planned_production_qty, 3),
        "available_qty": available_qty,
        "available_percentage": available_percentage,
        "inventory_health": inventory_health,
        "inventory_health_color": HEALTH_META[inventory_health]["color"],
        "production_requirement": production_requirement,
        "suggested_work_order_qty": production_requirement,
        "planning_reason": planning_reason,
        "item_route": get_doc_route("Item", item["item_code"]),
        "stock_ledger_route": get_list_route("Stock Ledger Entry"),
        "work_order_route": get_list_route("Work Order"),
        "sales_order_route": get_list_route("Sales Order"),
    }


def classify_inventory_health(available_percentage: float) -> str:
    if available_percentage >= 100:
        return "Surplus"
    if available_percentage >= 67:
        return "Healthy"
    if available_percentage >= 34:
        return "Watch"
    return "Critical"


def get_planning_reason(
    production_requirement: float,
    current_stock: float,
    planned_production_qty: float,
    forecast_qty: float,
    open_sales_order_qty: float,
    planning_demand: float,
) -> str:
    if production_requirement <= 0:
        if planning_demand > 0 and planned_production_qty > 0 and current_stock < planning_demand:
            return "Already covered by planned production"
        return "Surplus / no production needed"
    if open_sales_order_qty > 0:
        return "Sales Order demand"
    if forecast_qty > 0:
        return "Forecast shortage"
    return "Forecast shortage"


def dashboard_row_matches_filters(row: dict[str, object], filters: dict[str, object]) -> bool:
    if filters.get("item_code"):
        needle = str(filters["item_code"]).strip().lower()
        haystack = f"{row.get('item_code') or ''} {row.get('item_name') or ''}".lower()
        if needle not in haystack:
            return False
    if filters.get("inventory_health") and row["inventory_health"] != filters["inventory_health"]:
        return False
    if filters.get("only_items_requiring_production") and flt(row.get("production_requirement") or 0) <= 0:
        return False
    return True


def build_dashboard_cards(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"label": "FG Items", "value": len(rows), "suffix": ""},
        {"label": "Critical", "value": sum(1 for row in rows if row["inventory_health"] == "Critical"), "suffix": ""},
        {"label": "Watch", "value": sum(1 for row in rows if row["inventory_health"] == "Watch"), "suffix": ""},
        {"label": "Healthy", "value": sum(1 for row in rows if row["inventory_health"] == "Healthy"), "suffix": ""},
        {"label": "Surplus", "value": sum(1 for row in rows if row["inventory_health"] == "Surplus"), "suffix": ""},
        {"label": "Production Requirement", "value": round(sum(flt(row.get("production_requirement") or 0) for row in rows), 3), "suffix": ""},
    ]


def create_draft_work_order(item_code: str, qty: float) -> dict[str, object]:
    bom_no = get_default_bom(item_code)
    if not bom_no:
        frappe.throw(_("No submitted active BOM found for FG Item {0}.").format(item_code))

    meta = frappe.get_meta("Work Order")
    doc = frappe.new_doc("Work Order")
    doc.production_item = item_code
    doc.bom_no = bom_no
    doc.qty = qty
    doc.company = get_default_company()
    if meta.has_field("planned_start_date"):
        doc.planned_start_date = getdate(today())
    if meta.has_field("fg_warehouse"):
        doc.fg_warehouse = get_default_fg_warehouse()
    if meta.has_field("wip_warehouse"):
        doc.wip_warehouse = get_default_wip_warehouse()
    if meta.has_field("source_warehouse"):
        doc.source_warehouse = get_default_rm_warehouse()
    if meta.has_field("custom_production_stage"):
        doc.custom_production_stage = "Material Transfer Pending"
    doc.insert()
    return {"name": doc.name, "route": ["Form", "Work Order", doc.name], "item_code": item_code, "qty": qty}


def get_default_company() -> str:
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
    if company:
        return company
    company = frappe.db.get_value("Company", {}, "name")
    if not company:
        frappe.throw(_("Default Company is required before creating draft Work Orders."))
    return company


def get_default_fg_warehouse() -> str:
    warehouses = get_fg_warehouses()
    return warehouses[0] if warehouses else ""


def get_default_wip_warehouse() -> str:
    return (
        frappe.db.get_value("Warehouse", {"name": ("like", "%WIP%"), "is_group": 0}, "name")
        or frappe.db.get_value("Warehouse", {"name": ("like", "%Work In Progress%"), "is_group": 0}, "name")
        or ""
    )


def get_default_rm_warehouse() -> str:
    return (
        frappe.db.get_value("Warehouse", {"name": "Stores - CPPL"}, "name")
        or frappe.db.get_value("Warehouse", {"name": ("like", "%Stores%"), "is_group": 0}, "name")
        or ""
    )
