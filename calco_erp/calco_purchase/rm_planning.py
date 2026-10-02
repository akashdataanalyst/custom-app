from __future__ import annotations

import calendar
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from calco_erp.dashboard_utils import get_doc_route, get_list_route
from calco_erp.calco_purchase.rm_planning_dynamic import (
    get_dynamic_demand_profiles,
    get_three_month_forecast_profiles,
)


RM_ITEM_GROUP = "Raw Material"
RM_STORE_WAREHOUSE = "Stores - CPPL"
APPROVED_MATRIX_STATUSES = ("Approved", "Conditional Approval")
MAXIMUM_INVENTORY_MODE_CONFIG_KEY = "calco_rm_maximum_inventory_mode"
MAXIMUM_INVENTORY_MODES = {"legacy", "dynamic"}
DEFAULT_MAXIMUM_INVENTORY_MODE = "dynamic"
HEALTH_META = {
    "Critical": {"color": "red", "rank": 0},
    "Low": {"color": "yellow", "rank": 1},
    "Healthy": {"color": "green", "rank": 2},
    "Overstock": {"color": "purple", "rank": 3},
}


@dataclass
class LeadTimeStat:
    average_days: float = 0.0
    minimum_days: float = 0.0
    maximum_days: float = 0.0
    last_three: list[float] | None = None
    on_time_percentage: float = 0.0
    sample_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "average_days": round(self.average_days, 2),
            "minimum_days": round(self.minimum_days, 2),
            "maximum_days": round(self.maximum_days, 2),
            "last_three": [round(value, 2) for value in (self.last_three or [])],
            "on_time_percentage": round(self.on_time_percentage, 2),
            "sample_count": self.sample_count,
        }


def _clean_planning_kwargs(kwargs: dict[str, object] | None) -> dict[str, object]:
    cleaned = dict(kwargs or {})
    cleaned.pop("cmd", None)
    cleaned.pop("method", None)
    return cleaned


def get_maximum_inventory_mode() -> str:
    configured_mode = (
        str(
            (frappe.conf or {}).get(
                MAXIMUM_INVENTORY_MODE_CONFIG_KEY,
                DEFAULT_MAXIMUM_INVENTORY_MODE,
            )
        )
        .strip()
        .lower()
    )
    if configured_mode not in MAXIMUM_INVENTORY_MODES:
        frappe.throw(
            _("Invalid {0}: {1}. Expected legacy or dynamic.").format(
                MAXIMUM_INVENTORY_MODE_CONFIG_KEY,
                configured_mode,
            )
        )
    return configured_mode


@frappe.whitelist()
def get_dashboard_data(*args, **kwargs) -> dict[str, object]:
    kwargs = _clean_planning_kwargs(kwargs)
    category = kwargs.get("category")
    item_code = kwargs.get("item_code")
    supplier = kwargs.get("supplier")
    inventory_health = kwargs.get("inventory_health")
    supplier_type = kwargs.get("supplier_type")
    current_season = kwargs.get("current_season")
    only_items_requiring_purchase = kwargs.get("only_items_requiring_purchase", 0)
    only_critical_items = kwargs.get("only_critical_items", 0)
    report_date = kwargs.get("report_date")
    requested_item_codes = kwargs.get("item_codes") or []
    if isinstance(requested_item_codes, str):
        requested_item_codes = json.loads(requested_item_codes or "[]")
    requested_item_codes = sorted(
        {str(code).strip() for code in requested_item_codes if str(code).strip()}
    )

    planning_date = getdate(report_date or nowdate())
    month_start = planning_date.replace(day=1)
    month_end = planning_date.replace(day=calendar.monthrange(planning_date.year, planning_date.month)[1])
    month_days = month_end.day

    filters = {
        "category": (category or "").strip(),
        "item_code": (item_code or "").strip(),
        "supplier": (supplier or "").strip(),
        "inventory_health": (inventory_health or "").strip(),
        "supplier_type": (supplier_type or "").strip(),
        "current_season": (current_season or "").strip(),
        "only_items_requiring_purchase": cint(only_items_requiring_purchase),
        "only_critical_items": cint(only_critical_items),
    }

    items = get_rm_items(filters, requested_item_codes)
    item_codes = [row["item_code"] for row in items]
    parameters = get_planning_parameter_map(item_codes)
    projection_map = get_monthly_projection_requirement_map(month_start, month_end)
    stock_map = get_store_stock_map(item_codes)
    open_po_map = get_open_po_map(item_codes)
    open_material_request_map = get_open_material_request_map(item_codes)
    reorder_map = get_item_reorder_defaults(item_codes)
    supplier_rows = get_supplier_matrix_rows(item_codes)
    lead_time_data = get_lead_time_data(item_codes)
    maximum_inventory_mode = get_maximum_inventory_mode()
    demand_profiles = (
        get_dynamic_demand_profiles(item_codes, planning_date)
        if maximum_inventory_mode == "dynamic"
        else {}
    )
    forecast_horizon = get_three_month_forecast_profiles(item_codes, planning_date)
    purchase_price_map, price_currency = get_latest_purchase_price_map(
        item_codes,
        planning_date,
    )

    rows = []
    for item in items:
        code = item["item_code"]
        parameter = parameters.get(code, {})
        supplier_profile = resolve_supplier_profile(code, parameter, supplier_rows.get(code, []))
        lead_stats = resolve_lead_time_stats(code, supplier_profile.get("supplier"), lead_time_data)
        row = build_dashboard_row(
            item=item,
            parameter=parameter,
            supplier_profile=supplier_profile,
            lead_stats=lead_stats,
            projection_qty=flt(projection_map.get(code) or 0),
            month_days=month_days,
            current_stock=flt(stock_map.get(code) or 0),
            open_po_qty=flt(open_po_map.get(code) or 0),
            open_material_request_qty=flt(open_material_request_map.get(code) or 0),
            reorder_defaults=reorder_map.get(code, {}),
            planning_date=planning_date,
            purchase_price=purchase_price_map.get(code),
            demand_profile=demand_profiles.get(code, {}),
            forecast_profile=(forecast_horizon.get("items") or {}).get(code, {}),
            maximum_inventory_mode=maximum_inventory_mode,
        )
        if not dashboard_row_matches_filters(row, filters):
            continue
        rows.append(row)

    rows.sort(key=lambda row: (HEALTH_META.get(row["inventory_health"], {}).get("rank", 9), row["item_code"]))
    analytics = build_lead_time_analytics(rows, lead_time_data)
    cards = build_dashboard_cards(rows)
    total_estimated_purchase_value = round(
        sum(
            flt(row.get("estimated_purchase_value") or 0)
            for row in rows
            if flt(row.get("suggested_order_qty") or 0) > 0
            and row.get("has_latest_purchase_price")
        ),
        2,
    )

    return {
        "report_date": str(planning_date),
        "month_days": month_days,
        "forecast_months": forecast_horizon.get("months") or [],
        "forecast_available_months": forecast_horizon.get("available_months") or 0,
        "forecast_coverage": forecast_horizon.get("forecast_coverage") or {},
        "bom_coverage": forecast_horizon.get("bom_coverage") or {},
        "planning_confidence": forecast_horizon.get("planning_confidence") or "Forecast Missing",
        "blocked_forecast_items": forecast_horizon.get("blocked_forecast_items") or [],
        "blocked_forecast_qty": flt(forecast_horizon.get("blocked_forecast_qty") or 0),
        "warehouse": RM_STORE_WAREHOUSE,
        "cards": cards,
        "rows": rows,
        "total_estimated_purchase_value": total_estimated_purchase_value,
        "price_currency": price_currency,
        "lead_time_analytics": analytics,
        "filters": filters,
        "formulas": {
            "safety_stock": "Selected Daily Consumption × Safety Days",
            "reorder_level": "(Selected Daily Consumption × Lead Time Days) + Safety Stock",
            "maximum_level": "Reorder Level + (Selected Daily Consumption × Review Period Days)",
            "projected_available_qty": "Current RM Store Stock + Open PO / In Transit Qty",
            "coverage_days": "Projected Available Qty / Selected Daily Consumption",
            "three_month_forecast": "This Month + Next Month + Next + 1 Month",
            "physical_procurement_gap": "MAX(0, 3-Month Forecast - Available Inventory - Open PO / In Transit)",
            "suggested_order_qty": "MAX(0, Physical Procurement Gap - Open Unconverted MR), then Pack Size and MOQ",
        },
    }


@frappe.whitelist()
def create_material_request(
    item_code: str | None = None,
    qty=None,
    required_by: str | None = None,
    warehouse: str | None = None,
    *args,
    **kwargs,
) -> dict[str, object]:
    kwargs = _clean_planning_kwargs(kwargs)
    item_code = str(item_code or kwargs.get("item_code") or "").strip()
    if not item_code:
        frappe.throw(_("Item Code is required."))

    _lock_planning_items([item_code])
    _assert_no_draft_material_requests([item_code])
    row = _recompute_material_request_rows([item_code]).get(item_code)
    if not row:
        frappe.throw(_("Active Raw Material {0} was not found.").format(item_code))
    if flt(row.get("net_purchase_requirement")) <= 0:
        frappe.throw(
            _("The current Net Purchase Requirement for {0} is zero. No Material Request was created.").format(
                item_code
            )
        )

    mr = frappe.get_doc(
        {
            "doctype": "Material Request",
            "material_request_type": "Purchase",
            "schedule_date": getdate(row["required_by_date"]),
            "items": [_build_material_request_item(row)],
        }
    )
    mr.insert()
    return {
        "name": mr.name,
        "route": ["Form", "Material Request", mr.name],
        "recomputed_qty": flt(row["net_purchase_requirement"]),
        "stock_uom": row.get("stock_uom") or "",
    }


@frappe.whitelist()
def create_bulk_material_requests(
    rows_json: str | None = None, *args, **kwargs
) -> dict[str, object]:
    kwargs = _clean_planning_kwargs(kwargs)
    rows_json = rows_json or kwargs.get("rows_json")
    requested_rows = json.loads(rows_json or "[]")
    item_codes = sorted(
        {
            str(row.get("item_code") or "").strip()
            for row in requested_rows
            if str(row.get("item_code") or "").strip()
        }
    )
    if not item_codes:
        frappe.throw(_("No Raw Material items were selected."))

    _lock_planning_items(item_codes)
    _assert_no_draft_material_requests(item_codes)
    recalculated = _recompute_material_request_rows(item_codes)
    rows = [
        recalculated[code]
        for code in item_codes
        if code in recalculated
        and flt(recalculated[code].get("net_purchase_requirement")) > 0
    ]
    if not rows:
        frappe.throw(
            _("All selected items now have zero Net Purchase Requirement. No Material Request was created.")
        )

    schedule_date = min(getdate(row["required_by_date"]) for row in rows)
    mr = frappe.get_doc(
        {
            "doctype": "Material Request",
            "material_request_type": "Purchase",
            "schedule_date": schedule_date,
            "items": [_build_material_request_item(row) for row in rows],
        }
    )
    mr.insert()
    return {
        "name": mr.name,
        "route": ["Form", "Material Request", mr.name],
        "recomputed_items": [
            {
                "item_code": row["item_code"],
                "qty": flt(row["net_purchase_requirement"]),
                "stock_uom": row.get("stock_uom") or "",
            }
            for row in rows
        ],
    }


def _lock_planning_items(item_codes: list[str]) -> None:
    if not item_codes:
        return
    locked = frappe.db.sql(
        """
        select name
        from `tabItem`
        where name in %(item_codes)s
        order by name
        for update
        """,
        {"item_codes": tuple(sorted(set(item_codes)))},
        as_dict=True,
    )
    locked_names = {row["name"] for row in locked}
    missing = sorted(set(item_codes) - locked_names)
    if missing:
        frappe.throw(_("Items not found: {0}").format(", ".join(missing)))


def _assert_no_draft_material_requests(item_codes: list[str]) -> None:
    rows = frappe.db.sql(
        """
        select mri.item_code, group_concat(distinct mr.name order by mr.name) as requests
        from `tabMaterial Request Item` mri
        inner join `tabMaterial Request` mr on mr.name = mri.parent
        where mr.docstatus = 0
          and ifnull(mr.material_request_type, '') = 'Purchase'
          and mri.item_code in %(item_codes)s
          and ifnull(mri.warehouse, '') = %(warehouse)s
          and greatest(
                ifnull(mri.stock_qty, mri.qty * ifnull(nullif(mri.conversion_factor, 0), 1)),
                0
              ) > 0
        group by mri.item_code
        """,
        {"item_codes": tuple(item_codes), "warehouse": RM_STORE_WAREHOUSE},
        as_dict=True,
    )
    if rows:
        details = "; ".join(
            f"{row['item_code']}: {row['requests']}" for row in rows
        )
        frappe.throw(
            _("A Draft Purchase Material Request already covers the selected item(s): {0}").format(
                details
            )
        )


def _recompute_material_request_rows(
    item_codes: list[str],
) -> dict[str, dict[str, object]]:
    result = get_dashboard_data(item_codes=item_codes, report_date=nowdate())
    return {row["item_code"]: row for row in result.get("rows") or []}


def _build_material_request_item(row: dict[str, object]) -> dict[str, object]:
    quantity = flt(row.get("net_purchase_requirement") or 0)
    stock_uom = row.get("stock_uom") or ""
    return {
        "item_code": row["item_code"],
        "qty": quantity,
        "uom": stock_uom,
        "stock_uom": stock_uom,
        "conversion_factor": 1,
        "warehouse": row.get("warehouse") or RM_STORE_WAREHOUSE,
        "schedule_date": getdate(row["required_by_date"]),
    }
@frappe.whitelist()
def search_rm_items(txt: str | None = None, term: str | None = None, limit: int = 12, *args, **kwargs) -> list[dict[str, str]]:
    kwargs = _clean_planning_kwargs(kwargs)
    search_term = (txt or term or kwargs.get("txt") or kwargs.get("term") or "").strip()
    if not search_term:
        return []

    rows = frappe.get_all(
        "Item",
        filters={"disabled": 0, "item_group": RM_ITEM_GROUP},
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


def get_rm_items(
    filters: dict[str, object], item_codes: list[str] | None = None
) -> list[dict[str, object]]:
    item_filters = {"disabled": 0, "item_group": RM_ITEM_GROUP}
    if item_codes:
        item_filters["name"] = ("in", item_codes)
    if filters.get("category"):
        item_filters["item_group"] = filters["category"]
    item_search = (filters.get("item_code") or "").strip()

    query_args = {
        "doctype": "Item",
        "filters": item_filters,
        "fields": ["name as item_code", "item_name", "item_group as category", "stock_uom", "lead_time_days", "safety_stock"],
        "order_by": "name asc",
    }
    if item_search:
        query_args["or_filters"] = [
            ["Item", "name", "like", f"%{item_search}%"],
            ["Item", "item_name", "like", f"%{item_search}%"],
        ]

    rows = frappe.get_all(**query_args)
    return rows


def get_latest_purchase_price_map(
    item_codes: list[str],
    planning_date: date,
) -> tuple[dict[str, dict[str, object]], str]:
    if not item_codes:
        return {}, ""

    company = frappe.db.get_value("Warehouse", RM_STORE_WAREHOUSE, "company") or ""
    company_currency = (
        frappe.db.get_value("Company", company, "default_currency") if company else ""
    ) or ""
    if not company or not company_currency:
        return {}, company_currency

    price_map = get_latest_purchase_receipt_inward_costs(
        item_codes,
        company=company,
        company_currency=company_currency,
        planning_date=planning_date,
    )
    return price_map, company_currency


def get_latest_purchase_receipt_inward_costs(
    item_codes: list[str],
    *,
    company: str,
    company_currency: str,
    planning_date: date,
) -> dict[str, dict[str, object]]:
    """Return the final operational inward cost from the latest eligible PR."""
    if not item_codes:
        return {}

    from calco_erp.calco_purchase.import_landed_cost import (
        ELIGIBLE_CHARGES,
        IMPORT_FLAG,
        calculate_estimated_landed_cost,
    )

    candidates = frappe.db.sql(
        """
        select distinct
            parent.name,
            parent.posting_date,
            parent.posting_time
        from `tabPurchase Receipt` parent
        inner join `tabPurchase Receipt Item` child on child.parent = parent.name
        where parent.docstatus = 1
          and ifnull(parent.is_return, 0) = 0
          and parent.company = %(company)s
          and parent.posting_date <= %(planning_date)s
          and child.item_code in %(item_codes)s
          and child.stock_qty > 0
        order by
            parent.posting_date desc,
            parent.posting_time desc,
            parent.name desc
        """,
        {
            "company": company,
            "planning_date": planning_date,
            "item_codes": tuple(item_codes),
        },
        as_dict=True,
    )

    requested = set(item_codes)
    history: dict[str, list[dict[str, object]]] = defaultdict(list)
    for candidate in candidates:
        if all(len(history.get(item_code, ())) >= 2 for item_code in requested):
            break

        pr = frappe.get_doc("Purchase Receipt", candidate["name"])
        rows_by_item: dict[str, list[object]] = defaultdict(list)
        for row in pr.get("items") or []:
            item_code = row.get("item_code")
            if item_code in requested and _decimal_price(row.get("stock_qty")) > 0:
                rows_by_item[item_code].append(row)

        import_costs: dict[str, list[dict[str, object]]] = defaultdict(list)
        allocation_method = ""
        has_import_charge_evidence = any(
            _decimal_price(pr.get(fieldname)) != 0
            for fieldname, _label, _account_field in ELIGIBLE_CHARGES
        )
        if cint(pr.get(IMPORT_FLAG)) and has_import_charge_evidence:
            cost = calculate_estimated_landed_cost(pr)
            if cost.get("calculation_status") == "Calculated":
                allocation_method = cost.get("allocation_method") or ""
                for row in cost.get("item_costs") or []:
                    if row.get("item_code") in requested:
                        import_costs[row["item_code"]].append(row)

        for item_code, item_rows in rows_by_item.items():
            if len(history[item_code]) >= 2:
                continue

            accepted_qty = sum(
                _decimal_price(row.get("stock_qty")) for row in item_rows
            )
            base_material_value = sum(
                _decimal_price(row.get("base_net_amount") or row.get("base_amount"))
                for row in item_rows
            )
            if accepted_qty <= 0 or base_material_value <= 0:
                continue

            inward_value = base_material_value
            cost_basis = "Base Material Rate"
            if import_costs.get(item_code):
                import_qty = sum(
                    _decimal_price(row.get("accepted_stock_qty"))
                    for row in import_costs[item_code]
                )
                import_value = sum(
                    _decimal_price(row.get("rm_inward_value"))
                    for row in import_costs[item_code]
                )
                if import_qty > 0 and import_value > 0:
                    accepted_qty = import_qty
                    inward_value = import_value
                    cost_basis = "Import Inward Cost"

            supplier_amount = sum(
                _decimal_price(row.get("net_amount") or row.get("amount"))
                for row in item_rows
            )
            stock_uom = item_rows[0].get("stock_uom") or ""
            history[item_code].append(
                {
                    "rate": float(inward_value / accepted_qty),
                    "currency": company_currency,
                    "stock_uom": stock_uom,
                    "price_date": str(pr.posting_date or ""),
                    "posting_time": str(pr.posting_time or ""),
                    "supplier": pr.supplier or "",
                    "source": "Purchase Receipt",
                    "source_name": pr.name,
                    "source_currency": pr.currency or "",
                    "source_rate": float(supplier_amount / accepted_qty)
                    if supplier_amount > 0
                    else 0,
                    "source_uom": stock_uom,
                    "source_conversion_rate": flt(pr.conversion_rate or 0),
                    "cost_basis": cost_basis,
                    "allocation_method": allocation_method,
                    "accepted_stock_qty": float(accepted_qty),
                    "base_material_value": float(base_material_value),
                    "import_charges": float(max(0, inward_value - base_material_value)),
                }
            )

    prices: dict[str, dict[str, object]] = {}
    for item_code, item_history in history.items():
        if not item_history:
            continue
        latest = item_history[0]
        if len(item_history) > 1:
            previous = item_history[1]
            latest["trend"] = _build_purchase_price_trend(
                latest["rate"],
                previous["rate"],
                latest["price_date"],
                previous["price_date"],
                "Purchase Receipt",
            )
        prices[item_code] = latest
    return prices


def get_latest_import_inward_costs(
    item_codes: list[str],
    *,
    company: str,
    company_currency: str,
    planning_date: date,
) -> dict[str, dict[str, object]]:
    """Return reproducible operational costs from submitted Import PR evidence."""
    if not item_codes:
        return {}

    from calco_erp.calco_purchase.import_landed_cost import (
        IMPORT_FLAG,
        calculate_estimated_landed_cost,
    )

    candidates = frappe.db.sql(
        f"""
        select distinct
            parent.name,
            parent.posting_date,
            parent.posting_time,
            parent.modified
        from `tabPurchase Receipt` parent
        inner join `tabPurchase Receipt Item` child on child.parent = parent.name
        inner join `tabSupplier` supplier
            on supplier.name = parent.supplier
            and ifnull(supplier.disabled, 0) = 0
        where parent.docstatus = 1
          and ifnull(parent.is_return, 0) = 0
          and ifnull(parent.{IMPORT_FLAG}, 0) = 1
          and parent.company = %(company)s
          and parent.posting_date <= %(planning_date)s
          and child.item_code in %(item_codes)s
        order by
            parent.posting_date desc,
            parent.posting_time desc,
            parent.modified desc,
            parent.name desc
        """,
        {
            "company": company,
            "planning_date": planning_date,
            "item_codes": tuple(item_codes),
        },
        as_dict=True,
    )
    requested = set(item_codes)
    history: dict[str, list[dict[str, object]]] = defaultdict(list)
    for candidate in candidates:
        pr = frappe.get_doc("Purchase Receipt", candidate["name"])
        cost = calculate_estimated_landed_cost(pr)
        if cost.get("calculation_status") != "Calculated":
            continue

        by_item: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in cost.get("item_costs") or []:
            if row.get("item_code") in requested:
                by_item[row["item_code"]].append(row)
        for item_code, rows in by_item.items():
            accepted_qty = sum(
                _decimal_price(row.get("accepted_stock_qty")) for row in rows
            )
            inward_value = sum(
                _decimal_price(row.get("rm_inward_value")) for row in rows
            )
            if accepted_qty <= 0 or inward_value <= 0:
                continue
            rate = float(inward_value / accepted_qty)
            first = rows[0]
            history[item_code].append(
                {
                    "rate": rate,
                    "currency": company_currency,
                    "stock_uom": first.get("stock_uom") or "",
                    "price_date": str(pr.posting_date or ""),
                    "supplier": pr.supplier or "",
                    "source": "Import Inward Cost",
                    "source_name": pr.name,
                    "source_currency": pr.currency or "",
                    "source_rate": flt(first.get("supplier_rate") or 0),
                    "source_uom": first.get("stock_uom") or "",
                    "source_conversion_rate": flt(pr.conversion_rate or 0),
                    "allocation_method": cost.get("allocation_method") or "",
                }
            )

    prices = {}
    for item_code, item_history in history.items():
        latest = item_history[0]
        if len(item_history) > 1:
            previous = item_history[1]
            latest["trend"] = _build_purchase_price_trend(
                latest["rate"],
                previous["rate"],
                latest["price_date"],
                previous["price_date"],
                latest["source"],
            )
        prices[item_code] = latest
    return prices


def _decimal_price(value):
    from decimal import Decimal

    return Decimal(str(value or 0))


def _build_purchase_price_trend(
    latest_rate,
    previous_rate,
    latest_date,
    previous_date,
    source,
):
    latest_rate = flt(latest_rate)
    previous_rate = flt(previous_rate)
    if previous_rate <= 0:
        return {}
    change_percentage = ((latest_rate - previous_rate) / previous_rate) * 100
    if change_percentage < -1:
        status = "decreased"
    elif change_percentage > 1:
        status = "increased"
    else:
        status = "unchanged"
    return {
        "status": status,
        "previous_rate": round(previous_rate, 6),
        "previous_date": str(previous_date or ""),
        "latest_rate": round(latest_rate, 6),
        "latest_date": str(latest_date or ""),
        "change_percentage": round(change_percentage, 2),
        "source": source,
    }


def get_latest_submitted_transaction_prices(
    item_codes: list[str],
    *,
    company: str,
    company_currency: str,
    planning_date: date,
    source: str,
) -> dict[str, dict[str, object]]:
    if not item_codes:
        return {}

    source_config = {
        "Purchase Receipt": {
            "parent_table": "tabPurchase Receipt",
            "child_table": "tabPurchase Receipt Item",
        },
        "Purchase Invoice": {
            "parent_table": "tabPurchase Invoice",
            "child_table": "tabPurchase Invoice Item",
        },
    }
    config = source_config.get(source)
    if not config:
        frappe.throw(_("Unsupported purchase price source: {0}").format(source))

    rows = frappe.db.sql(
        f"""
        select *
        from (
            select
                document_price.*,
                row_number() over (
                    partition by document_price.item_code
                    order by
                        document_price.price_date desc,
                        document_price.posting_time desc,
                        document_price.modified desc,
                        document_price.source_name desc
                ) as row_rank
            from (
                select *
                from (
                    select
                        child.item_code,
                        child.base_rate / child.conversion_factor as effective_rate,
                        child.rate / child.conversion_factor as source_rate,
                        child.stock_uom,
                        child.uom as purchase_uom,
                        parent.currency as source_currency,
                        parent.conversion_rate as source_conversion_rate,
                        parent.posting_date as price_date,
                        parent.posting_time,
                        parent.modified,
                        parent.supplier,
                        parent.name as source_name,
                        row_number() over (
                            partition by child.item_code, parent.name
                            order by child.idx desc
                        ) as document_item_rank
                    from `{config["child_table"]}` child
                    inner join `{config["parent_table"]}` parent on parent.name = child.parent
                    inner join `tabSupplier` supplier
                        on supplier.name = parent.supplier
                        and ifnull(supplier.disabled, 0) = 0
                    where parent.docstatus = 1
                      and ifnull(parent.is_return, 0) = 0
                      and parent.company = %(company)s
                      and parent.posting_date <= %(planning_date)s
                      and child.item_code in %(item_codes)s
                      and child.qty > 0
                      and child.stock_qty > 0
                      and child.rate > 0
                      and child.base_rate > 0
                      and child.conversion_factor > 0
                ) document_items
                where document_items.document_item_rank = 1
            ) document_price
        ) ranked
        where ranked.row_rank <= 2
        order by ranked.item_code asc, ranked.row_rank asc
        """,
        {
            "company": company,
            "planning_date": planning_date,
            "item_codes": tuple(item_codes),
        },
        as_dict=True,
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[row["item_code"]].append(row)

    prices: dict[str, dict[str, object]] = {}
    for item_code, item_rows in grouped.items():
        latest = item_rows[0]
        latest_rate = flt(latest.get("effective_rate") or 0)
        if latest_rate <= 0:
            continue
        price = {
            "rate": latest_rate,
            "currency": company_currency,
            "stock_uom": latest.get("stock_uom") or "",
            "price_date": str(latest.get("price_date") or ""),
            "supplier": latest.get("supplier") or "",
            "source": source,
            "source_name": latest.get("source_name") or "",
            "source_currency": latest.get("source_currency") or "",
            "source_rate": flt(latest.get("source_rate") or 0),
            "source_uom": latest.get("purchase_uom") or "",
            "source_conversion_rate": flt(latest.get("source_conversion_rate") or 0),
        }
        if len(item_rows) > 1:
            previous = item_rows[1]
            previous_rate = flt(previous.get("effective_rate") or 0)
            if previous_rate > 0:
                price["trend"] = _build_purchase_price_trend(
                    latest_rate,
                    previous_rate,
                    latest.get("price_date"),
                    previous.get("price_date"),
                    source,
                )
        prices[item_code] = price
    return prices


def get_latest_item_prices(
    item_codes: list[str],
    *,
    company_currency: str,
    planning_date: date,
) -> dict[str, dict[str, object]]:
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        """
        select
            item_price.name as source_name,
            item_price.item_code,
            item_price.price_list_rate,
            coalesce(nullif(item_price.currency, ''), price_list.currency) as source_currency,
            item_price.valid_from as price_date,
            item_price.supplier,
            item.stock_uom,
            coalesce(nullif(item_price.uom, ''), item.stock_uom) as price_uom,
            case
                when ifnull(item_price.uom, '') = ''
                  or item_price.uom = item.stock_uom then 1
                else uom_conversion.conversion_factor
            end as uom_conversion_factor
        from `tabItem Price` item_price
        inner join `tabPrice List` price_list
            on price_list.name = item_price.price_list
            and ifnull(price_list.enabled, 0) = 1
            and ifnull(price_list.buying, 0) = 1
        inner join `tabItem` item
            on item.name = item_price.item_code
            and ifnull(item.disabled, 0) = 0
            and item.item_group = %(item_group)s
        left join `tabSupplier` supplier on supplier.name = item_price.supplier
        left join `tabUOM Conversion Detail` uom_conversion
            on uom_conversion.parent = item.name
            and uom_conversion.uom = item_price.uom
        where item_price.item_code in %(item_codes)s
          and ifnull(item_price.buying, 0) = 1
          and item_price.price_list_rate > 0
          and (item_price.valid_from is null or item_price.valid_from <= %(planning_date)s)
          and (item_price.valid_upto is null or item_price.valid_upto >= %(planning_date)s)
          and (
              ifnull(item_price.supplier, '') = ''
              or (supplier.name is not null and ifnull(supplier.disabled, 0) = 0)
          )
          and (
              ifnull(item_price.uom, '') = ''
              or item_price.uom = item.stock_uom
              or ifnull(uom_conversion.conversion_factor, 0) > 0
          )
        order by
            item_price.item_code asc,
            coalesce(item_price.valid_from, '1000-01-01') desc,
            item_price.modified desc,
            item_price.name desc
        """,
        {
            "item_codes": tuple(item_codes),
            "item_group": RM_ITEM_GROUP,
            "planning_date": planning_date,
        },
        as_dict=True,
    )
    source_currencies = {
        (row.get("source_currency") or "").strip()
        for row in rows
        if (row.get("source_currency") or "").strip() != company_currency
    }
    exchange_rates = get_buying_exchange_rate_map(
        source_currencies,
        company_currency=company_currency,
        planning_date=planning_date,
    )

    prices: dict[str, dict[str, object]] = {}
    for row in rows:
        item_code = row["item_code"]
        if item_code in prices:
            continue
        source_currency = (row.get("source_currency") or "").strip()
        exchange_rate = 1.0 if source_currency == company_currency else flt(
            exchange_rates.get(source_currency) or 0
        )
        uom_conversion_factor = flt(row.get("uom_conversion_factor") or 0)
        if exchange_rate <= 0 or uom_conversion_factor <= 0:
            continue
        effective_rate = (
            flt(row.get("price_list_rate") or 0)
            * exchange_rate
            / uom_conversion_factor
        )
        if effective_rate <= 0:
            continue
        prices[item_code] = {
            "rate": effective_rate,
            "currency": company_currency,
            "stock_uom": row.get("stock_uom") or "",
            "price_date": str(row.get("price_date") or ""),
            "supplier": row.get("supplier") or "",
            "source": "Item Price",
            "source_name": row.get("source_name") or "",
            "source_currency": source_currency,
            "source_rate": flt(row.get("price_list_rate") or 0) / uom_conversion_factor,
            "source_uom": row.get("price_uom") or "",
            "source_conversion_rate": exchange_rate,
        }
    return prices


def get_buying_exchange_rate_map(
    currencies: set[str],
    *,
    company_currency: str,
    planning_date: date,
) -> dict[str, float]:
    if not currencies:
        return {}
    rows = frappe.db.sql(
        """
        select *
        from (
            select
                exchange.from_currency,
                exchange.to_currency,
                exchange.exchange_rate,
                row_number() over (
                    partition by exchange.from_currency, exchange.to_currency
                    order by exchange.date desc, exchange.modified desc, exchange.name desc
                ) as row_rank
            from `tabCurrency Exchange` exchange
            where exchange.date <= %(planning_date)s
              and ifnull(exchange.for_buying, 0) = 1
              and exchange.exchange_rate > 0
              and (
                  (
                      exchange.from_currency in %(currencies)s
                      and exchange.to_currency = %(company_currency)s
                  )
                  or (
                      exchange.from_currency = %(company_currency)s
                      and exchange.to_currency in %(currencies)s
                  )
              )
        ) ranked
        where ranked.row_rank = 1
        """,
        {
            "currencies": tuple(currencies),
            "company_currency": company_currency,
            "planning_date": planning_date,
        },
        as_dict=True,
    )
    rates: dict[str, float] = {}
    for row in rows:
        rate = flt(row.get("exchange_rate") or 0)
        if rate <= 0:
            continue
        if row.get("to_currency") == company_currency:
            rates[row["from_currency"]] = rate
        elif row.get("from_currency") == company_currency and row.get("to_currency") not in rates:
            rates[row["to_currency"]] = 1 / rate
    return rates


def get_planning_parameter_map(item_codes: list[str]) -> dict[str, dict[str, object]]:
    if not item_codes or not frappe.db.exists("DocType", "RM Planning Parameter"):
        return {}
    rows = frappe.get_all(
        "RM Planning Parameter",
        filters={"item_code": ("in", item_codes)},
        fields=[
            "name",
            "item_code",
            "preferred_supplier",
            "daily_avg_consumption_low",
            "daily_avg_consumption_peak",
            "current_season",
            "manual_lead_time_days",
            "safety_days",
            "review_period_days",
            "manual_maximum_inventory_level",
            "minimum_order_qty",
            "purchase_pack_size",
            "is_active",
        ],
    )
    return {row["item_code"]: row for row in rows if cint(row.get("is_active") or 0)}


def get_monthly_projection_requirement_map(month_start: date, month_end: date) -> dict[str, float]:
    if not frappe.db.exists("DocType", "Production Plan Item"):
        return {}

    plan_items = frappe.db.sql(
        """
        select
            ppi.item_code,
            ppi.bom_no,
            ppi.planned_qty,
            ppi.planned_start_date,
            pp.name as production_plan,
            pp.posting_date,
            pp.from_date,
            pp.to_date
        from `tabProduction Plan Item` ppi
        inner join `tabProduction Plan` pp on pp.name = ppi.parent
        where pp.docstatus < 2
        """,
        as_dict=True,
    )

    requirement_map: dict[str, float] = defaultdict(float)
    explosion_cache: dict[str, list[dict[str, object]]] = {}
    for row in plan_items:
        if not plan_item_in_month(row, month_start, month_end):
            continue
        bom_no = (row.get("bom_no") or "").strip()
        if not bom_no:
            continue
        if bom_no not in explosion_cache:
            explosion_cache[bom_no] = frappe.get_all(
                "BOM Explosion Item",
                filters={"parent": bom_no},
                fields=["item_code", "qty_consumed_per_unit", "stock_qty"],
            )
        for bom_row in explosion_cache[bom_no]:
            qty_per_unit = flt(bom_row.get("qty_consumed_per_unit") or 0)
            if qty_per_unit <= 0:
                qty_per_unit = flt(bom_row.get("stock_qty") or 0)
            requirement_map[bom_row["item_code"]] += flt(row.get("planned_qty") or 0) * qty_per_unit

    return {key: round(value, 3) for key, value in requirement_map.items()}


def plan_item_in_month(row: dict[str, object], month_start: date, month_end: date) -> bool:
    if row.get("planned_start_date"):
        planned_start = getdate(row["planned_start_date"])
        return month_start <= planned_start <= month_end

    if row.get("from_date") or row.get("to_date"):
        from_date = getdate(row.get("from_date") or month_start)
        to_date = getdate(row.get("to_date") or month_end)
        return from_date <= month_end and to_date >= month_start

    posting_date = getdate(row.get("posting_date") or month_start)
    return month_start <= posting_date <= month_end


def get_store_stock_map(item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select item_code, round(sum(actual_qty), 3) as qty
        from `tabBin`
        where warehouse = %(warehouse)s
          and item_code in %(item_codes)s
        group by item_code
        """,
        {"warehouse": RM_STORE_WAREHOUSE, "item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["qty"]) for row in rows}


def get_open_po_map(item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select
            poi.item_code,
            round(sum(greatest(
                (poi.qty - ifnull(poi.received_qty, 0))
                * ifnull(nullif(poi.conversion_factor, 0), 1),
                0
            )), 3) as outstanding_qty
        from `tabPurchase Order Item` poi
        inner join `tabPurchase Order` po on po.name = poi.parent
        where po.docstatus = 1
          and ifnull(po.status, '') not in ('Closed', 'Completed', 'Cancelled')
          and poi.item_code in %(item_codes)s
          and greatest(
                (poi.qty - ifnull(poi.received_qty, 0))
                * ifnull(nullif(poi.conversion_factor, 0), 1),
                0
              ) > 0
        group by poi.item_code
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["outstanding_qty"]) for row in rows}


def get_open_material_request_map(item_codes: list[str]) -> dict[str, float]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select
            mri.item_code,
            round(sum(greatest(
                ifnull(mri.stock_qty, mri.qty * ifnull(nullif(mri.conversion_factor, 0), 1))
                - ifnull(mri.ordered_qty, 0),
                0
            )), 3) as pending_qty
        from `tabMaterial Request Item` mri
        inner join `tabMaterial Request` mr on mr.name = mri.parent
        where mr.docstatus = 1
          and ifnull(mr.material_request_type, '') = 'Purchase'
          and ifnull(mr.status, '') not in ('Stopped', 'Completed', 'Closed', 'Cancelled')
          and mri.item_code in %(item_codes)s
          and greatest(
                ifnull(mri.stock_qty, mri.qty * ifnull(nullif(mri.conversion_factor, 0), 1))
                - ifnull(mri.ordered_qty, 0),
                0
              ) > 0
        group by mri.item_code
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {row["item_code"]: flt(row["pending_qty"]) for row in rows}


def get_item_reorder_defaults(item_codes: list[str]) -> dict[str, dict[str, float]]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select parent as item_code, max(warehouse_reorder_level) as reorder_level, max(warehouse_reorder_qty) as reorder_qty
        from `tabItem Reorder`
        where parent in %(item_codes)s
        group by parent
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {
        row["item_code"]: {
            "reorder_level": flt(row.get("reorder_level") or 0),
            "reorder_qty": flt(row.get("reorder_qty") or 0),
        }
        for row in rows
    }


def get_supplier_matrix_rows(item_codes: list[str]) -> dict[str, list[dict[str, object]]]:
    if not item_codes or not frappe.db.exists("DocType", "Supplier Approval Matrix"):
        return {}
    rows = frappe.get_all(
        "Supplier Approval Matrix",
        filters={
            "item_code": ("in", item_codes),
            "approval_status": ("in", APPROVED_MATRIX_STATUSES),
        },
        fields=[
            "name",
            "item_code",
            "supplier",
            "supplier_type",
            "approval_status",
            "supplier_rating",
            "lead_time",
            "payment_terms",
            "effective_date",
            "expiry_date",
        ],
        order_by="item_code asc, approval_status asc, supplier asc",
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[row["item_code"]].append(row)
    return grouped


def resolve_supplier_profile(item_code: str, parameter: dict[str, object], rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {
            "supplier": parameter.get("preferred_supplier") or "",
            "supplier_type": "",
            "approval_status": "",
            "payment_terms": "",
            "supplier_rating": 0,
        }

    preferred_supplier = (parameter.get("preferred_supplier") or "").strip()
    if preferred_supplier:
        for row in rows:
            if row.get("supplier") == preferred_supplier:
                return row

    approved = [row for row in rows if row.get("approval_status") == "Approved"]
    if approved:
        return approved[0]
    return rows[0]


def get_lead_time_data(item_codes: list[str]) -> dict[tuple[str, str], LeadTimeStat]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select
            pri.item_code,
            po.supplier,
            datediff(pr.posting_date, po.transaction_date) as lead_time_days,
            pri.schedule_date,
            pr.posting_date
        from `tabPurchase Receipt Item` pri
        inner join `tabPurchase Receipt` pr on pr.name = pri.parent
        inner join `tabPurchase Order` po on po.name = pri.purchase_order
        where pr.docstatus = 1
          and ifnull(pr.is_return, 0) = 0
          and po.docstatus = 1
          and pri.item_code in %(item_codes)s
          and pri.purchase_order is not null
          and pri.purchase_order != ''
        order by pr.posting_date desc, pr.name desc
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )

    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    grouped_item_only: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        item_key = (row["item_code"], row.get("supplier") or "")
        grouped[item_key].append(row)
        grouped_item_only[(row["item_code"], "")].append(row)

    stats: dict[tuple[str, str], LeadTimeStat] = {}
    for key, sample_rows in {**grouped, **grouped_item_only}.items():
        lead_days = [flt(r.get("lead_time_days") or 0) for r in sample_rows]
        on_time_hits = 0
        eligible_rows = 0
        for sample in sample_rows:
            if sample.get("schedule_date"):
                eligible_rows += 1
                if getdate(sample["posting_date"]) <= getdate(sample["schedule_date"]):
                    on_time_hits += 1
        stats[key] = LeadTimeStat(
            average_days=(sum(lead_days) / len(lead_days)) if lead_days else 0,
            minimum_days=min(lead_days) if lead_days else 0,
            maximum_days=max(lead_days) if lead_days else 0,
            last_three=lead_days[:3],
            on_time_percentage=((on_time_hits / eligible_rows) * 100) if eligible_rows else 0,
            sample_count=len(sample_rows),
        )
    return stats


def resolve_lead_time_stats(item_code: str, supplier: str | None, lead_time_data: dict[tuple[str, str], LeadTimeStat]) -> dict[str, object]:
    supplier = (supplier or "").strip()
    if supplier and (item_code, supplier) in lead_time_data:
        return lead_time_data[(item_code, supplier)].as_dict()
    if (item_code, "") in lead_time_data:
        return lead_time_data[(item_code, "")].as_dict()
    return LeadTimeStat().as_dict()


def build_dashboard_row(
    item: dict[str, object],
    parameter: dict[str, object],
    supplier_profile: dict[str, object],
    lead_stats: dict[str, object],
    projection_qty: float,
    month_days: int,
    current_stock: float,
    open_po_qty: float,
    open_material_request_qty: float,
    reorder_defaults: dict[str, float],
    planning_date: date,
    purchase_price: dict[str, object] | None,
    demand_profile: dict[str, object],
    forecast_profile: dict[str, object] | None = None,
    maximum_inventory_mode: str = DEFAULT_MAXIMUM_INVENTORY_MODE,
) -> dict[str, object]:
    season = ((parameter.get("current_season") or "Normal").strip().title()) or "Normal"
    low_daily = flt(parameter.get("daily_avg_consumption_low") or 0)
    peak_daily = flt(parameter.get("daily_avg_consumption_peak") or 0)
    if maximum_inventory_mode == "legacy":
        normal_daily = round((projection_qty / month_days), 3) if projection_qty > 0 and month_days else 0
        selected_daily = {
            "Low": low_daily,
            "Peak": peak_daily,
            "Normal": normal_daily,
        }.get(season, normal_daily)
    else:
        normal_daily = flt(demand_profile.get("daily_demand") or 0)
        selected_daily = normal_daily

    manual_lead = flt(parameter.get("manual_lead_time_days") or 0)
    lead_time = flt(lead_stats.get("average_days") or 0) or manual_lead or flt(item.get("lead_time_days") or 0)
    safety_days = flt(parameter.get("safety_days") or 0)
    review_period_days = flt(parameter.get("review_period_days") or 0)
    manual_maximum_level = flt(parameter.get("manual_maximum_inventory_level") or 0)
    safety_stock = round(selected_daily * safety_days, 3)
    reorder_level = round((selected_daily * lead_time) + safety_stock, 3)
    calculated_maximum_level = round(reorder_level + (selected_daily * review_period_days), 3)
    maximum_level = (
        manual_maximum_level
        if maximum_inventory_mode == "legacy" and manual_maximum_level > 0
        else calculated_maximum_level
    )
    projected_available_qty = round(current_stock + open_po_qty, 3)
    production_requirement = round(projection_qty, 3)
    coverage_days = round(projected_available_qty / selected_daily, 2) if selected_daily > 0 else None

    forecast_profile = forecast_profile or {}
    forecast_months = forecast_profile.get("months") or {}
    month_quantities = [
        flt((forecast_months.get(key) or {}).get("forecast_qty") or 0)
        for key in ("month_1", "month_2", "month_3")
    ]
    three_month_forecast = round(sum(month_quantities), 3)
    physical_procurement_gap = round(
        max(0, three_month_forecast - current_stock - open_po_qty), 3
    )
    raw_net_requirement = max(
        0, physical_procurement_gap - open_material_request_qty
    )
    old_raw_requirement = max(
        0,
        maximum_level
        + (production_requirement if maximum_inventory_mode == "legacy" else 0)
        - current_stock
        - open_po_qty
        - open_material_request_qty,
    )
    rounding_args = {
        "minimum_order_qty": flt(
            parameter.get("minimum_order_qty")
            or reorder_defaults.get("reorder_qty")
            or 0
        ),
        "purchase_pack_size": flt(parameter.get("purchase_pack_size") or 0),
    }
    previous_suggested_order_qty = round_order_qty(old_raw_requirement, **rounding_args)
    suggested_order_qty = round_order_qty(raw_net_requirement, **rounding_args)
    purchase_price = purchase_price or {}
    purchase_price_trend = purchase_price.get("trend") or {}
    latest_purchase_price = flt(purchase_price.get("rate") or 0)
    has_latest_purchase_price = latest_purchase_price > 0
    estimated_purchase_value = (
        round(suggested_order_qty * latest_purchase_price, 2)
        if suggested_order_qty > 0 and has_latest_purchase_price
        else 0
    )

    health = classify_forecast_inventory_health(
        month_quantities=month_quantities,
        forecast_months=forecast_months,
        planning_date=planning_date,
        lead_time_days=lead_time,
        projected_available_qty=projected_available_qty,
        maximum_level=maximum_level,
        raw_net_requirement=raw_net_requirement,
    )
    inventory_health = health["inventory_health"]
    required_by_date = get_required_by_date(planning_date, lead_time, inventory_health)

    issues = []
    if projection_qty <= 0:
        issues.append("Projection Missing")
    if not supplier_profile.get("supplier"):
        issues.append("Supplier Missing")
    if not supplier_profile.get("supplier_type"):
        issues.append("Supplier Type Missing")
    if not supplier_profile.get("payment_terms"):
        issues.append("Payment Terms Missing")

    return {
        "item_code": item["item_code"],
        "category": item.get("category") or "",
        "item_name": item.get("item_name") or "",
        "daily_avg_consumption_low": round(low_daily, 3),
        "daily_avg_consumption_normal": round(normal_daily, 3),
        "daily_avg_consumption_peak": round(peak_daily, 3),
        "current_season": season,
        "selected_daily_consumption": round(selected_daily, 3),
        "dynamic_monthly_demand": (
            flt(demand_profile.get("selected_monthly_demand") or 0)
            if maximum_inventory_mode == "dynamic"
            else 0
        ),
        "dynamic_demand_source": (
            demand_profile.get("source") or ""
            if maximum_inventory_mode == "dynamic"
            else ""
        ),
        "dynamic_maximum_version": (
            demand_profile.get("version") or ""
            if maximum_inventory_mode == "dynamic"
            else ""
        ),
        "maximum_inventory_mode": maximum_inventory_mode,
        "lead_time_days": round(lead_time, 2),
        "safety_days": round(safety_days, 2),
        "safety_stock": round(safety_stock, 3),
        "reorder_level": round(reorder_level, 3),
        "maximum_level": round(maximum_level, 3),
        "inventory_policy_maximum": round(maximum_level, 3),
        "month_1_forecast_qty": round(month_quantities[0], 3),
        "month_1_forecast_status": (forecast_months.get("month_1") or {}).get("forecast_status") or "Missing",
        "month_2_forecast_qty": round(month_quantities[1], 3),
        "month_2_forecast_status": (forecast_months.get("month_2") or {}).get("forecast_status") or "Missing",
        "month_3_forecast_qty": round(month_quantities[2], 3),
        "month_3_forecast_status": (forecast_months.get("month_3") or {}).get("forecast_status") or "Missing",
        "three_month_forecast": three_month_forecast,
        "forecast_complete": bool(forecast_profile.get("forecast_complete")),
        "forecast_missing": not bool(forecast_profile.get("forecast_complete")),
        "physical_procurement_gap": physical_procurement_gap,
        "raw_net_purchase_requirement": round(raw_net_requirement, 3),
        "previous_suggested_order_qty": round(previous_suggested_order_qty, 3),
        "current_rm_store_stock": round(current_stock, 3),
        "available_inventory": round(current_stock, 3),
        "open_po_in_transit_qty": round(open_po_qty, 3),
        "open_material_request_qty": round(open_material_request_qty, 3),
        "projected_available_qty": round(projected_available_qty, 3),
        "production_requirement": round(production_requirement, 3),
        "coverage_days": coverage_days,
        "inventory_health": inventory_health,
        "inventory_health_color": HEALTH_META[inventory_health]["color"],
        "health_confidence": forecast_profile.get("planning_confidence")
        or ("Forecast Missing" if not forecast_profile.get("forecast_complete") else "Complete"),
        "first_shortage_month": health.get("first_shortage_month") or "",
        "lead_time_action_required": bool(health.get("lead_time_action_required")),
        "open_po_timing_unallocated": bool(open_po_qty > 0),
        "suggested_order_qty": round(suggested_order_qty, 3),
        "net_purchase_requirement": round(suggested_order_qty, 3),
        "stock_uom": item.get("stock_uom") or "",
        "latest_available_purchase_price": round(latest_purchase_price, 6)
        if has_latest_purchase_price
        else None,
        "has_latest_purchase_price": has_latest_purchase_price,
        "latest_purchase_price_currency": purchase_price.get("currency") or "",
        "latest_purchase_price_uom": purchase_price.get("stock_uom") or item.get("stock_uom") or "",
        "latest_purchase_price_date": purchase_price.get("price_date") or "",
        "latest_purchase_price_supplier": purchase_price.get("supplier") or "",
        "latest_purchase_price_source": purchase_price.get("source") or "",
        "latest_purchase_price_source_name": purchase_price.get("source_name") or "",
        "latest_purchase_price_source_currency": purchase_price.get("source_currency") or "",
        "latest_purchase_price_source_rate": flt(purchase_price.get("source_rate") or 0),
        "latest_purchase_price_source_uom": purchase_price.get("source_uom") or "",
        "latest_purchase_price_source_conversion_rate": flt(
            purchase_price.get("source_conversion_rate") or 0
        ),
        "latest_purchase_price_trend": purchase_price_trend.get("status") or "",
        "previous_purchase_price": flt(purchase_price_trend.get("previous_rate") or 0),
        "previous_purchase_price_date": purchase_price_trend.get("previous_date") or "",
        "latest_purchase_price_change_percentage": flt(
            purchase_price_trend.get("change_percentage") or 0
        ),
        "estimated_purchase_value": estimated_purchase_value,
        "required_by_date": str(required_by_date),
        "warehouse": RM_STORE_WAREHOUSE,
        "projection_missing": projection_qty <= 0,
        "issues": issues,
        "preferred_supplier": supplier_profile.get("supplier") or "",
        "supplier_type": supplier_profile.get("supplier_type") or "",
        "approval_status": supplier_profile.get("approval_status") or "",
        "payment_terms": supplier_profile.get("payment_terms") or "",
        "supplier_rating": flt(supplier_profile.get("supplier_rating") or 0),
        "lead_time_stats": lead_stats,
        "planning_parameter": parameter.get("name") or "",
        "item_route": get_doc_route("Item", item["item_code"]),
        "stock_ledger_route": get_list_route("Stock Ledger Entry"),
        "open_po_route": get_list_route("Purchase Order"),
        "planning_parameter_route": get_doc_route("RM Planning Parameter", parameter["name"]) if parameter.get("name") else "",
    }


def dashboard_row_matches_filters(row: dict[str, object], filters: dict[str, object]) -> bool:
    if filters.get("item_code"):
        needle = str(filters["item_code"]).strip().lower()
        haystack = f"{row.get('item_code') or ''} {row.get('item_name') or ''}".lower()
        if needle not in haystack:
            return False
    if filters.get("inventory_health") and row["inventory_health"] != filters["inventory_health"]:
        return False
    if filters.get("supplier") and row.get("preferred_supplier") != filters["supplier"]:
        return False
    if filters.get("supplier_type") and row.get("supplier_type") != filters["supplier_type"]:
        return False
    if filters.get("current_season") and row.get("current_season") != filters["current_season"]:
        return False
    if filters.get("only_items_requiring_purchase") and flt(row.get("suggested_order_qty") or 0) <= 0:
        return False
    if filters.get("only_critical_items") and row.get("inventory_health") != "Critical":
        return False
    return True


def classify_forecast_inventory_health(
    *,
    month_quantities: list[float],
    forecast_months: dict[str, dict[str, object]],
    planning_date: date,
    lead_time_days: float,
    projected_available_qty: float,
    maximum_level: float,
    raw_net_requirement: float,
) -> dict[str, object]:
    cumulative = 0.0
    first_shortage_key = ""
    first_shortage_date = None
    for index, key in enumerate(("month_1", "month_2", "month_3")):
        cumulative += flt(month_quantities[index])
        if not first_shortage_key and cumulative > projected_available_qty:
            first_shortage_key = key
            start_date = (forecast_months.get(key) or {}).get("start_date")
            first_shortage_date = getdate(start_date) if start_date else None

    lead_time_action_required = bool(
        raw_net_requirement > 0
        and first_shortage_date
        and getdate(add_days(planning_date, math.ceil(lead_time_days or 0)))
        >= first_shortage_date
    )
    if first_shortage_key == "month_1" or lead_time_action_required:
        inventory_health = "Critical"
    elif first_shortage_key:
        inventory_health = "Low"
    elif maximum_level > 0 and projected_available_qty > maximum_level:
        inventory_health = "Overstock"
    else:
        inventory_health = "Healthy"

    return {
        "inventory_health": inventory_health,
        "first_shortage_month": first_shortage_key,
        "lead_time_action_required": lead_time_action_required,
    }
def classify_inventory_health(
    coverage_days: float | None,
    lead_time_days: float,
    safety_days: float,
    projected_available_qty: float,
    maximum_level: float,
) -> str:
    if maximum_level > 0 and projected_available_qty >= maximum_level:
        return "Overstock"

    if coverage_days is not None and coverage_days < lead_time_days:
        return "Critical"

    if maximum_level > 0:
        projected_ratio = projected_available_qty / maximum_level
        if projected_ratio <= 0.33:
            return "Critical"
        if 0.33 < projected_ratio <= 0.66:
            return "Low"
        if 0.66 < projected_ratio <= 0.99:
            return "Healthy"

    if coverage_days is not None and lead_time_days <= coverage_days < (lead_time_days + safety_days):
        return "Low"

    return "Healthy"


def round_order_qty(qty: float, minimum_order_qty: float, purchase_pack_size: float) -> float:
    qty = flt(qty or 0)
    if qty <= 0:
        return 0
    if purchase_pack_size > 0:
        qty = math.ceil(qty / purchase_pack_size) * purchase_pack_size
    if minimum_order_qty > 0 and qty < minimum_order_qty:
        qty = minimum_order_qty
    return round(qty, 3)


def get_required_by_date(planning_date: date, lead_time_days: float, inventory_health: str) -> date:
    if inventory_health == "Critical":
        return getdate(add_days(planning_date, 2))
    return getdate(add_days(planning_date, max(0, int(math.ceil(lead_time_days or 0)))))


def build_lead_time_analytics(rows: list[dict[str, object]], lead_time_data: dict[tuple[str, str], LeadTimeStat]) -> list[dict[str, object]]:
    analytics = []
    seen = set()
    for row in rows:
        supplier = row.get("preferred_supplier") or ""
        if not supplier:
            continue
        key = (supplier, row["item_code"])
        if key in seen:
            continue
        seen.add(key)
        stat = lead_time_data.get((row["item_code"], supplier))
        if not stat:
            stat = lead_time_data.get((row["item_code"], ""))
        analytics.append(
            {
                "supplier": supplier,
                "item_code": row["item_code"],
                "average_lead_time": round((stat.average_days if stat else 0), 2),
                "min_lead_time": round((stat.minimum_days if stat else 0), 2),
                "max_lead_time": round((stat.maximum_days if stat else 0), 2),
                "last_three_receipts": [round(value, 2) for value in (stat.last_three if stat else [])],
                "on_time_percentage": round((stat.on_time_percentage if stat else 0), 2),
                "supplier_type": row.get("supplier_type") or "",
            }
        )
    analytics.sort(key=lambda row: (row["supplier"], row["item_code"]))
    return analytics


def build_dashboard_cards(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    total_items = len(rows)
    critical = sum(1 for row in rows if row["inventory_health"] == "Critical")
    low = sum(1 for row in rows if row["inventory_health"] == "Low")
    healthy = sum(1 for row in rows if row["inventory_health"] == "Healthy")
    overstock = sum(1 for row in rows if row["inventory_health"] == "Overstock")
    requiring_purchase = sum(1 for row in rows if flt(row.get("suggested_order_qty") or 0) > 0)
    return [
        {"label": "RM Items", "value": total_items, "suffix": ""},
        {"label": "Critical", "value": critical, "suffix": ""},
        {"label": "Requiring Purchase", "value": requiring_purchase, "suffix": ""},
        {"label": "Healthy", "value": healthy + overstock, "suffix": ""},
        {"label": "Low", "value": low, "suffix": ""},
    ]
