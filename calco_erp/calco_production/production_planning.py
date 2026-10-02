from __future__ import annotations
from calco_erp.calco_production.forecast_period import period_sql, LEGACY_NOTICE

import json
import time
from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime, nowdate

from calco_erp.calco_production import fg_planning


SOURCE_SALES_ORDER = "Sales Order"
SOURCE_FORECAST = "Forecast"
SOURCE_BALANCE_RETURN = "Balance Return"

STATUS_RECOMMENDED = "Recommended"
STATUS_DRAFT_PLAN = "Added to Draft Production Plan"
STATUS_APPROVED_PLAN = "Approved in Production Plan"
STATUS_RELEASED = "Released through Work Order"
STATUS_PARTIAL = "Partially Produced"
STATUS_COMPLETED = "Completed"
STATUS_BLOCKED = "Blocked"

REVIEW_PENDING = "Pending Review"
REVIEW_CONFIRMED = "Confirmed"
REVIEW_ON_HOLD = "On Hold"
REVIEW_REJECTED = "Rejected"
REVIEW_DECISIONS = {REVIEW_CONFIRMED, REVIEW_ON_HOLD, REVIEW_REJECTED}
REVIEW_FIELDNAMES = (
    "custom_planning_review_status",
    "custom_planning_reviewed_qty",
    "custom_planning_reviewed_by",
    "custom_planning_reviewed_on",
    "custom_planning_review_remarks",
)

SOURCE_FIELDNAMES = (
    "custom_planning_source_type",
    "custom_planning_source_doctype",
    "custom_planning_source_name",
    "custom_planning_source_row",
    "custom_required_delivery_date",
)


def _clean_kwargs(kwargs: dict[str, object] | None) -> dict[str, object]:
    cleaned = dict(kwargs or {})
    cleaned.pop("cmd", None)
    cleaned.pop("method", None)
    return cleaned


def _default_period(report_date: str | date | None = None) -> tuple[date, date]:
    planning_date = getdate(report_date or nowdate())
    week_start = planning_date - timedelta(days=planning_date.weekday())
    return week_start, week_start + timedelta(days=6)


@frappe.whitelist()
def get_planning_data(*args, **kwargs) -> dict[str, object]:
    kwargs = _clean_kwargs(kwargs)
    from calco_erp.calco_production.planning_release import build_planning_data

    return build_planning_data(
        from_date=kwargs.get("from_date"),
        to_date=kwargs.get("to_date"),
        item_code=kwargs.get("item_code"),
        source_type=kwargs.get("source_type"),
        planning_status=kwargs.get("planning_status"),
        review_status=kwargs.get("review_status"),
    )


def _build_planning_data(
    from_date=None,
    to_date=None,
    item_code=None,
    source_type=None,
    planning_status=None,
    review_status=None,
    exclude_production_plan: str | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    default_from, default_to = _default_period(from_date)
    from_date = getdate(from_date) if from_date else default_from
    to_date = getdate(to_date) if to_date else default_to
    if to_date < from_date:
        frappe.throw(_("To Date cannot be before From Date."))

    company = _get_default_company()
    fg_items = fg_planning.get_fg_items({"item_code": (item_code or "").strip()})
    item_map = {row["item_code"]: dict(row) for row in fg_items}
    item_codes = list(item_map)
    if not item_codes:
        return _empty_payload(from_date, to_date, company, started)

    demands = _get_sales_order_demands(company, from_date, to_date, item_map)
    demands.extend(_get_forecast_demands(company, from_date, to_date, item_map))

    plan_rows = _get_active_plan_rows(item_codes, exclude_production_plan)
    plan_source_map = {row["name"]: _plan_source_key(row) for row in plan_rows}
    work_orders = _get_work_orders(item_codes)
    _add_balance_return_demands(demands, work_orders, plan_source_map, item_map)

    demand_item_codes = sorted({row["item_code"] for row in demands})
    bom_map = _get_default_bom_map(demand_item_codes)
    rm_governance_blockers = _get_rm_governance_blockers(bom_map)
    stock_map = fg_planning.get_fg_stock_map(item_codes)
    coverage = _build_coverage(plan_rows, work_orders, plan_source_map)
    rows = _allocate_recommendations(
        demands,
        stock_map,
        bom_map,
        coverage,
        rm_governance_blockers=rm_governance_blockers,
        review_map=_get_review_map(demands),
    )

    requested_source = (source_type or "").strip()
    requested_status = (planning_status or "").strip()
    requested_review = (review_status or "").strip()
    if requested_source:
        rows = [row for row in rows if row["source_type"] == requested_source]
    card_rows = rows
    if requested_status:
        matching_statuses = (
            {STATUS_RELEASED, STATUS_PARTIAL}
            if requested_status == STATUS_RELEASED
            else {requested_status}
        )
        rows = [row for row in rows if row["planning_status"] in matching_statuses]
    if requested_review:
        rows = [row for row in rows if row["review_status"] == requested_review]

    return {
        "company": company,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "warehouse_scope": "Finished Goods warehouses",
        "cards": _build_cards(card_rows),
        "rows": rows,
        "filters": {
            "item_code": (item_code or "").strip(),
            "source_type": requested_source,
            "planning_status": requested_status,
            "review_status": requested_review,
        },
        "formulas": {
            "sales_order_demand": "MAX(Stock Qty - Delivered Qty, 0)",
            "forecast_demand": "Submitted ERPNext Sales Forecast demand converted to stock UOM",
            "balance_return": "MAX(Closed Work Order Qty - Produced Qty - follow-up coverage, 0)",
            "recommended_qty": "MAX(Demand - FG Allocated - Draft Plan - Approved Plan Remaining - Open Work Order, 0)",
        },
        "backend_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _empty_payload(from_date, to_date, company, started) -> dict[str, object]:
    return {
        "company": company,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "warehouse_scope": "Finished Goods warehouses",
        "cards": _build_cards([]),
        "rows": [],
        "filters": {},
        "formulas": {},
        "backend_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _get_sales_order_demands(company, from_date, to_date, item_map) -> list[dict[str, object]]:
    rows = frappe.db.sql(
        """
        select
            soi.name as source_row,
            soi.parent as source_name,
            soi.item_code,
            soi.item_name,
            coalesce(soi.stock_qty, soi.qty, 0) as stock_qty,
            coalesce(soi.delivered_qty, 0) as delivered_qty,
            date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date)) as required_date
        from `tabSales Order Item` soi
        inner join `tabSales Order` so on so.name = soi.parent
        where so.docstatus = 1
          and so.company = %(company)s
          and coalesce(so.status, '') not in ('Closed', 'Completed', 'Cancelled', 'Stopped')
          and soi.item_code in %(item_codes)s
          and date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date))
              between %(from_date)s and %(to_date)s
          and coalesce(soi.stock_qty, soi.qty, 0) > coalesce(soi.delivered_qty, 0)
        order by required_date asc, soi.parent asc, soi.idx asc
        """,
        {
            "company": company,
            "item_codes": tuple(item_map),
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=True,
    )
    return [
        _demand_row(
            source_type=SOURCE_SALES_ORDER,
            source_doctype="Sales Order",
            source_name=row.source_name,
            source_row=row.source_row,
            item=item_map[row.item_code],
            required_date=row.required_date,
            demand_qty=max(flt(row.stock_qty) - flt(row.delivered_qty), 0),
            priority=1,
        )
        for row in rows
    ]


def _get_forecast_demands(company, from_date, to_date, item_map) -> list[dict[str, object]]:
    rows = frappe.db.sql(
        f"""
        select
            sfi.name as source_row,
            sfi.parent as source_name,
            sfi.item_code,
            sfi.uom,
            sfi.demand_qty,
            sfi.delivery_date as required_date, {period_sql()} as forecast_date, sfi.custom_forecast_period_start, sfi.delivery_date
        from `tabSales Forecast Item` sfi
        inner join `tabSales Forecast` sf on sf.name = sfi.parent
        where sf.docstatus = 1
          and sf.company = %(company)s
          and coalesce(sf.status, '') != 'Cancelled'
          and sfi.parentfield = 'items'
          and sfi.item_code in %(item_codes)s
          and {period_sql()} between %(from_date)s and %(to_date)s
          and coalesce(sfi.demand_qty, 0) > 0
        order by {period_sql()} asc, sfi.parent asc, sfi.idx asc
        """,
        {
            "company": company,
            "item_codes": tuple(item_map),
            "from_date": from_date,
            "to_date": to_date,
        },
        as_dict=True,
    )
    conversion_map = _get_uom_conversion_map({(row.item_code, row.uom) for row in rows})
    output = []
    for row in rows:
        item = item_map[row.item_code]
        stock_uom = item.get("stock_uom") or ""
        factor = 1.0 if row.uom == stock_uom else conversion_map.get((row.item_code, row.uom))
        blocked_reason = ""
        demand_qty = 0.0
        if factor:
            demand_qty = flt(row.demand_qty) * flt(factor)
        else:
            blocked_reason = _("Missing UOM conversion from {0} to {1}.").format(row.uom, stock_uom)
        output.append(
            _demand_row(
                source_type=SOURCE_FORECAST,
                source_doctype="Sales Forecast",
                source_name=row.source_name,
                source_row=row.source_row,
                item=item,
                required_date=row.required_date,
                demand_qty=demand_qty,
                priority=2,
                blocked_reason=blocked_reason,
            )
        )
    for entry, raw in zip(output, rows):
        entry["forecast_date"] = str(raw.get("forecast_date") or raw.required_date)
        entry["forecast_period_start"] = str(raw.get("custom_forecast_period_start") or "")
        entry["delivery_date"] = str(raw.get("delivery_date") or "")
        entry["compatibility_notice"] = LEGACY_NOTICE if not raw.get("custom_forecast_period_start") else ""
    return output


def _get_uom_conversion_map(item_uoms: set[tuple[str, str]]) -> dict[tuple[str, str], float]:
    item_codes = {item for item, _uom in item_uoms if item}
    if not item_codes:
        return {}
    rows = frappe.get_all(
        "UOM Conversion Detail",
        filters={"parent": ("in", list(item_codes))},
        fields=["parent", "uom", "conversion_factor"],
        limit_page_length=0,
    )
    return {(row.parent, row.uom): flt(row.conversion_factor) for row in rows}


def _demand_row(
    source_type,
    source_doctype,
    source_name,
    source_row,
    item,
    required_date,
    demand_qty,
    priority,
    blocked_reason="",
) -> dict[str, object]:
    return {
        "recommendation_key": f"{source_type}:{source_row or source_name}",
        "source_type": source_type,
        "source_doctype": source_doctype,
        "source_name": source_name,
        "source_row": source_row or "",
        "item_code": item["item_code"],
        "item_name": item.get("item_name") or "",
        "stock_uom": item.get("stock_uom") or "",
        "required_date": str(getdate(required_date)),
        "demand_qty": round(flt(demand_qty), 3),
        "priority": priority,
        "blocked_reason": blocked_reason,
        "balance_return_qty": 0.0,
    }


def _review_target(row) -> tuple[str, str]:
    if row["source_type"] == SOURCE_SALES_ORDER:
        return "Sales Order Item", row["source_row"]
    if row["source_type"] == SOURCE_FORECAST:
        return "Sales Forecast Item", row["source_row"]
    return "Work Order", row["source_name"]


def _get_review_map(demands) -> dict[str, dict[str, object]]:
    targets = defaultdict(set)
    for row in demands:
        doctype, name = _review_target(row)
        if name:
            targets[doctype].add(name)

    review_map = {}
    for doctype, names in targets.items():
        meta = frappe.get_meta(doctype)
        if any(not meta.has_field(fieldname) for fieldname in REVIEW_FIELDNAMES):
            continue
        for record in frappe.get_all(
            doctype,
            filters={"name": ("in", list(names))},
            fields=["name", *REVIEW_FIELDNAMES],
            limit_page_length=0,
        ):
            review_map[f"{doctype}:{record.name}"] = dict(record)
    return review_map


def _apply_review_state(row, review=None) -> None:
    review = review or {}
    stored_status = (review.get("custom_planning_review_status") or "").strip()
    reviewed_qty = flt(review.get("custom_planning_reviewed_qty"))
    stale = (
        stored_status == REVIEW_CONFIRMED
        and (reviewed_qty <= 0 or reviewed_qty - flt(row["recommended_qty"]) > 1e-9)
    )
    row.update(
        {
            "review_status": REVIEW_PENDING if stale or not stored_status else stored_status,
            "reviewed_qty": round(reviewed_qty, 3),
            "reviewed_by": review.get("custom_planning_reviewed_by") or "",
            "reviewed_on": review.get("custom_planning_reviewed_on") or "",
            "review_remarks": review.get("custom_planning_review_remarks") or "",
            "review_stale": int(stale),
        }
    )
    if row["planning_status"] == STATUS_RECOMMENDED:
        row["next_action"] = {
            REVIEW_PENDING: _("Complete Production Planning Review"),
            REVIEW_CONFIRMED: _("Select for Draft Production Plan"),
            REVIEW_ON_HOLD: _("Resolve planning hold"),
            REVIEW_REJECTED: _("Review rejected recommendation"),
        }.get(row["review_status"], _("Complete Production Planning Review"))


def _get_default_bom_map(item_codes: list[str]) -> dict[str, str]:
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        f"""
        select item, name, is_default
        from `tabBOM`
        where docstatus = 1 and is_active = 1 and is_default = 1 and item in %(item_codes)s
        order by item asc, modified desc
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    output = {}
    for row in rows:
        output.setdefault(row.item, row.name)
    return output


def _get_rm_governance_blockers(bom_map: dict[str, str]) -> dict[str, list[str]]:
    if not bom_map:
        return {}
    rows = frappe.db.sql(
        f"""
        select distinct bei.parent as bom_no, bei.item_code
        from `tabBOM Explosion Item` bei
        inner join `tabItem` item on item.name = bei.item_code
        where bei.parent in %(bom_names)s
          and item.item_group = 'Raw Material'
        order by bei.parent asc, bei.item_code asc
        """,
        {"bom_names": tuple(set(bom_map.values()))},
        as_dict=True,
    )
    components_by_bom = defaultdict(list)
    for row in rows:
        components_by_bom[row.bom_no].append(row.item_code)

    component_codes = sorted({row.item_code for row in rows})
    if not component_codes:
        return {}
    from calco_erp.calco_purchase.master_data_governance_dashboard import (
        get_operational_readiness_by_item,
    )

    readiness = get_operational_readiness_by_item(component_codes)
    return {
        item_code: [
            component
            for component in components_by_bom.get(bom_no, [])
            if not readiness.get(component, {}).get("ready")
        ]
        for item_code, bom_no in bom_map.items()
    }


def _get_active_plan_rows(item_codes: list[str], exclude_plan=None) -> list[frappe._dict]:
    if not item_codes:
        return []
    exclude_condition = "and pp.name != %(exclude_plan)s" if exclude_plan else ""
    return frappe.db.sql(
        f"""
        select
            ppi.name,
            ppi.parent,
            ppi.item_code,
            ppi.planned_qty,
            ppi.ordered_qty,
            ppi.produced_qty,
            ppi.sales_order,
            ppi.sales_order_item,
            ppi.custom_planning_source_type,
            ppi.custom_planning_source_doctype,
            ppi.custom_planning_source_name,
            ppi.custom_planning_source_row,
            ppi.custom_required_delivery_date,
            pp.docstatus as plan_docstatus,
            pp.status as plan_status
        from `tabProduction Plan Item` ppi
        inner join `tabProduction Plan` pp on pp.name = ppi.parent
        where pp.docstatus < 2
          and ppi.item_code in %(item_codes)s
          {exclude_condition}
        order by pp.creation asc, ppi.idx asc
        """,
        {"item_codes": tuple(item_codes), "exclude_plan": exclude_plan},
        as_dict=True,
    )


def _get_work_orders(item_codes: list[str]) -> list[frappe._dict]:
    if not item_codes:
        return []
    return frappe.get_all(
        "Work Order",
        filters={"docstatus": ("<", 2), "production_item": ("in", item_codes)},
        fields=[
            "name",
            "docstatus",
            "status",
            "production_item",
            "qty",
            "produced_qty",
            "production_plan",
            "production_plan_item",
            "sales_order",
            "sales_order_item",
            "planned_start_date",
            "planned_end_date",
        ],
        limit_page_length=0,
    )


def _plan_source_key(row) -> str:
    source_type = (row.get("custom_planning_source_type") or "").strip()
    source_row = (row.get("custom_planning_source_row") or "").strip()
    source_name = (row.get("custom_planning_source_name") or "").strip()
    if row.get("sales_order_item"):
        return f"{SOURCE_SALES_ORDER}:{row.sales_order_item}"
    if source_type and (source_row or source_name):
        return f"{source_type}:{source_row or source_name}"
    return ""


def _add_balance_return_demands(demands, work_orders, plan_source_map, item_map) -> None:
    demand_by_key = {row["recommendation_key"]: row for row in demands}
    for work_order in work_orders:
        if work_order.docstatus != 1 or work_order.status != "Closed":
            continue
        pending_qty = max(flt(work_order.qty) - flt(work_order.produced_qty), 0)
        if pending_qty <= 0:
            continue
        source_key = plan_source_map.get(work_order.production_plan_item) or (
            f"{SOURCE_SALES_ORDER}:{work_order.sales_order_item}" if work_order.sales_order_item else ""
        )
        if source_key and source_key in demand_by_key:
            demand_by_key[source_key]["balance_return_qty"] = round(
                flt(demand_by_key[source_key].get("balance_return_qty")) + pending_qty, 3
            )
            continue
        item = item_map.get(work_order.production_item)
        if not item:
            continue
        row = _demand_row(
            source_type=SOURCE_BALANCE_RETURN,
            source_doctype="Work Order",
            source_name=work_order.name,
            source_row="",
            item=item,
            required_date=work_order.planned_end_date or work_order.planned_start_date or nowdate(),
            demand_qty=pending_qty,
            priority=2,
            blocked_reason=(
                _("Manual Work Order has no originating Production Plan demand and requires planning review.")
                if not source_key
                else ""
            ),
        )
        demands.append(row)
        demand_by_key[row["recommendation_key"]] = row


def _build_coverage(plan_rows, work_orders, plan_source_map) -> dict[str, object]:
    coverage = {
        "draft_plan": defaultdict(float),
        "approved_plan": defaultdict(float),
        "plan_names": defaultdict(set),
        "work_order": defaultdict(float),
        "work_order_names": defaultdict(list),
        "produced_qty": defaultdict(float),
        "manual_work_order": defaultdict(float),
        "manual_work_order_names": defaultdict(list),
        "plan_items": defaultdict(list),
        "draft_work_order_names": defaultdict(list),
    }

    draft_qty_by_plan_item = defaultdict(float)
    for work_order in work_orders:
        if work_order.docstatus == 0 and work_order.production_plan_item:
            draft_qty_by_plan_item[work_order.production_plan_item] += max(
                flt(work_order.qty) - flt(work_order.produced_qty), 0
            )

    for row in plan_rows:
        source_key = plan_source_map.get(row.name) or ""
        if not source_key:
            continue
        coverage["plan_names"][source_key].add(row.parent)
        draft_work_order_qty = flt(draft_qty_by_plan_item.get(row.name))
        remaining_qty = max(
            flt(row.planned_qty) - flt(row.ordered_qty) - draft_work_order_qty,
            0,
        )
        coverage["plan_items"][source_key].append(
            {
                "name": row.name,
                "production_plan": row.parent,
                "docstatus": row.plan_docstatus,
                "status": row.plan_status,
                "planned_qty": flt(row.planned_qty),
                "ordered_qty": flt(row.ordered_qty),
                "draft_work_order_qty": draft_work_order_qty,
                "remaining_qty": remaining_qty,
            }
        )
        if row.plan_docstatus == 0:
            coverage["draft_plan"][source_key] += flt(row.planned_qty)
        elif row.plan_docstatus == 1:
            coverage["approved_plan"][source_key] += remaining_qty

    for work_order in work_orders:
        if work_order.status in {"Cancelled", "Completed", "Closed"}:
            continue
        open_qty = max(flt(work_order.qty) - flt(work_order.produced_qty), 0)
        if open_qty <= 0:
            continue
        source_key = plan_source_map.get(work_order.production_plan_item) or (
            f"{SOURCE_SALES_ORDER}:{work_order.sales_order_item}" if work_order.sales_order_item else ""
        )
        if source_key:
            coverage["work_order"][source_key] += open_qty
            coverage["produced_qty"][source_key] += flt(work_order.produced_qty)
            coverage["work_order_names"][source_key].append(work_order.name)
            if work_order.docstatus == 0:
                coverage["draft_work_order_names"][source_key].append(work_order.name)
        else:
            coverage["manual_work_order"][work_order.production_item] += open_qty
            coverage["manual_work_order_names"][work_order.production_item].append(work_order.name)
    return coverage


def _allocate_recommendations(
    demands,
    stock_map,
    bom_map,
    coverage,
    rm_governance_blockers=None,
    review_map=None,
) -> list[dict[str, object]]:
    rm_governance_blockers = rm_governance_blockers or {}
    review_map = review_map or {}
    grouped = defaultdict(list)
    for row in demands:
        grouped[row["item_code"]].append(row)

    output = []
    for item_code, item_rows in grouped.items():
        stock_remaining = max(flt(stock_map.get(item_code)), 0)
        manual_remaining = max(flt(coverage["manual_work_order"].get(item_code)), 0)
        item_rows.sort(
            key=lambda row: (
                row["priority"],
                getdate(row["required_date"]),
                row["source_name"],
                row["source_row"],
            )
        )
        for row in item_rows:
            key = row["recommendation_key"]
            demand_qty = flt(row["demand_qty"])
            fg_allocated = min(stock_remaining, demand_qty)
            stock_remaining -= fg_allocated
            after_fg = max(demand_qty - fg_allocated, 0)

            draft_plan_qty = flt(coverage["draft_plan"].get(key))
            approved_plan_qty = flt(coverage["approved_plan"].get(key))
            open_work_order_qty = flt(coverage["work_order"].get(key))
            produced_qty = flt(coverage["produced_qty"].get(key))
            manual_allocated = min(manual_remaining, max(after_fg - draft_plan_qty - approved_plan_qty - open_work_order_qty, 0))
            manual_remaining -= manual_allocated
            recommended_qty = max(
                after_fg - draft_plan_qty - approved_plan_qty - open_work_order_qty - manual_allocated,
                0,
            )

            blocked_reasons = [row.get("blocked_reason") or ""]
            if not bom_map.get(item_code):
                blocked_reasons.append(_("No Active Default BOM"))
            rm_blockers = rm_governance_blockers.get(item_code) or []
            blocked_reasons.extend(
                _("RM {0} not Operational Ready").format(rm_code)
                for rm_code in rm_blockers
            )
            if manual_allocated > 0:
                blocked_reasons.append(_("Unlinked manual Work Order coverage requires planner review."))
            if len(coverage["plan_names"].get(key) or set()) > 1:
                blocked_reasons.append(_("The same demand is present in multiple active Production Plans."))
            blocked_reason = " ".join(reason for reason in blocked_reasons if reason).strip()

            status = _derive_status(
                blocked_reason=blocked_reason,
                demand_qty=demand_qty,
                fg_allocated=fg_allocated,
                draft_plan_qty=draft_plan_qty,
                approved_plan_qty=approved_plan_qty,
                open_work_order_qty=open_work_order_qty,
                produced_qty=produced_qty,
                recommended_qty=recommended_qty,
            )
            plan_names = sorted(coverage["plan_names"].get(key) or set())
            work_order_names = coverage["work_order_names"].get(key) or []
            plan_items = coverage["plan_items"].get(key) or []
            submitted_plan_items = [item for item in plan_items if item["docstatus"] == 1]
            draft_work_orders = coverage["draft_work_order_names"].get(key) or []
            row.update(
                {
                    "bom_no": bom_map.get(item_code) or "",
                    "current_fg_stock": round(flt(stock_map.get(item_code)), 3),
                    "fg_allocated": round(fg_allocated, 3),
                    "draft_plan_qty": round(draft_plan_qty, 3),
                    "approved_plan_qty": round(approved_plan_qty, 3),
                    "open_work_order_qty": round(open_work_order_qty + manual_allocated, 3),
                    "recommended_qty": round(recommended_qty, 3),
                    "planning_status": status,
                    "next_action": _next_action(status),
                    "blocked_reason": blocked_reason,
                    "production_plan": plan_names[0] if plan_names else "",
                    "production_plan_item": submitted_plan_items[0]["name"] if len(submitted_plan_items) == 1 else "",
                    "production_plan_items": plan_items,
                    "work_order": work_order_names[0] if work_order_names else "",
                    "draft_work_order": draft_work_orders[0] if len(draft_work_orders) == 1 else "",
                    "manual_work_orders": coverage["manual_work_order_names"].get(item_code) or [],
                    "rm_governance_blockers": rm_blockers,
                    "calculation_explanation": _calculation_explanation(
                        demand_qty,
                        fg_allocated,
                        draft_plan_qty,
                        approved_plan_qty,
                        open_work_order_qty,
                        manual_allocated,
                        recommended_qty,
                    ),
                }
            )
            review_doctype, review_name = _review_target(row)
            _apply_review_state(row, review_map.get(f"{review_doctype}:{review_name}"))
            if len(submitted_plan_items) == 1:
                plan_item = submitted_plan_items[0]
                row["review_status"] = REVIEW_CONFIRMED
                row["reviewed_qty"] = round(flt(plan_item["planned_qty"]), 3)
                row["review_stale"] = 0
            row["can_create_work_order"] = int(
                not blocked_reason
                and row["review_status"] == REVIEW_CONFIRMED
                and not row["review_stale"]
                and (
                    flt(row["recommended_qty"]) > 0
                    or any(flt(item["remaining_qty"]) > 0 for item in submitted_plan_items)
                    or bool(row["draft_work_order"])
                )
            )
            output.append(row)
    output.sort(key=lambda row: (row["priority"], getdate(row["required_date"]), row["item_code"]))
    return output


def _derive_status(**values) -> str:
    if values["blocked_reason"]:
        return STATUS_BLOCKED
    if values["recommended_qty"] > 0:
        return STATUS_RECOMMENDED
    if values["produced_qty"] > 0 and values["open_work_order_qty"] > 0:
        return STATUS_PARTIAL
    if values["open_work_order_qty"] > 0:
        return STATUS_RELEASED
    if values["approved_plan_qty"] > 0:
        return STATUS_APPROVED_PLAN
    if values["draft_plan_qty"] > 0:
        return STATUS_DRAFT_PLAN
    if values["demand_qty"] <= values["fg_allocated"] + 1e-9:
        return STATUS_COMPLETED
    return STATUS_COMPLETED


def _next_action(status: str) -> str:
    return {
        STATUS_RECOMMENDED: _("Select for Draft Production Plan"),
        STATUS_DRAFT_PLAN: _("Review Draft Production Plan"),
        STATUS_APPROVED_PLAN: _("Open Approved Production Plan"),
        STATUS_RELEASED: _("Open Work Order"),
        STATUS_PARTIAL: _("Review Work Order Balance"),
        STATUS_COMPLETED: _("No production action required"),
        STATUS_BLOCKED: _("Resolve planning blocker"),
    }.get(status, "")


def _calculation_explanation(demand, fg, draft, approved, work_order, manual, recommended) -> str:
    return _(
        "Demand {0} - FG allocated {1} - Draft plan {2} - Approved plan {3} "
        "- Open Work Order {4} - Manual Work Order coverage {5} = Recommended {6}"
    ).format(*[round(flt(value), 3) for value in (demand, fg, draft, approved, work_order, manual, recommended)])


def _build_cards(rows) -> list[dict[str, object]]:
    return [
        {"label": "Recommended", "value": sum(row["planning_status"] == STATUS_RECOMMENDED for row in rows), "status": STATUS_RECOMMENDED},
        {"label": "Waiting Planning Approval", "value": sum(row["planning_status"] == STATUS_DRAFT_PLAN for row in rows), "status": STATUS_DRAFT_PLAN},
        {"label": "Approved in Production Plan", "value": sum(row["planning_status"] == STATUS_APPROVED_PLAN for row in rows), "status": STATUS_APPROVED_PLAN},
        {"label": "Released to Work Order", "value": sum(row["planning_status"] in {STATUS_RELEASED, STATUS_PARTIAL} for row in rows), "status": STATUS_RELEASED},
        {"label": "Blocked", "value": sum(row["planning_status"] == STATUS_BLOCKED for row in rows), "status": STATUS_BLOCKED},
    ]


def _validate_review_decision(decision, reviewed_qty, recommended_qty, remarks) -> None:
    if decision not in REVIEW_DECISIONS:
        frappe.throw(_("Select a valid planning review decision."))
    if decision == REVIEW_CONFIRMED:
        if reviewed_qty <= 0 or reviewed_qty - recommended_qty > 1e-9:
            frappe.throw(
                _("Reviewed quantity must be greater than zero and cannot exceed {0}.").format(
                    round(recommended_qty, 3)
                )
            )
    elif not remarks:
        frappe.throw(_("Remarks are mandatory when a recommendation is On Hold or Rejected."))


@frappe.whitelist()
def set_planning_review(
    recommendation_key="",
    decision="",
    reviewed_qty=0,
    remarks="",
    from_date=None,
    to_date=None,
) -> dict[str, object]:
    from calco_erp.calco_production.planning_release import set_planning_review as set_consolidated_review

    return set_consolidated_review(
        recommendation_key=recommendation_key,
        decision=decision,
        reviewed_qty=reviewed_qty,
        remarks=remarks,
        from_date=from_date,
        to_date=to_date,
    )


@frappe.whitelist()
def search_draft_production_plans(txt="", company="", *args, **kwargs) -> list[dict[str, str]]:
    frappe.has_permission("Production Plan", ptype="read", throw=True)
    filters = {"docstatus": 0}
    if company:
        filters["company"] = company
    rows = frappe.get_list(
        "Production Plan",
        filters=filters,
        or_filters={"name": ("like", f"%{txt}%")},
        fields=["name", "company", "from_date", "to_date"],
        order_by="modified desc",
        limit_page_length=20,
    )
    return [
        {
            "value": row.name,
            "description": f"{row.company} | {row.from_date or ''} to {row.to_date or ''}",
        }
        for row in rows
    ]


@frappe.whitelist()
def create_or_update_draft_production_plan(
    rows_json: str | None = None,
    production_plan: str | None = None,
    from_date=None,
    to_date=None,
    *args,
    **kwargs,
) -> dict[str, object]:
    _validate_source_fields()
    selections = json.loads(rows_json or "[]")
    if not selections:
        frappe.throw(_("Select at least one recommended planning line."))

    target = None
    if production_plan:
        target = frappe.get_doc("Production Plan", production_plan)
        target.check_permission("write")
        if target.docstatus != 0:
            frappe.throw(_("Only a Draft Production Plan can be updated."))
    else:
        frappe.has_permission("Production Plan", ptype="create", throw=True)

    data = _build_planning_data(
        from_date=from_date,
        to_date=to_date,
        exclude_production_plan=target.name if target else None,
    )
    current_rows = {row["recommendation_key"]: row for row in data["rows"]}
    selected = []
    for selection in selections:
        key = (selection.get("recommendation_key") or "").strip()
        row = current_rows.get(key)
        if not row:
            frappe.throw(_("Planning recommendation {0} is stale or no longer available.").format(key))
        qty = flt(selection.get("qty") or row["recommended_qty"])
        if row["planning_status"] == STATUS_BLOCKED:
            frappe.throw(_("{0} is blocked: {1}").format(row["item_code"], row["blocked_reason"]))
        if row["review_status"] != REVIEW_CONFIRMED or row["review_stale"]:
            frappe.throw(
                _("{0} requires a current Confirmed Production Planning Review.").format(
                    row["item_code"]
                )
            )
        allowed_qty = min(flt(row["recommended_qty"]), flt(row["reviewed_qty"]))
        if qty <= 0 or qty - allowed_qty > 1e-9:
            frappe.throw(
                _("Approved quantity for {0} must be greater than zero and cannot exceed {1}.").format(
                    row["item_code"], allowed_qty
                )
            )
        selected.append((row, qty))

    if not target:
        target = frappe.new_doc("Production Plan")
        target.company = data["company"]
        target.posting_date = nowdate()
        target.from_date = data["from_date"]
        target.to_date = data["to_date"]
        target.get_items_from = "Sales Order"
        target.combine_items = 0

    existing = {_document_plan_source_key(row): row for row in target.get("po_items") or []}
    sales_orders = {row.sales_order for row in target.get("sales_orders") or [] if row.sales_order}
    for row, qty in selected:
        plan_item = existing.get(row["recommendation_key"])
        if not plan_item:
            plan_item = target.append("po_items", {})
            existing[row["recommendation_key"]] = plan_item
        _set_plan_item(plan_item, row, qty)
        if row["source_type"] == SOURCE_SALES_ORDER and row["source_name"] not in sales_orders:
            target.append("sales_orders", {"sales_order": row["source_name"]})
            sales_orders.add(row["source_name"])

    target.flags.ignore_mandatory = False
    if target.is_new():
        target.insert()
    else:
        target.save()
    return {
        "production_plan": target.name,
        "count": len(selected),
        "route": ["Form", "Production Plan", target.name],
        "message": _("Draft Production Plan {0} updated with {1} planning line(s).").format(target.name, len(selected)),
    }


def _validate_source_fields() -> None:
    meta = frappe.get_meta("Production Plan Item")
    missing = [fieldname for fieldname in (*SOURCE_FIELDNAMES, *REVIEW_FIELDNAMES) if not meta.has_field(fieldname)]
    if missing:
        frappe.throw(_("Production Planning metadata is not synchronized. Missing fields: {0}").format(", ".join(missing)))


def _validate_review_fields() -> None:
    missing = []
    for doctype in ("Sales Order Item", "Sales Forecast Item", "Work Order"):
        meta = frappe.get_meta(doctype)
        missing.extend(
            f"{doctype}.{fieldname}"
            for fieldname in REVIEW_FIELDNAMES
            if not meta.has_field(fieldname)
        )
    if missing:
        frappe.throw(
            _("Production Planning Review metadata is not synchronized. Missing fields: {0}").format(
                ", ".join(missing)
            )
        )


def _document_plan_source_key(row) -> str:
    if row.get("sales_order_item"):
        return f"{SOURCE_SALES_ORDER}:{row.sales_order_item}"
    source_type = row.get("custom_planning_source_type") or ""
    source_row = row.get("custom_planning_source_row") or ""
    source_name = row.get("custom_planning_source_name") or ""
    return f"{source_type}:{source_row or source_name}" if source_type and (source_row or source_name) else ""


def _set_plan_item(plan_item, row, qty) -> None:
    plan_item.item_code = row["item_code"]
    plan_item.bom_no = row["bom_no"]
    plan_item.planned_qty = qty
    plan_item.pending_qty = qty
    plan_item.stock_uom = row["stock_uom"]
    plan_item.description = row["item_name"] or row["item_code"]
    plan_item.planned_start_date = get_datetime(f"{row['required_date']} 00:00:00")
    plan_item.custom_planning_source_type = row["source_type"]
    plan_item.custom_planning_source_doctype = row["source_doctype"]
    plan_item.custom_planning_source_name = row["source_name"]
    plan_item.custom_planning_source_row = row["source_row"]
    plan_item.custom_required_delivery_date = row["required_date"]
    plan_item.custom_planning_review_status = row.get("review_status") or ""
    plan_item.custom_planning_reviewed_qty = qty
    plan_item.custom_planning_reviewed_by = row.get("reviewed_by") or ""
    plan_item.custom_planning_reviewed_on = row.get("reviewed_on") or ""
    plan_item.custom_planning_review_remarks = row.get("review_remarks") or ""
    if row["source_type"] == SOURCE_SALES_ORDER:
        plan_item.sales_order = row["source_name"]
        plan_item.sales_order_item = row["source_row"]
    else:
        plan_item.sales_order = ""
        plan_item.sales_order_item = ""


def _get_default_company() -> str:
    company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    if not company:
        frappe.throw(_("Default Company is required for Production Planning."))
    return company
