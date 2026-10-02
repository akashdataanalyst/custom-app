from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import frappe
from frappe.utils import getdate, nowdate


def resolve_report_date(sql_queries: list[str] | None = None):
    for query in sql_queries or []:
        rows = frappe.db.sql(query, as_dict=True)
        if rows and rows[0].get("report_date"):
            return getdate(rows[0]["report_date"])
    return getdate(nowdate())


def last_n_dates(report_date, days: int = 7) -> list[str]:
    base_date = getdate(report_date)
    return [str(base_date - timedelta(days=offset)) for offset in range(days - 1, -1, -1)]


def get_doc_route(doctype: str, name: str) -> str:
    return f"/app/{frappe.scrub(doctype).replace('_', '-')}/{name}"


def get_list_route(doctype: str) -> str:
    return f"/app/{frappe.scrub(doctype).replace('_', '-')}/view/list"


def get_report_route(report_name: str) -> str:
    return f"/app/query-report/{quote(report_name, safe='')}"


def make_card(
    label: str,
    value,
    suffix: str = "",
    route: str | None = None,
    route_doctype: str | None = None,
    route_options: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "label": label,
        "value": value,
        "suffix": suffix,
        "route": route,
        "route_doctype": route_doctype,
        "route_options": route_options,
    }


def make_chart(
    key: str,
    title: str,
    labels: list[str],
    datasets: list[dict[str, object]],
    chart_type: str = "bar",
    colors: list[str] | None = None,
    suffix: str = "",
    route: str | None = None,
    route_doctype: str | None = None,
    route_report: str | None = None,
    route_options: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "key": key,
        "title": title,
        "type": chart_type,
        "labels": labels,
        "datasets": datasets,
        "colors": colors or ["#1f77b4"],
        "suffix": suffix,
        "route": route,
        "route_doctype": route_doctype,
        "route_report": route_report,
        "route_options": route_options,
    }


def make_drilldown_row(doctype: str, name: str, label: str | None = None, meta: str = "") -> dict[str, str]:
    return {
        "label": label or name,
        "meta": meta,
        "route": get_doc_route(doctype, name),
    }


def make_drilldown(title: str, rows: list[dict[str, str]]) -> dict[str, object]:
    return {
        "title": title,
        "rows": rows,
    }
