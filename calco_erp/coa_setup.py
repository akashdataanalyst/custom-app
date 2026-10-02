from __future__ import annotations

from pathlib import Path

import frappe
from frappe.utils.file_manager import save_file
from frappe.utils.weasyprint import import_weasyprint

from calco_erp.utils.dependencies import get_final_qc_release


COA_PRINT_FORMAT = "COA Certificate"
COA_PRINT_MODULE = "Calco Dispatch"
COA_INLINE_CSS = """
.coa-sheet { width: 760px; padding: 18px; font-size: 12px; font-family: Arial, sans-serif; }
.coa-title { font-size: 20px; font-weight: 700; margin-bottom: 10px; }
.coa-subtitle { margin-bottom: 14px; color: #444; }
.coa-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
.coa-table td, .coa-table th { border: 1px solid #333; padding: 6px 8px; }
.coa-table th { background: #f3f3f3; }
.coa-meta { margin-bottom: 8px; }
"""


def template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "print_formats" / "coa_certificate.html"


def ensure_coa_setup():
    ensure_coa_print_format()
    frappe.clear_cache()


def ensure_coa_print_format():
    html = template_path().read_text(encoding="utf-8")
    if frappe.db.exists("Print Format", COA_PRINT_FORMAT):
        doc = frappe.get_doc("Print Format", COA_PRINT_FORMAT)
    else:
        doc = frappe.new_doc("Print Format")
        doc.name = COA_PRINT_FORMAT

    doc.print_format_for = "DocType"
    doc.doc_type = "COA Record"
    doc.module = COA_PRINT_MODULE
    doc.standard = "No"
    doc.custom_format = 1
    doc.disabled = 0
    doc.print_format_type = "Jinja"
    doc.raw_printing = 0
    doc.html = html
    doc.css = COA_INLINE_CSS

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def get_or_create_coa_record(final_qc_release_name: str) -> str:
    release = frappe.get_doc("Final QC Release", final_qc_release_name)
    if release.coa_record and frappe.db.exists("COA Record", release.coa_record):
        return release.coa_record
    release.create_or_update_coa()
    release.reload()
    return release.coa_record


def attach_coa_pdf_to_delivery_note(doc, method=None):
    attached_files = []
    for row in doc.get("items", []):
        if not row.get("item_code") or not row.get("batch_no"):
            continue

        final_qc_release = get_final_qc_release(row.item_code, row.batch_no)
        if not final_qc_release:
            continue

        coa_name = get_or_create_coa_record(final_qc_release)
        coa_doc = frappe.get_doc("COA Record", coa_name)

        file_stem = f"COA-{row.item_code}-{row.batch_no}"
        filename = f"{file_stem}.pdf"
        remove_existing_delivery_note_attachment(doc.name, filename)

        pdf_payload = {
            "fname": f"{file_stem}.pdf",
            "fcontent": render_coa_pdf(coa_doc),
        }

        file_doc = save_file(
            pdf_payload["fname"],
            pdf_payload["fcontent"],
            "Delivery Note",
            doc.name,
            is_private=0,
        )
        attached_files.append(file_doc.name)

    return attached_files


def render_coa_pdf(coa_doc) -> bytes:
    HTML, _CSS = import_weasyprint()
    body_html = frappe.render_template(template_path().read_text(encoding="utf-8"), {"doc": coa_doc, "frappe": frappe})
    html = f"""
    <html>
      <head>
        <meta charset="utf-8">
        <style>{COA_INLINE_CSS}</style>
      </head>
      <body>{body_html}</body>
    </html>
    """
    return HTML(string=html, base_url=frappe.utils.get_url()).write_pdf()


def remove_existing_delivery_note_attachment(delivery_note: str, filename: str) -> None:
    existing = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Delivery Note",
            "attached_to_name": delivery_note,
            "file_name": filename,
        },
        pluck="name",
        limit_page_length=100,
    )
    for file_name in existing:
        frappe.delete_doc("File", file_name, ignore_permissions=True)


def coa_status(delivery_note: str | None = None) -> dict[str, object]:
    filters = {"attached_to_doctype": "Delivery Note"}
    if delivery_note:
        filters["attached_to_name"] = delivery_note
    return {
        "print_format_exists": bool(frappe.db.exists("Print Format", COA_PRINT_FORMAT)),
        "coa_record_count": frappe.db.count("COA Record"),
        "delivery_note_coa_attachments": frappe.get_all(
            "File",
            filters=filters,
            fields=["name", "attached_to_name", "file_name", "file_url"],
            limit_page_length=100,
        ),
    }
