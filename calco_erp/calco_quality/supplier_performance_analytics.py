from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def get_supplier_performance_data() -> dict[str, object]:
    rows = build_supplier_performance_rows()
    return {
        "rows": rows,
        "cards": {
            "total_suppliers": len(rows),
            "avg_on_time": round(sum(row["on_time_delivery_percentage"] for row in rows) / len(rows), 2) if rows else 0,
            "open_capa": sum(row["open_capa"] for row in rows),
            "rm_rejections": sum(row["rm_rejections"] for row in rows),
        },
    }


def build_supplier_performance_rows() -> list[dict[str, object]]:
    deliveries = _get_delivery_rows()
    rejection_map = _get_rm_rejection_counts()
    capa_map = _get_open_capa_counts()
    rating_map = _get_supplier_rating_map()
    type_map = _get_supplier_type_map()

    aggregated: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "supplier": "",
            "avg_lead_time": 0.0,
            "min_lead_time": 0.0,
            "max_lead_time": 0.0,
            "on_time_delivery_percentage": 0.0,
            "late_deliveries": 0,
            "rm_rejections": 0,
            "open_capa": 0,
            "supplier_rating": 0.0,
            "supplier_type": "",
        }
    )

    grouped_days: dict[str, list[float]] = defaultdict(list)
    grouped_on_time: dict[str, int] = defaultdict(int)
    grouped_eligible: dict[str, int] = defaultdict(int)

    for row in deliveries:
        supplier = row.get("supplier") or ""
        if not supplier:
            continue
        grouped_days[supplier].append(flt(row.get("lead_time_days") or 0))
        if row.get("schedule_date"):
            grouped_eligible[supplier] += 1
            if flt(row.get("is_on_time") or 0):
                grouped_on_time[supplier] += 1

    all_suppliers = set(grouped_days) | set(rejection_map) | set(capa_map) | set(rating_map)
    rows: list[dict[str, object]] = []
    for supplier in sorted(all_suppliers):
        lead_days = grouped_days.get(supplier, [])
        eligible = grouped_eligible.get(supplier, 0)
        on_time = grouped_on_time.get(supplier, 0)
        late = max(eligible - on_time, 0)
        rows.append(
            {
                "supplier": supplier,
                "avg_lead_time": round(sum(lead_days) / len(lead_days), 2) if lead_days else 0,
                "min_lead_time": round(min(lead_days), 2) if lead_days else 0,
                "max_lead_time": round(max(lead_days), 2) if lead_days else 0,
                "on_time_delivery_percentage": round((on_time * 100.0 / eligible), 2) if eligible else 0,
                "late_deliveries": late,
                "rm_rejections": rejection_map.get(supplier, 0),
                "open_capa": capa_map.get(supplier, 0),
                "supplier_rating": round(rating_map.get(supplier, 0), 2),
                "supplier_type": type_map.get(supplier, ""),
            }
        )

    rows.sort(key=lambda row: (-row["open_capa"], -row["rm_rejections"], row["supplier"]))
    return rows


def _get_delivery_rows() -> list[dict[str, object]]:
    return frappe.db.sql(
        """
        select
            po.supplier,
            datediff(pr.posting_date, po.transaction_date) as lead_time_days,
            pri.schedule_date,
            case when pri.schedule_date is not null and pr.posting_date <= pri.schedule_date then 1 else 0 end as is_on_time
        from `tabPurchase Receipt Item` pri
        inner join `tabPurchase Receipt` pr on pr.name = pri.parent
        inner join `tabPurchase Order` po on po.name = pri.purchase_order
        where pr.docstatus = 1
          and ifnull(pr.is_return, 0) = 0
          and po.docstatus = 1
          and pri.purchase_order is not null
          and pri.purchase_order != ''
          and ifnull(po.supplier, '') != ''
        """,
        as_dict=True,
    )


def _get_rm_rejection_counts() -> dict[str, int]:
    rows = frappe.db.sql(
        """
        select
            pr.supplier,
            count(distinct qi.name) as rejected_count
        from `tabQuality Inspection` qi
        inner join `tabPurchase Receipt` pr on pr.name = qi.reference_name
        where qi.docstatus = 1
          and qi.reference_type = 'Purchase Receipt'
          and ifnull(pr.supplier, '') != ''
          and (
            ifnull(qi.status, '') = 'Rejected'
            or ifnull(qi.custom_overall_result, '') in ('REJECTED', 'REVIEW REQUIRED')
          )
        group by pr.supplier
        """,
        as_dict=True,
    )
    return {row["supplier"]: int(row["rejected_count"] or 0) for row in rows if row.get("supplier")}


def _get_open_capa_counts() -> dict[str, int]:
    if not frappe.db.exists("DocType", "Supplier CAPA Request"):
        return {}
    rows = frappe.db.sql(
        """
        select supplier, count(*) as open_count
        from `tabSupplier CAPA Request`
        where docstatus < 2
          and ifnull(supplier, '') != ''
        group by supplier
        """,
        as_dict=True,
    )
    return {row["supplier"]: int(row["open_count"] or 0) for row in rows if row.get("supplier")}


def _get_supplier_rating_map() -> dict[str, float]:
    if not frappe.db.exists("DocType", "Supplier Approval Matrix"):
        return {}
    rows = frappe.db.sql(
        """
        select supplier, avg(ifnull(supplier_rating, 0)) as supplier_rating
        from `tabSupplier Approval Matrix`
        where ifnull(supplier, '') != ''
        group by supplier
        """,
        as_dict=True,
    )
    return {row["supplier"]: flt(row["supplier_rating"] or 0) for row in rows if row.get("supplier")}


def _get_supplier_type_map() -> dict[str, str]:
    if not frappe.db.exists("DocType", "Supplier Approval Matrix"):
        return {}
    rows = frappe.db.sql(
        """
        select supplier, supplier_type
        from `tabSupplier Approval Matrix`
        where ifnull(supplier, '') != ''
          and ifnull(supplier_type, '') != ''
        order by modified desc
        """,
        as_dict=True,
    )
    mapping: dict[str, str] = {}
    for row in rows:
        mapping.setdefault(row["supplier"], row["supplier_type"])
    return mapping

