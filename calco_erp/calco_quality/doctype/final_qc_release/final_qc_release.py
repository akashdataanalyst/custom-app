from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, now_datetime
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.serial_batch_bundle import SerialBatchCreation


FG_QUARANTINE_WAREHOUSE = "FG Quarantine - CPPL"
FG_RELEASED_WAREHOUSE = "FG Released - CPPL"
EPSILON = 1e-9


class FinalQCRelease(Document):
    def validate(self):
        context = resolve_release_context(self)
        apply_release_context(self, context)
        if not self.status:
            self.status = "Pending"

    def before_submit(self):
        if self.status != "Released":
            frappe.throw(_("Final QC Release must be submitted with status Released."))

        lock_release_batch(self.batch_no)
        context = resolve_release_context(self)
        assert_no_duplicate_release(self)

        release_qty = flt(self.release_qty or context["accepted_qty"])
        if abs(release_qty - flt(context["accepted_qty"])) > EPSILON:
            frappe.throw(
                _("Phase 7C requires full-batch release of {0} Kg; partial release is not approved.").format(
                    round(flt(context["accepted_qty"]), 6)
                )
            )

        quarantine_qty = get_fg_batch_quantity(
            self.item_code, self.batch_no, FG_QUARANTINE_WAREHOUSE
        )
        previously_released = get_previously_released_qty(self)
        maximum_releasable = max(
            min(flt(context["accepted_qty"]), quarantine_qty) - previously_released,
            0,
        )
        if release_qty > maximum_releasable + EPSILON:
            frappe.throw(
                _(
                    "Release quantity {0} exceeds maximum releasable quantity {1}. "
                    "Accepted QC: {2}, quarantine stock: {3}, previously released: {4}."
                ).format(
                    round(release_qty, 6),
                    round(maximum_releasable, 6),
                    round(flt(context["accepted_qty"]), 6),
                    round(quarantine_qty, 6),
                    round(previously_released, 6),
                )
            )

        self.release_qty = release_qty
        self.released_by = self.released_by or frappe.session.user
        self.released_on = self.released_on or now_datetime()
        apply_quality_metrics(self, context["quality_inspection"])

    def on_submit(self):
        context = resolve_release_context(self)
        transfer = create_release_stock_entry(self, context)
        self.release_stock_entry = transfer.name
        self.db_set("release_stock_entry", transfer.name, update_modified=False)
        self.create_or_update_coa()
        context["batch_production_record"].db_set(
            "status", "Released", update_modified=False
        )

    def before_cancel(self):
        submitted_clearance = frappe.db.exists(
            "Dispatch Clearance",
            {"final_qc_release": self.name, "docstatus": 1},
        )
        if submitted_clearance:
            frappe.throw(
                _("Cancel Dispatch Clearance {0} before cancelling this release.").format(
                    submitted_clearance
                )
            )

    def on_cancel(self):
        if self.release_stock_entry and frappe.db.exists(
            "Stock Entry", self.release_stock_entry
        ):
            transfer = frappe.get_doc("Stock Entry", self.release_stock_entry)
            if cint(transfer.docstatus) == 1:
                transfer.flags.from_final_qc_release_cancel = True
                transfer.cancel()

        if self.coa_record and frappe.db.exists("COA Record", self.coa_record):
            frappe.db.set_value(
                "COA Record", self.coa_record, "status", "Cancelled", update_modified=False
            )
        if self.batch_production_record and frappe.db.exists(
            "Batch Production Record", self.batch_production_record
        ):
            frappe.db.set_value(
                "Batch Production Record",
                self.batch_production_record,
                "status",
                "Final QC Pending",
                update_modified=False,
            )

    def create_or_update_coa(self):
        if self.coa_record and frappe.db.exists("COA Record", self.coa_record):
            coa = frappe.get_doc("COA Record", self.coa_record)
        elif frappe.db.exists("COA Record", {"final_qc_release": self.name}):
            coa_name = frappe.db.get_value(
                "COA Record", {"final_qc_release": self.name}, "name"
            )
            coa = frappe.get_doc("COA Record", coa_name)
        else:
            coa = frappe.new_doc("COA Record")

        coa.final_qc_release = self.name
        coa.item_code = self.item_code
        coa.batch_no = self.batch_no
        coa.issue_date = self.released_on
        coa.moisture = self.moisture
        coa.mfi = self.mfi
        coa.ash = self.ash
        coa.density = self.density
        coa.status = "Issued"

        if coa.is_new():
            coa.insert(ignore_permissions=True)
        else:
            coa.save(ignore_permissions=True)

        self.coa_record = coa.name
        self.db_set("coa_record", coa.name, update_modified=False)


@frappe.whitelist()
def make_final_qc_release(
    quality_inspection: str | None = None, *args, **kwargs
) -> dict[str, Any]:
    quality_inspection = cstr(
        quality_inspection or kwargs.get("quality_inspection")
    ).strip()
    if not quality_inspection or not frappe.db.exists(
        "Quality Inspection", quality_inspection
    ):
        frappe.throw(_("Valid Quality Inspection is required."))

    frappe.db.sql(
        "select name from `tabQuality Inspection` where name = %s for update",
        (quality_inspection,),
    )
    existing = frappe.db.get_value(
        "Final QC Release",
        {"quality_inspection": quality_inspection, "docstatus": ("<", 2)},
        "name",
        order_by="creation desc",
    )
    if existing:
        return frappe.get_doc("Final QC Release", existing).as_dict()

    qi = frappe.get_doc("Quality Inspection", quality_inspection)
    manufacture = cstr(qi.get("reference_name")).strip()
    bpr = frappe.db.get_value(
        "Batch Production Record",
        {"stock_entry": manufacture, "docstatus": 1},
        "name",
    )
    if not bpr:
        frappe.throw(
            _("Submitted Batch Production Record is required for Manufacture {0}.").format(
                manufacture or _("Not Set")
            )
        )

    frappe.has_permission("Final QC Release", "create", throw=True)
    release = frappe.get_doc(
        {
            "doctype": "Final QC Release",
            "batch_production_record": bpr,
            "quality_inspection": qi.name,
            "status": "Pending",
        }
    )
    context = resolve_release_context(release)
    apply_release_context(release, context)
    release.release_qty = context["accepted_qty"]
    apply_quality_metrics(release, qi)
    release.insert()
    return release.as_dict()


def resolve_release_context(doc) -> dict[str, Any]:
    if not doc.get("quality_inspection"):
        frappe.throw(_("Accepted Final Quality Inspection is mandatory."))
    qi = frappe.get_doc("Quality Inspection", doc.quality_inspection)
    if cint(qi.docstatus) != 1 or cstr(qi.status).strip() != "Accepted":
        frappe.throw(_("Final QC Release requires a submitted Accepted Quality Inspection."))
    if cstr(qi.get("custom_work_order_qc_stage")).strip() != "Final QC":
        frappe.throw(_("Quality Inspection must be a Work Order Final QC inspection."))
    if cstr(qi.reference_type).strip() != "Stock Entry" or not qi.reference_name:
        frappe.throw(_("Final QC must reference its submitted Manufacture Stock Entry."))

    manufacture = frappe.get_doc("Stock Entry", qi.reference_name)
    if cint(manufacture.docstatus) != 1 or cstr(
        manufacture.get("purpose") or manufacture.get("stock_entry_type")
    ).strip() != "Manufacture":
        frappe.throw(_("Final QC reference must be a submitted Manufacture Stock Entry."))

    bpr_name = cstr(doc.get("batch_production_record")).strip() or frappe.db.get_value(
        "Batch Production Record",
        {"stock_entry": manufacture.name, "docstatus": 1},
        "name",
    )
    if not bpr_name:
        frappe.throw(_("Submitted Batch Production Record is mandatory for Final QC Release."))
    bpr = frappe.get_doc("Batch Production Record", bpr_name)
    if cint(bpr.docstatus) != 1 or bpr.stock_entry != manufacture.name:
        frappe.throw(_("Batch Production Record does not match the Manufacture Stock Entry."))

    finished = next(
        (
            row
            for row in manufacture.get("items") or []
            if row.get("is_finished_item") and row.get("item_code") == bpr.item_code
        ),
        None,
    )
    if not finished:
        frappe.throw(_("Manufacture Stock Entry has no matching finished item row."))
    from calco_erp.utils.dependencies import get_stock_entry_row_batch_no

    batch_no = get_stock_entry_row_batch_no(finished)
    accepted_qty = flt(finished.get("transfer_qty") or finished.get("qty"))
    if accepted_qty <= EPSILON:
        frappe.throw(_("Manufactured FG quantity must be positive."))
    if (
        cstr(qi.item_code).strip() != cstr(bpr.item_code).strip()
        or cstr(qi.batch_no).strip() != cstr(batch_no).strip()
        or cstr(bpr.fg_batch_no).strip() != cstr(batch_no).strip()
    ):
        frappe.throw(_("Final QC, Manufacture, BPR, item, and batch lineage do not match."))
    batch_item = frappe.db.get_value("Batch", batch_no, "item")
    if cstr(batch_item).strip() != cstr(bpr.item_code).strip():
        frappe.throw(_("Batch {0} does not belong to item {1}.").format(batch_no, bpr.item_code))

    return {
        "quality_inspection": qi,
        "manufacture": manufacture,
        "batch_production_record": bpr,
        "item_code": bpr.item_code,
        "batch_no": batch_no,
        "accepted_qty": accepted_qty,
        "company": manufacture.company,
    }


def apply_release_context(doc, context: dict[str, Any]):
    doc.batch_production_record = context["batch_production_record"].name
    doc.stock_entry = context["manufacture"].name
    doc.item_code = context["item_code"]
    doc.batch_no = context["batch_no"]
    doc.release_qty = doc.get("release_qty") or context["accepted_qty"]


def apply_quality_metrics(doc, qi):
    samples = {
        cstr(row.get("parameter")).strip().lower(): row.get("reading")
        for row in qi.get("parameter_samples") or []
        if cstr(row.get("reading")).strip()
    }
    readings = {
        cstr(row.get("specification")).strip().lower(): next(
            (
                row.get(fieldname)
                for fieldname in ["reading_value"]
                + [f"reading_{index}" for index in range(1, 11)]
                if cstr(row.get(fieldname)).strip()
            ),
            None,
        )
        for row in qi.get("readings") or []
    }
    aliases = {
        "moisture": ("moisture", "moisture content"),
        "mfi": ("mfi", "melt flow index"),
        "ash": ("ash", "ash content"),
        "density": ("density", "bulk density"),
    }
    for fieldname, names in aliases.items():
        value = next(
            (samples.get(name) for name in names if samples.get(name) is not None),
            None,
        )
        if value is None:
            value = next(
                (readings.get(name) for name in names if readings.get(name) is not None),
                None,
            )
        if value is not None and cstr(value).strip():
            doc.set(fieldname, flt(value))


def lock_release_batch(batch_no: str):
    frappe.db.sql(
        "select name from `tabBatch` where name = %s for update",
        (batch_no,),
    )


def assert_no_duplicate_release(doc):
    duplicate = frappe.db.get_value(
        "Final QC Release",
        {
            "item_code": doc.item_code,
            "batch_no": doc.batch_no,
            "stock_entry": doc.stock_entry,
            "quality_inspection": doc.quality_inspection,
            "docstatus": 1,
            "name": ("!=", doc.name or ""),
        },
        "name",
    )
    if duplicate:
        frappe.throw(_("Final QC Release {0} already owns this accepted batch.").format(duplicate))


def get_previously_released_qty(doc) -> float:
    rows = frappe.get_all(
        "Final QC Release",
        filters={
            "item_code": doc.item_code,
            "batch_no": doc.batch_no,
            "status": "Released",
            "docstatus": 1,
            "name": ("!=", doc.name or ""),
        },
        pluck="release_qty",
    )
    return sum(flt(value) for value in rows)


def get_fg_batch_quantity(item_code: str, batch_no: str, warehouse: str) -> float:
    return flt(
        get_batch_qty(
            batch_no=batch_no,
            warehouse=warehouse,
            item_code=item_code,
            for_stock_levels=True,
            ignore_reserved_stock=True,
        )
    )


def get_fg_released_batch_qty(item_code: str, batch_no: str) -> float:
    return get_fg_batch_quantity(item_code, batch_no, FG_RELEASED_WAREHOUSE)


def create_release_stock_entry(doc, context: dict[str, Any]):
    posting_datetime = now_datetime()
    qty = flt(doc.release_qty)
    bundle = (
        SerialBatchCreation(
            {
                "item_code": doc.item_code,
                "warehouse": FG_QUARANTINE_WAREHOUSE,
                "voucher_type": "Stock Entry",
                "total_qty": -qty,
                "batches": frappe._dict({doc.batch_no: qty}),
                "type_of_transaction": "Outward",
                "company": context["company"],
                "posting_datetime": posting_datetime,
                "do_not_submit": True,
            }
        )
        .make_serial_and_batch_bundle()
        .name
    )
    stock_uom = frappe.db.get_value("Item", doc.item_code, "stock_uom")
    transfer = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "purpose": "Material Transfer",
            "stock_entry_type": "Material Transfer",
            "company": context["company"],
            "posting_date": posting_datetime.date(),
            "posting_time": posting_datetime.strftime("%H:%M:%S"),
            "remarks": _("Physical FG release from Final QC Release {0}").format(doc.name),
            "items": [
                {
                    "item_code": doc.item_code,
                    "qty": qty,
                    "transfer_qty": qty,
                    "uom": stock_uom,
                    "stock_uom": stock_uom,
                    "conversion_factor": 1,
                    "s_warehouse": FG_QUARANTINE_WAREHOUSE,
                    "t_warehouse": FG_RELEASED_WAREHOUSE,
                    "batch_no": "",
                    "serial_no": "",
                    "serial_and_batch_bundle": bundle,
                    "use_serial_batch_fields": 0,
                }
            ],
        }
    )
    transfer.insert(ignore_permissions=True)
    transfer.submit()
    return transfer


def prevent_direct_release_stock_entry_cancel(doc, method=None):
    if doc.flags.get("from_final_qc_release_cancel"):
        return
    release = frappe.db.get_value(
        "Final QC Release",
        {"release_stock_entry": doc.name, "docstatus": 1},
        "name",
    )
    if release:
        frappe.throw(
            _("Cancel Final QC Release {0}; its Stock Entry cannot be cancelled directly.").format(
                release
            )
        )
