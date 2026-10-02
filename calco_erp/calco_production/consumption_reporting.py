from __future__ import annotations

from frappe.utils import getdate


def build_consumption_row_source() -> str:
    return """
        select
            pci.parent,
            pci.rm_code,
            pci.rm_batch_no,
            pci.rm_qty_consumed as rm_qty,
            pci.category,
            pci.challan_invoice_no,
            pci.remarks,
            pci.name as row_name
        from `tabProduction Consumption RM Item` pci

        union all

        select
            pce_legacy.name as parent,
            pce_legacy.rm_code,
            pce_legacy.rm_batch_no,
            pce_legacy.rm_qty as rm_qty,
            pce_legacy.category,
            pce_legacy.challan_invoice_no,
            '' as remarks,
            concat(pce_legacy.name, '-legacy') as row_name
        from `tabProduction Consumption Entry` pce_legacy
        where ifnull(pce_legacy.rm_code, '') != ''
          and ifnull(pce_legacy.rm_batch_no, '') != ''
          and ifnull(pce_legacy.rm_qty, 0) > 0
          and not exists (
              select 1
              from `tabProduction Consumption RM Item` legacy_child
              where legacy_child.parent = pce_legacy.name
          )
    """


def build_common_conditions(filters: dict | None) -> tuple[str, dict[str, object]]:
    filters = filters or {}
    conditions = ["pce.docstatus = 1"]
    # Legacy rows remain unchanged. Only the latest submitted final correction
    # contributes its additional measured quantity to consumption reports.
    import frappe
    if frappe.get_meta('Production Consumption Entry').has_field('consumption_mode'):
        conditions.append("""(ifnull(pce.consumption_mode, '') != 'Final Confirmation' or not exists (
            select 1 from `tabProduction Consumption Entry` corrected
            where corrected.supersedes=pce.name and corrected.consumption_mode='Final Confirmation'
            and corrected.docstatus=1))""")
    params: dict[str, object] = {}

    if filters.get("from_date"):
        params["from_date"] = getdate(filters["from_date"])
        conditions.append("date(pce.posting_datetime) >= %(from_date)s")
    if filters.get("to_date"):
        params["to_date"] = getdate(filters["to_date"])
        conditions.append("date(pce.posting_datetime) <= %(to_date)s")
    if filters.get("warehouse"):
        params["warehouse"] = filters["warehouse"]
        conditions.append("pce.warehouse = %(warehouse)s")
    if filters.get("rm_code"):
        params["rm_code"] = filters["rm_code"]
        conditions.append("pci.rm_code = %(rm_code)s")
    if filters.get("fg_code"):
        params["fg_code"] = filters["fg_code"]
        conditions.append("pce.fg_code = %(fg_code)s")
    if filters.get("fg_batch_no"):
        params["fg_batch_no"] = filters["fg_batch_no"]
        conditions.append("pce.fg_batch_no = %(fg_batch_no)s")
    if filters.get("production_line"):
        params["production_line"] = filters["production_line"]
        conditions.append("pce.production_line = %(production_line)s")
    if filters.get("category"):
        params["category"] = filters["category"]
        conditions.append("pci.category = %(category)s")

    return " and ".join(conditions), params
