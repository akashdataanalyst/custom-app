from __future__ import annotations

import frappe
from frappe import _


SOURCE_WAREHOUSE = "Stores - CPPL"
TARGET_WAREHOUSE = "Work In Progress - CPPL"
TRANSFER_PURPOSE = "Material Transfer for Manufacture"


def is_work_order_material_transfer(doc) -> bool:
    return bool(
        doc.get("work_order")
        and (doc.get("purpose") or "").strip() == TRANSFER_PURPOSE
    )


def apply_work_order_transfer_warehouses(doc, method=None) -> None:
    if not is_work_order_material_transfer(doc):
        return

    _validate_fixed_warehouses()
    is_return = bool(doc.get("is_return"))
    source = TARGET_WAREHOUSE if is_return else SOURCE_WAREHOUSE
    target = SOURCE_WAREHOUSE if is_return else TARGET_WAREHOUSE
    doc.from_warehouse = source
    doc.to_warehouse = target

    for row in doc.get("items") or []:
        row.s_warehouse = source
        row.t_warehouse = target


def _validate_fixed_warehouses() -> None:
    for warehouse in (SOURCE_WAREHOUSE, TARGET_WAREHOUSE):
        values = frappe.db.get_value(
            "Warehouse",
            warehouse,
            ["is_group", "disabled"],
            as_dict=True,
        )
        if not values:
            frappe.throw(
                _("Required production warehouse {0} does not exist.").format(warehouse)
            )
        if values.is_group or values.disabled:
            frappe.throw(
                _("Required production warehouse {0} must be an enabled leaf warehouse.").format(
                    warehouse
                )
            )
