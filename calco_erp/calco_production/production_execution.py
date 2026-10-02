from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime, today

from calco_erp.machine_setup import MACHINE_FIELD


PRODUCTION_REQUIREMENT_DOCTYPE = "Production Requirement"
PRODUCTION_JOB_CARD_DOCTYPE = "Production Job Card"
WORK_ORDER_DOCTYPE = "Work Order"
PRODUCTION_RM_REQUISITION_DOCTYPE = "Production RM Requisition"
GRADE_CHANGE_CLEARANCE_DOCTYPE = "Grade Change Clearance"
PREMIX_PREPARATION_DOCTYPE = "Premix Preparation"
FG_DELIVERY_NOTE_DOCTYPE = "FG Delivery Note"
WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD = "custom_production_requirement"
WORK_ORDER_PRODUCTION_JOB_CARD_FIELD = "custom_production_job_card"
WORK_ORDER_FG_BATCH_FIELD = "custom_fg_batch_no"
WORK_ORDER_SECTION_FIELD = "custom_production_journey_section"
WORK_ORDER_TRACKER_FIELD = "custom_production_journey_html"
TRACKER_HTML_FIELD = "journey_tracker_html"
TRACKER_SECTION_FIELD = "journey_section"

PRODUCTION_EXECUTION_ROLES = (
    "Production Engineer",
    "Production Manager",
    "Production Head",
    "Stores User",
    "Commercial User",
)

PRODUCTION_STAGE_ORDER = [
    "Production Requirement",
    "Work Order",
    "Job Card",
    "RM Requisition",
    "Grade Change Clearance",
    "Premix Preparation",
    "FG Delivery Note",
    "FG Quarantine",
]

DOWNTIME_CATEGORIES = (
    "Process",
    "Mechanical/Electrical",
    "QC/R&D",
    "Grade Change",
    "External",
    "Commercial",
)


def ensure_production_execution_setup():
    ensure_roles(PRODUCTION_EXECUTION_ROLES)
    ensure_production_execution_custom_fields()
    frappe.clear_cache()


def ensure_production_execution_custom_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    if not frappe.db.exists("DocType", WORK_ORDER_DOCTYPE):
        return

    create_custom_fields(
        {
            WORK_ORDER_DOCTYPE: [
                {
                    "fieldname": WORK_ORDER_SECTION_FIELD,
                    "label": "Production Execution",
                    "fieldtype": "Section Break",
                    "insert_after": MACHINE_FIELD,
                },
                {
                    "fieldname": WORK_ORDER_TRACKER_FIELD,
                    "label": "Production Execution Tracker",
                    "fieldtype": "HTML",
                    "insert_after": WORK_ORDER_SECTION_FIELD,
                },
                {
                    "fieldname": WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD,
                    "label": "Production Requirement",
                    "fieldtype": "Link",
                    "options": PRODUCTION_REQUIREMENT_DOCTYPE,
                    "insert_after": WORK_ORDER_TRACKER_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": WORK_ORDER_PRODUCTION_JOB_CARD_FIELD,
                    "label": "Production Job Card",
                    "fieldtype": "Link",
                    "options": PRODUCTION_JOB_CARD_DOCTYPE,
                    "insert_after": WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": WORK_ORDER_FG_BATCH_FIELD,
                    "label": "FG Batch No",
                    "fieldtype": "Data",
                    "insert_after": WORK_ORDER_PRODUCTION_JOB_CARD_FIELD,
                    "read_only": 1,
                },
            ]
        },
        update=True,
    )


def ensure_roles(role_names: tuple[str, ...] | list[str]):
    if not frappe.db.exists("DocType", "Role"):
        return
    for role_name in role_names:
        if frappe.db.exists("Role", role_name):
            continue
        frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


class ProductionRequirementMixin(Document):
    def validate(self):
        self.status = self.status or "Draft"
        self.week_start_date = getdate(self.week_start_date) if self.week_start_date else getdate(today())
        self.week_end_date = getdate(self.week_end_date) if self.week_end_date else self.week_start_date
        if self.week_end_date < self.week_start_date:
            frappe.throw(_("Week End Date cannot be before Week Start Date."))

        if cint(self.get("pull_sales_orders")):
            sync_sales_order_lines(self)
        compute_requirement_lines(self)
        self.total_demand_qty = round(sum(flt(row.requested_qty) for row in self.get("items") or []), 3)
        self.total_net_required_qty = round(sum(flt(row.net_required_qty) for row in self.get("items") or []), 3)

        if not self.get("items"):
            self.status = "Draft"
        elif any(flt(row.net_required_qty) > 0 for row in self.get("items") or []):
            self.status = "Open"
        else:
            self.status = "Planned"


class ProductionJobCardMixin(Document):
    def validate(self):
        self.grade_code = self.grade_code or self.item_code
        self.grade_name = self.grade_name or (frappe.db.get_value("Item", self.grade_code, "item_name") if self.grade_code else "")
        self.target_date = getdate(self.target_date) if self.target_date else getdate(today())
        self.bom_no = self.bom_no or get_default_bom(self.grade_code)
        self.status = self.status or "Draft"
        self.current_stage = self.current_stage or "Material Availability"
        self.fg_batch_no = self.fg_batch_no or generate_fg_batch_for_job_card(self)
        if not self.grade_code:
            frappe.throw(_("Grade Code is mandatory."))
        if flt(self.planned_qty) <= 0:
            frappe.throw(_("Planned Qty must be greater than zero."))

        refresh_job_card_materials(self)
        self.executable_qty = round(sum(flt(row.allocated_qty) for row in self.get("materials") or []), 3)
        self.material_status = derive_material_status(self)
        self._validate_stage_gates()
        self._validate_quantity_logic()
        self._sync_stage_from_status()

    def _validate_stage_gates(self):
        if self.status in {"In Progress", "Closed", "FG Quarantine"}:
            if not self.grade_change_clearance:
                frappe.throw(_("Grade Change Clearance is mandatory before production can start."))
            if frappe.db.get_value(GRADE_CHANGE_CLEARANCE_DOCTYPE, self.grade_change_clearance, "status") != "Approved":
                frappe.throw(_("Grade Change Clearance must be Approved before production can start."))

        if self.current_stage in {"Premix Preparation", "Production Run", "FG Packing & Labeling", "FG Delivery Note", "FG Quarantine"}:
            if not self.premix_preparation:
                frappe.throw(_("Premix Preparation must be linked before moving to %(stage)s.") % {"stage": self.current_stage})
            if frappe.db.get_value(PREMIX_PREPARATION_DOCTYPE, self.premix_preparation, "status") not in {"Verified", "Completed"}:
                frappe.throw(_("Premix Preparation must be Verified before moving to %(stage)s.") % {"stage": self.current_stage})

        if self.current_stage in {"Production Run", "FG Packing & Labeling", "FG Delivery Note", "FG Quarantine"} and not self.rm_requisition:
            frappe.throw(_("RM Requisition is mandatory before production run can begin."))

    def _validate_quantity_logic(self):
        actual_qty = flt(self.actual_qty)
        planned_qty = flt(self.planned_qty)
        if self.execution_decision == "Wait For Material" and self.status in {"Closed", "FG Quarantine"} and actual_qty > 0:
            frappe.throw(_("Job Card cannot be closed with output when execution decision is Wait For Material."))

        if self.status in {"Closed", "FG Quarantine"}:
            if actual_qty > planned_qty and not self.over_under_reason:
                frappe.throw(_("Production Head reason is mandatory for overproduction."))
            if actual_qty < planned_qty and not self.over_under_reason:
                frappe.throw(_("Production Head reason is mandatory for underproduction."))

    def _sync_stage_from_status(self):
        if self.status == "Draft":
            self.current_stage = "Material Availability"
        elif self.status in {"Ready", "Partially Available", "Blocked"}:
            self.current_stage = "RM Requisition"
        elif self.status == "In Progress":
            self.current_stage = "Production Run"
        elif self.status == "Closed":
            self.current_stage = "FG Delivery Note"
        elif self.status == "FG Quarantine":
            self.current_stage = "FG Quarantine"

    def on_update(self):
        if self.status in {"Closed", "FG Quarantine"}:
            handle_partial_balance(self)


class ProductionRMRequisitionMixin(Document):
    def validate(self):
        self.status = self.status or "Draft"
        if self.job_card and not self.get("items"):
            sync_requisition_items_from_job_card(self)
        self.required_qty = round(sum(flt(row.required_qty) for row in self.get("items") or []), 3)
        self.issued_qty = round(sum(flt(row.issued_qty) for row in self.get("items") or []), 3)
        if self.issued_qty and self.issued_qty > self.required_qty + 1e-9:
            frappe.throw(_("Issued Qty cannot exceed Required Qty on RM Requisition."))


class GradeChangeClearanceMixin(Document):
    def validate(self):
        from calco_erp.calco_production.grade_change_control import validate_clearance

        if self.get("work_order") or self.get("erpnext_job_card"):
            validate_clearance(self)
            return

        # Preserve the historical Production Job Card path without making it
        # authoritative for new ERPNext Work Orders.
        if self.is_new() and self.status == "Pending Evaluation":
            self.status = "Draft"
        self.status = self.status or "Draft"
        if self.critical_grade_change and not self.purging_confirmation_required:
            self.purging_confirmation_required = 1
        if self.status == "Approved":
            if not self.prepared_by:
                frappe.throw(_("Prepared By is mandatory for legacy Grade Change Clearance."))
            if not self.approved_by:
                frappe.throw(_("Approved By is mandatory for legacy Grade Change Clearance."))
            if self.purging_confirmation_required and not self.purging_confirmation_done:
                frappe.throw(_("Purging Confirmation is mandatory for critical grade changes."))


class PremixPreparationMixin(Document):
    def validate(self):
        self.status = self.status or "Draft"
        for row in self.get("items") or []:
            if flt(row.actual_qty) <= 0:
                continue
            if flt(row.allocated_qty) <= 0:
                frappe.throw(_("Allocated Qty must be greater than zero before entering Actual Qty for premix rows."))
        if self.status in {"Verified", "Completed"} and not self.verified_by:
            frappe.throw(_("Verified By is mandatory once Premix Preparation is verified."))
        if self.status in {"Verified", "Completed"} and not self.verified_on:
            self.verified_on = now_datetime()


class FGDeliveryNoteMixin(Document):
    def validate(self):
        self.status = self.status or "Draft"
        self.total_output_qty = round(
            flt(self.prime_fg_qty)
            + flt(self.spy_qty)
            + flt(self.tpy_qty)
            + flt(self.metal_separator_qty)
            + flt(self.pmx_qty)
            + flt(self.samples_qty),
            3,
        )
        self.settlement_summary = "\n".join(
            [
                "SPY -> L/OLD",
                "TPY -> WX",
                "Metal Separator -> WX",
                "PMX -> PMX",
            ]
        )


def sync_sales_order_lines(doc):
    existing_forecast_rows = [row.as_dict() for row in (doc.get("items") or []) if row.get("source_type") == "Forecast"]
    doc.set("items", [])

    for row in get_open_sales_order_demands(doc.week_start_date, doc.week_end_date):
        doc.append(
            "items",
            {
                "source_type": "Sales Order",
                "source_reference": row["sales_order"],
                "sales_order_reference": row["sales_order"],
                "item_code": row["item_code"],
                "item_name": row["item_name"],
                "requested_qty": row["qty"],
                "target_date": row["delivery_date"],
                "priority_rank": 1,
                "line_status": "Open",
            },
        )

    for row in existing_forecast_rows:
        row["priority_rank"] = 2
        row["line_status"] = row.get("line_status") or "Open"
        doc.append("items", row)


def compute_requirement_lines(doc):
    if doc.get("custom_rm_planning_snapshot"):
        # PPC already deducted eligible FG and prior production coverage. The
        # immutable RM snapshot authorizes this net release, not gross demand.
        # Snapshot creation/protection is enforced by the validate document hook.
        for row in doc.get("items") or []:
            row.fg_inventory_qty = 0
            row.net_required_qty = row.requested_qty
            row.line_status = row.line_status or "Open"
        return
    inventory_map = defaultdict(float)
    for row in sorted(
        (doc.get("items") or []),
        key=lambda item: (cint(item.priority_rank) or 99, getdate(item.target_date) if item.target_date else getdate(today()), item.idx),
    ):
        if not row.item_code:
            continue
        if inventory_map[row.item_code] == 0:
            inventory_map[row.item_code] = flt(get_fg_inventory_qty(row.item_code))
        available_fg = inventory_map[row.item_code]
        requested_qty = flt(row.requested_qty)
        consumed_fg = min(available_fg, requested_qty)
        net_required_qty = max(requested_qty - consumed_fg, 0)
        inventory_map[row.item_code] = max(available_fg - consumed_fg, 0)
        row.fg_inventory_qty = round(available_fg, 3)
        row.net_required_qty = round(net_required_qty, 3)
        row.line_status = "Planned" if net_required_qty <= 0 else (row.line_status or "Open")


def get_open_sales_order_demands(week_start, week_end) -> list[dict[str, object]]:
    if not frappe.db.exists("DocType", "Sales Order Item"):
        return []
    return frappe.db.sql(
        """
        select
            soi.parent as sales_order,
            soi.item_code,
            soi.item_name,
            round(coalesce(soi.qty, 0) - coalesce(soi.delivered_qty, 0), 3) as qty,
            date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date)) as delivery_date
        from `tabSales Order Item` soi
        inner join `tabSales Order` so on so.name = soi.parent
        where so.docstatus = 1
          and coalesce(so.status, '') not in ('Closed', 'Completed', 'Cancelled')
          and coalesce(soi.qty, 0) > coalesce(soi.delivered_qty, 0)
          and date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date)) between %(week_start)s and %(week_end)s
        order by date(coalesce(soi.delivery_date, so.delivery_date, so.transaction_date)) asc, soi.parent asc, soi.idx asc
        """,
        {"week_start": getdate(week_start), "week_end": getdate(week_end)},
        as_dict=True,
    )


def get_fg_inventory_qty(item_code: str) -> float:
    if not item_code or not frappe.db.exists("DocType", "Bin"):
        return 0
    warehouses = get_fg_warehouses()
    if not warehouses:
        return 0
    return flt(
        frappe.db.sql(
            """
            select coalesce(sum(actual_qty), 0)
            from `tabBin`
            where item_code = %s and warehouse in %s
            """,
            (item_code, tuple(warehouses)),
        )[0][0]
        or 0
    )


def get_fg_warehouses() -> list[str]:
    return frappe.get_all("Warehouse", filters={"name": ("like", "%Finished Goods%")}, pluck="name")


def get_default_bom(item_code: str | None) -> str:
    if not item_code or not frappe.db.exists("DocType", "BOM"):
        return ""
    return (
        frappe.db.get_value("BOM", {"item": item_code, "is_default": 1, "is_active": 1, "docstatus": 1}, "name")
        or frappe.db.get_value("BOM", {"item": item_code, "is_active": 1, "docstatus": 1}, "name")
        or ""
    )


def generate_fg_batch_for_job_card(doc) -> str:
    line_number = (doc.get("line_number") or "0").strip() or "0"
    return next_fg_batch_number(line_number=line_number, posting_date=getdate(doc.target_date or today()))


def refresh_job_card_materials(doc):
    if not doc.bom_no or flt(doc.planned_qty) <= 0:
        return
    existing = {(row.item_code, row.batch_no, row.source_warehouse): row for row in doc.get("materials") or []}
    doc.set("materials", [])
    for allocation in get_bom_allocations(doc.bom_no, doc.planned_qty):
        key = (allocation["item_code"], allocation["batch_no"], allocation["source_warehouse"])
        row = existing.get(key)
        payload = {
            "item_code": allocation["item_code"],
            "item_name": allocation["item_name"],
            "batch_no": allocation["batch_no"],
            "source_warehouse": allocation["source_warehouse"],
            "required_qty": allocation["required_qty"],
            "available_qty": allocation["available_qty"],
            "allocated_qty": allocation["allocated_qty"],
            "fifo_sequence": allocation["fifo_sequence"],
            "availability_status": allocation["availability_status"],
        }
        if row:
            payload["issued_qty"] = row.issued_qty
        doc.append("materials", payload)


def derive_material_status(doc) -> str:
    rows = doc.get("materials") or []
    if not rows:
        return "Blocked"
    statuses = {row.availability_status for row in rows if row.availability_status}
    if statuses == {"Available"}:
        return "Available"
    if "Partially Available" in statuses or ("Available" in statuses and "Blocked" in statuses):
        return "Partially Available"
    return "Blocked"


def get_bom_allocations(bom_no: str, planned_qty: float) -> list[dict[str, object]]:
    bom_qty = flt(frappe.db.get_value("BOM", bom_no, "quantity") or 1)
    factor = flt(planned_qty) / flt(bom_qty or 1)
    allocations: list[dict[str, object]] = []
    fifo_counter = 1

    for bom_item in frappe.get_all(
        "BOM Item",
        filters={"parent": bom_no},
        fields=["item_code", "item_name", "qty", "source_warehouse"],
        order_by="idx asc",
    ):
        required_qty = round(flt(bom_item.qty) * factor, 3)
        remaining = required_qty
        batch_rows = get_fifo_batch_rows(bom_item.item_code, bom_item.source_warehouse)
        if not batch_rows:
            allocations.append(
                {
                    "item_code": bom_item.item_code,
                    "item_name": bom_item.item_name,
                    "batch_no": "",
                    "source_warehouse": bom_item.source_warehouse or get_default_rm_warehouse(),
                    "required_qty": required_qty,
                    "available_qty": 0,
                    "allocated_qty": 0,
                    "fifo_sequence": fifo_counter,
                    "availability_status": "Blocked",
                }
            )
            fifo_counter += 1
            continue

        for batch_row in batch_rows:
            if remaining <= 0:
                break
            available_qty = flt(batch_row.qty)
            allocated_qty = min(remaining, available_qty)
            status = "Available" if allocated_qty + 1e-9 >= remaining else "Partially Available"
            allocations.append(
                {
                    "item_code": bom_item.item_code,
                    "item_name": bom_item.item_name,
                    "batch_no": batch_row.batch_no,
                    "source_warehouse": batch_row.warehouse,
                    "required_qty": required_qty,
                    "available_qty": available_qty,
                    "allocated_qty": allocated_qty,
                    "fifo_sequence": fifo_counter,
                    "availability_status": status,
                }
            )
            fifo_counter += 1
            remaining = round(remaining - allocated_qty, 3)

        if remaining > 0:
            allocations.append(
                {
                    "item_code": bom_item.item_code,
                    "item_name": bom_item.item_name,
                    "batch_no": "",
                    "source_warehouse": bom_item.source_warehouse or get_default_rm_warehouse(),
                    "required_qty": required_qty,
                    "available_qty": max(required_qty - remaining, 0),
                    "allocated_qty": 0,
                    "fifo_sequence": fifo_counter,
                    "availability_status": "Blocked",
                }
            )
            fifo_counter += 1

    return allocations


def get_default_rm_warehouse() -> str:
    return (
        frappe.db.get_value("Warehouse", {"name": ("like", "%Stores%")}, "name")
        or frappe.db.get_value("Warehouse", {}, "name")
        or ""
    )


def get_default_wip_warehouse() -> str:
    return (
        frappe.db.get_value("Warehouse", {"name": ("like", "%WIP%")}, "name")
        or frappe.db.get_value("Warehouse", {"name": ("like", "%Work In Progress%")}, "name")
        or get_default_rm_warehouse()
    )


def get_fifo_batch_rows(item_code: str, warehouse: str | None = None) -> list[frappe._dict]:
    if not frappe.db.exists("DocType", "Stock Ledger Entry"):
        return []
    filters = "and sle.warehouse = %(warehouse)s" if warehouse else ""
    return frappe.db.sql(
        f"""
        select
            sle.batch_no,
            sle.warehouse,
            round(sum(sle.actual_qty), 3) as qty,
            min(sle.posting_date) as first_posting_date,
            min(sle.creation) as first_creation
        from `tabStock Ledger Entry` sle
        where sle.item_code = %(item_code)s
          and coalesce(sle.batch_no, '') != ''
          {filters}
        group by sle.batch_no, sle.warehouse
        having sum(sle.actual_qty) > 0
        order by min(sle.posting_date) asc, min(sle.creation) asc, sle.batch_no asc
        """,
        {"item_code": item_code, "warehouse": warehouse},
        as_dict=True,
    )


def sync_requisition_items_from_job_card(doc):
    if not doc.job_card:
        return
    job_card = frappe.get_doc(PRODUCTION_JOB_CARD_DOCTYPE, doc.job_card)
    doc.production_requirement = job_card.production_requirement
    doc.target_warehouse = doc.target_warehouse or get_default_wip_warehouse()
    doc.set("items", [])
    for row in job_card.get("materials") or []:
        if not row.batch_no or flt(row.allocated_qty) <= 0:
            continue
        doc.append(
            "items",
            {
                "rm_code": row.item_code,
                "batch_no": row.batch_no,
                "source_warehouse": row.source_warehouse,
                "required_qty": row.allocated_qty,
                "issued_qty": row.issued_qty or 0,
            },
        )


def handle_partial_balance(doc):
    if not doc.production_requirement or flt(doc.actual_qty) + 1e-9 >= flt(doc.planned_qty):
        return
    balance_qty = round(flt(doc.planned_qty) - flt(doc.actual_qty), 3)
    requirement = frappe.get_doc(PRODUCTION_REQUIREMENT_DOCTYPE, doc.production_requirement)
    exists = any(
        row.item_code == doc.grade_code
        and row.source_type == "Balance Return"
        and flt(row.requested_qty) == balance_qty
        and row.line_status == "Open"
        for row in requirement.get("items") or []
    )
    if exists:
        return
    requirement.append(
        "items",
        {
            "source_type": "Balance Return",
            "source_reference": doc.name,
            "item_code": doc.grade_code,
            "item_name": doc.grade_name,
            "requested_qty": balance_qty,
            "target_date": doc.target_date,
            "priority_rank": 1,
            "line_status": "Open",
        },
    )
    requirement.save(ignore_permissions=True)


@frappe.whitelist()
def create_job_card_from_requirement(requirement_name: str, requirement_item_name: str | None = None) -> str:
    requirement = frappe.get_doc(PRODUCTION_REQUIREMENT_DOCTYPE, requirement_name)
    row = None
    rows = requirement.get("items") or []
    if requirement_item_name:
        row = next((item for item in rows if item.name == requirement_item_name), None)
    else:
        row = next((item for item in rows if flt(item.net_required_qty) > 0 and item.line_status == "Open"), None)
    if not row:
        frappe.throw(_("No open Production Requirement line is available for Job Card creation."))

    job_card = frappe.get_doc(
        {
            "doctype": PRODUCTION_JOB_CARD_DOCTYPE,
            "production_requirement": requirement.name,
            "production_requirement_item": row.name,
            "grade_code": row.item_code,
            "grade_name": row.item_name,
            "planned_qty": row.net_required_qty or row.requested_qty,
            "target_date": row.target_date,
            "sales_order_reference": row.sales_order_reference,
            "remarks": requirement.remarks,
            "status": "Draft",
        }
    )
    job_card.insert(ignore_permissions=True)
    row.line_status = "Job Card Created"
    requirement.save(ignore_permissions=True)
    return job_card.name


@frappe.whitelist()
def create_rm_requisition_from_job_card(job_card_name: str) -> str:
    job_card = frappe.get_doc(PRODUCTION_JOB_CARD_DOCTYPE, job_card_name)
    if job_card.rm_requisition and frappe.db.exists(PRODUCTION_RM_REQUISITION_DOCTYPE, job_card.rm_requisition):
        return job_card.rm_requisition

    requisition = frappe.get_doc(
        {
            "doctype": PRODUCTION_RM_REQUISITION_DOCTYPE,
            "job_card": job_card.name,
            "production_requirement": job_card.production_requirement,
            "status": "Pending Issue",
            "target_warehouse": get_default_wip_warehouse(),
        }
    )
    requisition.insert(ignore_permissions=True)
    job_card.rm_requisition = requisition.name
    job_card.save(ignore_permissions=True)
    return requisition.name


@frappe.whitelist()
def confirm_rm_issue(requisition_name: str) -> dict[str, str]:
    doc = frappe.get_doc(PRODUCTION_RM_REQUISITION_DOCTYPE, requisition_name)
    if not doc.target_warehouse:
        doc.target_warehouse = get_default_wip_warehouse()
    stock_entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Transfer",
            "purpose": "Material Transfer",
            "to_warehouse": doc.target_warehouse,
            "remarks": f"Production RM Requisition {doc.name}",
            "items": [],
        }
    )
    for row in doc.get("items") or []:
        if flt(row.required_qty) <= 0:
            continue
        stock_entry.append(
            "items",
            {
                "item_code": row.rm_code,
                "qty": row.required_qty,
                "transfer_qty": row.required_qty,
                "s_warehouse": row.source_warehouse,
                "t_warehouse": doc.target_warehouse,
                "batch_no": row.batch_no,
            },
        )
        row.issued_qty = row.required_qty
    stock_entry.insert(ignore_permissions=True)
    doc.stock_entry = stock_entry.name
    doc.status = "Issued"
    doc.stores_confirmed_by = frappe.session.user
    doc.stores_confirmed_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"stock_entry": stock_entry.name, "requisition": doc.name}


@frappe.whitelist()
def sync_work_order_execution_context(doc, method=None):
    if doc.doctype != WORK_ORDER_DOCTYPE:
        return


def get_requirement_for_work_order(work_order):
    if not work_order:
        return None
    if work_order.get(WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD):
        return frappe.get_doc(PRODUCTION_REQUIREMENT_DOCTYPE, work_order.get(WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD))
    return None


def get_work_order_for_job_card(job_card, requirement=None):
    if not job_card:
        return None
    if requirement:
        rows = frappe.get_all(
            WORK_ORDER_DOCTYPE,
            filters={WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD: requirement.name},
            fields=["name", "custom_fg_batch_no"],
            order_by="creation desc",
            limit_page_length=20,
        )
        for row in rows:
            if job_card.fg_batch_no and row.custom_fg_batch_no == job_card.fg_batch_no:
                return frappe.get_doc(WORK_ORDER_DOCTYPE, row.name)

    return None


def get_latest_work_order_for_requirement(requirement):
    if not requirement:
        return None
    rows = frappe.get_all(
        WORK_ORDER_DOCTYPE,
        filters={WORK_ORDER_PRODUCTION_REQUIREMENT_FIELD: requirement.name},
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return frappe.get_doc(WORK_ORDER_DOCTYPE, rows[0].name) if rows else None


def get_active_job_card(requirement=None, work_order=None):
    if work_order and work_order.get(WORK_ORDER_PRODUCTION_JOB_CARD_FIELD):
        card_name = work_order.get(WORK_ORDER_PRODUCTION_JOB_CARD_FIELD)
        if frappe.db.exists(PRODUCTION_JOB_CARD_DOCTYPE, card_name):
            return frappe.get_doc(PRODUCTION_JOB_CARD_DOCTYPE, card_name)

    filters = {}
    if requirement:
        filters["production_requirement"] = requirement.name
    rows = frappe.get_all(
        PRODUCTION_JOB_CARD_DOCTYPE,
        filters=filters,
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    return frappe.get_doc(PRODUCTION_JOB_CARD_DOCTYPE, rows[0].name) if rows else None


@frappe.whitelist()
def get_production_execution_payload(doctype: str, docname: str) -> dict[str, object]:
    if doctype == PRODUCTION_REQUIREMENT_DOCTYPE:
        requirement = frappe.get_doc(PRODUCTION_REQUIREMENT_DOCTYPE, docname)
        work_order = get_latest_work_order_for_requirement(requirement)
        job_card = get_active_job_card(requirement=requirement, work_order=work_order)
        return build_execution_payload(requirement=requirement, work_order=work_order, job_card=job_card)

    if doctype == WORK_ORDER_DOCTYPE:
        work_order = frappe.get_doc(WORK_ORDER_DOCTYPE, docname)
        requirement = get_requirement_for_work_order(work_order)
        job_card = get_active_job_card(requirement=requirement, work_order=work_order)
        return build_execution_payload(requirement=requirement, work_order=work_order, job_card=job_card)

    if doctype == PRODUCTION_JOB_CARD_DOCTYPE:
        job_card = frappe.get_doc(PRODUCTION_JOB_CARD_DOCTYPE, docname)
        requirement = frappe.get_doc(PRODUCTION_REQUIREMENT_DOCTYPE, job_card.production_requirement) if job_card.production_requirement else None
        work_order = get_work_order_for_job_card(job_card, requirement)
        return build_execution_payload(requirement=requirement, work_order=work_order, job_card=job_card)

    frappe.throw(_("Production execution payload is not supported for %(doctype)s.") % {"doctype": doctype})


def build_execution_payload(requirement, work_order, job_card) -> dict[str, object]:
    stages = []
    requirement_status = "Completed" if requirement and requirement.status in {"Planned", "Closed"} else ("In Progress" if requirement else "Not Started")
    stages.append(make_stage("production_requirement", "Production Requirement", requirement_status, requirement, requirement_summary(requirement)))

    work_order_status = (
        work_order.get("custom_production_stage")
        if work_order and work_order.get("custom_production_stage")
        else normalize_work_order_status(work_order.status if work_order else "Not Started")
    )
    stages.append(make_stage("work_order", "Work Order", work_order_status, work_order, work_order_summary(work_order)))

    jc_status = job_card.status if job_card else "Not Started"
    stages.append(make_stage("job_card", "Job Card", normalize_tracker_status(jc_status), job_card, job_card_summary(job_card)))

    material_status = job_card.material_status if job_card else "Not Started"
    stages.append(make_stage("material_availability", "Material Availability", normalize_tracker_status(material_status), job_card, material_summary(job_card)))

    requisition = frappe.get_doc(PRODUCTION_RM_REQUISITION_DOCTYPE, job_card.rm_requisition) if job_card and job_card.rm_requisition else None
    stages.append(make_stage("rm_requisition", "RM Requisition", normalize_tracker_status(requisition.status if requisition else "Not Started"), requisition, requisition_summary(requisition)))

    clearance = frappe.get_doc(GRADE_CHANGE_CLEARANCE_DOCTYPE, job_card.grade_change_clearance) if job_card and job_card.grade_change_clearance else None
    stages.append(make_stage("grade_change", "Grade Change Clearance", normalize_tracker_status(clearance.status if clearance else "Not Started"), clearance, clearance_summary(clearance)))

    premix = frappe.get_doc(PREMIX_PREPARATION_DOCTYPE, job_card.premix_preparation) if job_card and job_card.premix_preparation else None
    stages.append(make_stage("premix", "Premix Preparation", normalize_tracker_status(premix.status if premix else "Not Started"), premix, premix_summary(premix)))

    run_status = "In Progress" if job_card and (job_card.get("run_readings") or job_card.get("downtime_entries")) and job_card.status not in {"Closed", "FG Quarantine"} else ("Completed" if job_card and job_card.status in {"Closed", "FG Quarantine"} else "Not Started")
    stages.append(make_stage("production_run", "Production Run", run_status, job_card, run_summary(job_card)))

    packing_status = "Completed" if job_card and job_card.status in {"Closed", "FG Quarantine"} else ("In Progress" if job_card and job_card.status == "In Progress" else "Not Started")
    stages.append(make_stage("packing", "FG Packing & Labeling", packing_status, job_card, packing_summary(job_card)))

    delivery_note = frappe.get_doc(FG_DELIVERY_NOTE_DOCTYPE, job_card.fg_delivery_note) if job_card and job_card.fg_delivery_note else None
    stages.append(make_stage("fg_delivery_note", "FG Delivery Note", normalize_tracker_status(delivery_note.status if delivery_note else "Not Started"), delivery_note, delivery_note_summary(delivery_note)))

    quarantine_status = "Completed" if job_card and job_card.status == "FG Quarantine" else "Not Started"
    stages.append(make_stage("fg_quarantine", "FG Quarantine", quarantine_status, job_card, "Production execution ends at FG Quarantine."))

    return {
        "summary": {
            "requirement": requirement.name if requirement else "",
            "job_card": job_card.name if job_card else "",
            "work_order": work_order.name if work_order else "",
            "fg_batch_no": work_order.get(WORK_ORDER_FG_BATCH_FIELD) if work_order else (job_card.fg_batch_no if job_card else ""),
            "grade_code": job_card.grade_code if job_card else (requirement.items[0].item_code if requirement and requirement.get("items") else ""),
            "planned_qty": flt(work_order.qty) if work_order else (flt(job_card.planned_qty) if job_card else flt(requirement.total_net_required_qty if requirement else 0)),
            "actual_qty": flt(job_card.actual_qty) if job_card else 0,
        },
        "stages": stages,
        "stage_order_source": PRODUCTION_STAGE_ORDER,
    }


def make_stage(key: str, label: str, status: str, doc=None, summary: str = "") -> dict[str, object]:
    color_map = {
        "Not Started": "grey",
        "Draft": "grey",
        "Open": "blue",
        "In Progress": "blue",
        "Available": "green",
        "Ready": "green",
        "Completed": "green",
        "Approved": "green",
        "Partially Available": "orange",
        "Blocked": "red",
        "Rejected": "red",
        "Issued": "green",
        "Verified": "green",
        "FG Quarantine": "green",
        "Closed": "green",
    }
    normalized = normalize_tracker_status(status)
    route = ["Form", doc.doctype, doc.name] if doc else None
    return {
        "key": key,
        "label": label,
        "status": normalized,
        "color": color_map.get(normalized, "blue"),
        "summary": summary or normalized,
        "route": route,
        "doctype": doc.doctype if doc else "",
        "name": doc.name if doc else "",
    }


def normalize_tracker_status(status: str | None) -> str:
    value = (status or "").strip()
    mapping = {
        "": "Not Started",
        "Draft": "Not Started",
        "Open": "In Progress",
        "Planned": "Completed",
        "Job Card Created": "In Progress",
        "Ready": "Completed",
        "Available": "Completed",
        "Issued": "Completed",
        "Verified": "Completed",
        "Closed": "Completed",
    }
    return mapping.get(value, value)


def normalize_work_order_status(status: str | None) -> str:
    value = (status or "").strip()
    mapping = {
        "": "Not Started",
        "Draft": "Not Started",
        "Not Started": "Not Started",
        "Open": "In Progress",
        "In Process": "In Progress",
        "Stopped": "Blocked",
        "Hold": "Blocked",
        "Partially Fulfilled": "In Progress",
        "Completed": "Completed",
        "Closed": "Completed",
        "Cancelled": "Rejected",
    }
    return mapping.get(value, value)


def work_order_summary(work_order) -> str:
    if not work_order:
        return _("Work Order has not been created.")
    return _("%(item)s | Qty %(qty)s | Stage %(stage)s") % {
        "item": work_order.production_item or "-",
        "qty": round(flt(work_order.qty), 3),
        "stage": work_order.get("custom_production_stage") or work_order.status or "",
    }


def requirement_summary(requirement) -> str:
    if not requirement:
        return _("Waiting for weekly planning document.")
    return _("%(lines)s demand lines | Net required %(qty)s Kg") % {
        "lines": len(requirement.get("items") or []),
        "qty": round(flt(requirement.total_net_required_qty), 3),
    }


def job_card_summary(job_card) -> str:
    if not job_card:
        return _("Job Card will be created from Production Requirement.")
    return _("%(batch)s | Planned %(planned)s Kg | Actual %(actual)s Kg") % {
        "batch": job_card.fg_batch_no or "-",
        "planned": round(flt(job_card.planned_qty), 3),
        "actual": round(flt(job_card.actual_qty), 3),
    }


def material_summary(job_card) -> str:
    if not job_card:
        return _("BOM vs RM availability will be evaluated after Job Card creation.")
    rows = job_card.get("materials") or []
    allocated = round(sum(flt(row.allocated_qty) for row in rows), 3)
    required = round(sum(flt(row.required_qty) for row in rows), 3)
    return _("%(status)s | Allocated %(allocated)s of %(required)s Kg") % {
        "status": job_card.material_status or "Blocked",
        "allocated": allocated,
        "required": required,
    }


def requisition_summary(requisition) -> str:
    if not requisition:
        return _("RM Requisition is pending material allocation approval.")
    return _("%(status)s | Required %(required)s Kg | Issued %(issued)s Kg") % {
        "status": requisition.status,
        "required": round(flt(requisition.required_qty), 3),
        "issued": round(flt(requisition.issued_qty), 3),
    }


def clearance_summary(clearance) -> str:
    if not clearance:
        return _("Grade Change Clearance must be completed before production can start.")
    return _("%(status)s | Prepared by %(prepared)s | Approved by %(approved)s") % {
        "status": clearance.status,
        "prepared": clearance.prepared_by or "-",
        "approved": clearance.approved_by or "-",
    }


def premix_summary(premix) -> str:
    if not premix:
        return _("Premix Preparation is pending.")
    total_actual = round(sum(flt(row.actual_qty) for row in premix.get("items") or []), 3)
    return _("%(status)s | Actual premix %(qty)s Kg") % {"status": premix.status, "qty": total_actual}


def run_summary(job_card) -> str:
    if not job_card:
        return _("Production Run has not started.")
    readings = len(job_card.get("run_readings") or [])
    downtimes = len(job_card.get("downtime_entries") or [])
    return _("%(readings)s readings | %(downtimes)s downtime entries") % {"readings": readings, "downtimes": downtimes}


def packing_summary(job_card) -> str:
    if not job_card:
        return _("Packing starts after production output is confirmed.")
    return _("FG batch %(batch)s packed from production side.") % {"batch": job_card.fg_batch_no or "-"}


def delivery_note_summary(note) -> str:
    if not note:
        return _("FG Delivery Note is pending.")
    return _("Prime %(prime)s | SPY %(spy)s | TPY %(tpy)s | PMX %(pmx)s") % {
        "prime": round(flt(note.prime_fg_qty), 3),
        "spy": round(flt(note.spy_qty), 3),
        "tpy": round(flt(note.tpy_qty), 3),
        "pmx": round(flt(note.pmx_qty), 3),
    }
