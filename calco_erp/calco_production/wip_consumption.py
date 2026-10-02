from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from calco_erp.calco_production.material_reservation_transfer import has_any_calco_reservation
from calco_erp.calco_production.production_readiness import assert_production_readiness
from calco_erp.calco_production.stock_entry_warehouse_defaults import TARGET_WAREHOUSE
from calco_erp.inventory.availability import get_batch_availability


CONSUMPTION_PURPOSE = "Material Consumption for Manufacture"
EPSILON = 1e-9


class ControlledWIPConsumptionStockEntryMixin:
    def check_if_operations_completed(self):
        if not (
            _purpose(self) == CONSUMPTION_PURPOSE
            and is_controlled_work_order(self.get("work_order"))
        ):
            return super().check_if_operations_completed()

        _assert_consumption_operations_completed(self)


def _assert_consumption_operations_completed(stock_entry):
    """Require completed production operations while leaving Packing downstream."""
    from calco_erp.calco_production.operation_master import PACKING_OPERATION

    work_order = frappe.get_doc("Work Order", stock_entry.work_order)
    allowance_percentage = _get_overproduction_allowance_percentage()
    total_completed_qty = flt(stock_entry.fg_completed_qty) + flt(work_order.produced_qty)

    for operation in work_order.get("operations") or []:
        if cstr(operation.operation).strip() == PACKING_OPERATION:
            continue
        completed_qty = (
            flt(operation.completed_qty)
            + flt(operation.process_loss_qty)
            + (allowance_percentage / 100 * flt(operation.completed_qty))
        )
        if total_completed_qty > completed_qty + EPSILON:
            frappe.throw(
                _(
                    "Operation {0} is not completed for {1} quantity before WIP "
                    "material consumption."
                ).format(operation.operation, total_completed_qty)
            )


def _get_overproduction_allowance_percentage():
    return flt(
        frappe.db.get_single_value(
            "Manufacturing Settings", "overproduction_percentage_for_work_order"
        )
    )


def is_controlled_work_order(work_order: str | None) -> bool:
    from calco_erp.calco_production.fg_planning_authority import is_dashboard_name
    return is_dashboard_name(cstr(work_order).strip()) or has_any_calco_reservation(cstr(work_order).strip())


@frappe.whitelist()
def get_wip_consumption_preview(work_order: str | None = None, *args, **kwargs) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
    wo = _validate_work_order(work_order)
    if not is_controlled_work_order(work_order):
        return {
            "work_order": work_order,
            "controlled": False,
            "can_create": False,
            "warehouse": wo.wip_warehouse or "",
            "rows": [],
            "reason": _("This Work Order does not use the controlled Calco reservation flow."),
        }
    rows = _get_consumable_rows(wo)
    return {
        "work_order": work_order,
        "controlled": True,
        "can_create": bool(rows),
        "warehouse": TARGET_WAREHOUSE,
        "rows": rows,
        "reason": "" if rows else _("No transferred WIP material remains available for consumption."),
    }


def get_execution_wip_context(work_order: str | None, include_history=False) -> dict[str, Any]:
    """Keep selection unchanged; cockpit may include exhausted historical lineage."""
    # Historical display is read-only; terminal WO state blocks posting, not audit visibility.
    if include_history and frappe.db.get_value("Work Order", work_order, "status") in {"Completed", "Closed", "Stopped", "Cancelled"}:
        wo = frappe.get_doc("Work Order", work_order)
        wo.check_permission("read")
        preview = {"work_order": wo.name, "warehouse": wo.wip_warehouse, "rows": _get_consumable_rows(wo)}
    else:
        preview = get_wip_consumption_preview(work_order)
    rows = []
    for row in preview.get("rows") or []:
        evidence = get_batch_availability(
            item_code=row["item_code"],
            batch_no=row["batch_no"],
            warehouse=TARGET_WAREHOUSE,
            work_order=work_order,
        )
        lineage = (evidence.get("evidence_source") or {}).get("wip_lineage") or {}
        rows.append({
            "item_code": row["item_code"],
            "batch_no": row["batch_no"],
            "warehouse": TARGET_WAREHOUSE,
            "physical_wip_qty": round(flt(evidence.get("physical_quantity")), 6),
            "wo_attributed_available_qty": round(flt(row.get("available_qty")), 6),
            "consumed_qty": round(flt(lineage.get("consumed_qty")), 6),
            "returned_qty": round(flt(lineage.get("returned_qty")), 6),
            "stock_uom": row.get("stock_uom") or "",
            "evidence_source": row.get("evidence_source") or {},
            "ordering_sequence": row.get("ordering_sequence"),
        })
    if include_history:
        from calco_erp.inventory.availability import get_work_order_wip_lineage
        history = get_work_order_wip_lineage(work_order)
        indexed = {(r['item_code'], r['batch_no']): r for r in rows}
        for (item, batch), values in sorted(history.items()):
            if not any(flt(values.get(k)) for k in ('transferred_qty', 'consumed_qty', 'returned_qty')):
                continue
            row = indexed.get((item, batch))
            if row is None:
                from calco_erp.inventory.availability import get_item_availability
                evidence = get_batch_availability(item_code=item,batch_no=batch,warehouse=TARGET_WAREHOUSE,work_order=work_order) if batch else get_item_availability(item_code=item,warehouse=TARGET_WAREHOUSE,work_order=work_order)
                row = {'item_code':item, 'batch_no':batch, 'warehouse':TARGET_WAREHOUSE,
                       'wo_attributed_available_qty':0.0, 'physical_wip_qty':round(flt(evidence.get('physical_quantity')),6),
                       'stock_uom':values.get('stock_uom') or '', 'evidence_source':{'wip_lineage':values}}
                indexed[(item,batch)] = row
            for field in ('transferred_qty','consumed_qty','returned_qty'):
                row[field] = round(flt(values.get(field)),6)
        rows = [indexed[key] for key in sorted(indexed)]
    return {
        "work_order": preview.get("work_order") or "",
        "warehouse": preview.get("warehouse") or TARGET_WAREHOUSE,
        "rows": rows,
        "totals": {
            "transferred_qty": round(sum(flt(row.get("transferred_qty")) for row in rows), 6),
            "physical_wip_qty": round(sum(row["physical_wip_qty"] for row in rows), 6),
            "wo_attributed_available_qty": round(sum(row["wo_attributed_available_qty"] for row in rows), 6),
            "consumed_qty": round(sum(row["consumed_qty"] for row in rows), 6),
            "returned_qty": round(sum(row["returned_qty"] for row in rows), 6),
        },
    }


@frappe.whitelist()
def make_wip_consumption_stock_entry(
    work_order: str | None = None,
    rows: str | list[dict[str, Any]] | None = None,
    basis_qty: float | None = None,
    *args,
    **kwargs,
) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order") or kwargs.get("work_order_id")).strip()
    frappe.has_permission("Stock Entry", "create", throw=True)
    frappe.has_permission("Work Order", "read", doc=work_order, throw=True)
    wo = _validate_work_order(work_order)
    if not is_controlled_work_order(work_order):
        frappe.throw(_("Work Order {0} does not use controlled WIP consumption.").format(work_order))
    assert_production_readiness(work_order)
    preview = _get_consumable_rows(wo)
    if not preview:
        frappe.throw(_("No transferred WIP material remains available for consumption."))
    requested = _parse_requested_rows(rows)
    if not requested:
        requested = _requested_rows_from_standard_quantity(wo, preview, basis_qty)
    selected = _validate_requested_rows(requested, preview)
    return _build_standard_consumption_draft(wo, selected, basis_qty).as_dict()


def validate_wip_consumption(doc, method=None):
    if _is_controlled_consumption(doc):
        _validate_consumption_document(doc)


def validate_wip_consumption_on_submit(doc, method=None):
    if not _is_controlled_consumption(doc):
        return
    frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name = %s FOR UPDATE", (doc.work_order,))
    _validate_consumption_document(doc)


def _is_controlled_consumption(doc) -> bool:
    return bool(
        cstr(doc.get("work_order")).strip()
        and _purpose(doc) == CONSUMPTION_PURPOSE
        and is_controlled_work_order(doc.get("work_order"))
    )


def _validate_work_order(work_order: str):
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))
    wo = frappe.get_doc("Work Order", work_order)
    if cint(wo.docstatus) != 1:
        frappe.throw(_("Work Order {0} must be submitted.").format(work_order))
    if cstr(wo.status).strip() in {"Stopped", "Cancelled", "Completed", "Closed"}:
        frappe.throw(_("Work Order {0} status {1} does not allow WIP consumption.").format(work_order, wo.status))
    if cstr(wo.wip_warehouse).strip() != TARGET_WAREHOUSE:
        frappe.throw(_("Work Order {0} must use WIP warehouse {1}.").format(work_order, TARGET_WAREHOUSE))
    if flt(wo.material_transferred_for_manufacturing) <= 0:
        frappe.throw(_("Material must be transferred to WIP before consumption."))
    return wo


def _validate_consumption_document(doc):
    from calco_erp.calco_production.fg_planning_authority import assert_execution_allowed
    assert_execution_allowed(doc.work_order)
    wo = _validate_work_order(doc.work_order)
    assert_production_readiness(doc.work_order)
    if cstr(doc.get("from_warehouse")).strip() not in {"", TARGET_WAREHOUSE}:
        frappe.throw(
            _("Controlled material consumption must use source warehouse {0}.").format(
                TARGET_WAREHOUSE
            )
        )
    if cstr(doc.get("to_warehouse")).strip():
        frappe.throw(_("Material Consumption for Manufacture must not have a target warehouse."))

    allowed = {
        (row["item_code"], row["batch_no"]): flt(row["available_qty"])
        for row in _get_consumable_rows(wo)
    }
    actual = _document_consumption_map(doc)
    if not actual:
        frappe.throw(_("At least one WIP consumption row with a positive quantity is required."))

    for row in doc.get("items") or []:
        if row.get("is_finished_item") or row.get("t_warehouse"):
            frappe.throw(
                _("Controlled WIP consumption cannot create finished goods or target-warehouse rows.")
            )
        if cstr(row.get("s_warehouse")).strip() != TARGET_WAREHOUSE:
            frappe.throw(
                _("Item {0} must be consumed from {1}.").format(row.item_code, TARGET_WAREHOUSE)
            )

    for (item_code, batch_no), quantity in actual.items():
        permitted = flt(allowed.get((item_code, batch_no)))
        if quantity > permitted + EPSILON:
            frappe.throw(
                _(
                    "Consumption {0} for item {1}, batch {2} exceeds transferred and "
                    "physically available WIP quantity {3}."
                ).format(
                    round(quantity, 3),
                    item_code,
                    batch_no or _("non-batch stock"),
                    round(permitted, 3),
                )
            )


def _get_consumable_rows(wo) -> list[dict[str, Any]]:
    from erpnext.stock.doctype.stock_entry.stock_entry import get_available_materials

    standard_rows = get_available_materials(wo.name)
    remaining_by_item = _remaining_by_item(wo)
    result = []

    for (item_code, warehouse), material in standard_rows.items():
        if cstr(warehouse).strip() != TARGET_WAREHOUSE:
            continue
        item_remaining = flt(remaining_by_item.get(item_code))
        if item_remaining <= EPSILON:
            continue
        item = frappe.db.get_value(
            "Item", item_code, ["has_batch_no", "stock_uom"], as_dict=True
        ) or frappe._dict()
        if not cint(item.has_batch_no):
            physical = flt(
                frappe.db.get_value(
                    "Bin",
                    {"item_code": item_code, "warehouse": TARGET_WAREHOUSE},
                    "actual_qty",
                )
            )
            available = min(item_remaining, max(flt(material.qty), 0), max(physical, 0))
            if available > EPSILON:
                result.append(
                    _preview_row(
                        item_code, "", available, item.stock_uom, {}, len(result) + 1
                    )
                )
            continue

        for batch_no, lineage_qty in (material.batch_details or {}).items():
            lineage_qty = max(flt(lineage_qty), 0)
            if lineage_qty <= EPSILON or item_remaining <= EPSILON:
                continue
            evidence = get_batch_availability(
                item_code=item_code,
                batch_no=batch_no,
                warehouse=TARGET_WAREHOUSE,
                work_order=wo.name,
            )
            physical = max(flt(evidence.get("physical_quantity")), 0)
            eligible = max(flt(evidence.get("available_to_current_work_order")), 0)
            available = min(item_remaining, lineage_qty, physical, eligible)
            if available <= EPSILON:
                continue
            result.append(
                _preview_row(
                    item_code,
                    batch_no,
                    available,
                    item.stock_uom,
                    evidence,
                    len(result) + 1,
                )
            )
            item_remaining -= available
    return result


def _remaining_by_item(wo) -> dict[str, float]:
    remaining = defaultdict(float)
    for row in wo.get("required_items") or []:
        remaining[row.item_code] += max(
            flt(row.transferred_qty) - flt(row.consumed_qty), 0
        )
    return dict(remaining)


def _preview_row(item_code, batch_no, quantity, stock_uom, evidence, sequence):
    return {
        "item_code": item_code,
        "batch_no": batch_no,
        "warehouse": TARGET_WAREHOUSE,
        "available_qty": round(flt(quantity), 6),
        "stock_uom": stock_uom or "",
        "expiry_date": evidence.get("expiry_date") if evidence else None,
        "evidence_source": {
            "lineage": "ERPNext Work Order Material Transfer for Manufacture",
            "physical": (evidence.get("evidence_source") or {}).get("physical")
            if evidence
            else "Bin",
        },
        "ordering_sequence": sequence,
    }


def _parse_requested_rows(rows) -> list[dict[str, Any]]:
    if isinstance(rows, str):
        rows = frappe.parse_json(rows)
    return list(rows or [])


def _requested_rows_from_standard_quantity(wo, preview, basis_qty):
    basis_qty = flt(basis_qty)
    if basis_qty <= 0:
        basis_qty = max(
            flt(wo.material_transferred_for_manufacturing) - flt(wo.produced_qty), 0
        ) or flt(wo.qty)

    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

    standard = frappe.get_doc(
        make_stock_entry(wo.name, CONSUMPTION_PURPOSE, basis_qty)
    )
    required = defaultdict(float)
    for row in standard.get("items") or []:
        if row.get("s_warehouse") and not row.get("is_finished_item"):
            required[row.item_code] += flt(row.get("transfer_qty") or row.get("qty"))

    requested = []
    for available in preview:
        pending = required.get(available["item_code"], 0)
        if pending <= EPSILON:
            continue
        quantity = min(pending, flt(available["available_qty"]))
        if quantity > EPSILON:
            requested.append(
                {
                    "item_code": available["item_code"],
                    "batch_no": available["batch_no"],
                    "qty": quantity,
                }
            )
            required[available["item_code"]] -= quantity
    shortages = {item: qty for item, qty in required.items() if qty > EPSILON}
    if shortages:
        frappe.throw(
            _("Transferred WIP material is insufficient: {0}.").format(
                ", ".join(
                    f"{item} {round(qty, 3)}" for item, qty in shortages.items()
                )
            )
        )
    return requested


def _validate_requested_rows(requested, preview):
    allowed = {
        (row["item_code"], row["batch_no"]): flt(row["available_qty"])
        for row in preview
    }
    selected = defaultdict(float)
    for index, row in enumerate(requested, start=1):
        item_code = cstr(row.get("item_code")).strip()
        batch_no = cstr(row.get("batch_no")).strip()
        quantity = flt(row.get("qty") or row.get("consume_qty"))
        if not item_code or quantity <= 0:
            frappe.throw(
                _("Row #{0}: Item and positive consumption quantity are required.").format(
                    index
                )
            )
        key = (item_code, batch_no)
        if key not in allowed:
            frappe.throw(
                _(
                    "Row #{0}: Item {1}, batch {2} was not transferred to WIP "
                    "for this Work Order."
                ).format(index, item_code, batch_no or _("blank"))
            )
        selected[key] += quantity
        if selected[key] > allowed[key] + EPSILON:
            frappe.throw(
                _(
                    "Row #{0}: Requested {1} exceeds available WIP quantity {2} "
                    "for {3} / {4}."
                ).format(
                    index,
                    round(selected[key], 3),
                    round(allowed[key], 3),
                    item_code,
                    batch_no or _("non-batch"),
                )
            )
    return [
        {"item_code": item, "batch_no": batch, "qty": qty}
        for (item, batch), qty in selected.items()
    ]


def _build_standard_consumption_draft(wo, selected, basis_qty, stock_entry=None):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

    basis_qty = flt(basis_qty) or max(
        flt(wo.material_transferred_for_manufacturing) - flt(wo.produced_qty), 0
    ) or flt(wo.qty)
    if stock_entry is None:
        stock_entry = frappe.get_doc(
            make_stock_entry(wo.name, CONSUMPTION_PURPOSE, basis_qty)
        )
    templates = {}
    for row in stock_entry.get("items") or []:
        if row.get("s_warehouse") and not row.get("is_finished_item"):
            templates.setdefault(row.item_code, row)

    stock_entry.set("items", [])
    for selection in selected:
        template = templates.get(selection["item_code"])
        if not template:
            frappe.throw(
                _("ERPNext did not generate a consumption template for item {0}.").format(
                    selection["item_code"]
                )
            )
        values = _copy_child_values(template)
        conversion_factor = flt(values.get("conversion_factor")) or 1
        values.update(
            {
                "qty": flt(selection["qty"]) / conversion_factor,
                "transfer_qty": flt(selection["qty"]),
                "s_warehouse": TARGET_WAREHOUSE,
                "t_warehouse": "",
                "batch_no": selection["batch_no"],
                "serial_no": "",
                "serial_and_batch_bundle": "",
                "use_serial_batch_fields": 1 if selection["batch_no"] else 0,
                "is_finished_item": 0,
            }
        )
        stock_entry.append("items", values)
    stock_entry.from_warehouse = TARGET_WAREHOUSE
    stock_entry.to_warehouse = ""
    stock_entry.purpose = CONSUMPTION_PURPOSE
    stock_entry.stock_entry_type = CONSUMPTION_PURPOSE
    return stock_entry


def _document_consumption_map(doc):
    result = defaultdict(float)
    for row in doc.get("items") or []:
        if not row.get("s_warehouse"):
            continue
        quantity = flt(row.get("transfer_qty") or row.get("qty"))
        batches = _row_batch_quantities(row)
        if batches:
            for batch_no, batch_qty in batches.items():
                result[(row.item_code, batch_no)] += flt(batch_qty)
        else:
            result[(row.item_code, "")] += quantity
    return result


def _row_batch_quantities(row):
    batch_no = cstr(row.get("batch_no")).strip()
    if batch_no:
        return {batch_no: flt(row.get("transfer_qty") or row.get("qty"))}
    bundle = cstr(row.get("serial_and_batch_bundle")).strip()
    if not bundle:
        return {}
    rows = frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle},
        fields=["batch_no", "qty"],
        limit_page_length=0,
    )
    result = defaultdict(float)
    for entry in rows:
        if entry.batch_no:
            result[entry.batch_no] += abs(flt(entry.qty))
    return dict(result)


def _copy_child_values(row):
    as_dict = getattr(row, "as_dict", None)
    values = dict(as_dict() if callable(as_dict) else row)
    for fieldname in (
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "parent",
        "parentfield",
        "parenttype",
    ):
        values.pop(fieldname, None)
    return values


def _purpose(doc) -> str:
    return cstr(doc.get("purpose") or doc.get("stock_entry_type")).strip()
