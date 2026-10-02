"""Read-only, permission-filtered child summaries for the native Purchase MR list."""
from time import perf_counter
import frappe
from frappe import _

MAX_NAMES = 10000  # Native 2500 selection plus Load More; no per-row calls.


def summarize(rows):
    items = [dict(item_code=r.item_code, stock_qty=r.stock_qty,
                  stock_uom=r.stock_uom, schedule_date=r.schedule_date) for r in rows]
    dates = sorted({str(r.schedule_date) for r in rows if r.schedule_date})
    missing = any(not r.schedule_date for r in rows)
    return dict(items=items, required_by=dates[0] if dates else None,
                date_mode="review" if missing or not rows else "earliest" if len(dates) > 1 else "same",
                required_dates=dates)


@frappe.whitelist()
def get_purchase_summaries(names):
    started = perf_counter()
    names = frappe.parse_json(names) if isinstance(names, str) else names
    if not isinstance(names, list) or len(names) > MAX_NAMES or any(
        not isinstance(n, str) or not n or len(n) > 140 for n in names
    ):
        frappe.throw(_("Supply at most {0} Material Request names.").format(MAX_NAMES))
    names = list(dict.fromkeys(names))
    if not frappe.has_permission("Material Request", "read"):
        frappe.throw(_("Not permitted to read Material Requests."), frappe.PermissionError)
    if not names:
        return dict(summaries={}, performance_ms=0)
    # Same permission query, user and sharing scope as the native parent list.
    allowed = frappe.get_list("Material Request", filters={
        "name": ["in", names], "material_request_type": "Purchase"},
        fields=["name"], limit_page_length=len(names))
    grouped = {row.name: [] for row in allowed}
    if grouped:
        # Parent authorization is mandatory before reading child rows.
        rows = frappe.get_all("Material Request Item", filters={
            "parent": ["in", list(grouped)], "parenttype": "Material Request", "parentfield": "items"},
            fields=["parent", "item_code", "stock_qty", "stock_uom", "schedule_date"],
            order_by="parent asc, idx asc, name asc", limit_page_length=0)
        for row in rows:
            grouped[row.parent].append(row)
    return dict(summaries={name: summarize(rows) for name, rows in grouped.items()},
                performance_ms=round((perf_counter() - started) * 1000, 3))
