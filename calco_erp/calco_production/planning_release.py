from __future__ import annotations

import json
import time
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from calco_erp.calco_production import fg_planning


STATUS_WAITING = "Waiting Planning Approval"
STATUS_READY = "Ready to Release"
STATUS_PARTIAL = "Partially Released to Production"
STATUS_RELEASED = "Released to Production"
STATUS_COVERED = "Covered by Production"
STATUS_ON_HOLD = "On Hold"
STATUS_REJECTED = "Rejected"
STATUS_BLOCKED = "Blocked"

REVIEW_PENDING = "Pending Review"
REVIEW_CONFIRMED = "Confirmed"
REVIEW_ON_HOLD = "On Hold"
REVIEW_REJECTED = "Rejected"
REVIEW_DECISIONS = {REVIEW_CONFIRMED, REVIEW_ON_HOLD, REVIEW_REJECTED}

RELEASE_FIELDS = (
    "custom_calco_release_to_production",
    "custom_release_key",
    "custom_release_requirement_key",
    "custom_release_authority",
    "custom_release_item_code",
    "custom_release_qty",
    "custom_release_priority",
    "custom_release_required_date",
    "custom_release_period_start",
    "custom_release_period_end",
    "custom_release_source_details",
    "custom_released_by",
    "custom_released_on",
    "custom_release_reviewed_by",
    "custom_release_reviewed_on",
    "custom_release_review_remarks",
)
RELEASE_AUTHORITY_FIELDS = (
    "custom_calco_release_to_production",
    "custom_release_key",
    "custom_release_requirement_key",
    "custom_release_company",
    "custom_release_priority",
    "custom_release_source_details",
    "custom_released_by",
    "custom_released_on",
    "custom_release_reviewed_by",
    "custom_release_reviewed_on",
    "custom_release_review_remarks",
)
PLANNING_ROLES = {
    "System Manager", "Sales User", "Sales Manager", "Customer Service",
    "Manufacturing User", "Manufacturing Manager", "Production Manager", "Production Head",
}
CUSTOMER_SERVICE_ROLES = {"System Manager", "Sales User", "Sales Manager", "Customer Service"}
PRODUCTION_ROLES = {
    "System Manager", "Manufacturing User", "Manufacturing Manager", "Production Manager", "Production Head",
}


def ensure_planning_access():
    if not PLANNING_ROLES.intersection(frappe.get_roles()):
        frappe.throw(_("You are not permitted to use Production Planning Center."), frappe.PermissionError)


def ensure_customer_service_access():
    if not CUSTOMER_SERVICE_ROLES.intersection(frappe.get_roles()):
        frappe.throw(_("Only Customer Service may review or release production demand."), frappe.PermissionError)


def ensure_production_access():
    if not PRODUCTION_ROLES.intersection(frappe.get_roles()):
        frappe.throw(_("Only Production may create Work Orders from released demand."), frappe.PermissionError)


def build_planning_data(
    from_date=None,
    to_date=None,
    item_code=None,
    source_type=None,
    planning_status=None,
    review_status=None,
    **_kwargs,
):
    from calco_erp.calco_production import production_planning as legacy

    ensure_planning_access()
    started = time.perf_counter()
    default_from, default_to = legacy._default_period(from_date)
    from_date = getdate(from_date) if from_date else default_from
    to_date = getdate(to_date) if to_date else default_to
    if to_date < from_date:
        frappe.throw(_("To Date cannot be before From Date."))

    company = legacy._get_default_company()
    items = fg_planning.get_fg_items({"item_code": (item_code or "").strip()})
    item_map = {row["item_code"]: dict(row) for row in items}
    if not item_map:
        return _empty_payload(company, from_date, to_date, started)

    demands = legacy._get_sales_order_demands(company, from_date, to_date, item_map)
    demands.extend(legacy._get_forecast_demands(company, from_date, to_date, item_map))
    review_map = legacy._get_review_map(demands)
    grouped = _group_sources(demands, item_map, from_date, to_date, review_map)
    from calco_erp.planning_upgrade.fg_coverage import coverage as released_fg_coverage
    stock_map, fg_evidence = released_fg_coverage(list(item_map), company, demands)
    coverage = _get_item_coverage(list(item_map), from_date, to_date)
    rows = [_build_requirement(row, stock_map.get(row["item_code"], 0), coverage) for row in grouped]
    for row in rows:
        row["fg_stock_evidence"] = fg_evidence.get(row["item_code"], {})

    requested_source = (source_type or "").strip()
    requested_status = (planning_status or "").strip()
    requested_review = (review_status or "").strip()
    if requested_source:
        rows = [row for row in rows if requested_source in row["source_types"]]
    card_rows = rows
    if requested_status:
        rows = [row for row in rows if row["planning_status"] == requested_status]
    if requested_review:
        rows = [row for row in rows if row["review_status"] == requested_review]
    rows.sort(key=lambda row: (row["priority_rank"], getdate(row["required_date"]), row["item_code"]))

    return {
        "company": company,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "warehouse_scope": "Controlled FG Released warehouse; external commitments excluded",
        "cards": _build_cards(card_rows),
        "rows": rows,
        "production_queue": build_production_queue(company, item_code),
        "filters": {
            "item_code": (item_code or "").strip(),
            "source_type": requested_source,
            "planning_status": requested_status,
            "review_status": requested_review,
        },
        "formulas": {
            "demand_basis": "MAX(Submitted Sales Forecast Qty, Firm Sales Order Qty)",
            "net_requirement": "MAX(Demand Basis - FG Coverage - External Active Production Coverage, 0)",
            "remaining_to_release": "MAX(MIN(Reviewed Qty, Net Requirement) - Active Released Qty, 0)",
        },
        "backend_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _empty_payload(company, from_date, to_date, started):
    return {
        "company": company,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "warehouse_scope": "Controlled FG Released warehouse; external commitments excluded",
        "cards": _build_cards([]),
        "rows": [],
        "production_queue": [],
        "filters": {},
        "formulas": {},
        "backend_time_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def requirement_key(item_code, from_date, to_date):
    return f"FG:{item_code}:{getdate(from_date)}:{getdate(to_date)}"


def _group_sources(demands, item_map, from_date, to_date, review_map):
    from calco_erp.calco_production import production_planning as legacy

    grouped = defaultdict(list)
    for demand in demands:
        grouped[demand["item_code"]].append(demand)
    output = []
    for item_code, sources in grouped.items():
        sources.sort(key=lambda row: (row["priority"], getdate(row["required_date"]), row["source_name"], row["source_row"]))
        sales_qty = sum(flt(row["demand_qty"]) for row in sources if row["source_type"] == "Sales Order")
        forecast_qty = sum(flt(row["demand_qty"]) for row in sources if row["source_type"] == "Forecast")
        details = []
        for row in sources:
            review_doctype, review_name = legacy._review_target(row)
            review = review_map.get(f"{review_doctype}:{review_name}") or {}
            details.append({
                "source_type": row["source_type"],
                "source_doctype": row["source_doctype"],
                "source_name": row["source_name"],
                "source_row": row["source_row"],
                "review_doctype": review_doctype,
                "review_name": review_name,
                "qty": round(flt(row["demand_qty"]), 3),
                "required_date": row["required_date"],
                "review_status": (review.get("custom_planning_review_status") or "").strip(),
                "reviewed_qty": round(flt(review.get("custom_planning_reviewed_qty")), 3),
                "reviewed_by": review.get("custom_planning_reviewed_by") or "",
                "reviewed_on": review.get("custom_planning_reviewed_on") or "",
                "review_remarks": review.get("custom_planning_review_remarks") or "",
            })
        demand_basis = max(sales_qty, forecast_qty)
        output.append({
            "recommendation_key": requirement_key(item_code, from_date, to_date),
            "item_code": item_code,
            "item_name": item_map[item_code].get("item_name") or "",
            "stock_uom": item_map[item_code].get("stock_uom") or "",
            "period_start": str(from_date),
            "period_end": str(to_date),
            "required_date": str(min(getdate(row["required_date"]) for row in sources)),
            "forecast_qty": round(forecast_qty, 3),
            "sales_order_qty": round(sales_qty, 3),
            "demand_basis": round(demand_basis, 3),
            "demand_qty": round(demand_basis, 3),
            "basis_source": "Sales Order" if sales_qty >= forecast_qty and sales_qty > 0 else "Forecast",
            "priority": "High" if sales_qty > 0 else "Normal",
            "priority_rank": 1 if sales_qty > 0 else 2,
            "source_type": "Consolidated",
            "source_types": sorted({row["source_type"] for row in sources}),
            "source_name": ", ".join(sorted({row["source_name"] for row in sources})),
            "source_details": details,
            **_consolidated_review(details),
        })
    return output


def _consolidated_review(details):
    signatures = {
        (
            row["review_status"], flt(row["reviewed_qty"]), row["reviewed_by"],
            str(row["reviewed_on"] or ""), row["review_remarks"],
        )
        for row in details
    }
    if len(signatures) != 1:
        return _pending_review(stale=1)
    status, qty, reviewed_by, reviewed_on, remarks = signatures.pop()
    if not status:
        return _pending_review(stale=0)
    return {
        "review_status": status,
        "reviewed_qty": round(flt(qty), 3),
        "reviewed_by": reviewed_by,
        "reviewed_on": reviewed_on,
        "review_remarks": remarks,
        "review_stale": 0,
    }


def _pending_review(stale):
    return {
        "review_status": REVIEW_PENDING,
        "reviewed_qty": 0.0,
        "reviewed_by": "",
        "reviewed_on": "",
        "review_remarks": "",
        "review_stale": stale,
    }


def _get_item_coverage(item_codes, from_date=None, to_date=None):
    output = {
        "external": defaultdict(float),
        "external_plans": defaultdict(set),
        "external_work_orders": defaultdict(list),
        "manual_work_orders": defaultdict(list),
        "released": defaultdict(float),
        "release_plans": defaultdict(list),
        "release_work_orders": defaultdict(float),
    }
    if not item_codes:
        return output
    from_date = getdate(from_date or "1900-01-01")
    to_date = getdate(to_date or "2999-12-31")
    release_metadata_ready = frappe.get_meta("Production Plan").has_field("custom_calco_release_to_production")
    release_authority_ready = frappe.get_meta("Production Requirement").has_field(
        "custom_calco_release_to_production"
    )
    if release_authority_ready:
        release_rows = frappe.db.sql(
            """
            select pr.name as production_requirement, pr.status,
                   pri.item_code, pri.requested_qty
            from `tabProduction Requirement` pr
            inner join `tabProduction Requirement Item` pri on pri.parent = pr.name
            where pr.docstatus < 2
              and pr.custom_calco_release_to_production = 1
              and pr.week_start_date = %(from_date)s
              and pr.week_end_date = %(to_date)s
              and pri.item_code in %(item_codes)s
            order by pr.creation asc
            """,
            {"item_codes": tuple(item_codes), "from_date": from_date, "to_date": to_date},
            as_dict=True,
        )
        for release in release_rows:
            output["released"][release.item_code] += flt(release.requested_qty)
            output["release_plans"][release.item_code].append({
                "production_requirement": release.production_requirement,
                "status": release.status,
            })

    release_columns = (
        "coalesce(pp.custom_calco_release_to_production, 0) as is_release, "
        "coalesce(pp.custom_release_qty, 0) as release_qty, "
        "coalesce(pp.custom_release_item_code, '') as release_item"
        if release_metadata_ready
        else "0 as is_release, 0 as release_qty, '' as release_item"
    )
    if release_metadata_ready:
        scope_condition = """
          and (
            (pp.custom_calco_release_to_production = 1
             and pp.custom_release_item_code in %(item_codes)s
             and pp.custom_release_period_start = %(from_date)s
             and pp.custom_release_period_end = %(to_date)s)
            or
            (coalesce(pp.custom_calco_release_to_production, 0) = 0
             and ppi.item_code in %(item_codes)s
             and coalesce(ppi.custom_required_delivery_date, date(ppi.planned_start_date))
                 between %(from_date)s and %(to_date)s)
          )
        """
    else:
        scope_condition = """
          and ppi.item_code in %(item_codes)s
          and coalesce(ppi.custom_required_delivery_date, date(ppi.planned_start_date))
              between %(from_date)s and %(to_date)s
        """
    plan_rows = frappe.db.sql(
        f"""
        select pp.name as production_plan, pp.docstatus, pp.status,
               {release_columns},
               ppi.name as plan_item, ppi.item_code, ppi.planned_qty, ppi.ordered_qty
        from `tabProduction Plan` pp
        left join `tabProduction Plan Item` ppi on ppi.parent = pp.name
        where pp.docstatus < 2
          {scope_condition}
        order by pp.creation asc, ppi.idx asc
        """,
        {"item_codes": tuple(item_codes), "from_date": from_date, "to_date": to_date},
        as_dict=True,
    )
    work_orders = frappe.get_all(
        "Work Order",
        filters={"docstatus": ("<", 2), "production_item": ("in", item_codes)},
        fields=["name", "docstatus", "status", "production_item", "qty", "produced_qty", "production_plan", "production_plan_item", "planned_start_date"],
        limit_page_length=0,
    )
    draft_by_plan_item = defaultdict(float)
    for work_order in work_orders:
        if work_order.docstatus == 0 and work_order.production_plan_item:
            draft_by_plan_item[work_order.production_plan_item] += max(flt(work_order.qty) - flt(work_order.produced_qty), 0)

    release_plan_names = {row.production_plan for row in plan_rows if row.is_release}
    relevant_plan_items = {row.plan_item for row in plan_rows if row.plan_item}
    seen_release_plans = set()
    for row in plan_rows:
        item_code = row.item_code or row.release_item
        if not item_code:
            continue
        if row.is_release:
            if row.production_plan not in seen_release_plans and not release_authority_ready:
                seen_release_plans.add(row.production_plan)
                output["released"][item_code] += flt(row.release_qty)
                output["release_plans"][item_code].append({
                    "production_plan": row.production_plan,
                    "docstatus": row.docstatus,
                    "status": row.status,
                })
            continue
        if not row.plan_item:
            continue
        draft_qty = draft_by_plan_item.get(row.plan_item, 0)
        balance = flt(row.planned_qty) if row.docstatus == 0 else max(flt(row.planned_qty) - flt(row.ordered_qty) - draft_qty, 0)
        output["external"][item_code] += balance
        output["external_plans"][item_code].add(row.production_plan)

    for work_order in work_orders:
        if work_order.status in {"Cancelled", "Completed", "Closed", "Stopped"}:
            continue
        if work_order.production_plan_item:
            if work_order.production_plan_item not in relevant_plan_items:
                continue
        elif not work_order.planned_start_date or not (
            from_date <= getdate(work_order.planned_start_date) <= to_date
        ):
            continue
        open_qty = max(flt(work_order.qty) - flt(work_order.produced_qty), 0)
        if open_qty <= 0:
            continue
        if work_order.production_plan in release_plan_names:
            output["release_work_orders"][work_order.production_item] += open_qty
        else:
            output["external"][work_order.production_item] += open_qty
            output["external_work_orders"][work_order.production_item].append(work_order.name)
            if not work_order.production_plan_item:
                output["manual_work_orders"][work_order.production_item].append(work_order.name)
    return output


def _build_requirement(row, current_stock, coverage):
    item_code = row["item_code"]
    fg_allocated = min(max(flt(current_stock), 0), flt(row["demand_basis"]))
    external = max(flt(coverage["external"].get(item_code)), 0)
    net_requirement = max(flt(row["demand_basis"]) - fg_allocated - external, 0)
    released = max(flt(coverage["released"].get(item_code)), 0)
    remaining = max(net_requirement - released, 0)
    reviewed_ceiling = min(max(flt(row["reviewed_qty"]), 0), net_requirement)
    remaining_to_release = max(reviewed_ceiling - released, 0)
    manual = coverage["manual_work_orders"].get(item_code) or []

    if manual:
        status, blocker = STATUS_BLOCKED, _("Unlinked manual Work Order coverage requires planner review.")
    elif row["review_status"] == REVIEW_ON_HOLD:
        status, blocker = STATUS_ON_HOLD, ""
    elif row["review_status"] == REVIEW_REJECTED:
        status, blocker = STATUS_REJECTED, ""
    elif net_requirement <= 1e-9:
        status, blocker = STATUS_COVERED, ""
    elif released > 0 and remaining > 1e-9:
        status, blocker = STATUS_PARTIAL, ""
    elif released > 0:
        status, blocker = STATUS_RELEASED, ""
    elif row["review_status"] == REVIEW_CONFIRMED and not row["review_stale"]:
        status, blocker = STATUS_READY, ""
    else:
        status, blocker = STATUS_WAITING, ""

    row.update({
        "current_fg_stock": round(flt(current_stock), 3),
        "fg_allocated": round(fg_allocated, 3),
        "open_production_qty": round(external, 3),
        "open_work_order_qty": round(external, 3),
        "already_released_qty": round(released, 3),
        "released_work_order_qty": round(flt(coverage["release_work_orders"].get(item_code)), 3),
        "net_requirement": round(net_requirement, 3),
        "recommended_qty": round(remaining, 3),
        "remaining_qty": round(remaining, 3),
        "remaining_to_release": round(remaining_to_release, 3),
        "planning_status": status,
        "blocked_reason": blocker,
        "next_action": _next_action(status),
        "production_plans": sorted(coverage["external_plans"].get(item_code) or []),
        "work_orders": coverage["external_work_orders"].get(item_code) or [],
        "release_plans": coverage["release_plans"].get(item_code) or [],
        "manual_work_orders": manual,
        "can_release": int(status in {STATUS_READY, STATUS_PARTIAL} and remaining_to_release > 0),
        "calculation_explanation": _(
            "Demand Basis {0} - FG Coverage {1} - External Production Coverage {2} = "
            "Net Requirement {3}; Active Released Qty {4}; Remaining {5}"
        ).format(*[round(flt(value), 3) for value in (
            row["demand_basis"], fg_allocated, external, net_requirement, released, remaining
        )]),
    })
    return row


def _next_action(status):
    return {
        STATUS_WAITING: _("Complete Planning Review"),
        STATUS_READY: _("Release to Production"),
        STATUS_PARTIAL: _("Release remaining approved quantity"),
        STATUS_RELEASED: _("Production creates Work Orders"),
        STATUS_COVERED: _("No planning action required"),
        STATUS_ON_HOLD: _("Resolve planning hold"),
        STATUS_REJECTED: _("No production release"),
        STATUS_BLOCKED: _("Resolve duplicate production coverage"),
    }.get(status, _("Review requirement"))


def _build_cards(rows):
    statuses = (STATUS_WAITING, STATUS_READY, STATUS_PARTIAL, STATUS_RELEASED, STATUS_BLOCKED)
    return [{"label": status, "value": sum(row["planning_status"] == status for row in rows), "status": status} for status in statuses]


def _get_requirement(recommendation_key, from_date, to_date):
    data = build_planning_data(from_date=from_date, to_date=to_date)
    row = next((item for item in data["rows"] if item["recommendation_key"] == recommendation_key), None)
    if not row:
        frappe.throw(_("This consolidated planning requirement is stale or no longer available."))
    return row, data


@frappe.whitelist()
def get_release_context(recommendation_key="", from_date=None, to_date=None, **_kwargs):
    row, data = _get_requirement(recommendation_key, from_date, to_date)
    from calco_erp.planning_upgrade.planning import check_month
    material_check=check_month(from_date,to_date)
    allocation=next((r for r in material_check['rows'] if r['key']==recommendation_key),None)
    return {**row, "company": data["company"], "release_token": frappe.generate_hash(length=20),
            "rm_allocation":allocation,"rm_blockers":material_check['blockers']}


@frappe.whitelist()
def set_planning_review(recommendation_key="", decision="", reviewed_qty=0, remarks="", from_date=None, to_date=None, **_kwargs):
    ensure_customer_service_access()
    row, _data = _get_requirement(recommendation_key, from_date, to_date)
    decision = (decision or "").strip()
    reviewed_qty = flt(reviewed_qty)
    remarks = (remarks or "").strip()
    if decision not in REVIEW_DECISIONS:
        frappe.throw(_("Select a valid planning review decision."))
    if decision == REVIEW_CONFIRMED and (reviewed_qty <= 0 or reviewed_qty - flt(row["net_requirement"]) > 1e-9):
        frappe.throw(_("Reviewed quantity must be greater than zero and cannot exceed {0}.").format(row["net_requirement"]))
    if decision != REVIEW_CONFIRMED and not remarks:
        frappe.throw(_("Remarks are mandatory when a requirement is On Hold or Rejected."))

    updates = {
        "custom_planning_review_status": decision,
        "custom_planning_reviewed_qty": reviewed_qty if decision == REVIEW_CONFIRMED else 0,
        "custom_planning_reviewed_by": frappe.session.user,
        "custom_planning_reviewed_on": now_datetime(),
        "custom_planning_review_remarks": remarks,
    }
    commented = set()
    for source in row["source_details"]:
        frappe.db.sql(f"select name from `tab{source['review_doctype']}` where name=%s for update", (source["review_name"],))
        frappe.db.set_value(source["review_doctype"], source["review_name"], updates, update_modified=False)
        key = (source["source_doctype"], source["source_name"])
        if key not in commented:
            frappe.get_doc(*key).add_comment(
                "Info",
                _("Consolidated Production Planning Review: {0}; quantity {1}; remarks: {2}").format(
                    decision, round(reviewed_qty, 3) if decision == REVIEW_CONFIRMED else 0, remarks or "-"
                ),
            )
            commented.add(key)
    return {"recommendation_key": recommendation_key, "review_status": decision, "message": _("Production Planning Review saved.")}


def _validate_release_metadata():
    plan_meta = frappe.get_meta("Production Plan")
    authority_meta = frappe.get_meta("Production Requirement")
    missing = [
        f"Production Plan.{fieldname}"
        for fieldname in RELEASE_FIELDS
        if not plan_meta.has_field(fieldname)
    ]
    missing.extend(
        f"Production Requirement.{fieldname}"
        for fieldname in RELEASE_AUTHORITY_FIELDS
        if not authority_meta.has_field(fieldname)
    )
    if not authority_meta.has_field("custom_rm_planning_snapshot"):
        missing.append("Production Requirement.custom_rm_planning_snapshot")
    if missing:
        frappe.throw(_("Production release metadata is not synchronized. Missing fields: {0}").format(", ".join(missing)))


@frappe.whitelist()
def release_to_production(recommendation_key="", qty=0, priority="Normal", release_token="", from_date=None, to_date=None, **_kwargs):
    ensure_customer_service_access()
    _validate_release_metadata()
    release_token = (release_token or "").strip()
    if not release_token:
        frappe.throw(_("Release request token is required."))
    existing = frappe.db.get_value("Production Requirement", {"custom_release_key": release_token}, "name")
    if existing:
        return _release_result(existing, existing=True)

    row, data = _get_requirement(recommendation_key, from_date, to_date)
    frappe.db.sql("SELECT name FROM tabCompany WHERE name=%s FOR UPDATE", data["company"])
    for source in sorted(row["source_details"], key=lambda r: (r["review_doctype"], r["review_name"])):
        frappe.db.sql(f"select name from `tab{source['review_doctype']}` where name=%s for update", (source["review_name"],))
    matches = frappe.db.sql("SELECT name FROM `tabProduction Requirement` WHERE custom_release_key=%s FOR UPDATE", release_token)
    existing = matches[0][0] if matches else None
    if existing:
        return _release_result(existing, existing=True)
    row, data = _get_requirement(recommendation_key, from_date, to_date)
    qty = flt(qty)
    if row["review_status"] != REVIEW_CONFIRMED or row["review_stale"]:
        frappe.throw(_("A current Confirmed Production Planning Review is required."))
    if qty <= 0 or qty - flt(row["remaining_to_release"]) > 1e-9:
        frappe.throw(_("Release quantity must be greater than zero and cannot exceed {0}.").format(row["remaining_to_release"]))
    if priority not in {"High", "Normal", "Low"}:
        frappe.throw(_("Select a valid production priority."))

    from calco_erp.planning_upgrade.planning import release_snapshot, authorized_snapshot_creation
    snapshot = release_snapshot(recommendation_key, qty, from_date, to_date)
    row = json.loads(snapshot)["requirement"]
    authority = frappe.new_doc("Production Requirement")
    authority.custom_rm_planning_snapshot = snapshot
    authority.week_start_date = row["period_start"]
    authority.week_end_date = row["period_end"]
    authority.pull_sales_orders = 0
    authority.custom_calco_release_to_production = 1
    authority.custom_release_key = release_token
    authority.custom_release_requirement_key = recommendation_key
    authority.custom_release_company = data["company"]
    authority.custom_release_priority = priority
    authority.custom_release_source_details = json.dumps(row["source_details"], default=str, sort_keys=True)
    authority.custom_released_by = frappe.session.user
    authority.custom_released_on = now_datetime()
    authority.custom_release_reviewed_by = row["reviewed_by"]
    authority.custom_release_reviewed_on = row["reviewed_on"]
    authority.custom_release_review_remarks = row["review_remarks"]
    source = get_primary_source(authority)
    authority.append("items", {
        "source_type": source["source_type"],
        "source_reference": source["source_name"],
        "sales_order_reference": source["source_name"] if source["source_type"] == "Sales Order" else "",
        "item_code": row["item_code"],
        "item_name": row["item_name"],
        "requested_qty": qty,
        "net_required_qty": qty,
        "target_date": row["required_date"],
        "priority_rank": {"High": 1, "Normal": 2, "Low": 3}[priority],
        "line_status": "Open",
    })
    with authorized_snapshot_creation():
        authority.insert(ignore_permissions=True)
    return _release_result(authority.name, existing=False)


def _release_result(authority_name, existing):
    authority = get_release_authority(authority_name)
    item = get_release_item(authority)
    return {
        "production_requirement": authority.name,
        "released_qty": flt(item.requested_qty),
        "existing": int(existing),
        "message": _("Production requirement released to Production as {0}.").format(authority.name),
    }


def build_production_queue(company="", item_code=""):
    if not frappe.get_meta("Production Requirement").has_field("custom_calco_release_to_production"):
        return []
    conditions = ["pr.custom_calco_release_to_production = 1", "pr.docstatus < 2"]
    values = {}
    if company:
        conditions.append("pr.custom_release_company = %(company)s")
        values["company"] = company
    if item_code:
        conditions.append("pri.item_code = %(item_code)s")
        values["item_code"] = item_code
    releases = frappe.db.sql(
        f"""
        select pr.name as production_requirement,
               pr.custom_release_company as company,
               pr.custom_release_key as release_key,
               pr.custom_release_priority as priority,
               pr.custom_release_source_details as source_details,
               pri.item_code, item.item_name,
               pri.requested_qty as released_qty,
               pri.target_date as required_date,
               pp.name as production_plan,
               ppi.name as production_plan_item,
               ppi.ordered_qty
        from `tabProduction Requirement` pr
        inner join `tabProduction Requirement Item` pri on pri.parent = pr.name
        left join `tabItem` item on item.name = pri.item_code
        left join `tabProduction Plan` pp
          on pp.custom_release_authority = pr.name and pp.docstatus < 2
        left join `tabProduction Plan Item` ppi
          on ppi.parent = pp.name and ppi.item_code = pri.item_code
        where {" and ".join(conditions)}
        order by pri.target_date asc, pr.creation asc
        """,
        values,
        as_dict=True,
    )
    output = []
    for release in releases:
        draft_wos = (
            frappe.get_all(
                "Work Order",
                filters={"production_plan": release.production_plan, "docstatus": 0},
                fields=["name", "qty"],
                limit_page_length=0,
            )
            if release.production_plan
            else []
        )
        draft_qty = sum(flt(row.qty) for row in draft_wos)
        converted = min(
            flt(release.released_qty),
            flt(release.ordered_qty) + draft_qty,
        )
        remaining = max(flt(release.released_qty) - converted, 0)
        status = "Covered by Production" if remaining <= 1e-9 else (
            "Partially Converted" if converted > 0 else "Released to Production"
        )
        output.append({
            "production_requirement": release.production_requirement,
            "production_plan": release.production_plan or "",
            "production_plan_item": release.production_plan_item or "",
            "item_code": release.item_code,
            "item_name": release.item_name or "",
            "released_qty": round(flt(release.released_qty), 3),
            "work_order_qty": round(converted, 3),
            "remaining_qty": round(remaining, 3),
            "required_date": str(release.required_date or ""),
            "priority": release.priority or "Normal",
            "source_summary": _source_summary(release.source_details),
            "status": status,
            "draft_work_order": draft_wos[0].name if len(draft_wos) == 1 else "",
            "can_create_work_order": int(
                remaining > 1e-9 and frappe.has_permission("Work Order", ptype="create")
            ),
        })
    return output


def _source_summary(value):
    try:
        rows = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ""
    counts = defaultdict(int)
    for row in rows:
        counts[row.get("source_type") or "Source"] += 1
    return ", ".join(f"{key}: {count}" for key, count in sorted(counts.items()))


def get_release_authority(authority_name):
    authority = frappe.get_doc("Production Requirement", authority_name)
    if not authority.get("custom_calco_release_to_production") or authority.docstatus >= 2:
        frappe.throw(_("Select an active requirement created by Release to Production."))
    get_release_item(authority)
    return authority


def get_release_item(authority):
    items = authority.get("items") or []
    if len(items) != 1:
        frappe.throw(_("Controlled Production Release must contain exactly one FG row."))
    item = items[0]
    if flt(item.requested_qty) <= 0:
        frappe.throw(_("Controlled Production Release quantity must be greater than zero."))
    return item


def get_release_plan(plan_name):
    plan = frappe.get_doc("Production Plan", plan_name)
    plan.check_permission("read")
    if not plan.get("custom_calco_release_to_production") or plan.docstatus >= 2:
        frappe.throw(_("Select an active Production Plan created by Release to Production."))
    return plan


def get_release_sources(plan):
    try:
        return json.loads(plan.get("custom_release_source_details") or "[]")
    except (TypeError, ValueError):
        frappe.throw(_("Production release source lineage is invalid."))


def get_primary_source(plan):
    sources = get_release_sources(plan)
    if not sources:
        frappe.throw(_("Production release has no source lineage."))
    sales_qty = sum(flt(row.get("qty")) for row in sources if row.get("source_type") == "Sales Order")
    forecast_qty = sum(flt(row.get("qty")) for row in sources if row.get("source_type") == "Forecast")
    preferred = "Sales Order" if sales_qty >= forecast_qty and sales_qty > 0 else "Forecast"
    return next((row for row in sources if row.get("source_type") == preferred), sources[0])
