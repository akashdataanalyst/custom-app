from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cstr, flt, get_datetime, now_datetime

from calco_erp.calco_quality.rm_warehouse_flow import (
    create_outward_batch_bundle,
    get_batch_balance,
)
from calco_erp.machine_setup import MACHINE_FIELD, ensure_valid_machine


CONSUMPTION_ENTRY_FIELD = "custom_production_consumption_entry"
FG_CODE_FIELD = "custom_fg_code"
FG_BATCH_FIELD = "custom_fg_batch_no"
CHALLAN_FIELD = "custom_challan_invoice_no"
CATEGORY_FIELD = "custom_consumption_category"


def ensure_production_consumption_setup():
    if not frappe.db.exists("DocType", "Stock Entry"):
        return

    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": CONSUMPTION_ENTRY_FIELD,
                    "label": "Production Consumption Entry",
                    "fieldtype": "Link",
                    "options": "Production Consumption Entry",
                    "insert_after": "work_order",
                    "read_only": 1,
                    "search_index": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": FG_CODE_FIELD,
                    "label": "FG Code",
                    "fieldtype": "Link",
                    "options": "Item",
                    "insert_after": CONSUMPTION_ENTRY_FIELD,
                    "read_only": 1,
                    "search_index": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": FG_BATCH_FIELD,
                    "label": "FG Batch No",
                    "fieldtype": "Data",
                    "insert_after": FG_CODE_FIELD,
                    "read_only": 1,
                    "search_index": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": CHALLAN_FIELD,
                    "label": "Challan / Invoice No",
                    "fieldtype": "Data",
                    "insert_after": FG_BATCH_FIELD,
                    "read_only": 1,
                    "search_index": 1,
                    "no_copy": 1,
                },
                {
                    "fieldname": CATEGORY_FIELD,
                    "label": "Consumption Category",
                    "fieldtype": "Data",
                    "insert_after": CHALLAN_FIELD,
                    "read_only": 1,
                    "search_index": 1,
                    "no_copy": 1,
                },
            ]
        },
        update=True,
    )
    frappe.clear_cache(doctype="Stock Entry")


class ProductionConsumptionEntry(Document):
    def validate(self):
        from calco_erp.calco_production import final_consumption as final
        from calco_erp.calco_production.parallel_production import prepare_manual
        prepare_manual(self)
        final.guard_mode(self)
        if final.is_final(self):
            return final.validate(self)
        self.user = self.user or frappe.session.user
        self.posting_datetime = self.posting_datetime or now_datetime()
        self.company = self.company or get_company_for_warehouse(self.warehouse)

        if self.production_line:
            ensure_valid_machine(self.production_line)

        prevent_controlled_work_order_consumption(self)
        ensure_consumption_rows(self)
        validate_consumption_rows(self)
        sync_legacy_fields_for_backward_compatibility(self)

    def _submit(self):
        from calco_erp.calco_production import final_consumption as final
        if final.is_final(self):
            # Include the parent docstatus transition in the same rollback scope
            # as the native issue and immutable closure insertion.
            return final.closure.physical.atomic(super()._submit)()
        return super()._submit()

    def on_submit(self):
        from calco_erp.calco_production import final_consumption as final
        if final.is_final(self):
            return final.submit(self)
        if self.stock_entry:
            validate_linked_stock_entry(self)
            return

        stock_entry = create_material_issue_stock_entry(self)
        self.db_set("stock_entry", stock_entry.name, update_modified=False)

    def before_rename(self, old, new, merge=False):
        from calco_erp.calco_production import final_consumption as final
        if final.is_final(self):
            frappe.throw("Final consumption identity is immutable.")

    def before_cancel(self):
        from calco_erp.calco_production import final_consumption as final
        if final.is_final(self):
            final.prevent_cancel(self)

    def on_update_after_submit(self):
        from calco_erp.calco_production import final_consumption as final
        if final.is_final(self):
            final.prevent_cancel(self)

    def on_cancel(self):
        if not self.stock_entry or not frappe.db.exists("Stock Entry", self.stock_entry):
            return

        stock_entry = frappe.get_doc("Stock Entry", self.stock_entry)
        if stock_entry.docstatus == 1:
            stock_entry.cancel()


BATCH_FIFO_DATE_RE = re.compile(r"^(?P<day>\d{2})(?P<month>\d{2}).*?(?P<year>\d{2})(?:[A-Za-z]\d*)?$")


def parse_batch_fifo_date(batch_label: str) -> date | None:
    batch_label = cstr(batch_label).strip()
    match = BATCH_FIFO_DATE_RE.match(batch_label)
    if not match:
        return None

    try:
        day = int(match.group("day"))
        month = int(match.group("month"))
        year = 2000 + int(match.group("year"))
        return date(year, month, day)
    except ValueError:
        return None


def get_available_batch_rows(item_code: str, warehouse: str, txt: str = "") -> list[dict[str, object]]:
    item_code = cstr(item_code).strip()
    warehouse = cstr(warehouse).strip()
    txt_lower = cstr(txt).strip().lower()
    if not item_code or not warehouse:
        return []

    rows = []
    batch_docs = frappe.get_all(
        "Batch",
        filters={"item": item_code},
        fields=["name", "batch_id", "creation"],
        limit_page_length=0,
    )

    for batch in batch_docs:
        batch_label = cstr(batch.batch_id or batch.name).strip()
        if txt_lower and txt_lower not in batch_label.lower() and txt_lower not in cstr(batch.name).lower():
            continue

        available_qty = flt(get_batch_balance(item_code, batch.name, warehouse))
        if available_qty <= 0:
            continue

        fifo_date = parse_batch_fifo_date(batch_label)
        rows.append(
            {
                "name": batch.name,
                "batch_label": batch_label,
                "available_qty": round(available_qty, 3),
                "fifo_date": fifo_date,
                "fifo_key": fifo_date.isoformat() if fifo_date else "",
                "creation": batch.creation,
            }
        )

    rows.sort(
        key=lambda row: (
            row["fifo_date"] is None,
            row["fifo_date"] or date.max,
            row["creation"],
            row["batch_label"],
        )
    )
    return rows

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_rm_batches(doctype, txt, searchfield, start, page_len, filters=None):
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    item_code = filters.get("item_code")
    warehouse = filters.get("warehouse")
    rows = get_available_batch_rows(
        item_code=item_code,
        warehouse=warehouse,
        txt=txt,
    )
    paged_rows = rows[start : start + page_len]
    return [
        [
            row["name"],
            f"{row['batch_label']} | Qty: {row['available_qty']:.3f}",
        ]
        for row in paged_rows
    ]


@frappe.whitelist()
def get_available_rm_batches_for_consumption(item_code: str, warehouse: str) -> list[dict[str, object]]:
    return [
        {
            "batch_no": row["name"],
            "available_qty": row["available_qty"],
            "batch_date": row["fifo_key"],
            "fifo_key": row["fifo_key"],
        }
        for row in get_available_batch_rows(item_code=item_code, warehouse=warehouse)
    ]


@frappe.whitelist()
def get_rm_batch_availability(item_code: str, batch_no: str, warehouse: str) -> dict[str, float]:
    item_code = cstr(item_code).strip()
    batch_no = cstr(batch_no).strip()
    warehouse = cstr(warehouse).strip()
    if not item_code or not batch_no or not warehouse:
        return {"available_qty": 0.0}

    validate_rm_batch(item_code, batch_no)
    return {"available_qty": round(flt(get_batch_balance(item_code, batch_no, warehouse)), 3)}


def ensure_consumption_rows(doc: ProductionConsumptionEntry):
    if doc.get("items"):
        return

    legacy_row = build_legacy_row(doc)
    if legacy_row:
        doc.append("items", legacy_row)

    if not doc.get("items"):
        frappe.throw(_("At least one RM row is required."))


def build_legacy_row(doc: ProductionConsumptionEntry) -> dict[str, object] | None:
    rm_code = cstr(doc.get("rm_code") or "").strip()
    rm_batch_no = cstr(doc.get("rm_batch_no") or "").strip()
    rm_qty = flt(doc.get("rm_qty"))
    if not rm_code and not rm_batch_no and rm_qty <= 0:
        return None

    return {
        "rm_code": rm_code,
        "rm_batch_no": rm_batch_no,
        "available_batch_qty": 0,
        "rm_qty_consumed": rm_qty,
        "category": doc.get("category"),
        "challan_invoice_no": doc.get("challan_invoice_no"),
        "remarks": "",
    }


def validate_consumption_rows(doc: ProductionConsumptionEntry):
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(doc.get("items") or [], start=1):
        row.rm_code = cstr(row.get("rm_code") or "").strip()
        row.rm_batch_no = cstr(row.get("rm_batch_no") or "").strip()
        row.category = cstr(row.get("category") or "").strip()
        row.challan_invoice_no = cstr(row.get("challan_invoice_no") or "").strip()

        if not row.rm_code:
            frappe.throw(_("Row #{0}: RM Code is mandatory.").format(index))
        if not row.rm_batch_no:
            frappe.throw(_("Row #{0}: RM Batch No is mandatory.").format(index))
        if flt(row.rm_qty_consumed) <= 0:
            frappe.throw(_("Row #{0}: RM Qty Consumed must be greater than zero.").format(index))

        duplicate_key = (row.rm_code, row.rm_batch_no)
        if duplicate_key in seen:
            frappe.throw(
                _("Row #{0}: Duplicate RM Code + Batch combination {1} / {2} is not allowed.").format(
                    index, row.rm_code, row.rm_batch_no
                )
            )
        seen.add(duplicate_key)

        validate_rm_batch(row.rm_code, row.rm_batch_no)
        available_qty = get_batch_balance(row.rm_code, row.rm_batch_no, doc.warehouse)
        row.available_batch_qty = flt(available_qty)
        if flt(available_qty) + 1e-9 < flt(row.rm_qty_consumed):
            frappe.throw(
                _(
                    "Row #{0}: Insufficient stock for item {1}, batch {2} in warehouse {3}. Available qty is {4}."
                ).format(
                    index,
                    row.rm_code,
                    row.rm_batch_no,
                    doc.warehouse,
                    round(flt(available_qty), 3),
                )
            )


def sync_legacy_fields_for_backward_compatibility(doc: ProductionConsumptionEntry):
    rows = list(doc.get("items") or [])
    if len(rows) == 1:
        row = rows[0]
        doc.rm_code = row.rm_code
        doc.rm_batch_no = row.rm_batch_no
        doc.rm_qty = flt(row.rm_qty_consumed)
        doc.challan_invoice_no = row.challan_invoice_no
        doc.category = row.category
        return

    doc.rm_code = ""
    doc.rm_batch_no = ""
    doc.rm_qty = 0
    doc.challan_invoice_no = ""
    doc.category = ""


def validate_rm_batch(item_code: str, batch_no: str):
    if not frappe.db.exists("Batch", batch_no):
        frappe.throw(_("RM Batch {0} does not exist.").format(batch_no))

    batch_item = frappe.db.get_value("Batch", batch_no, "item")
    if batch_item and batch_item != item_code:
        frappe.throw(_("RM Batch {0} belongs to item {1}, not {2}.").format(batch_no, batch_item, item_code))


def prevent_controlled_work_order_consumption(doc: ProductionConsumptionEntry):
    from calco_erp.calco_production.parallel_production import validate_manual
    validate_manual(doc)


def create_material_issue_stock_entry(doc: ProductionConsumptionEntry):
    posting_datetime = get_datetime(doc.posting_datetime)
    company = doc.company or get_company_for_warehouse(doc.warehouse)
    rows = list(doc.get("items") or [])
    stock_items = []

    for row in rows:
        stock_uom = frappe.db.get_value("Item", row.rm_code, "stock_uom")
        bundle = create_outward_batch_bundle(
            item_code=row.rm_code,
            batch_no=row.rm_batch_no,
            qty=flt(row.rm_qty_consumed),
            warehouse=doc.warehouse,
            company=company,
            posting_datetime=posting_datetime,
        )
        stock_items.append(
            {
                "item_code": row.rm_code,
                "qty": flt(row.rm_qty_consumed),
                "transfer_qty": flt(row.rm_qty_consumed),
                "uom": stock_uom,
                "stock_uom": stock_uom,
                "conversion_factor": 1,
                "s_warehouse": doc.warehouse,
                "batch_no": "",
                "serial_no": "",
                "serial_and_batch_bundle": bundle,
                "use_serial_batch_fields": 0,
                "description": build_stock_entry_item_description(row),
            }
        )

    stock_entry = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Issue",
            "stock_entry_type": "Material Issue",
            "company": company,
            "posting_date": posting_datetime.date(),
            "posting_time": posting_datetime.strftime("%H:%M:%S.%f" if doc.get("consumption_mode") == "Final Confirmation" else "%H:%M:%S"),
            "set_posting_time": 1,
            "remarks": build_stock_entry_remarks(doc, rows),
            "items": stock_items,
        }
    )

    stock_meta = frappe.get_meta("Stock Entry")
    if stock_meta.has_field(MACHINE_FIELD) and doc.production_line:
        stock_entry.set(MACHINE_FIELD, doc.production_line)
    if stock_meta.has_field(CONSUMPTION_ENTRY_FIELD):
        stock_entry.set(CONSUMPTION_ENTRY_FIELD, doc.name)
    if stock_meta.has_field(FG_CODE_FIELD):
        stock_entry.set(FG_CODE_FIELD, doc.fg_code)
    if stock_meta.has_field(FG_BATCH_FIELD):
        stock_entry.set(FG_BATCH_FIELD, doc.fg_batch_no)
    if stock_meta.has_field(CHALLAN_FIELD):
        stock_entry.set(CHALLAN_FIELD, summarize_row_values(rows, "challan_invoice_no"))
    if stock_meta.has_field(CATEGORY_FIELD):
        stock_entry.set(CATEGORY_FIELD, summarize_row_values(rows, "category"))

    from calco_erp.calco_production import final_consumption as final
    if final.is_final(doc):
        if doc.get("final_pce_posting_token") is not final.TOKEN:
            frappe.throw("Use the controlled final-consumption posting path.")
        stock_entry.flags.final_pce_posting_token = final.TOKEN
    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()
    return stock_entry


def validate_linked_stock_entry(doc: ProductionConsumptionEntry):
    if not frappe.db.exists("Stock Entry", doc.stock_entry):
        frappe.throw(_("Linked Stock Entry {0} does not exist.").format(doc.stock_entry))

    stock_entry = frappe.get_doc("Stock Entry", doc.stock_entry)
    if stock_entry.docstatus != 1:
        frappe.throw(_("Linked Stock Entry {0} must be submitted before this entry can be submitted.").format(doc.stock_entry))

    linked_entry = stock_entry.get(CONSUMPTION_ENTRY_FIELD) or ""
    if linked_entry != doc.name:
        frappe.throw(
            _("Linked Stock Entry {0} must reference Production Consumption Entry {1}.").format(
                doc.stock_entry, doc.name
            )
        )

    purpose = (stock_entry.get("stock_entry_type") or stock_entry.get("purpose") or "").strip()
    if purpose != "Material Issue":
        frappe.throw(_("Linked Stock Entry {0} must be a Material Issue.").format(doc.stock_entry))


def build_stock_entry_remarks(doc: ProductionConsumptionEntry, rows: Iterable[Document]) -> str:
    detail_parts = [
        _("Entry {0}").format(doc.name),
        _("FG {0}").format(doc.fg_code),
        _("FG Batch {0}").format(doc.fg_batch_no),
        _("RM Rows {0}").format(len(list(rows))),
    ]
    if doc.production_line:
        detail_parts.append(_("Line {0}").format(doc.production_line))
    category_summary = summarize_row_values(rows, "category")
    challan_summary = summarize_row_values(rows, "challan_invoice_no")
    if challan_summary:
        detail_parts.append(_("Challan/Invoice {0}").format(challan_summary))
    if category_summary:
        detail_parts.append(_("Category {0}").format(category_summary))
    return "Production Consumption Issue | " + " | ".join(detail_parts)


def build_stock_entry_item_description(row: Document) -> str:
    parts = [
        _("Batch {0}").format(row.rm_batch_no),
        _("Qty {0}").format(round(flt(row.rm_qty_consumed), 3)),
    ]
    if row.get("category"):
        parts.append(_("Category {0}").format(row.category))
    if row.get("challan_invoice_no"):
        parts.append(_("Challan/Invoice {0}").format(row.challan_invoice_no))
    if row.get("remarks"):
        parts.append(_("Remarks {0}").format(row.remarks))
    return " | ".join(parts)


def summarize_row_values(rows: Iterable[Document], fieldname: str) -> str:
    values = []
    for row in rows:
        value = cstr(row.get(fieldname) or "").strip()
        if value and value not in values:
            values.append(value)

    summary = ", ".join(values)
    return summary[:140]


def get_company_for_warehouse(warehouse: str) -> str:
    company = frappe.db.get_value("Warehouse", warehouse, "company")
    if company:
        return company

    company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        frappe.throw(_("Company could not be resolved from warehouse {0}.").format(warehouse))
    return company
