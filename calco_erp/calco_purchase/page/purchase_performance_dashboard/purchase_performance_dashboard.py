from __future__ import annotations

from collections import defaultdict
from datetime import date
from calendar import monthrange

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from calco_erp.dashboard_utils import (
    get_doc_route,
    get_list_route,
    make_card,
    make_chart,
    make_drilldown,
    make_drilldown_row,
)


@frappe.whitelist()
def get_dashboard_data(
    supplier: str | None = None,
    item: str | None = None,
    month: str | None = None,
    quarter: str | None = None,
    supplier_type: str | None = None,
) -> dict[str, object]:
    filters = build_filters(supplier=supplier, item=item, month=month, quarter=quarter, supplier_type=supplier_type)
    matrix_summary = get_supplier_matrix_summary(filters)
    procurement = build_procurement_section(filters)
    supplier_section = build_supplier_section(filters, matrix_summary)
    delivery = build_delivery_section(filters, matrix_summary["supplier_type_map"])
    quality = build_quality_section(filters, matrix_summary["supplier_type_map"])
    commercial = build_commercial_section(filters, matrix_summary["supplier_type_map"])
    risk = build_risk_section(filters, matrix_summary, delivery["supplier_delivery_map"], quality, commercial)

    supplier_cards = supplier_section["cards"]
    matrix_rows = matrix_summary["rows"]
    for index, status in enumerate((None, "approved", "conditional approval", "blocked")):
        matching = [r for r in matrix_rows if status is None or (r.get("approval_status") or "").lower() == status]
        bind_population(supplier_cards[index], "Supplier Approval Matrix", matching)
    bind_population(supplier_cards[4], "Purchase Receipt", delivery["rows"], "purchase_receipt")
    bind_population(supplier_cards[5], "Purchase Receipt", quality["receipt_rows"], "purchase_receipt")
    bind_population(supplier_cards[6], "Supplier Approval Matrix", matrix_rows)
    for card in delivery["section"]["cards"]:
        bind_population(card, "Purchase Receipt", delivery["rows"], "purchase_receipt")
    for card, rows, dt, key in zip(quality["section"]["cards"],
        (quality["receipt_rows"], quality["capa_rows"], quality["open_capa_rows"], quality["overdue_capa_rows"]),
        ("Purchase Receipt", "Supplier CAPA Request", "Supplier CAPA Request", "Supplier CAPA Request"),
        ("purchase_receipt", "name", "name", "name")):
        bind_population(card, dt, rows, key)
    for card in commercial["cards"]:
        bind_population(card, "Purchase Commercial Approval", commercial["rows"])
    bind_population(risk["cards"][0], "Supplier Approval Matrix", risk["rows"], "matrix_name")
    for section in (procurement, supplier_section, delivery["section"], quality["section"], commercial, risk):
        for card in section["cards"]:
            card["precision"] = 3 if card.get("suffix") else 0
        for chart in section["charts"]:
            chart["precision"] = 3 if chart.get("suffix") else 0

    return {
        "title": "Purchase Performance Dashboard",
        "as_of": str(filters["as_of"]),
        "period_label": filters["period_label"],
        "scope_note": "Current document/approval status. Period uses transaction dates (receipt posting / approval and CAPA creation); supplier relationships use effective/expiry overlap. Overdue and quotation expiry use the displayed As Of date.",
        "sections": [
            procurement,
            supplier_section,
            delivery["section"],
            quality["section"],
            commercial,
            risk,
        ],
    }


def build_filters(
    supplier: str | None = None,
    item: str | None = None,
    month: str | None = None,
    quarter: str | None = None,
    supplier_type: str | None = None,
) -> dict[str, object]:
    supplier = (supplier or "").strip() or None
    item = (item or "").strip() or None
    supplier_type = (supplier_type or "").strip() or None
    date_from, date_to, period_label = resolve_period(month=month, quarter=quarter)
    return {
        "supplier": supplier,
        "item": item,
        "supplier_type": supplier_type,
        "date_from": date_from,
        "date_to": date_to,
        "period_label": period_label,
        "as_of": getdate(nowdate()),
    }


def resolve_period(month: str | None = None, quarter: str | None = None) -> tuple[date | None, date | None, str]:
    if month:
        year_str, month_str = month.split("-", 1)
        year = int(year_str)
        month_number = int(month_str)
        start = getdate(f"{year}-{month_number:02d}-01")
        end = add_days(add_days(start, 32).replace(day=1), -1)
        return start, end, start.strftime("%b %Y")

    if quarter:
        year_str, quarter_str = quarter.split("-Q", 1)
        year = int(year_str)
        quarter_number = int(quarter_str)
        start_month = ((quarter_number - 1) * 3) + 1
        start = getdate(f"{year}-{start_month:02d}-01")
        end_month = start_month + 2
        end = date(year, end_month, monthrange(year, end_month)[1])
        return start, end, f"Q{quarter_number} {year}"

    return None, None, "All Time"


def build_procurement_section(filters: dict[str, object]) -> dict[str, object]:
    # Count the complete document population. Preview limits belong only to the
    # separate preview query, never to the count or the exact drill-down IDs.
    populations = {key: procurement_population(key, filters) for key in PROCUREMENT_METRICS}
    cards = []
    for key, (label, doctype) in PROCUREMENT_METRICS.items():
        pop = populations[key]
        card = make_card(label, pop["count"])
        bind_population(card, doctype, pop["rows"])
        cards.append(card)
    previews = []
    for key in ("rfq", "quotation", "po"):
        doctype = PROCUREMENT_METRICS[key][1]
        for row in procurement_population(key, filters, preview_limit=4)["rows"]:
            previews.append(make_drilldown_row(doctype, row["name"], meta=f"{doctype} | {row.get('supplier') or '-'} | {row.get('transaction_date') or '-'}"))
    return {
        "key": "procurement", "title": "Procurement Health", "cards": cards,
        "charts": [make_chart("procurement-pipeline", "Procurement Pipeline",
            ["RFQs", "Supplier Quotations", "Commercial Approvals", "Open POs", "Delayed POs"],
            [{"name": "Count", "values": [populations[key]["count"] for key in PROCUREMENT_METRICS]}], colors=["#0f766e"] )],
        "drilldowns": [make_drilldown("Latest Procurement Records", previews)],
    }


def build_supplier_section(filters: dict[str, object], matrix_summary: dict[str, object]) -> dict[str, object]:
    rows = matrix_summary["rows"]
    approved_count = sum(1 for row in rows if (row.get("approval_status") or "").lower() == "approved")
    conditional_count = sum(1 for row in rows if (row.get("approval_status") or "").lower() == "conditional approval")
    blocked_count = sum(1 for row in rows if (row.get("approval_status") or "").lower() == "blocked")
    active_count = len(rows)

    delivery_scores = matrix_summary["delivery_scores"]
    quality_scores = matrix_summary["quality_scores"]
    matrix_ratings = [flt(row.get("supplier_rating") or 0) for row in rows if flt(row.get("supplier_rating") or 0) > 0]
    avg_delivery = round(sum(delivery_scores.values()) / len(delivery_scores), 2) if delivery_scores else 0
    avg_quality = round(sum(quality_scores.values()) / len(quality_scores), 2) if quality_scores else 0
    avg_overall = round(sum(matrix_ratings) / len(matrix_ratings), 2) if matrix_ratings else round((avg_delivery + avg_quality) / 2, 2)

    cards = [
        make_card("Active Suppliers", active_count, route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(supplier_type=filters["supplier_type"], item_code=filters["item"])),
        make_card("Approved Suppliers", approved_count, route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(approval_status="Approved", supplier_type=filters["supplier_type"], item_code=filters["item"])),
        make_card("Conditional Suppliers", conditional_count, route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(approval_status="Conditional Approval", supplier_type=filters["supplier_type"], item_code=filters["item"])),
        make_card("Blocked Suppliers", blocked_count, route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(approval_status="Blocked", supplier_type=filters["supplier_type"], item_code=filters["item"])),
        make_card("Supplier Delivery Rating", avg_delivery, suffix="%", route=get_list_route("Purchase Receipt"), route_doctype="Purchase Receipt", route_options=build_route_options(supplier=filters["supplier"])),
        make_card("Supplier Quality Rating", avg_quality, suffix="%", route=get_list_route("Quality Inspection"), route_doctype="Quality Inspection", route_options=build_route_options(reference_type="Purchase Receipt")),
        make_card("Overall Supplier Rating", avg_overall, suffix="%", route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(supplier_type=filters["supplier_type"], item_code=filters["item"])),
    ]

    status_counts = defaultdict(int)
    top_rated = []
    for row in rows:
        status_counts[row.get("approval_status") or "Unknown"] += 1
        top_rated.append((row.get("supplier"), flt(row.get("supplier_rating") or 0)))
    top_rated.sort(key=lambda entry: (-entry[1], entry[0] or ""))

    charts = [
        make_chart(
            "supplier-status",
            "Supplier Status Mix",
            list(status_counts.keys()),
            [{"name": "Suppliers", "values": list(status_counts.values())}],
            chart_type="donut",
            colors=["#15803d", "#d97706", "#b91c1c", "#475569"],
        ),
        make_chart(
            "top-supplier-ratings",
            "Top 8 Supplier Ratings",
            [entry[0] for entry in top_rated[:8]],
            [{"name": "Rating", "values": [entry[1] for entry in top_rated[:8]]}],
            colors=["#2563eb"],
            suffix="%",
        ),
    ]

    drilldowns = [
        make_drilldown(
            "Supplier Approval Snapshot",
            [
                make_drilldown_row(
                    "Supplier Approval Matrix",
                    row["name"],
                    label=row.get("supplier") or row["name"],
                    meta=f"{row.get('approval_status') or '-'} | {row.get('supplier_type') or '-'} | Rating {flt(row.get('supplier_rating') or 0)}",
                )
                for row in rows[:10]
            ],
        )
    ]

    return {
        "key": "supplier",
        "title": "Supplier Performance",
        "cards": cards,
        "charts": charts,
        "drilldowns": drilldowns,
    }


def build_delivery_section(filters: dict[str, object], supplier_type_map: dict[str, str]) -> dict[str, object]:
    rows = get_delivery_rows(filters, supplier_type_map)
    on_time_count = sum(1 for row in rows if row["is_on_time"])
    eligible_count = sum(1 for row in rows if row["schedule_date"])
    on_time_pct = round((on_time_count * 100.0 / eligible_count), 2) if eligible_count else 0
    avg_lead_time = round(sum(row["actual_lead_time"] for row in rows) / len(rows), 2) if rows else 0
    accuracy_values = [row["lead_time_accuracy"] for row in rows if row["lead_time_accuracy"] is not None]
    lead_time_accuracy = round(sum(accuracy_values) / len(accuracy_values), 2) if accuracy_values else 0

    supplier_delivery_map = defaultdict(list)
    delayed_rows = []
    for row in rows:
        supplier_delivery_map[row["supplier"]].append(row)
        if row["schedule_date"] and not row["is_on_time"]:
            delayed_rows.append(row)

    supplier_bars = []
    for supplier, supplier_rows in supplier_delivery_map.items():
        eligible = [row for row in supplier_rows if row["schedule_date"]]
        if not eligible:
            continue
        supplier_bars.append(
            (
                supplier,
                round(sum(1 for row in eligible if row["is_on_time"]) * 100.0 / len(eligible), 2),
            )
        )
    supplier_bars.sort(key=lambda entry: (-entry[1], entry[0]))

    section = {
        "key": "delivery",
        "title": "Delivery Performance",
        "cards": [
            make_card("On-Time Delivery %", on_time_pct, suffix="%", route=get_list_route("Purchase Receipt"), route_doctype="Purchase Receipt", route_options=build_route_options(supplier=filters["supplier"])),
            make_card("Avg Lead Time", avg_lead_time, suffix=" days", route=get_list_route("Purchase Receipt"), route_doctype="Purchase Receipt", route_options=build_route_options(supplier=filters["supplier"])),
            make_card("Lead Time Accuracy", lead_time_accuracy, suffix="%", route=get_list_route("Purchase Receipt"), route_doctype="Purchase Receipt", route_options=build_route_options(supplier=filters["supplier"])),
        ],
        "charts": [
            make_chart(
                "delivery-by-supplier",
                "On-Time Delivery by Supplier",
                [entry[0] for entry in supplier_bars],
                [{"name": "On-Time %", "values": [entry[1] for entry in supplier_bars]}],
                colors=["#0891b2"],
                suffix="%",
            )
        ],
        "drilldowns": [
            make_drilldown(
                "Delayed Deliveries",
                [
                    make_drilldown_row(
                        "Purchase Receipt",
                        row["purchase_receipt"],
                        label=row["supplier"],
                        meta=f"{row['item_code']} | Planned {row['schedule_date']} | Actual {row['posting_date']}",
                    )
                    for row in delayed_rows[:10]
                ],
            )
        ],
    }
    return {"section": section, "supplier_delivery_map": supplier_delivery_map, "rows": rows}


def build_quality_section(filters: dict[str, object], supplier_type_map: dict[str, str]) -> dict[str, object]:
    receipt_rows = get_receipt_quality_rows(filters, supplier_type_map)
    total_received = sum(flt(row.get("received_qty") or 0) for row in receipt_rows)
    total_rejected = sum(flt(row.get("rejected_qty") or 0) for row in receipt_rows)
    rejection_pct = round((total_rejected * 100.0 / total_received), 2) if total_received else 0

    capa_rows = get_capa_rows(filters, supplier_type_map)
    open_capa = [row for row in capa_rows if not is_capa_closed(row)]
    overdue_capa = [row for row in open_capa if row.get("required_response_date") and getdate(row["required_response_date"]) < filters["as_of"]]
    closed_capa = [row for row in capa_rows if is_capa_closed(row)]
    capa_closure_pct = round((len(closed_capa) * 100.0 / len(capa_rows)), 2) if capa_rows else 0

    supplier_rejections = defaultdict(float)
    for row in receipt_rows:
        supplier_rejections[row["supplier"]] += flt(row.get("rejected_qty") or 0)
    rejection_bars = sorted(supplier_rejections.items(), key=lambda entry: (-entry[1], entry[0]))

    section = {
        "key": "quality",
        "title": "Quality Performance",
        "cards": [
            make_card("RM Rejection %", rejection_pct, suffix="%", route=get_list_route("Quality Inspection"), route_doctype="Quality Inspection", route_options=build_route_options(reference_type="Purchase Receipt")),
            make_card("CAPA Closure %", capa_closure_pct, suffix="%", route=get_list_route("Supplier CAPA Request"), route_doctype="Supplier CAPA Request", route_options=build_route_options(supplier=filters["supplier"])),
            make_card("Open CAPA", len(open_capa), route=get_list_route("Supplier CAPA Request"), route_doctype="Supplier CAPA Request", route_options=build_route_options(supplier=filters["supplier"])),
            make_card("Overdue CAPA", len(overdue_capa), route=get_list_route("Supplier CAPA Request"), route_doctype="Supplier CAPA Request", route_options=build_route_options(supplier=filters["supplier"])),
        ],
        "charts": [
            make_chart(
                "supplier-rejections",
                "Top 8 RM Rejections by Supplier",
                [entry[0] for entry in rejection_bars[:8]],
                [{"name": "Rejected Qty", "values": [entry[1] for entry in rejection_bars[:8]]}],
                colors=["#dc2626"],
                suffix=" Kg",
            )
        ],
        "drilldowns": [
            make_drilldown(
                "Open / Overdue CAPA Cases",
                [
                    make_drilldown_row(
                        "Supplier CAPA Request",
                        row["name"],
                        label=row.get("supplier") or row["name"],
                        meta=f"{row.get('item_code') or '-'} | Due {row.get('required_response_date') or '-'} | {'Closed' if is_capa_closed(row) else 'Open'}",
                    )
                    for row in (overdue_capa + [r for r in open_capa if r not in overdue_capa])[:10]
                ],
            )
        ],
    }
    return {
        "section": section,
        "rejection_pct": rejection_pct,
        "open_capa": len(open_capa),
        "overdue_capa": len(overdue_capa),
        "capa_rows": capa_rows,
        "receipt_rows": receipt_rows,
        "open_capa_rows": open_capa,
        "overdue_capa_rows": overdue_capa,
    }


def build_commercial_section(filters: dict[str, object], supplier_type_map: dict[str, str]) -> dict[str, object]:
    rows = get_commercial_approval_rows(filters, supplier_type_map)
    variance_count = len(rows)
    variance_value = round(sum(flt(row.get("variance_amount") or 0) for row in rows), 2)
    top_cases = sorted(rows, key=lambda row: (-flt(row.get("variance_amount") or 0), row.get("name") or ""))[:10]

    charts = [
        make_chart(
            "commercial-variance",
            "Top 8 Commercial Variance Cases",
            [row.get("supplier") or row.get("name") for row in top_cases[:8]],
            [{"name": "Variance", "values": [flt(row.get("variance_amount") or 0) for row in top_cases[:8]]}],
            colors=["#7c3aed"],
            suffix=" Rs",
        )
    ]

    drilldowns = [
        make_drilldown(
            "Top Variance Cases",
            [
                make_drilldown_row(
                    "Purchase Commercial Approval",
                    row["name"],
                    label=row.get("supplier") or row["name"],
                    meta=f"{row.get('item_code') or '-'} | Variance {flt(row.get('variance_amount') or 0)} | {row.get('approval_status') or '-'}",
                )
                for row in top_cases
            ],
        )
    ]

    return {
        "key": "commercial",
        "title": "Commercial Performance",
        "cards": [
            make_card("Commercial Variance Count", variance_count, route=get_list_route("Purchase Commercial Approval"), route_doctype="Purchase Commercial Approval", route_options=build_route_options(supplier=filters["supplier"], item_code=filters["item"])),
            make_card("Commercial Variance Value", variance_value, suffix=" Rs", route=get_list_route("Purchase Commercial Approval"), route_doctype="Purchase Commercial Approval", route_options=build_route_options(supplier=filters["supplier"], item_code=filters["item"])),
        ],
        "charts": charts,
        "drilldowns": drilldowns,
        "rows": rows,
    }


def build_risk_section(
    filters: dict[str, object],
    matrix_summary: dict[str, object],
    supplier_delivery_map: dict[str, list[dict[str, object]]],
    quality_summary: dict[str, object],
    commercial_summary: dict[str, object],
) -> dict[str, object]:
    capa_rows = quality_summary["capa_rows"]
    commercial_rows = commercial_summary["rows"]
    delivery_supplier_map = {}
    for supplier, rows in supplier_delivery_map.items():
        eligible = [row for row in rows if row["schedule_date"]]
        on_time_pct = round(sum(1 for row in eligible if row["is_on_time"]) * 100.0 / len(eligible), 2) if eligible else 100
        delivery_supplier_map[supplier] = on_time_pct

    rejection_by_supplier = defaultdict(lambda: {"received": 0.0, "rejected": 0.0})
    for row in quality_summary["receipt_rows"]:
        rejection_by_supplier[row["supplier"]]["received"] += flt(row.get("received_qty") or 0)
        rejection_by_supplier[row["supplier"]]["rejected"] += flt(row.get("rejected_qty") or 0)

    overdue_capa_by_supplier = defaultdict(int)
    open_capa_by_supplier = defaultdict(int)
    for row in capa_rows:
        supplier = row.get("supplier") or ""
        if not supplier or is_capa_closed(row):
            continue
        open_capa_by_supplier[supplier] += 1
        if row.get("required_response_date") and getdate(row["required_response_date"]) < filters["as_of"]:
            overdue_capa_by_supplier[supplier] += 1

    pending_commercial_by_supplier = defaultdict(int)
    for row in get_pending_commercial_approvals(filters):
        pending_commercial_by_supplier[row.get("supplier") or ""] += 1

    risk_rows = []
    for row in matrix_summary["rows"]:
        supplier = row.get("supplier") or ""
        if not supplier:
            continue
        approval_status = (row.get("approval_status") or "").lower()
        received = rejection_by_supplier[supplier]["received"]
        rejected = rejection_by_supplier[supplier]["rejected"]
        rejection_pct = round((rejected * 100.0 / received), 2) if received else 0
        on_time_pct = delivery_supplier_map.get(supplier, 100)
        risk_score = 0
        if approval_status == "blocked":
            risk_score += 5
        elif approval_status == "conditional approval":
            risk_score += 2
        if on_time_pct < 80:
            risk_score += 2
        if rejection_pct > 5:
            risk_score += 2
        if overdue_capa_by_supplier[supplier]:
            risk_score += 3
        if pending_commercial_by_supplier[supplier]:
            risk_score += 1
        if risk_score >= 3:
            risk_rows.append(
                {
                    "supplier": supplier,
                    "risk_score": risk_score,
                    "approval_status": row.get("approval_status") or "",
                    "on_time_pct": on_time_pct,
                    "rejection_pct": rejection_pct,
                    "overdue_capa": overdue_capa_by_supplier[supplier],
                    "pending_commercial": pending_commercial_by_supplier[supplier],
                    "matrix_name": row.get("name"),
                }
            )

    risk_rows.sort(key=lambda row: (-row["risk_score"], row["supplier"]))
    return {
        "key": "risk",
        "title": "Risk Dashboard",
        "rows": risk_rows,
        "cards": [
            make_card("High Risk Suppliers", len(risk_rows), route=get_list_route("Supplier Approval Matrix"), route_doctype="Supplier Approval Matrix", route_options=build_route_options(supplier_type=filters["supplier_type"])),
        ],
        "charts": [
            make_chart(
                "risk-suppliers",
                "High Risk Suppliers",
                [row["supplier"] for row in risk_rows],
                [{"name": "Risk Score", "values": [row["risk_score"] for row in risk_rows]}],
                colors=["#b91c1c"],
            )
        ],
        "drilldowns": [
            make_drilldown(
                "High Risk Supplier Details",
                [
                    make_drilldown_row(
                        "Supplier Approval Matrix",
                        row["matrix_name"],
                        label=row["supplier"],
                        meta=f"Score {row['risk_score']} | {row['approval_status']} | OTD {row['on_time_pct']}% | Rejection {row['rejection_pct']}% | Overdue CAPA {row['overdue_capa']}",
                    )
                    for row in risk_rows[:10]
                ],
            )
        ],
    }


def get_supplier_matrix_summary(filters: dict[str, object]) -> dict[str, object]:
    conditions = ["ifnull(sam.supplier, '') != ''", "sam.docstatus < 2"]
    values: dict[str, object] = {}
    if filters["supplier"]:
        conditions.append("sam.supplier = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters["item"]:
        conditions.append("sam.item_code = %(item)s")
        values["item"] = filters["item"]

    if filters["date_from"]:
        conditions += ["(sam.effective_date is null or sam.effective_date <= %(date_to)s)",
                       "(sam.expiry_date is null or sam.expiry_date >= %(date_from)s)"]
        values.update(date_from=filters["date_from"], date_to=filters["date_to"])
    rows = frappe.db.sql(
        f"""
        select
            sam.name,
            sam.supplier,
            sam.item_code,
            sam.approval_status,
            sam.supplier_type,
            sam.supplier_rating,
            sam.effective_date,
            sam.expiry_date,
            sam.modified
        from `tabSupplier Approval Matrix` sam
        where {' and '.join(conditions)}
        order by sam.modified desc, sam.name desc
        """,
        values,
        as_dict=True,
    )

    type_map = get_supplier_type_map(filters)
    supplier_rows = {}
    for row in rows:
        if filters["supplier_type"] and type_map.get(row["supplier"]) != filters["supplier_type"]:
            continue
        supplier_rows.setdefault(row["supplier"], row)

    delivery_rows = get_delivery_rows(filters, type_map)
    quality_rows = get_receipt_quality_rows(filters, type_map)
    delivery_scores = defaultdict(list)
    for row in delivery_rows:
        if row["schedule_date"]:
            delivery_scores[row["supplier"]].append(100.0 if row["is_on_time"] else 0.0)
    quality_received = defaultdict(float)
    quality_rejected = defaultdict(float)
    for row in quality_rows:
        quality_received[row["supplier"]] += flt(row.get("received_qty") or 0)
        quality_rejected[row["supplier"]] += flt(row.get("rejected_qty") or 0)

    return {
        "rows": list(supplier_rows.values()),
        "supplier_type_map": type_map,
        "delivery_scores": {
            supplier: round(sum(scores) / len(scores), 2) if scores else 0
            for supplier, scores in delivery_scores.items()
        },
        "quality_scores": {
            supplier: round(max(0, 100 - (quality_rejected[supplier] * 100.0 / quality_received[supplier])), 2)
            if quality_received[supplier]
            else 100
            for supplier in supplier_rows
        },
    }


PROCUREMENT_METRICS = {
    "rfq": ("Open RFQs", "Request for Quotation"),
    "quotation": ("Open Supplier Quotations", "Supplier Quotation"),
    "approval": ("Pending Commercial Approvals", "Purchase Commercial Approval"),
    "po": ("Open POs", "Purchase Order"),
    "delayed": ("Delayed POs", "Purchase Order"),
}


def get_supplier_type_map(filters):
    """One current classification per supplier, shared by every dashboard section."""
    if "_supplier_type_map" not in filters:
        rows = frappe.db.sql("""select supplier, supplier_type from `tabSupplier Approval Matrix`
            where docstatus < 2 and ifnull(supplier, '') != ''
            order by modified desc, name desc""", as_dict=True)
        mapping = {}
        for row in rows:
            mapping.setdefault(row["supplier"], row.get("supplier_type") or "")
        filters["_supplier_type_map"] = mapping
    return filters["_supplier_type_map"]


def supplier_conditions(field, filters, values):
    clauses = []
    if filters.get("supplier"):
        clauses.append(f"{field} = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters.get("supplier_type"):
        suppliers = tuple(sorted(s for s, t in get_supplier_type_map(filters).items() if t == filters["supplier_type"]))
        if suppliers:
            clauses.append(f"{field} in %(scope_suppliers)s")
            values["scope_suppliers"] = suppliers
        else:
            clauses.append("1=0")
    return clauses


def procurement_query(kind, filters):
    """The single population predicate for COUNT, preview and native list links.

    Open solicitation/quotation includes Draft and Submitted work, excluding
    terminal/amended documents and completed matching downstream quantities.
    A PO is open only when a matching item has a positive unreceived balance.
    """
    values = {"today": filters.get("as_of") or getdate(nowdate())}
    terminal = "('Closed','Completed','Cancelled','Stopped','Expired','Superseded')"
    if kind == "rfq":
        alias, table, child = "rfq", "Request for Quotation", "Request for Quotation Item"
        select = "rfq.name, rfq.transaction_date, rfq.modified, '' as supplier"
        joins = "inner join `tabRequest for Quotation Item` r on r.parent=rfq.name"
        conditions = ["rfq.docstatus < 2", f"ifnull(rfq.status,'') not in {terminal}"]
        # At least one matching invitation/item still awaits a submitted response.
        supplier_scope = supplier_conditions("rs.supplier", filters, values)
        missing_response = """not exists (select 1 from `tabSupplier Quotation Item` qi
            inner join `tabSupplier Quotation` q on q.name=qi.parent
            where q.docstatus=1 and ifnull(q.status,'') not in ('Cancelled','Stopped','Superseded')
            and q.supplier=rs.supplier and qi.request_for_quotation=rfq.name
            and (qi.request_for_quotation_item=r.name or
                 (ifnull(qi.request_for_quotation_item,'')='' and qi.item_code=r.item_code)))"""
        invitation = "exists (select 1 from `tabRequest for Quotation Supplier` rs where rs.parent=rfq.name and " + " and ".join([missing_response]+supplier_scope) + ")"
        if not filters.get("supplier") and not filters.get("supplier_type"):
            invitation = "("+invitation+" or not exists (select 1 from `tabRequest for Quotation Supplier` rs where rs.parent=rfq.name))"
        conditions.append(invitation)
    elif kind == "quotation":
        alias, table, child = "sq", "Supplier Quotation", "Supplier Quotation Item"
        select = "sq.name, sq.supplier, sq.transaction_date, sq.modified"
        joins = "inner join `tabSupplier Quotation Item` r on r.parent=sq.name"
        conditions = ["sq.docstatus < 2", f"ifnull(sq.status,'') not in {terminal}",
            "(sq.valid_till is null or sq.valid_till >= %(today)s)",
            """(select sum(ifnull(required.qty,0)*ifnull(nullif(required.conversion_factor,0),1))
                from `tabSupplier Quotation Item` required where required.parent=sq.name and required.item_code=r.item_code) >
             ifnull((select sum(ifnull(pi.qty,0)*ifnull(nullif(pi.conversion_factor,0),1))
                from `tabPurchase Order Item` pi inner join `tabPurchase Order` p on p.name=pi.parent
                where p.docstatus=1 and ifnull(p.status,'') not in ('Cancelled','Stopped','Superseded')
                and pi.supplier_quotation=sq.name and pi.item_code=r.item_code),0)"""]
        conditions += supplier_conditions("sq.supplier", filters, values)
    elif kind == "approval":
        alias, table, child = "pca", "Purchase Commercial Approval", None
        select = "pca.name, pca.supplier, date(pca.creation) as transaction_date, pca.modified"
        joins = "inner join `tabSupplier Quotation` sq on sq.name=pca.supplier_quotation"
        conditions = ["pca.docstatus < 2", "pca.approval_status in ('Draft','Reopened')",
            "sq.docstatus=1", f"ifnull(sq.status,'') not in {terminal}",
            "not exists (select 1 from `tabSupplier Quotation` amended where amended.amended_from=sq.name and amended.docstatus<2)",
            "(sq.valid_till is null or sq.valid_till >= %(today)s)",
            """not exists (select 1 from `tabPurchase Commercial Approval` newer
               where newer.docstatus < 2 and newer.supplier_quotation=pca.supplier_quotation
               and ifnull(newer.supplier_quotation_item,'')=ifnull(pca.supplier_quotation_item,'')
               and newer.item_code=pca.item_code
               and (newer.creation>pca.creation or (newer.creation=pca.creation and newer.name>pca.name)))"""]
        conditions += supplier_conditions("pca.supplier", filters, values)
    elif kind in ("po", "delayed"):
        alias, table, child = "po", "Purchase Order", "Purchase Order Item"
        select = "po.name, po.supplier, po.transaction_date, po.modified"
        joins = "inner join `tabPurchase Order Item` r on r.parent=po.name"
        conditions = ["po.docstatus=1", f"ifnull(po.status,'') not in {terminal}",
            "ifnull(po.custom_calco_permanent_closure,0)=0", "ifnull(r.qty,0)>ifnull(r.received_qty,0)"]
        if kind == "delayed":conditions.append("r.schedule_date < %(today)s")
        conditions += supplier_conditions("po.supplier", filters, values)
    else:
        raise ValueError("Unknown procurement population")
    if kind in ("rfq", "quotation"):
        conditions.append("""(ifnull(r.material_request,'')='' or exists
            (select 1 from `tabMaterial Request` mr where mr.name=r.material_request
             and mr.docstatus<2 and ifnull(mr.status,'') not in ('Cancelled','Stopped','Closed')
             and ifnull(mr.custom_calco_permanent_closure,0)=0))""")
    if kind != "approval":
        conditions.append(f"not exists (select 1 from `tab{table}` amended where amended.amended_from={alias}.name and amended.docstatus<2)")
    if filters.get("item"):
        conditions.append(("r.item_code" if child else "pca.item_code")+" = %(item)s")
        values["item"] = filters["item"]
    if filters.get("date_from"):
        date_field = "date(pca.creation)" if kind == "approval" else alias+".transaction_date"
        conditions.append(date_field+" between %(date_from)s and %(date_to)s")
        values.update(date_from=filters["date_from"], date_to=filters["date_to"])
    return f"select distinct {select} from `tab{table}` {alias} {joins} where " + " and ".join(conditions), values


def procurement_population(kind, filters, preview_limit=None):
    query, values = procurement_query(kind, filters)
    if preview_limit is not None:
        limit = int(preview_limit)
        if limit < 1 or limit > 20:raise ValueError("Invalid procurement preview size")
        rows = frappe.db.sql("select * from ("+query+f") population order by transaction_date desc, modified desc, name desc limit {limit}", values, as_dict=True)
        return {"rows": rows}
    count = frappe.db.sql("select count(*) as total from ("+query+") population", values, as_dict=True)[0]["total"]
    # Exact identifiers keep native List drill-downs faithful to child-row,
    # supplier-scope, expiry, closure and downstream-allocation predicates.
    rows = frappe.db.sql("select * from ("+query+") population order by transaction_date desc, modified desc, name desc", values, as_dict=True)
    return {"count": int(count), "rows": rows}


def get_open_rfqs(filters):return procurement_population("rfq", filters)["rows"]
def get_open_supplier_quotations(filters):return procurement_population("quotation", filters)["rows"]
def get_pending_commercial_approvals(filters):return procurement_population("approval", filters)["rows"]
def get_open_purchase_orders(filters):return procurement_population("po", filters)["rows"]
def get_delayed_purchase_orders(filters):return procurement_population("delayed", filters)["rows"]


def bind_population(card, doctype, rows, key="name"):
    names = sorted({r[key] for r in rows if r.get(key)})
    card.update(route=get_list_route(doctype), route_doctype=doctype,
                route_options={"name": ["in", names]} if names else {"name": ["=", ""]})


def get_delivery_rows(filters: dict[str, object], supplier_type_map: dict[str, str]) -> list[dict[str, object]]:
    conditions = [
        "pr.docstatus = 1",
        "ifnull(pr.is_return, 0) = 0",
        "po.docstatus = 1",
        "ifnull(po.supplier, '') != ''",
    ]
    values: dict[str, object] = {}
    if filters["item"]:
        conditions.append("pri.item_code = %(item)s")
        values["item"] = filters["item"]
    if filters["supplier"]:
        conditions.append("po.supplier = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters["date_from"]:
        conditions.append("pr.posting_date between %(date_from)s and %(date_to)s")
        values["date_from"] = filters["date_from"]
        values["date_to"] = filters["date_to"]
    rows = frappe.db.sql(
        f"""
        select
            pr.name as purchase_receipt,
            pr.posting_date,
            po.name as purchase_order,
            po.transaction_date,
            po.supplier,
            pri.item_code,
            pri.schedule_date,
            datediff(pr.posting_date, po.transaction_date) as actual_lead_time,
            case
                when pri.schedule_date is not null then datediff(pri.schedule_date, po.transaction_date)
                else null
            end as planned_lead_time
        from `tabPurchase Receipt Item` pri
        inner join `tabPurchase Receipt` pr on pr.name = pri.parent
        inner join `tabPurchase Order` po on po.name = pri.purchase_order
        where {' and '.join(conditions)}
        order by pr.posting_date desc, pr.modified desc
        """,
        values,
        as_dict=True,
    )
    filtered = []
    for row in rows:
        if filters["supplier_type"] and supplier_type_map.get(row["supplier"]) != filters["supplier_type"]:
            continue
        planned = flt(row.get("planned_lead_time") or 0)
        actual = flt(row.get("actual_lead_time") or 0)
        row["is_on_time"] = bool(row.get("schedule_date") and getdate(row["posting_date"]) <= getdate(row["schedule_date"]))
        row["lead_time_accuracy"] = round(max(0, 100 - (abs(actual - planned) * 100 / planned)), 2) if planned > 0 else None
        filtered.append(row)
    return filtered


def get_receipt_quality_rows(filters: dict[str, object], supplier_type_map: dict[str, str]) -> list[dict[str, object]]:
    conditions = [
        "pr.docstatus = 1",
        "ifnull(pr.is_return, 0) = 0",
        "ifnull(pr.supplier, '') != ''",
    ]
    values: dict[str, object] = {}
    if filters["item"]:
        conditions.append("pri.item_code = %(item)s")
        values["item"] = filters["item"]
    if filters["supplier"]:
        conditions.append("pr.supplier = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters["date_from"]:
        conditions.append("pr.posting_date between %(date_from)s and %(date_to)s")
        values["date_from"] = filters["date_from"]
        values["date_to"] = filters["date_to"]
    rows = frappe.db.sql(
        f"""
        select
            pr.name as purchase_receipt,
            pr.posting_date,
            pr.supplier,
            pri.item_code,
            ifnull(pri.received_qty, pri.qty) as received_qty,
            ifnull(pri.custom_rejected_qty, 0) as rejected_qty
        from `tabPurchase Receipt Item` pri
        inner join `tabPurchase Receipt` pr on pr.name = pri.parent
        where {' and '.join(conditions)}
        order by pr.posting_date desc, pr.modified desc
        """,
        values,
        as_dict=True,
    )
    return [row for row in rows if not filters["supplier_type"] or supplier_type_map.get(row["supplier"]) == filters["supplier_type"]]


def get_capa_rows(filters: dict[str, object], supplier_type_map: dict[str, str]) -> list[dict[str, object]]:
    if not frappe.db.exists("DocType", "Supplier CAPA Request"):
        return []
    conditions = ["ifnull(scr.supplier, '') != ''", "scr.docstatus < 2"]
    values: dict[str, object] = {}
    if filters["item"]:
        conditions.append("scr.item_code = %(item)s")
        values["item"] = filters["item"]
    if filters["supplier"]:
        conditions.append("scr.supplier = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters["date_from"]:
        conditions.append("date(scr.creation) between %(date_from)s and %(date_to)s")
        values["date_from"] = filters["date_from"]
        values["date_to"] = filters["date_to"]
    rows = frappe.db.sql(
        f"""
        select scr.name, scr.supplier, scr.item_code, scr.docstatus, scr.required_response_date
        from `tabSupplier CAPA Request` scr
        where {' and '.join(conditions)}
        order by scr.creation desc
        """,
        values,
        as_dict=True,
    )
    return [row for row in rows if not filters["supplier_type"] or supplier_type_map.get(row["supplier"]) == filters["supplier_type"]]


def get_commercial_approval_rows(filters: dict[str, object], supplier_type_map: dict[str, str]) -> list[dict[str, object]]:
    conditions = ["pca.docstatus < 2"]
    values: dict[str, object] = {}
    if filters["item"]:
        conditions.append("pca.item_code = %(item)s")
        values["item"] = filters["item"]
    if filters["supplier"]:
        conditions.append("pca.supplier = %(supplier)s")
        values["supplier"] = filters["supplier"]
    if filters["date_from"]:
        conditions.append("date(pca.creation) between %(date_from)s and %(date_to)s")
        values["date_from"] = filters["date_from"]
        values["date_to"] = filters["date_to"]
    rows = frappe.db.sql(
        f"""
        select
            pca.name,
            pca.supplier,
            pca.item_code,
            pca.variance_amount,
            pca.approval_status,
            pca.quoted_rate,
            pca.benchmark_rate
        from `tabPurchase Commercial Approval` pca
        where {' and '.join(conditions)}
        order by pca.creation desc
        """,
        values,
        as_dict=True,
    )
    return [row for row in rows if not filters["supplier_type"] or supplier_type_map.get(row["supplier"]) == filters["supplier_type"]]


def filter_rows_by_supplier_type(rows: list[dict[str, object]], filters: dict[str, object]) -> list[dict[str, object]]:
    if not filters["supplier_type"]:
        return rows
    supplier_type_map = get_supplier_type_map(filters)
    return [row for row in rows if supplier_type_map.get(row.get("supplier") or "") == filters["supplier_type"]]


def is_capa_closed(row: dict[str, object]) -> bool:
    return int(row.get("docstatus") or 0) == 1


def build_route_options(**kwargs) -> dict[str, object]:
    options = {}
    for key, value in kwargs.items():
        if value in (None, "", [], {}):
            continue
        options[key] = value
    return options
