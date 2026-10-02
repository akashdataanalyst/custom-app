from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from calco_erp.calco_production.material_reservation_transfer import has_any_calco_reservation
from calco_erp.calco_production.wip_return import _get_reconciliation_rows
from calco_erp.machine_setup import MACHINE_FIELD, OPERATOR_FIELD, SHIFT_FIELD


MANUFACTURE_PURPOSE = "Manufacture"
FG_QUARANTINE_WAREHOUSE = "FG Quarantine - CPPL"
FG_RELEASED_WAREHOUSE = "FG Released - CPPL"
NO_OPERATION_ROLES = {"Production Head"}
EPSILON = 1e-9


def ensure_fg_warehouses() -> dict[str, str]:
    company = _get_cppl_company()
    parent = _get_company_warehouse_root(company)
    return {
        "quarantine": _ensure_warehouse(company, parent, "FG Quarantine"),
        "released": _ensure_warehouse(company, parent, "FG Released"),
    }


@frappe.whitelist()
def get_controlled_manufacture_preview(work_order=None, *args, **kwargs) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    wo = _get_work_order(work_order)
    frappe.has_permission("Work Order", "read", doc=wo, throw=True)
    if not is_controlled_work_order(wo.name):
        return {
            "work_order": wo.name,
            "controlled": False,
            "can_create": False,
            "reason": _("This Work Order does not use the controlled Calco material flow."),
        }

    existing = _get_existing_manufacture_entries(wo.name)
    if existing["submitted"]:
        return {
            "work_order": wo.name,
            "controlled": True,
            "can_create": False,
            "reason": _("A final Manufacture Stock Entry is already submitted."),
            "existing": existing["submitted"][0],
        }
    if existing["draft"]:
        return {
            "work_order": wo.name,
            "controlled": True,
            "can_create": True,
            "existing_draft": existing["draft"][0],
            "reason": _("Open the existing Draft Manufacture Stock Entry."),
        }

    has_operations = _bom_has_operations(wo)
    authority = (
        _resolve_quantity_authority(wo)
        if has_operations
        else {
            "has_operations": False,
            "actual_fg_qty": 0,
            "process_loss_qty": 0,
            "evidence_source": _("Authorized no-operation execution evidence"),
            "job_cards": [],
        }
    )
    reconciliation = _get_reconciliation_result(wo.name)
    return {
        "work_order": wo.name,
        "controlled": True,
        "can_create": bool(reconciliation["reconciled"]),
        "has_operations": has_operations,
        "requires_manual_input": not has_operations,
        "actual_fg_qty": authority.get("actual_fg_qty", 0),
        "process_loss_qty": authority.get("process_loss_qty", 0),
        "quantity_authority": authority.get("evidence_source", ""),
        "job_cards": authority.get("job_cards", []),
        "remaining_work_order_qty": _remaining_work_order_qty(wo),
        "warehouse": get_fg_quarantine_warehouse(wo.company),
        "reconciliation": reconciliation,
        "reason": "" if reconciliation["reconciled"] else reconciliation["reason"],
    }


@frappe.whitelist()
def make_controlled_manufacture_entry(
    work_order=None,
    actual_fg_qty=None,
    process_loss_qty=None,
    execution_reason=None,
    *args,
    **kwargs,
) -> dict[str, Any]:
    work_order = cstr(work_order or kwargs.get("work_order")).strip()
    wo = _get_work_order(work_order)
    frappe.has_permission("Work Order", "read", doc=wo, throw=True)
    frappe.has_permission("Stock Entry", "create", throw=True)
    if not is_controlled_work_order(wo.name):
        frappe.throw(_("Work Order {0} does not use the controlled Calco material flow.").format(wo.name))

    existing = _get_existing_manufacture_entries(wo.name)
    if existing["submitted"]:
        frappe.throw(
            _("Manufacture Stock Entry {0} is already submitted for Work Order {1}.").format(
                existing["submitted"][0]["name"], wo.name
            )
        )
    if existing["draft"]:
        return frappe.get_doc("Stock Entry", existing["draft"][0]["name"]).as_dict()

    _validate_initial_qc(wo)
    _validate_prior_consumption(wo.name)
    _assert_zero_remaining_wip(wo.name)
    authority = _resolve_quantity_authority(
        wo,
        actual_fg_qty=actual_fg_qty,
        process_loss_qty=process_loss_qty,
        execution_reason=execution_reason,
        enforce_no_operation_role=True,
    )
    stock_entry = _build_standard_manufacture_draft(
        wo, authority, get_fg_quarantine_warehouse(wo.company)
    )
    return stock_entry.as_dict()


def validate_controlled_manufacture(doc, method=None):
    if _is_controlled_manufacture(doc):
        _validate_controlled_manufacture_document(doc)


def validate_controlled_manufacture_on_submit(doc, method=None):
    if not _is_controlled_manufacture(doc):
        return
    frappe.db.sql("SELECT name FROM `tabWork Order` WHERE name = %s FOR UPDATE", (doc.work_order,))
    _validate_controlled_manufacture_document(doc)


def is_controlled_work_order(work_order) -> bool:
    from calco_erp.calco_production.fg_planning_authority import is_dashboard_name
    return is_dashboard_name(cstr(work_order).strip()) or has_any_calco_reservation(cstr(work_order).strip())


def get_fg_quarantine_warehouse(company: str) -> str:
    warehouse = _get_existing_warehouse(company, "FG Quarantine")
    if not warehouse:
        frappe.throw(
            _("Warehouse {0} is not configured for company {1}.").format(
                FG_QUARANTINE_WAREHOUSE, company
            )
        )
    return warehouse


def _validate_controlled_manufacture_document(doc):
    if doc.get("custom_partial_fg_lot"):
        from calco_erp.calco_production.partial_fg_lots import validate_manufacture
        return validate_manufacture(doc)
    from calco_erp.calco_production.fg_planning_authority import assert_execution_allowed
    assert_execution_allowed(doc.work_order)
    wo = _get_work_order(doc.work_order)
    _validate_initial_qc(wo)
    _validate_prior_consumption(wo.name)
    _assert_zero_remaining_wip(wo.name)
    _validate_one_final_manufacture(doc)

    quarantine = get_fg_quarantine_warehouse(wo.company)
    if cstr(doc.get("to_warehouse")).strip() != quarantine:
        frappe.throw(_("Controlled Manufacture must target {0}.").format(quarantine))

    finished = _get_primary_finished_row(doc, wo.production_item)
    if cstr(finished.get("t_warehouse")).strip() != quarantine:
        frappe.throw(_("Finished item {0} must target {1}.").format(wo.production_item, quarantine))
    if any(row.get("s_warehouse") and not row.get("is_finished_item") for row in doc.get("items") or []):
        frappe.throw(
            _("Controlled Manufacture must not consume RM again; prior Material Consumption is authoritative.")
        )
    if any(
        cstr(row.get("batch_no")).strip() or cstr(row.get("serial_and_batch_bundle")).strip()
        for row in doc.get("items") or []
        if row.get("is_finished_item") and row.get("item_code") == wo.production_item
    ):
        frappe.throw(_("FG batch is assigned from the production-start identity during controlled Manufacture submission."))

    if _bom_has_operations(wo):
        authority = _resolve_quantity_authority(wo)
    else:
        authority = _resolve_quantity_authority(
            wo,
            actual_fg_qty=flt(finished.get("transfer_qty") or finished.get("qty")),
            process_loss_qty=flt(doc.get("process_loss_qty")),
            execution_reason=cstr(doc.get("remarks")).strip(),
            enforce_no_operation_role=True,
        )

    actual_fg_qty = flt(finished.get("transfer_qty") or finished.get("qty"))
    if abs(actual_fg_qty - flt(authority["actual_fg_qty"])) > EPSILON:
        frappe.throw(
            _("Actual FG quantity must equal the authorized quantity {0}.").format(
                round(flt(authority["actual_fg_qty"]), 6)
            )
        )
    if abs(flt(doc.get("process_loss_qty")) - flt(authority["process_loss_qty"])) > EPSILON:
        frappe.throw(
            _("Process loss must equal the execution evidence quantity {0}.").format(
                round(flt(authority["process_loss_qty"]), 6)
            )
        )
    expected_basis = flt(authority["actual_fg_qty"]) + flt(authority["process_loss_qty"])
    if abs(flt(doc.get("fg_completed_qty")) - expected_basis) > EPSILON:
        frappe.throw(
            _("Manufacture quantity basis must equal FG quantity plus process loss ({0}).").format(
                round(expected_basis, 6)
            )
        )


def _resolve_quantity_authority(
    wo,
    actual_fg_qty=None,
    process_loss_qty=None,
    execution_reason=None,
    enforce_no_operation_role=False,
) -> dict[str, Any]:
    if _bom_has_operations(wo):
        return _resolve_job_card_quantity(wo)
    return _resolve_no_operation_quantity(
        wo,
        actual_fg_qty,
        process_loss_qty,
        execution_reason,
        enforce_no_operation_role,
    )


def _resolve_job_card_quantity(wo) -> dict[str, Any]:
    operations = list(wo.get("operations") or [])
    if not operations:
        frappe.throw(_("BOM has operations but Work Order has no operation rows."))
    final_operation = max(
        operations,
        key=lambda row: (
            cint(row.get("sequence_id")) or cint(row.get("idx")),
            cint(row.get("idx")),
        ),
    )
    job_cards = frappe.get_all(
        "Job Card",
        filters={
            "work_order": wo.name,
            "operation_id": final_operation.name,
            "docstatus": 1,
        },
        fields=[
            "name",
            "status",
            "total_completed_qty",
            "process_loss_qty",
            "workstation",
            MACHINE_FIELD,
            OPERATOR_FIELD,
            SHIFT_FIELD,
        ],
        order_by="creation, name",
        limit_page_length=0,
    )
    if not job_cards:
        frappe.throw(_("A submitted final-operation Job Card is required before Manufacture."))
    incomplete = [row.name for row in job_cards if cstr(row.status).strip() != "Completed"]
    if incomplete:
        frappe.throw(_("Final-operation Job Cards are not completed: {0}.").format(", ".join(incomplete)))

    actual_fg_qty = sum(flt(row.total_completed_qty) for row in job_cards)
    process_loss_qty = sum(flt(row.process_loss_qty) for row in job_cards)
    if actual_fg_qty <= EPSILON:
        frappe.throw(_("Final-operation Job Card completed quantity must be positive."))
    _validate_quantity_ceiling(wo, actual_fg_qty, process_loss_qty)
    return {
        "has_operations": True,
        "actual_fg_qty": actual_fg_qty,
        "process_loss_qty": process_loss_qty,
        "quantity_basis": actual_fg_qty + process_loss_qty,
        "job_cards": [row.name for row in job_cards],
        "final_operation": final_operation.operation,
        "final_operation_id": final_operation.name,
        "evidence_source": _("Submitted completed final-operation Job Card"),
        **_resolve_job_card_tracking(wo, job_cards),
    }


def _resolve_no_operation_quantity(
    wo,
    actual_fg_qty,
    process_loss_qty,
    execution_reason,
    enforce_role,
) -> dict[str, Any]:
    if _bom_has_operations(wo):
        frappe.throw(_("No-operation fallback is not permitted for a BOM with operations."))
    if enforce_role:
        _require_no_operation_role()
    actual_fg_qty = flt(actual_fg_qty)
    process_loss_qty = flt(process_loss_qty)
    reason = cstr(execution_reason).strip()
    if actual_fg_qty <= EPSILON:
        frappe.throw(_("Actual FG Quantity must be positive for a no-operation BOM."))
    if process_loss_qty < 0:
        frappe.throw(_("Process Loss Quantity cannot be negative."))
    if not reason:
        frappe.throw(_("Execution reason/evidence is mandatory for a no-operation BOM."))
    _validate_quantity_ceiling(wo, actual_fg_qty, process_loss_qty)
    tracking = {
        "machine": cstr(wo.get(MACHINE_FIELD)).strip(),
        "operator": cstr(wo.get(OPERATOR_FIELD)).strip(),
        "shift_type": cstr(wo.get(SHIFT_FIELD)).strip(),
    }
    missing = [label for label, value in tracking.items() if not value]
    if missing:
        frappe.throw(
            _("{0} evidence is mandatory for no-operation Manufacture.").format(
                ", ".join(value.replace("_", " ").title() for value in missing)
            )
        )
    return {
        "has_operations": False,
        "actual_fg_qty": actual_fg_qty,
        "process_loss_qty": process_loss_qty,
        "quantity_basis": actual_fg_qty + process_loss_qty,
        "job_cards": [],
        "evidence_source": _("Authorized no-operation execution evidence"),
        "execution_reason": reason,
        **tracking,
    }


def _resolve_job_card_tracking(wo, job_cards) -> dict[str, str]:
    mappings = {
        "machine": [MACHINE_FIELD, "workstation"],
        "operator": [OPERATOR_FIELD],
        "shift_type": [SHIFT_FIELD],
    }
    fallback_fields = {
        "machine": MACHINE_FIELD,
        "operator": OPERATOR_FIELD,
        "shift_type": SHIFT_FIELD,
    }
    result = {}
    for target, sources in mappings.items():
        values = set()
        for row in job_cards:
            value = next(
                (
                    cstr(row.get(source)).strip()
                    for source in sources
                    if cstr(row.get(source)).strip()
                ),
                "",
            )
            if value:
                values.add(value)
        if len(values) > 1:
            frappe.throw(_("Final Job Cards contain conflicting {0} values.").format(target))
        result[target] = next(iter(values), cstr(wo.get(fallback_fields[target])).strip())
        if not result[target]:
            frappe.throw(_("{0} evidence is mandatory for controlled Manufacture.").format(target.title()))
    return result


def _build_standard_manufacture_draft(wo, authority, quarantine):
    from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

    stock_entry = frappe.get_doc(
        make_stock_entry(
            wo.name,
            MANUFACTURE_PURPOSE,
            flt(authority["quantity_basis"]),
            target_warehouse=quarantine,
        )
    )
    stock_entry.to_warehouse = quarantine
    stock_entry.fg_completed_qty = flt(authority["quantity_basis"])
    stock_entry.process_loss_qty = flt(authority["process_loss_qty"])
    finished = _get_primary_finished_row(stock_entry, wo.production_item)
    conversion_factor = flt(finished.get("conversion_factor")) or 1
    finished.qty = flt(authority["actual_fg_qty"]) / conversion_factor
    finished.transfer_qty = flt(authority["actual_fg_qty"])
    finished.t_warehouse = quarantine
    finished.batch_no = ""
    finished.serial_no = ""
    finished.serial_and_batch_bundle = ""
    finished.use_serial_batch_fields = 1

    if any(row.get("s_warehouse") and not row.get("is_finished_item") for row in stock_entry.get("items") or []):
        frappe.throw(
            _("ERPNext generated RM rows despite prior Material Consumption; Manufacture draft was not created.")
        )

    stock_entry.set(MACHINE_FIELD, authority["machine"])
    stock_entry.set(OPERATOR_FIELD, authority["operator"])
    stock_entry.set(SHIFT_FIELD, authority["shift_type"])
    if (
        len(authority.get("job_cards") or []) == 1
        and stock_entry.meta.has_field("job_card")
        and _job_card_finished_good(authority["job_cards"][0]) == wo.production_item
    ):
        stock_entry.job_card = authority["job_cards"][0]
    stock_entry.remarks = (
        _("Controlled Manufacture from final Job Card evidence: {0}.").format(
            ", ".join(authority["job_cards"])
        )
        if authority["has_operations"]
        else authority["execution_reason"]
    )
    return stock_entry


def _job_card_finished_good(job_card):
    return cstr(frappe.db.get_value("Job Card", job_card, "finished_good")).strip()


def _get_reconciliation_result(work_order: str) -> dict[str, Any]:
    rows = _get_reconciliation_rows(work_order)
    if not rows:
        return {
            "reconciled": False,
            "rows": [],
            "reason": _("No submitted transfer/consumption/return evidence exists."),
        }
    blockers = []
    for row in rows:
        balance = (
            flt(row["transferred_qty"])
            - flt(row["actual_consumed_qty"])
            - flt(row["returned_qty"])
        )
        if (
            abs(balance) > EPSILON
            or abs(flt(row["remaining_wip_qty"])) > EPSILON
            or abs(flt(row["physical_wip_qty"])) > EPSILON
            or abs(flt(row["discrepancy_qty"])) > EPSILON
        ):
            blockers.append(
                _(
                    "{0}/{1}: transferred {2}, consumed {3}, returned {4}, physical WIP {5}."
                ).format(
                    row["item_code"],
                    row["batch_no"] or _("no batch"),
                    round(flt(row["transferred_qty"]), 6),
                    round(flt(row["actual_consumed_qty"]), 6),
                    round(flt(row["returned_qty"]), 6),
                    round(flt(row["physical_wip_qty"]), 6),
                )
            )
    return {
        "reconciled": not blockers,
        "rows": rows,
        "reason": " ".join(blockers),
    }


def _assert_zero_remaining_wip(work_order: str):
    result = _get_reconciliation_result(work_order)
    if not result["reconciled"]:
        frappe.throw(_("RM reconciliation is incomplete. {0}").format(result["reason"]))


def _validate_prior_consumption(work_order: str):
    if not frappe.db.exists(
        "Stock Entry",
        {
            "work_order": work_order,
            "docstatus": 1,
            "purpose": "Material Consumption for Manufacture",
        },
    ):
        frappe.throw(_("Submitted Material Consumption for Manufacture is required."))


def _validate_initial_qc(wo):
    from calco_erp.calco_production.in_process_quality import assert_manufacture_allowed, is_parallel
    if is_parallel(wo):
        assert_manufacture_allowed(wo.name)
        return
    from calco_erp.calco_production import work_order_lifecycle as lifecycle

    if cstr(wo.get(lifecycle.WORK_ORDER_INITIAL_QC_STATUS_FIELD)).strip() != lifecycle.INITIAL_QC_PASSED:
        frappe.throw(_("Initial QC must be Passed before controlled Manufacture."))


def _validate_one_final_manufacture(doc):
    existing = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": doc.work_order,
            "purpose": MANUFACTURE_PURPOSE,
            "docstatus": 1,
            "name": ("!=", doc.name or ""),
        },
        pluck="name",
        limit_page_length=1,
    )
    if existing:
        frappe.throw(
            _("Only one submitted final Manufacture is allowed; {0} already exists.").format(
                existing[0]
            )
        )


def _validate_quantity_ceiling(wo, actual_fg_qty: float, process_loss_qty: float):
    remaining = _remaining_work_order_qty(wo)
    basis = flt(actual_fg_qty) + flt(process_loss_qty)
    if basis > remaining + EPSILON:
        frappe.throw(
            _("FG quantity plus process loss {0} exceeds remaining Work Order quantity {1}.").format(
                round(basis, 6), round(remaining, 6)
            )
        )


def _remaining_work_order_qty(wo) -> float:
    return max(flt(wo.qty) - flt(wo.produced_qty) - flt(wo.process_loss_qty), 0)


def _bom_has_operations(wo) -> bool:
    if wo.get("operations"):
        return True
    return bool(
        wo.bom_no
        and frappe.db.exists("BOM Operation", {"parent": wo.bom_no, "parenttype": "BOM"})
    )


def _get_primary_finished_row(doc, production_item: str):
    row = next(
        (
            item
            for item in doc.get("items") or []
            if item.get("is_finished_item") and item.get("item_code") == production_item
        ),
        None,
    )
    if not row:
        frappe.throw(
            _("Manufacture Stock Entry must contain finished item {0}.").format(production_item)
        )
    return row


def _get_existing_manufacture_entries(work_order: str) -> dict[str, list[dict[str, Any]]]:
    rows = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": work_order,
            "purpose": MANUFACTURE_PURPOSE,
            "docstatus": ("<", 2),
        },
        fields=["name", "docstatus", "posting_date", "creation"],
        order_by="creation desc",
        limit_page_length=0,
    )
    return {
        "draft": [dict(row) for row in rows if cint(row.docstatus) == 0],
        "submitted": [dict(row) for row in rows if cint(row.docstatus) == 1],
    }


def _is_controlled_manufacture(doc) -> bool:
    return bool(
        cstr(doc.get("work_order")).strip()
        and cstr(doc.get("purpose") or doc.get("stock_entry_type")).strip()
        == MANUFACTURE_PURPOSE
        and is_controlled_work_order(doc.work_order)
    )


def _get_work_order(work_order: str):
    if not work_order or not frappe.db.exists("Work Order", work_order):
        frappe.throw(_("Valid Work Order is required."))
    wo = frappe.get_doc("Work Order", work_order)
    if cint(wo.docstatus) != 1:
        frappe.throw(_("Work Order {0} must be submitted.").format(work_order))
    if cstr(wo.status).strip() in {"Stopped", "Cancelled", "Completed", "Closed"}:
        frappe.throw(
            _("Work Order {0} status {1} does not allow Manufacture.").format(
                work_order, wo.status
            )
        )
    return wo


def _require_no_operation_role():
    if frappe.session.user == "Administrator":
        return
    if not NO_OPERATION_ROLES.intersection(set(frappe.get_roles())):
        frappe.throw(
            _("Only Production Head may authorize no-operation Manufacture."),
            frappe.PermissionError,
        )


def _get_cppl_company() -> str:
    company = frappe.db.get_value("Company", {"abbr": "CPPL"}, "name")
    if not company:
        frappe.throw(_("Company with abbreviation CPPL is required."))
    return company


def _get_company_warehouse_root(company: str) -> str:
    parent = frappe.db.get_value(
        "Warehouse",
        {
            "company": company,
            "is_group": 1,
            "parent_warehouse": ("is", "not set"),
        },
        "name",
    )
    if not parent:
        frappe.throw(_("Root warehouse is missing for company {0}.").format(company))
    return parent


def _get_existing_warehouse(company: str, warehouse_name: str) -> str:
    canonical = f"{warehouse_name} - CPPL"
    return cstr(
        frappe.db.get_value(
            "Warehouse",
            {"name": canonical, "company": company, "disabled": 0},
            "name",
        )
        or frappe.db.get_value(
            "Warehouse",
            {
                "warehouse_name": warehouse_name,
                "company": company,
                "is_group": 0,
                "disabled": 0,
            },
            "name",
        )
        or ""
    ).strip()


def _ensure_warehouse(company: str, parent: str, warehouse_name: str) -> str:
    existing = _get_existing_warehouse(company, warehouse_name)
    if existing:
        return existing
    doc = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": warehouse_name,
            "company": company,
            "parent_warehouse": parent,
            "is_group": 0,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
