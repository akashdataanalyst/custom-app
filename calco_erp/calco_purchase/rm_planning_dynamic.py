from __future__ import annotations
from calco_erp.calco_production.forecast_period import period_sql, LEGACY_NOTICE

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import frappe
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate

from calco_erp.calco_production.consumption_reporting import build_consumption_row_source


DYNAMIC_MAXIMUM_VERSION = "1.0.4"
DEMAND_DAYS = 30
RM_WAREHOUSE = "Stores - CPPL"
HISTORICAL_WEIGHTS = (0.20, 0.30, 0.50)
FORECAST_MONTH_KEYS = ("month_1", "month_2", "month_3")
PLANNING_CONFIDENCE_COMPLETE = "Complete"
PLANNING_CONFIDENCE_FORECAST_MISSING = "Forecast Missing"
PLANNING_CONFIDENCE_BOM_INCOMPLETE = "BOM Coverage Incomplete"
PLANNING_CONFIDENCE_FORECAST_AND_BOM_INCOMPLETE = "Forecast + BOM Incomplete"
APPROVED_BASELINE_PATH = Path(__file__).with_name("data") / "dynamic_maximum_2026_08.json"
APPROVED_FG_FORECAST_PATH = (
    Path(__file__).with_name("data") / "dynamic_maximum_2026_08_fg_forecast.json"
)


def get_dynamic_demand_profiles(
    item_codes: list[str], planning_date: str | date
) -> dict[str, dict[str, object]]:
    """Return monthly demand without modifying planning or transaction data."""
    if not item_codes:
        return {}

    planning_month = get_first_day(getdate(planning_date))
    baseline = load_approved_baseline(planning_month)
    packaged_forecast = load_packaged_fg_forecast(planning_month)
    live_forecast = get_submitted_forecast_fg_context(
        planning_month,
        get_last_day(planning_month),
    )
    authoritative = resolve_authoritative_fg_forecast(
        packaged_forecast,
        live_forecast,
    )

    if not authoritative["items"]:
        if baseline:
            return {
                code: profile
                for code, profile in baseline.items()
                if code in set(item_codes)
            }
        return build_live_evidence_profiles(item_codes, planning_month, {})

    forecast_context = explode_authoritative_fg_forecast(
        item_codes,
        authoritative,
    )
    missing_codes = [code for code in item_codes if code not in baseline]
    live_history = (
        build_live_evidence_profiles(missing_codes, planning_month, {})
        if missing_codes
        else {}
    )
    return {
        code: apply_forecast_context(
            baseline.get(code) or live_history[code],
            forecast_context["requirements"].get(code, 0),
            forecast_context,
        )
        for code in item_codes
    }


def get_three_month_forecast_profiles(
    item_codes: list[str], planning_date: str | date
) -> dict[str, object]:
    """Build one set-based, forecast-only three-calendar-month RM horizon."""
    windows = get_three_month_windows(planning_date)
    output = {
        code: {
            "months": {
                window["key"]: {
                    "forecast_qty": 0.0,
                    "forecast_status": "Missing",
                }
                for window in windows
            },
            "three_month_forecast": 0.0,
        }
        for code in item_codes
    }
    if not item_codes:
        for window in windows:
            window.update(_empty_month_coverage())
        return {
            "months": windows,
            "items": {},
            "available_months": 0,
            **build_planning_confidence_metadata(windows),
        }

    contexts = get_submitted_forecast_fg_contexts(windows)
    all_fg_codes = sorted(
        {
            code
            for context in contexts.values()
            for code in (context.get("fg_quantities") or {})
        }
    )
    bom_map = get_active_default_bom_map(all_fg_codes)
    explosions = get_bom_explosion_map(sorted(set(bom_map.values())), item_codes)

    for window in windows:
        key = window["key"]
        context = contexts[key]
        requirements = defaultdict(float)
        blocked_items = []
        total_forecast_qty = round(
            sum(
                flt(quantity)
                for quantity in (context.get("fg_quantities") or {}).values()
            ),
            3,
        )
        for fg_code, quantity in (context.get("fg_quantities") or {}).items():
            bom = bom_map.get(fg_code)
            if not bom:
                blocked_items.append(
                    {
                        "month_key": key,
                        "month": window["label"],
                        "fg_item": fg_code,
                        "fg_name": (context.get("fg_names") or {}).get(fg_code) or "",
                        "forecast_qty": round(flt(quantity), 3),
                        "blocker": "No submitted active default BOM",
                    }
                )
                continue
            for component in explosions.get(bom) or []:
                requirements[component["item_code"]] += flt(quantity) * flt(
                    component.get("qty_per_unit")
                )

        blocked_forecast_qty = round(
            sum(flt(row["forecast_qty"]) for row in blocked_items), 3
        )
        forecast_fg_count = len(context.get("fg_quantities") or {})
        blocked_fg_count = len(blocked_items)
        calculable_fg_count = forecast_fg_count - blocked_fg_count
        calculable_forecast_qty = round(
            max(0, total_forecast_qty - blocked_forecast_qty), 3
        )
        window.update(
            {
                "forecast_status": "Available" if context["exists"] else "Missing",
                "forecast_documents": context.get("forecast_documents") or [],
                "missing_boms": sorted(
                    {row["fg_item"] for row in blocked_items}
                ),
                "uom_issues": context.get("uom_issues") or [],
                "compatibility_cases": context.get("compatibility_cases") or [],
                "forecast_fg_count": forecast_fg_count,
                "calculable_fg_count": calculable_fg_count,
                "blocked_fg_count": blocked_fg_count,
                "total_forecast_qty": total_forecast_qty,
                "calculable_forecast_qty": calculable_forecast_qty,
                "blocked_forecast_qty": blocked_forecast_qty,
                "fg_count_coverage_percent": _coverage_percent(
                    calculable_fg_count, forecast_fg_count
                ),
                "fg_quantity_coverage_percent": _coverage_percent(
                    calculable_forecast_qty, total_forecast_qty
                ),
                "blocked_forecast_items": blocked_items,
            }
        )
        for code in item_codes:
            quantity = round(requirements.get(code, 0), 3)
            output[code]["months"][key] = {
                "forecast_qty": quantity,
                "forecast_status": window["forecast_status"],
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "label": window["label"],
                "short_label": window["short_label"],
            }
            output[code]["three_month_forecast"] += quantity

    planning_metadata = build_planning_confidence_metadata(windows)
    for profile in output.values():
        profile["three_month_forecast"] = round(
            profile["three_month_forecast"], 3
        )
        profile["forecast_complete"] = all(
            row["forecast_status"] == "Available"
            for row in profile["months"].values()
        )
        profile["planning_confidence"] = planning_metadata["planning_confidence"]
    return {
        "months": windows,
        "items": output,
        "available_months": sum(
            1 for window in windows if window["forecast_status"] == "Available"
        ),
        **planning_metadata,
    }


def _empty_month_coverage() -> dict[str, object]:
    return {
        "forecast_status": "Missing",
        "forecast_documents": [],
        "missing_boms": [],
        "uom_issues": [],
        "forecast_fg_count": 0,
        "calculable_fg_count": 0,
        "blocked_fg_count": 0,
        "total_forecast_qty": 0.0,
        "calculable_forecast_qty": 0.0,
        "blocked_forecast_qty": 0.0,
        "fg_count_coverage_percent": 100.0,
        "fg_quantity_coverage_percent": 100.0,
        "blocked_forecast_items": [],
    }


def _coverage_percent(calculable: float, total: float) -> float:
    if flt(total) <= 0:
        return 100.0
    return round((flt(calculable) * 100) / flt(total), 2)


def build_planning_confidence_metadata(
    windows: list[dict[str, object]],
) -> dict[str, object]:
    available_months = sum(
        1 for window in windows if window.get("forecast_status") == "Available"
    )
    blocked_items = [
        row
        for window in windows
        for row in (window.get("blocked_forecast_items") or [])
    ]
    forecast_missing = available_months < len(FORECAST_MONTH_KEYS)
    bom_incomplete = bool(blocked_items)
    if forecast_missing and bom_incomplete:
        planning_confidence = PLANNING_CONFIDENCE_FORECAST_AND_BOM_INCOMPLETE
    elif forecast_missing:
        planning_confidence = PLANNING_CONFIDENCE_FORECAST_MISSING
    elif bom_incomplete:
        planning_confidence = PLANNING_CONFIDENCE_BOM_INCOMPLETE
    else:
        planning_confidence = PLANNING_CONFIDENCE_COMPLETE

    total_fg_count = sum(
        int(window.get("forecast_fg_count") or 0) for window in windows
    )
    calculable_fg_count = sum(
        int(window.get("calculable_fg_count") or 0) for window in windows
    )
    total_forecast_qty = round(
        sum(flt(window.get("total_forecast_qty") or 0) for window in windows), 3
    )
    calculable_forecast_qty = round(
        sum(
            flt(window.get("calculable_forecast_qty") or 0)
            for window in windows
        ),
        3,
    )
    blocked_forecast_qty = round(
        sum(flt(row.get("forecast_qty") or 0) for row in blocked_items), 3
    )
    return {
        "forecast_coverage": {
            "available_months": available_months,
            "required_months": len(FORECAST_MONTH_KEYS),
            "complete": not forecast_missing,
            "missing_months": [
                window["label"]
                for window in windows
                if window.get("forecast_status") != "Available"
            ],
        },
        "bom_coverage": {
            "complete": not bom_incomplete,
            "forecast_fg_count": total_fg_count,
            "calculable_fg_count": calculable_fg_count,
            "blocked_fg_count": len(blocked_items),
            "total_forecast_qty": total_forecast_qty,
            "calculable_forecast_qty": calculable_forecast_qty,
            "blocked_forecast_qty": blocked_forecast_qty,
            "fg_count_coverage_percent": _coverage_percent(
                calculable_fg_count, total_fg_count
            ),
            "fg_quantity_coverage_percent": _coverage_percent(
                calculable_forecast_qty, total_forecast_qty
            ),
        },
        "planning_confidence": planning_confidence,
        "blocked_forecast_items": blocked_items,
        "blocked_forecast_qty": blocked_forecast_qty,
    }

def get_three_month_windows(planning_date: str | date) -> list[dict[str, object]]:
    first_month = get_first_day(getdate(planning_date))
    windows = []
    for index, key in enumerate(FORECAST_MONTH_KEYS):
        month_start = get_first_day(add_months(first_month, index))
        windows.append(
            {
                "key": key,
                "start_date": str(month_start),
                "end_date": str(get_last_day(month_start)),
                "label": month_start.strftime("%b %Y"),
                "short_label": month_start.strftime("%b").upper(),
            }
        )
    return windows


def get_submitted_forecast_fg_contexts(
    windows: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    contexts = {
        window["key"]: {
            "exists": False,
            "fg_quantities": {},
            "forecast_documents": [],
            "uom_issues": [],
            "fg_names": {},
        }
        for window in windows
    }
    if not windows or not frappe.db.exists("DocType", "Sales Forecast"):
        return contexts

    rows = frappe.db.sql(
        f"""
        select sf.name as forecast_name, sfi.item_code, item.item_name, sfi.uom,
               sfi.demand_qty, {period_sql()} as delivery_date, sfi.custom_forecast_period_start, item.stock_uom
        from `tabSales Forecast Item` sfi
        inner join `tabSales Forecast` sf on sf.name = sfi.parent
        inner join `tabItem` item on item.name = sfi.item_code
        where sf.docstatus = 1
          and coalesce(sf.status, '') != 'Cancelled'
          and sfi.parentfield = 'items'
          and {period_sql()} between %(from_date)s and %(to_date)s
          and coalesce(sfi.demand_qty, 0) >= 0
          and coalesce(item.disabled, 0) = 0
        order by {period_sql()}, sf.name, sfi.idx
        """,
        {
            "from_date": windows[0]["start_date"],
            "to_date": windows[-1]["end_date"],
        },
        as_dict=True,
    )
    conversions = get_uom_conversion_map(
        {(row["item_code"], row.get("uom") or "") for row in rows}
    )
    ranges = [
        (window, getdate(window["start_date"]), getdate(window["end_date"]))
        for window in windows
    ]
    quantities = {window["key"]: defaultdict(float) for window in windows}
    documents = {window["key"]: set() for window in windows}

    for row in rows:
        delivery_date = getdate(row["delivery_date"])
        window = next(
            (
                candidate
                for candidate, month_start, month_end in ranges
                if month_start <= delivery_date <= month_end
            ),
            None,
        )
        if not window:
            continue
        key = window["key"]
        if not row.get("custom_forecast_period_start"):
            contexts[key].setdefault("compatibility_cases", []).append({"forecast": row["forecast_name"], "item_code": row["item_code"], "reason": LEGACY_NOTICE})
        documents[key].add(row["forecast_name"])
        contexts[key]["fg_names"][row["item_code"]] = row.get("item_name") or ""
        stock_uom = row.get("stock_uom") or ""
        source_uom = row.get("uom") or stock_uom
        factor = (
            1
            if source_uom == stock_uom
            else conversions.get((row["item_code"], source_uom), 0)
        )
        if factor <= 0:
            contexts[key]["uom_issues"].append(
                {
                    "forecast": row["forecast_name"],
                    "item_code": row["item_code"],
                    "uom": source_uom,
                    "stock_uom": stock_uom,
                }
            )
            continue
        quantities[key][row["item_code"]] += flt(row["demand_qty"]) * factor

    for window in windows:
        key = window["key"]
        contexts[key].update(
            {
                "exists": bool(documents[key]),
                "fg_quantities": {
                    code: round(quantity, 3)
                    for code, quantity in quantities[key].items()
                },
                "forecast_documents": sorted(documents[key]),
            }
        )
    return contexts
def build_live_evidence_profiles(
    item_codes: list[str],
    planning_month: date,
    forecast_map: dict[str, float],
) -> dict[str, dict[str, object]]:
    if not item_codes:
        return {}

    monthly_maps = []
    availability_sets = []
    for offset in (3, 2, 1):
        month_start = get_first_day(add_months(planning_month, -offset))
        month_end = get_last_day(month_start)
        consumption, available = get_net_production_consumption_map(
            item_codes, month_start, month_end
        )
        monthly_maps.append(consumption)
        availability_sets.append(available)

    return {
        code: build_demand_profile(
            historical_values=[monthly_maps[index].get(code) for index in range(3)],
            historical_available=[code in availability_sets[index] for index in range(3)],
            forecast_requirement=forecast_map.get(code, 0),
            source="Live ERP evidence",
        )
        for code in item_codes
    }


def apply_forecast_context(
    profile: dict[str, object],
    forecast_requirement: float,
    forecast_context: dict[str, object],
) -> dict[str, object]:
    """Replace only the forecast component after FG-level source resolution."""
    output = dict(profile)
    forecast = max(flt(forecast_requirement), 0)
    weighted_history = output.get("weighted_historical_consumption")
    selected_monthly = max(flt(weighted_history), forecast)
    documents = forecast_context.get("forecast_documents") or []
    source = forecast_context.get("source") or "Submitted Sales Forecast"

    output.update(
        {
            "forecast_rm_requirement": round(forecast, 3),
            "selected_monthly_demand": round(selected_monthly, 3),
            "daily_demand": selected_monthly / DEMAND_DAYS,
            "source": source,
            "forecast_documents": documents,
            "forecast_missing_boms": forecast_context.get("missing_boms") or [],
            "version": DYNAMIC_MAXIMUM_VERSION,
        }
    )
    return output


def apply_live_forecast(
    profile: dict[str, object],
    forecast_requirement: float,
    forecast_context: dict[str, object],
) -> dict[str, object]:
    """Backward-compatible alias for callers introduced in RC2."""
    return apply_forecast_context(profile, forecast_requirement, forecast_context)


def load_packaged_fg_forecast(planning_month: date) -> dict[str, dict[str, object]]:
    if not APPROVED_FG_FORECAST_PATH.exists():
        return {}
    payload = json.loads(APPROVED_FG_FORECAST_PATH.read_text(encoding="utf-8"))
    if payload.get("planning_month") != planning_month.strftime("%Y-%m"):
        return {}
    return {
        row["item_code"]: {
            "quantity": max(flt(row.get("quantity")), 0),
            "bom": row.get("bom") or "",
            "components": row.get("components") or [],
        }
        for row in payload.get("rows") or []
        if (row.get("item_code") or "").strip()
    }

def load_approved_baseline(planning_month: date) -> dict[str, dict[str, object]]:
    if not APPROVED_BASELINE_PATH.exists():
        return {}
    payload = json.loads(APPROVED_BASELINE_PATH.read_text(encoding="utf-8"))
    if payload.get("planning_month") != planning_month.strftime("%Y-%m"):
        return {}

    output = {}
    for row in payload.get("rows") or []:
        code = (row.get("item_code") or "").strip()
        if not code:
            continue
        output[code] = build_demand_profile(
            historical_values=[
                row.get("may_consumption"),
                row.get("june_consumption"),
                row.get("july_consumption"),
            ],
            historical_available=[
                bool(row.get("may_available")),
                bool(row.get("june_available")),
                bool(row.get("july_available")),
            ],
            forecast_requirement=row.get("forecast_requirement") or 0,
            source="Approved August 2026 planning baseline",
        )
    return output


def build_demand_profile(
    *,
    historical_values: list[float | None],
    historical_available: list[bool],
    forecast_requirement: float,
    source: str,
) -> dict[str, object]:
    weighted_history, weights_used = calculate_weighted_history(
        historical_values, historical_available
    )
    forecast = max(flt(forecast_requirement), 0)
    selected_monthly = max(weighted_history or 0, forecast)
    return {
        "weighted_historical_consumption": round(weighted_history, 3)
        if weighted_history is not None
        else None,
        "forecast_rm_requirement": round(forecast, 3),
        "selected_monthly_demand": round(selected_monthly, 3),
        "daily_demand": selected_monthly / DEMAND_DAYS,
        "weights_used": [round(value, 6) for value in weights_used],
        "weights_normalized": bool(sum(historical_available) and not all(historical_available)),
        "source": source,
        "version": DYNAMIC_MAXIMUM_VERSION,
    }


def calculate_weighted_history(
    values: list[float | None], available: list[bool]
) -> tuple[float | None, list[float]]:
    if len(values) != 3 or len(available) != 3:
        raise ValueError("Three historical months are required.")
    available_weight = sum(
        HISTORICAL_WEIGHTS[index] for index in range(3) if available[index]
    )
    if available_weight <= 0:
        return None, [0.0, 0.0, 0.0]

    weights = [
        HISTORICAL_WEIGHTS[index] / available_weight if available[index] else 0.0
        for index in range(3)
    ]
    weighted = sum(
        max(flt(values[index]), 0) * weights[index]
        for index in range(3)
        if available[index]
    )
    return weighted, weights


def get_net_production_consumption_map(
    item_codes: list[str], from_date: date, to_date: date
) -> tuple[dict[str, float], set[str]]:
    if not item_codes:
        return {}, set()
    source = build_consumption_row_source()
    consumed = frappe.db.sql(
        f"""
        select pci.rm_code as item_code, sum(pci.rm_qty) as qty
        from ({source}) pci
        inner join `tabProduction Consumption Entry` pce on pce.name = pci.parent
        where pce.docstatus = 1
          and pce.warehouse = %(warehouse)s
          and date(pce.posting_datetime) between %(from_date)s and %(to_date)s
          and pci.rm_code in %(item_codes)s
        group by pci.rm_code
        """,
        {
            "warehouse": RM_WAREHOUSE,
            "from_date": from_date,
            "to_date": to_date,
            "item_codes": tuple(item_codes),
        },
        as_dict=True,
    )
    returns = get_traceable_production_returns(item_codes, from_date, to_date)
    consumed_map = {row["item_code"]: flt(row["qty"]) for row in consumed}
    return_map = {row["item_code"]: flt(row["qty"]) for row in returns}
    available = set(consumed_map) | set(return_map)
    return (
        {
            code: round(max(consumed_map.get(code, 0) - return_map.get(code, 0), 0), 3)
            for code in available
        },
        available,
    )


def get_traceable_production_returns(item_codes, from_date, to_date):
    return frappe.db.sql(
        """
        select sed.item_code, sum(sed.qty) as qty
        from `tabStock Entry Detail` sed
        inner join `tabStock Entry` ret on ret.name = sed.parent
        left join `tabStock Entry` issue on issue.name = ret.outgoing_stock_entry
        where ret.docstatus = 1
          and ret.purpose in ('Material Receipt', 'Material Transfer')
          and sed.t_warehouse = %(warehouse)s
          and ret.posting_date between %(from_date)s and %(to_date)s
          and sed.item_code in %(item_codes)s
          and (
            coalesce(ret.work_order, '') != ''
            or coalesce(ret.custom_production_consumption_entry, '') != ''
            or coalesce(issue.work_order, '') != ''
            or coalesce(issue.custom_production_consumption_entry, '') != ''
          )
        group by sed.item_code
        """,
        {
            "warehouse": RM_WAREHOUSE,
            "from_date": from_date,
            "to_date": to_date,
            "item_codes": tuple(item_codes),
        },
        as_dict=True,
    )


def get_forecast_rm_requirement_map(
    rm_item_codes: list[str], from_date: date, to_date: date
) -> dict[str, float]:
    return get_submitted_forecast_context(
        rm_item_codes,
        from_date,
        to_date,
    )["requirements"]


def get_submitted_forecast_context(
    rm_item_codes: list[str], from_date: date, to_date: date
) -> dict[str, object]:
    """Backward-compatible live-only RM explosion contract."""
    live_forecast = get_submitted_forecast_fg_context(from_date, to_date)
    if not live_forecast["exists"]:
        return {
            "exists": False,
            "requirements": {},
            "forecast_documents": [],
            "missing_boms": [],
        }
    authoritative = resolve_authoritative_fg_forecast({}, live_forecast)
    return explode_authoritative_fg_forecast(rm_item_codes, authoritative)


def get_submitted_forecast_fg_context(
    from_date: date, to_date: date
) -> dict[str, object]:
    empty = {
        "exists": False,
        "fg_quantities": {},
        "forecast_documents": [],
    }
    if not frappe.db.exists("DocType", "Sales Forecast"):
        return empty
    forecast_rows = frappe.db.sql(
        f"""
        select sf.name as forecast_name, sfi.item_code, sfi.uom,
               sfi.demand_qty, sfi.custom_forecast_period_start, item.stock_uom
        from `tabSales Forecast Item` sfi
        inner join `tabSales Forecast` sf on sf.name = sfi.parent
        inner join `tabItem` item on item.name = sfi.item_code
        where sf.docstatus = 1
          and coalesce(sf.status, '') != 'Cancelled'
          and sfi.parentfield = 'items'
          and {period_sql()} between %(from_date)s and %(to_date)s
          and coalesce(sfi.demand_qty, 0) > 0
          and coalesce(item.disabled, 0) = 0
        """,
        {"from_date": from_date, "to_date": to_date},
        as_dict=True,
    )
    if not forecast_rows:
        return empty

    conversions = get_uom_conversion_map(
        {(row["item_code"], row.get("uom") or "") for row in forecast_rows}
    )
    fg_quantities = defaultdict(float)
    for row in forecast_rows:
        stock_uom = row.get("stock_uom") or ""
        source_uom = row.get("uom") or stock_uom
        factor = (
            1
            if source_uom == stock_uom
            else conversions.get((row["item_code"], source_uom), 0)
        )
        if factor > 0:
            fg_quantities[row["item_code"]] += flt(row["demand_qty"]) * factor

    return {
        "exists": True,
        "compatibility_cases": [{"forecast": row["forecast_name"], "item_code": row["item_code"], "reason": LEGACY_NOTICE} for row in forecast_rows if not row.get("custom_forecast_period_start")],
        "fg_quantities": {
            code: round(quantity, 3) for code, quantity in fg_quantities.items()
        },
        "forecast_documents": sorted(
            {row["forecast_name"] for row in forecast_rows}
        ),
    }


def resolve_authoritative_fg_forecast(
    packaged_forecast: dict[str, dict[str, object]],
    live_forecast: dict[str, object],
) -> dict[str, object]:
    items = {
        code: {
            "quantity": max(flt(row.get("quantity")), 0),
            "source": "packaged",
            "bom": row.get("bom") or "",
            "components": row.get("components") or [],
        }
        for code, row in packaged_forecast.items()
    }
    for code, quantity in (live_forecast.get("fg_quantities") or {}).items():
        items[code] = {
            "quantity": max(flt(quantity), 0),
            "source": "live",
            "bom": "",
            "components": [],
        }
    return {
        "items": items,
        "forecast_documents": live_forecast.get("forecast_documents") or [],
    }


def explode_authoritative_fg_forecast(
    rm_item_codes: list[str],
    authoritative: dict[str, object],
) -> dict[str, object]:
    items = authoritative.get("items") or {}
    wanted_rm_codes = set(rm_item_codes)
    live_fg_codes = sorted(
        code for code, row in items.items() if row.get("source") == "live"
    )
    live_bom_map = get_active_default_bom_map(live_fg_codes)
    live_explosions = get_bom_explosion_map(
        list(live_bom_map.values()),
        wanted_rm_codes,
    )

    requirements = defaultdict(float)
    missing_boms = []
    live_count = 0
    packaged_count = 0
    for fg_code, row in items.items():
        quantity = max(flt(row.get("quantity")), 0)
        if row.get("source") == "live":
            live_count += 1
            bom = live_bom_map.get(fg_code)
            if not bom:
                missing_boms.append(fg_code)
                continue
            components = live_explosions.get(bom) or []
        else:
            packaged_count += 1
            if not row.get("bom") or not row.get("components"):
                missing_boms.append(fg_code)
                continue
            components = row.get("components") or []

        for component in components:
            item_code = component.get("item_code")
            if item_code in wanted_rm_codes:
                requirements[item_code] += quantity * flt(
                    component.get("qty_per_unit")
                )

    documents = authoritative.get("forecast_documents") or []
    if live_count and packaged_count:
        source = "FG-level forecast: submitted Sales Forecast with packaged fallback"
    elif live_count:
        source = "Submitted Sales Forecast"
    else:
        source = "Approved August 2026 planning baseline"
    if documents:
        source = f"{source}: {', '.join(documents)}"

    return {
        "exists": bool(items),
        "requirements": {
            code: round(quantity, 3) for code, quantity in requirements.items()
        },
        "forecast_documents": documents,
        "missing_boms": sorted(set(missing_boms)),
        "live_fg_count": live_count,
        "packaged_fg_count": packaged_count,
        "source": source,
    }

def get_active_default_bom_map(item_codes):
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select item, name
        from `tabBOM`
        where docstatus = 1 and is_active = 1 and is_default = 1
          and item in %(item_codes)s
        order by item asc, modified desc
        """,
        {"item_codes": tuple(item_codes)},
        as_dict=True,
    )
    output = {}
    for row in rows:
        output.setdefault(row["item"], row["name"])
    return output


def get_bom_explosion_map(bom_names, rm_item_codes):
    if not bom_names:
        return {}
    rows = frappe.db.sql(
        """
        select bei.parent, bei.item_code,
               coalesce(nullif(bei.qty_consumed_per_unit, 0), bei.stock_qty, 0) as qty_per_unit
        from `tabBOM Explosion Item` bei
        inner join `tabItem` item on item.name = bei.item_code
        where bei.parent in %(bom_names)s
          and item.item_group = 'Raw Material'
          and coalesce(item.disabled, 0) = 0
        """,
        {"bom_names": tuple(bom_names)},
        as_dict=True,
    )
    output = defaultdict(list)
    for row in rows:
        if row["item_code"] in rm_item_codes:
            output[row["parent"]].append(dict(row))
    return output


def get_uom_conversion_map(item_uoms):
    item_codes = sorted({item for item, _uom in item_uoms if item})
    if not item_codes:
        return {}
    rows = frappe.get_all(
        "UOM Conversion Detail",
        filters={"parent": ("in", item_codes)},
        fields=["parent", "uom", "conversion_factor"],
        limit_page_length=0,
    )
    return {(row["parent"], row["uom"]): flt(row["conversion_factor"]) for row in rows}
