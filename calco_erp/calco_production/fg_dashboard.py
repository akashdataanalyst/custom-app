"""Three-month FG demand and supply. Plans/releases are evidence, never supply."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

import frappe
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

VERSION = "fg-monthly-v3-recovery-scenario"
TERMINAL = {"Completed", "Cancelled", "Stopped", "Closed"}


def month_key(value):
    return str(getdate(value).replace(day=1))


def months_at(value):
    start = getdate(value).replace(day=1)
    result = []
    for offset in range(3):
        index = start.year * 12 + start.month - 1 + offset
        y, m = divmod(index, 12)
        first = date(y, m + 1, 1)
        result.append({"key": str(first), "label": first.strftime("%B %Y"),
                       "end": str(first.replace(day=calendar.monthrange(y, m + 1)[1]))})
    return result


def remaining_demand(forecast, outstanding, fulfilled):
    return max(flt(outstanding), max(flt(forecast) - max(flt(fulfilled), 0), 0))


def wo_supply(row):
    if cint(row.get("docstatus")) == 2 or row.get("status") in TERMINAL:
        return "abandoned", 0.0
    qty = max(flt(row.get("qty")) - flt(row.get("produced_qty")) - flt(row.get("process_loss_qty")), 0)
    if cint(row.get("docstatus")) == 0:
        return "draft", qty
    return ("in_progress" if row.get("status") == "In Process" else "not_started"), qty


def project(months, demand, supply, released=0, overdue=0, pending=0):
    """Month-end balances are cumulative and must never be summed across months."""
    need, allocated = flt(overdue), flt(released) + flt(pending)
    result = []
    for month in months:
        key = month["key"]
        d, s = demand.get(key, {}), supply.get(key, {})
        remaining = remaining_demand(d.get("forecast"), d.get("outstanding_so"), d.get("fulfilled"))
        remaining += flt(d.get("uat_scenario_demand"))
        need += remaining
        allocated += sum(flt(s.get(k)) for k in ("draft", "not_started", "in_progress"))
        result.append({**month, **d, **s, "remaining_demand": remaining,
                       "cumulative_demand": need, "projected_coverage": allocated,
                       "balance_to_plan": max(need - allocated, 0)})
    return result


def ensure_access(company):
    from calco_erp.calco_production.planning_release import ensure_planning_access
    ensure_planning_access()
    frappe.get_doc("Company", company).check_permission("read")
    frappe.has_permission("Work Order", "read", throw=True)


def warehouses(company):
    rows = frappe.get_all("Warehouse", filters={"company": company, "is_group": 0, "disabled": 0,
                                               "warehouse_name": ("in", ["FG Released", "FG Quarantine"])},
                          fields=["name", "warehouse_name"])
    result = {}
    for label in ("FG Released", "FG Quarantine"):
        matches = [r.name for r in rows if r.warehouse_name == label]
        if len(matches) != 1:
            frappe.throw(f"Configure exactly one enabled {label} warehouse for {company}.")
        result[label] = matches[0]
    return result


def source(doctype, name, qty, **values):
    return {"doctype": doctype, "name": name, "qty": flt(qty), **values}


def build(company, item_code=None, as_of=None, exclude_wo=None):
    from calco_erp.calco_production.fg_planning import get_fg_items
    from calco_erp.calco_production.production_planning import _get_forecast_demands
    from calco_erp.calco_quality.doctype.final_qc_release.final_qc_release import get_fg_batch_quantity
    ensure_access(company)
    today = getdate(as_of or nowdate())
    months = months_at(today)
    end = months[-1]["end"]
    items = get_fg_items({"item_code": item_code})
    if item_code:
        items = [r for r in items if r["item_code"] == item_code]
    wh = warehouses(company)
    output, exceptions = [], []
    data = load_dashboard_sources(company, items, today, end, wh)
    for item in items:
        code = item["item_code"]
        demands = defaultdict(lambda: defaultdict(float))
        supplies = defaultdict(lambda: defaultdict(float))
        evidence = defaultdict(list)
        # Include past outstanding demand; calendar rollover cannot erase obligations.
        forecasts = data["forecasts"].get(code, [])
        for r in forecasts:
            if r.get("compatibility_notice"):
                exceptions.append({"item_code": code, "reason": r["compatibility_notice"], "doctype": r["source_doctype"], "name": r["source_name"]})
            if r.get("blocked_reason"):
                exceptions.append({"item_code": code, "reason": r["blocked_reason"], "doctype": r["source_doctype"], "name": r["source_name"]})
            key = month_key(r.get("forecast_date") or r["required_date"])
            demands[key]["forecast"] += flt(r["demand_qty"])
            evidence[key].append(source("Sales Forecast", r["source_name"], r["demand_qty"], metric="forecast", row=r["source_row"], date=r.get("forecast_date") or r["required_date"], delivery_date=r.get("delivery_date"), status="Legacy compatibility" if r.get("compatibility_notice") else "Explicit forecast period"))
        from calco_erp.calco_production.fg_uat_scenario import active
        scenario = active(company, code, item["stock_uom"])
        if scenario:
            demands[scenario["month"]]["uat_scenario_demand"] = scenario["demand_qty"]
            evidence[scenario["month"]].append({"doctype": "", "name": "", "row": scenario["id"],
                "metric": "Recovery UAT Scenario", "qty": scenario["demand_qty"], "date": scenario["month"],
                "status": "Recovery UAT Scenario", "scenario_id": scenario["id"],
                "activated_by": scenario["activated_by"], "activated_on": scenario["activated_on"]})
            exceptions.append({"item_code": code, "reason": f"Recovery UAT Scenario: {scenario['id']} — fixed 20 Kg demand; not Forecast/SO."})
        orders = data["orders"].get(code, [])
        order_dates = {r.name: r.due for r in orders}
        for r in orders:
            if r.status in {"Closed", "Cancelled", "Stopped", "Completed"}:
                continue
            qty = max(flt(r.qty) - flt(r.delivered_qty), 0) * flt(r.conversion_factor or 1)
            key = month_key(r.due)
            demands[key]["outstanding_so"] += qty
            evidence[key].append(source("Sales Order", r.parent, qty, metric="outstanding_so", row=r.name, date=str(r.due)))
        # DN is physical fulfillment; invoices count only when they independently update stock.
        # A return is signed negative and is attributed to its original demand period.
        for dt, child, sofield in [("Delivery Note", "Delivery Note Item", "so_detail"),
                                   ("Sales Invoice", "Sales Invoice Item", "so_detail")]:
            rows = data[dt].get(code, [])
            for r in rows:
                key = month_key(order_dates.get(r.so_row) or r.original_date or r.posting_date)
                demands[key]["fulfilled"] += flt(r.stock_qty)
                evidence[key].append(source(dt, r.parent, r.stock_qty, metric="fulfilled", row=r.name))
        stock = flt(data["stock"].get(code, 0))
        stock_evidence = [source("Warehouse", wh["FG Released"], stock, metric="released_stock", item_code=code)]
        pending = 0.0
        records = data["bprs"].get(code, [])
        batches = set()
        for bpr in records:
            if not bpr.fg_batch_no or bpr.fg_batch_no in batches:
                continue
            if frappe.db.get_value("Stock Entry", bpr.stock_entry, "company") != company:
                continue
            batches.add(bpr.fg_batch_no)
            qty = flt(get_fg_batch_quantity(code, bpr.fg_batch_no, wh["FG Quarantine"]))
            if qty <= 0:
                continue
            inspections = frappe.get_all("Quality Inspection", filters={"reference_type": "Stock Entry", "reference_name": bpr.stock_entry, "docstatus": ("<", 2)}, fields=["name", "status", "docstatus"])
            rejected = any(r.status == "Rejected" and cint(r.docstatus) == 1 for r in inspections)
            held = False
            if bpr.work_order:
                from calco_erp.calco_production.in_process_quality import active_hold, inspection_rows
                held = active_hold(inspection_rows(bpr.work_order))
            entry = source("Batch Production Record", bpr.name, qty, metric="pending_release", batch=bpr.fg_batch_no, conditional=True)
            if rejected or held:
                exceptions.append({**entry, "item_code": code, "reason": "Rejected or Quality-held FG excluded from projected supply; review before replacement planning."})
            else:
                pending += qty
                stock_evidence.append(entry)
        work_orders = data["work_orders"].get(code, [])
        for wo in work_orders:
            if wo.name == exclude_wo:
                continue
            category, qty = wo_supply(wo)
            date_value = wo.planned_end_date
            if qty and not date_value:
                exceptions.append({"item_code": code, "doctype": "Work Order", "name": wo.name, "reason": "Missing planned completion date; supply is unscheduled.", "qty": qty})
                continue
            key = month_key(date_value or wo.planned_start_date or today)
            if key < months[0]["key"]:
                key = months[0]["key"]
                if qty:
                    exceptions.append({"item_code": code, "doctype": "Work Order", "name": wo.name, "reason": "Overdue completion; outstanding supply carried into current month.", "qty": qty})
            supplies[key][category] += qty
            abandoned = max(flt(wo.qty) - flt(wo.produced_qty), 0) if wo.status in TERMINAL else 0
            supplies[key]["short_or_cancelled"] += abandoned
            evidence[key].append(source("Work Order", wo.name, qty, metric=category, status=wo.status, produced=wo.produced_qty, loss=wo.process_loss_qty, short_or_cancelled=abandoned, date=str(date_value or "")))
        # Production activity is bucketed by actual posting month, never planned WO date.
        manufactured = data["manufactured"].get(code, [])
        for r in manufactured:
            key = month_key(r.posting_date)
            good = flt(r.good_qty)
            supplies[key]["manufactured"] += good
            supplies[key]["process_loss"] += flt(r.process_loss_qty)
            evidence[key].append(source("Stock Entry", r.name, good, metric="manufactured", loss=r.process_loss_qty))
        overdue = sum(remaining_demand(d.get("forecast"), d.get("outstanding_so"), d.get("fulfilled")) + flt(d.get("uat_scenario_demand")) for k, d in demands.items() if k < months[0]["key"])
        rows = project(months, demands, supplies, stock, overdue, pending)
        for row in rows:
            row.update(item_code=code, item_name=item["item_name"], stock_uom=item["stock_uom"], company=company,
                       released_stock=stock, pending_release=pending, overdue_demand=overdue,
                       sources=[e for k in sorted(evidence) if k <= row["key"] for e in evidence[k]] + stock_evidence,
                       planning_key=f"{company}|{code}|{item['stock_uom']}|{row['key']}")
            output.append(row)
    # Historical releases remain visible; never silently converted and never supply.
    from calco_erp.calco_production import planning_release
    queue = planning_release.build_production_queue(company=company, item_code=item_code or "")
    transition = [r for r in queue if flt(r.get("remaining_qty")) > 0]
    return {"version": VERSION, "as_of": str(now_datetime()), "months": months, "rows": output,
            "exceptions": exceptions, "transition": transition, "warehouses": wh,
            "note": "Balances are cumulative by month-end; do not sum months. Draft and pending-QC supply are conditional, not released stock."}


def group_sources(rows, field="item_code"):
    result = defaultdict(list)
    for row in rows:
        result[row[field]].append(row)
    return result


def load_dashboard_sources(company, items, today, end, wh):
    """Request-local bulk reads. No cached stock, changed formula or permission bypass."""
    from calco_erp.calco_production.production_planning import _get_forecast_demands
    item_map = {r["item_code"]: r for r in items}
    keys = ("forecasts", "orders", "Delivery Note", "Sales Invoice", "stock", "bprs", "work_orders", "manufactured")
    data = {key: {} for key in keys}
    if not item_map:
        return data
    codes = tuple(item_map)
    data["forecasts"] = group_sources(_get_forecast_demands(company, "1900-01-01", end, item_map))
    data["orders"] = group_sources(frappe.db.sql("""select i.item_code, i.name, i.parent, i.qty,
        i.delivered_qty, i.conversion_factor, coalesce(i.delivery_date,s.delivery_date,s.transaction_date) as due, s.status
        from `tabSales Order Item` i join `tabSales Order` s on s.name=i.parent
        where s.docstatus=1 and s.company=%s and i.item_code in %s""", (company,codes),as_dict=True))
    for dt, child in (("Delivery Note","Delivery Note Item"),("Sales Invoice","Sales Invoice Item")):
        extra = " and p.update_stock=1 and coalesce(i.delivery_note,'')=''" if dt == "Sales Invoice" else ""
        data[dt] = group_sources(frappe.db.sql(f"""select i.item_code, i.name, i.parent, i.stock_qty,
            i.so_detail as so_row, p.posting_date, original.posting_date as original_date
            from `tab{child}` i join `tab{dt}` p on p.name=i.parent
            left join `tab{dt}` original on original.name=p.return_against
            where p.docstatus=1 and p.company=%s and i.item_code in %s and p.posting_date<=%s {extra}""",
            (company,codes,today),as_dict=True))
    data["stock"] = {r.item_code:r.actual_qty for r in frappe.get_all("Bin",
        filters={"item_code":("in",codes),"warehouse":wh["FG Released"]},fields=["item_code","actual_qty"])}
    data["bprs"] = group_sources(frappe.get_all("Batch Production Record",
        filters={"item_code":("in",codes),"docstatus":1},
        fields=["item_code","name","stock_entry","work_order","fg_batch_no","status"]))
    data["work_orders"] = group_sources(frappe.get_all("Work Order",
        filters={"company":company,"production_item":("in",codes)},
        fields=["production_item","name","docstatus","status","qty","produced_qty","process_loss_qty","planned_start_date","planned_end_date","expected_delivery_date"]),"production_item")
    data["manufactured"] = group_sources(frappe.db.sql("""select i.item_code, s.name, s.posting_date,
        s.fg_completed_qty, s.process_loss_qty, sum(i.transfer_qty) as good_qty
        from `tabStock Entry` s join `tabStock Entry Detail` i on i.parent=s.name
        where s.docstatus=1 and s.company=%s and s.purpose='Manufacture'
        and i.item_code in %s and i.is_finished_item=1
        group by i.item_code,s.name,s.posting_date,s.fg_completed_qty,s.process_loss_qty""",(company,codes),as_dict=True))
    return data


@frappe.whitelist()
def get_dashboard_data(company=None, item_code=None, **kwargs):
    company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
    if not company:
        frappe.throw("Select Company.")
    return build(company, item_code)

