from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from calco_erp.utils.dependencies import get_dispatch_clearance, get_final_qc_release, get_rm_release_note


class TechnicalAssistanceTicket(Document):
    def validate(self):
        self.complaint_date = self.complaint_date or getdate(nowdate())
        self.status = self.status or "Open"
        self.populate_traceability()

    def populate_traceability(self):
        trace = build_traceability_data(self.item_code, self.fg_batch_no, self.delivery_note)

        self.delivery_note = trace["delivery_note"]
        self.customer = self.customer or trace["customer"]
        self.item_code = trace["item_code"]
        self.fg_batch_no = trace["fg_batch_no"]
        self.batch_production_record = trace["batch_production_record"]
        self.work_order = trace["work_order"]
        self.stock_entry = trace["stock_entry"]
        self.dispatch_clearance = trace["dispatch_clearance"]
        self.final_qc_release = trace["final_qc_release"]
        self.final_quality_inspection = trace["final_quality_inspection"]
        self.coa_record = trace["coa_record"]
        self.moisture = trace["moisture"]
        self.mfi = trace["mfi"]
        self.ash = trace["ash"]
        self.density = trace["density"]
        self.traceability_notes = trace["traceability_notes"]

        self.set("rm_batches", [])
        for row in trace["rm_batches"]:
            self.append("rm_batches", row)


def build_traceability_data(item_code: str | None, fg_batch_no: str | None, delivery_note: str | None = None) -> dict[str, object]:
    item_code, fg_batch_no, delivery_note, customer = resolve_fg_reference(item_code, fg_batch_no, delivery_note)
    batch_record_name = frappe.db.get_value(
        "Batch Production Record",
        {"fg_batch_no": fg_batch_no, "docstatus": 1},
        "name",
    )
    if not batch_record_name:
        frappe.throw(f"No submitted Batch Production Record was found for FG batch {fg_batch_no}.")

    batch_record = frappe.get_doc("Batch Production Record", batch_record_name)
    final_qc_release = get_final_qc_release(item_code, fg_batch_no)
    release_doc = frappe.get_doc("Final QC Release", final_qc_release) if final_qc_release else None
    if not delivery_note:
        delivery_note = find_delivery_note_for_batch(item_code, fg_batch_no)

    dispatch_clearance = ""
    if delivery_note:
        dispatch_clearance = get_dispatch_clearance(delivery_note, item_code, fg_batch_no) or ""

    rm_batches = []
    for row in batch_record.get("materials", []):
        release_name = get_rm_release_note(row.item_code, row.batch_no) or ""
        rm_qc_decision = frappe.db.get_value("RM Release Note", release_name, "rm_qc_decision") if release_name else ""
        quality_inspection = (
            frappe.db.get_value("RM QC Decision", rm_qc_decision, "quality_inspection") if rm_qc_decision else ""
        )
        rm_batches.append(
            {
                "item_code": row.item_code,
                "batch_no": row.batch_no,
                "qty": row.qty,
                "source_warehouse": row.source_warehouse,
                "rm_release_note": release_name,
                "rm_qc_decision": rm_qc_decision,
                "quality_inspection": quality_inspection,
            }
        )

    notes = [
        f"FG Batch: {fg_batch_no}",
        f"Work Order: {batch_record.work_order or '-'}",
        f"Manufacture Stock Entry: {batch_record.stock_entry or '-'}",
    ]
    if release_doc:
        notes.append(f"Final QC Release: {release_doc.name}")
    if delivery_note:
        notes.append(f"Delivery Note: {delivery_note}")
    if dispatch_clearance:
        notes.append(f"Dispatch Clearance: {dispatch_clearance}")

    return {
        "customer": customer,
        "delivery_note": delivery_note,
        "item_code": item_code,
        "fg_batch_no": fg_batch_no,
        "batch_production_record": batch_record.name,
        "work_order": batch_record.work_order,
        "stock_entry": batch_record.stock_entry,
        "dispatch_clearance": dispatch_clearance,
        "final_qc_release": release_doc.name if release_doc else "",
        "final_quality_inspection": release_doc.quality_inspection if release_doc else "",
        "coa_record": release_doc.coa_record if release_doc else "",
        "moisture": release_doc.moisture if release_doc else None,
        "mfi": release_doc.mfi if release_doc else None,
        "ash": release_doc.ash if release_doc else None,
        "density": release_doc.density if release_doc else None,
        "traceability_notes": "\n".join(notes),
        "rm_batches": rm_batches,
    }


def resolve_fg_reference(item_code: str | None, fg_batch_no: str | None, delivery_note: str | None):
    item_code = (item_code or "").strip()
    fg_batch_no = (fg_batch_no or "").strip()
    delivery_note = (delivery_note or "").strip()
    customer = ""

    if delivery_note:
        delivery = frappe.get_doc("Delivery Note", delivery_note)
        customer = delivery.customer
        rows = [row for row in delivery.items if not item_code or row.item_code == item_code]
        rows = [row for row in rows if row.batch_no]
        if not rows:
            frappe.throw(f"No batch-tracked item was found in Delivery Note {delivery_note}.")
        if fg_batch_no:
            rows = [row for row in rows if row.batch_no == fg_batch_no]
            if not rows:
                frappe.throw(f"FG batch {fg_batch_no} was not found in Delivery Note {delivery_note}.")
        if len(rows) > 1 and (not item_code or not fg_batch_no):
            frappe.throw(
                f"Delivery Note {delivery_note} has multiple batch-tracked items. Select FG Item and FG Batch explicitly."
            )

        selected_row = rows[0]
        item_code = selected_row.item_code
        fg_batch_no = selected_row.batch_no

    if fg_batch_no and not item_code:
        item_code = frappe.db.get_value("Batch", fg_batch_no, "item") or ""
    if not fg_batch_no or not item_code:
        frappe.throw("FG Item and FG Batch are required to trace a complaint.")

    if not customer and delivery_note:
        customer = frappe.db.get_value("Delivery Note", delivery_note, "customer") or ""
    if not delivery_note:
        delivery_note = find_delivery_note_for_batch(item_code, fg_batch_no)
        if delivery_note:
            customer = frappe.db.get_value("Delivery Note", delivery_note, "customer") or ""

    return item_code, fg_batch_no, delivery_note, customer


def find_delivery_note_for_batch(item_code: str, fg_batch_no: str) -> str:
    return (
        frappe.db.sql(
            """
            select dn.name
            from `tabDelivery Note Item` dni
            inner join `tabDelivery Note` dn on dn.name = dni.parent
            where dn.docstatus = 1
              and dni.item_code = %(item_code)s
              and dni.batch_no = %(batch_no)s
            order by dn.posting_date desc, dn.modified desc
            limit 1
            """,
            {"item_code": item_code, "batch_no": fg_batch_no},
        )
        or [[""]]
    )[0][0]


@frappe.whitelist()
def complaint_traceability_smoke_test() -> dict[str, object]:
    latest = frappe.db.sql(
        """
        select item_code, batch_no, delivery_note
        from `tabDispatch Clearance`
        where docstatus = 1
        order by modified desc
        limit 1
        """,
        as_dict=True,
    )
    if not latest:
        return {"status": "No submitted Dispatch Clearance found for complaint traceability test."}

    trace = build_traceability_data(latest[0].item_code, latest[0].batch_no, latest[0].delivery_note)

    ticket = frappe.get_doc(
        {
            "doctype": "Technical Assistance Ticket",
            "delivery_note": trace["delivery_note"],
            "item_code": trace["item_code"],
            "fg_batch_no": trace["fg_batch_no"],
            "issue_summary": "Traceability smoke test",
        }
    )
    ticket.insert(ignore_permissions=True)

    return {
        "ticket": ticket.name,
        "delivery_note": ticket.delivery_note,
        "item_code": ticket.item_code,
        "fg_batch_no": ticket.fg_batch_no,
        "batch_production_record": ticket.batch_production_record,
        "work_order": ticket.work_order,
        "stock_entry": ticket.stock_entry,
        "final_qc_release": ticket.final_qc_release,
        "final_quality_inspection": ticket.final_quality_inspection,
        "dispatch_clearance": ticket.dispatch_clearance,
        "rm_batches": [row.as_dict() for row in ticket.rm_batches],
    }
