from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, get_first_day, getdate, nowdate


def execute(filters=None):
    filters = _normalize_filters(filters)
    batch_scope = _is_batch_scope(filters)
    consumption_rows = _get_consumption_rows(filters, ignore_dates=batch_scope)
    evidence_rows = _get_stock_evidence(consumption_rows)
    data, warnings = build_report_rows(consumption_rows, evidence_rows)

    summary_data = data
    if batch_scope:
        summary_filters = frappe._dict(
            fg_item=filters.fg_item,
            production_batch=filters.production_batch,
            from_date=filters.from_date,
            to_date=filters.to_date,
        )
        summary_consumption_rows = _get_consumption_rows(summary_filters, ignore_dates=True)
        summary_evidence_rows = _get_stock_evidence(summary_consumption_rows)
        summary_data, summary_warnings = build_report_rows(summary_consumption_rows, summary_evidence_rows)
        warnings = list(dict.fromkeys([*warnings, *summary_warnings]))
        _apply_cost_contribution(data, denominator=sum(flt(row.rm_cost) for row in summary_data))

    selected_pce = filters.production_consumption_entry
    return (
        get_columns(),
        data,
        _build_message(data, warnings, filters),
        None,
        build_report_summary(
            summary_data,
            selected_pce=selected_pce,
            selected_fg=filters.fg_item,
            selected_batch=filters.production_batch,
        ),
    )


def _normalize_filters(filters=None):
    filters = frappe._dict(filters or {})
    filters.from_date = getdate(filters.from_date or get_first_day(nowdate()))
    filters.to_date = getdate(filters.to_date or nowdate())
    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date cannot be after To Date."))
    return filters


def get_columns():
    return [
        {"label": _("Posting Date"), "fieldname": "posting_datetime", "fieldtype": "Datetime", "width": 165},
        {"label": _("Production Line"), "fieldname": "production_line", "fieldtype": "Link", "options": "Workstation", "width": 145},
        {"label": _("FG Item"), "fieldname": "fg_item", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": _("Production Batch"), "fieldname": "production_batch", "fieldtype": "Data", "width": 155},
        {
            "label": _("Production Consumption Entry"),
            "fieldname": "production_consumption_entry",
            "fieldtype": "Link",
            "options": "Production Consumption Entry",
            "width": 175,
        },
        {"label": _("RM Item"), "fieldname": "rm_item", "fieldtype": "Link", "options": "Item", "width": 135},
        {"label": _("RM Item Name"), "fieldname": "rm_item_name", "fieldtype": "Data", "width": 190},
        {"label": _("RM Batch"), "fieldname": "rm_batch", "fieldtype": "Data", "width": 165},
        {"label": _("Consumed Qty Kg"), "fieldname": "consumed_qty", "fieldtype": "Float", "precision": 3, "width": 140},
        {
            "label": _("Valuation Rate"),
            "fieldname": "valuation_rate",
            "fieldtype": "Currency",
            "options": "currency",
            "precision": 2,
            "width": 135,
        },
        {"label": _("RM Cost"), "fieldname": "rm_cost", "fieldtype": "Currency", "options": "currency", "precision": 2, "width": 135},
        {"label": _("Cost Contribution %"), "fieldname": "cost_contribution", "fieldtype": "Percent", "precision": 2, "width": 155},
        {"label": _("Consumption Stock Entry"), "fieldname": "stock_entry", "fieldtype": "Link", "options": "Stock Entry", "width": 175},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "hidden": 1},
        {"label": _("Batch Exists"), "fieldname": "batch_exists", "fieldtype": "Check", "hidden": 1},
    ]


def _is_batch_scope(filters):
    return bool(filters.get("fg_item") and filters.get("production_batch") and not filters.get("production_consumption_entry"))


def _get_consumption_rows(filters, ignore_dates=False):
    conditions, params = _build_consumption_conditions(filters, ignore_dates=ignore_dates)
    return frappe.db.sql(
        f"""
        select
            pce.name as production_consumption_entry,
            pce.posting_datetime,
            pce.production_line,
            pce.fg_code as fg_item,
            pce.fg_batch_no as production_batch,
            pce.stock_entry,
            pce.company,
            pci.name as consumption_row,
            pci.idx as consumption_idx,
            pci.rm_code as rm_item,
            item.item_name as rm_item_name,
            pci.rm_batch_no as rm_batch,
            pci.rm_qty_consumed as consumed_qty,
            se.docstatus as stock_entry_docstatus,
            case when batch.name is null then 0 else 1 end as batch_exists,
            company.default_currency as currency
        from `tabProduction Consumption Entry` pce
        inner join `tabProduction Consumption RM Item` pci on pci.parent = pce.name
        left join `tabStock Entry` se on se.name = pce.stock_entry
        left join `tabItem` item on item.name = pci.rm_code
        left join `tabBatch` batch on batch.name = pci.rm_batch_no and batch.item = pci.rm_code
        left join `tabCompany` company on company.name = pce.company
        where {" and ".join(conditions)}
        order by pce.posting_datetime desc, pce.name, pci.idx
        """,
        params,
        as_dict=True,
    )


def _build_consumption_conditions(filters, ignore_dates=False):
    conditions = ["pce.docstatus = 1"]
    params = {}
    if not ignore_dates:
        conditions.append("date(pce.posting_datetime) between %(from_date)s and %(to_date)s")
        params.update({"from_date": filters.from_date, "to_date": filters.to_date})
    field_map = {
        "production_line": "pce.production_line",
        "fg_item": "pce.fg_code",
        "production_batch": "pce.fg_batch_no",
        "production_consumption_entry": "pce.name",
        "rm_item": "pci.rm_code",
        "rm_batch": "pci.rm_batch_no",
    }
    for fieldname, sql_field in field_map.items():
        value = filters.get(fieldname)
        if value:
            conditions.append(f"{sql_field} = %({fieldname})s")
            params[fieldname] = value
    return conditions, params


def _get_stock_evidence(consumption_rows):
    stock_entries = sorted(
        {
            row.stock_entry
            for row in consumption_rows
            if row.stock_entry and int(row.stock_entry_docstatus or 0) == 1
        }
    )
    if not stock_entries:
        return []

    return frappe.db.sql(
        """
        select
            sed.name as stock_detail,
            sed.parent as stock_entry,
            sed.idx as stock_detail_idx,
            sed.item_code as rm_item,
            sed.qty as stock_qty,
            sed.basic_amount,
            sed.valuation_rate,
            sed.serial_and_batch_bundle,
            sabe.name as bundle_row,
            sabe.idx as bundle_idx,
            sabe.batch_no as rm_batch,
            sabe.qty as bundle_qty
        from `tabStock Entry Detail` sed
        inner join `tabStock Entry` se on se.name = sed.parent and se.docstatus = 1
        left join `tabSerial and Batch Entry` sabe on sabe.parent = sed.serial_and_batch_bundle
        where sed.parent in %(stock_entries)s
        order by sed.parent, sed.idx, sabe.idx
        """,
        {"stock_entries": tuple(stock_entries)},
        as_dict=True,
    )


def build_report_rows(consumption_rows: Iterable, evidence_rows: Iterable):
    evidence_by_detail = defaultdict(list)
    for evidence in evidence_rows:
        evidence = frappe._dict(evidence)
        evidence_by_detail[evidence.stock_detail].append(evidence)

    exact_evidence = defaultdict(deque)
    for rows in evidence_by_detail.values():
        valid_batches = [row for row in rows if row.serial_and_batch_bundle and row.rm_batch]
        if len(valid_batches) != 1:
            continue
        evidence = valid_batches[0]
        exact_evidence[(evidence.stock_entry, evidence.rm_item, evidence.rm_batch)].append(evidence)

    report_rows = []
    warnings = []
    for source in consumption_rows:
        source = frappe._dict(source)
        if int(source.stock_entry_docstatus or 0) != 1:
            warnings.append(_warning(source, _("linked Stock Entry is missing or not submitted")))
            continue

        candidates = exact_evidence.get((source.stock_entry, source.rm_item, source.rm_batch))
        if not candidates:
            warnings.append(_warning(source, _("no exact submitted bundle-backed Stock Entry row was found")))
            continue

        evidence = candidates.popleft()
        report_rows.append(
            frappe._dict(
                {
                    "posting_datetime": source.posting_datetime,
                    "production_line": source.production_line,
                    "fg_item": source.fg_item,
                    "production_batch": source.production_batch,
                    "production_consumption_entry": source.production_consumption_entry,
                    "stock_entry": source.stock_entry,
                    "rm_item": source.rm_item,
                    "rm_item_name": source.rm_item_name,
                    "rm_batch": source.rm_batch,
                    "consumed_qty": flt(source.consumed_qty, 3),
                    "valuation_rate": flt(evidence.valuation_rate, 2),
                    "rm_cost": flt(evidence.basic_amount, 2),
                    "currency": source.currency or frappe.defaults.get_global_default("currency") or "INR",
                    "batch_exists": int(source.batch_exists or 0),
                }
            )
        )

    _apply_cost_contribution(report_rows)
    return report_rows, warnings


def _apply_cost_contribution(rows, denominator=None):
    if denominator is not None:
        for row in rows:
            row.cost_contribution = flt((flt(row.rm_cost) / denominator * 100) if denominator else 0, 2)
        return

    totals = defaultdict(float)
    for row in rows:
        totals[row.production_consumption_entry] += flt(row.rm_cost)
    for row in rows:
        total = totals[row.production_consumption_entry]
        row.cost_contribution = flt((flt(row.rm_cost) / total * 100) if total else 0, 2)


def calculate_cost_summary(rows):
    total_qty = sum(flt(row.consumed_qty) for row in rows)
    total_cost = sum(flt(row.rm_cost) for row in rows)
    return frappe._dict(
        {
            "total_qty": flt(total_qty, 3),
            "total_cost": flt(total_cost, 2),
            "average_cost": flt((total_cost / total_qty) if total_qty else 0, 2),
            "cost_contribution": flt(100 if total_cost else 0, 2),
        }
    )


def calculate_pce_summary(rows):
    return calculate_cost_summary(rows)


def build_report_summary(rows, selected_pce=None, selected_fg=None, selected_batch=None):
    if not rows:
        return []

    if selected_pce:
        if {row.production_consumption_entry for row in rows} != {selected_pce}:
            return []
    elif selected_fg and selected_batch:
        if {(row.fg_item, row.production_batch) for row in rows} != {(selected_fg, selected_batch)}:
            return []
    else:
        return []

    totals = calculate_cost_summary(rows)
    currency = rows[0].currency if rows else "INR"
    return [
        {"label": _("TOTAL RM CONSUMED"), "value": f"{totals.total_qty:,.3f} Kg", "datatype": "Data", "indicator": "Blue"},
        {"label": _("TOTAL RM COST"), "value": fmt_money(totals.total_cost, currency=currency), "datatype": "Data", "indicator": "Blue"},
        {
            "label": _("AVERAGE RM INPUT COST/KG"),
            "value": _("{0}/Kg").format(fmt_money(totals.average_cost, currency=currency)),
            "datatype": "Data",
            "indicator": "Green",
        },
        {
            "label": _("COST CONTRIBUTION"),
            "value": f"{totals.cost_contribution:,.2f}%",
            "datatype": "Data",
            "indicator": "Green",
        },
    ]


def _warning(source, reason):
    return _("{0}, row {1}: {2}").format(source.production_consumption_entry, source.consumption_idx, reason)


def _build_message(data, warnings, filters=None):
    filters = frappe._dict(filters or {})
    parts = [
        _("Average RM Input Cost/Kg is based only on consumed RM transaction value. It is not FG Manufacturing Cost/Kg."),
        _("Production Batch summary includes all submitted Production Consumption Entries linked to the selected FG Item and Production Batch."),
    ]
    if _is_batch_scope(filters):
        parts.insert(0, _("<b>Production Batch RM Cost Summary</b>"))
    elif filters.get("production_consumption_entry"):
        parts.insert(0, _("<b>PCE RM Cost Summary</b>"))
    else:
        parts.append(_("Select one Production Batch to display batch-level RM cost summary."))
    if warnings:
        escaped = [frappe.utils.escape_html(warning) for warning in warnings[:20]]
        parts.append(_("Excluded {0} invalid line(s): {1}").format(len(warnings), "; ".join(escaped)))
    return "<br>".join(parts)
