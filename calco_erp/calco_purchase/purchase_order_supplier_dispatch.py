from __future__ import annotations

from functools import partial
from html import escape
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.contacts.doctype.contact.contact import get_default_contact
from frappe.core.doctype.communication.email import _make as make_communication
from frappe.utils import cint

CC_RECIPIENTS = (
    "harsh.gupta@calco.in",
    "tirthankar.bhowmick@calco.in",
    "purchase@calco.in",
    "rohit.gupta@calco.in",
)
PRINT_FORMAT = "Purchase Order Custom"
PDF_INTERNAL_URL_CONFIG = "calco_pdf_internal_url"
EMAIL_BODY = """Dear Supplier,

Please find the attached Purchase Order for your review.

We kindly ask you to confirm receipt and acceptance of this order by replying to this email.

If we do not receive your confirmation within 7 days, we can cancel the Purchase Order.

Thank you for your prompt attention to this matter."""


def schedule_automatic_supplier_dispatch(doc, method=None):
    """Create one durable outbox record and enqueue dispatch after PO commit."""
    if cint(doc.docstatus) != 1:
        return
    try:
        _lock_purchase_order_dispatch(doc.name)
        if _find_automatic_communication(doc.name):
            return
        recipient = resolve_supplier_email(doc)
        if not recipient:
            _create_failure_communication(doc, "Supplier email address not available.")
            return
        communication = _create_dispatch_communication(doc, recipient, automatic=True)
        _enqueue_dispatch(doc.name, communication, automatic=True)
    except Exception:
        frappe.log_error(
            title=f"Purchase Order supplier dispatch scheduling failed: {doc.name}",
            message=frappe.get_traceback(),
        )


@frappe.whitelist()
def resend_purchase_order_to_supplier(purchase_order: str):
    doc = frappe.get_doc("Purchase Order", purchase_order)
    doc.check_permission("email")
    if cint(doc.docstatus) != 1:
        frappe.throw(_("Only a submitted Purchase Order can be resent."))
    recipient = resolve_supplier_email(doc)
    if not recipient:
        frappe.throw(_("Supplier email address not available."))
    communication = _create_dispatch_communication(doc, recipient, automatic=False)
    _enqueue_dispatch(doc.name, communication, automatic=False)
    return {"communication": communication, "status": "Queued"}


def resolve_supplier_email(purchase_order) -> str | None:
    """Resolve the current email from the linked standard Contact master."""
    contact_name = purchase_order.get("contact_person") or get_default_contact(
        "Supplier", purchase_order.supplier
    )
    if not contact_name:
        return None
    return frappe.db.get_value("Contact", contact_name, "email_id") or None


def _cc_recipients(to_recipient: str | None = None) -> list[str]:
    seen = {(to_recipient or "").strip().casefold()}
    recipients = []
    for value in CC_RECIPIENTS:
        address = (value or "").strip()
        key = address.casefold()
        if not address or key in seen:
            continue
        seen.add(key)
        recipients.append(address)
    return recipients


def dispatch_purchase_order_email(purchase_order: str, communication: str, automatic: bool = True):
    lock_name = f"calco:po-supplier-dispatch:{communication}"
    try:
        with _dispatch_lock(lock_name):
            if _email_queue_exists(communication):
                return {"status": "Already Queued", "communication": communication}
            doc = frappe.get_doc("Purchase Order", purchase_order)
            if cint(doc.docstatus) != 1:
                return _mark_dispatch_failure(communication, "Purchase Order is no longer submitted.")
            recipient = resolve_supplier_email(doc)
            if not recipient:
                return _mark_dispatch_failure(communication, "Supplier email address not available.")
            attachment = _build_pdf_attachment(doc)
            comm = frappe.get_doc("Communication", communication)
            comm.db_set("recipients", recipient, update_modified=False)
            comm.db_set("cc", ", ".join(_cc_recipients(recipient)), update_modified=False)
            frappe.sendmail(
                recipients=[recipient],
                cc=_cc_recipients(recipient),
                subject=_subject(doc.name),
                message=_email_body_html(),
                attachments=[attachment],
                reference_doctype="Purchase Order",
                reference_name=doc.name,
                communication=communication,
                delayed=False,
                now=True,
                expose_recipients="header",
            )
            comm.db_set("delivery_status", "Scheduled", update_modified=False)
            return {"status": "Queued", "communication": communication}
    except Exception as exc:
        frappe.log_error(
            title=f"Purchase Order supplier dispatch failed: {purchase_order}",
            message=frappe.get_traceback(),
        )
        return _mark_dispatch_failure(communication, str(exc) or "Email dispatch failed.")


def _create_dispatch_communication(doc, recipient: str, *, automatic: bool) -> str:
    result = make_communication(
        doctype="Purchase Order",
        name=doc.name,
        content=_email_body_html(),
        subject=_subject(doc.name),
        recipients=[recipient],
        cc=_cc_recipients(recipient),
        communication_medium="Email",
        sent_or_received="Sent",
        send_email=False,
        communication_type="Automated Message" if automatic else "Communication",
    )
    communication = result["name"]
    values = {"delivery_status": "Scheduled"}
    if automatic:
        values["message_id"] = _automatic_message_id(doc.name)
    frappe.db.set_value("Communication", communication, values, update_modified=False)
    return communication


def _create_failure_communication(doc, reason: str) -> str:
    result = make_communication(
        doctype="Purchase Order",
        name=doc.name,
        content=f"{_email_body_html()}<hr><strong>Dispatch failed:</strong> {escape(reason)}",
        subject=_subject(doc.name),
        recipients=None,
        cc=_cc_recipients(),
        communication_medium="Email",
        sent_or_received="Sent",
        send_email=False,
        communication_type="Automated Message",
    )
    communication = result["name"]
    frappe.db.set_value(
        "Communication",
        communication,
        {"delivery_status": "Error", "message_id": _automatic_message_id(doc.name)},
        update_modified=False,
    )
    frappe.log_error(title=f"Purchase Order supplier dispatch failed: {doc.name}", message=reason)
    return communication


def _enqueue_dispatch(purchase_order: str, communication: str, *, automatic: bool):
    frappe.db.after_commit.add(
        partial(
            _enqueue_dispatch_after_commit,
            purchase_order,
            communication,
            automatic=automatic,
        )
    )


def _enqueue_dispatch_after_commit(
    purchase_order: str, communication: str, *, automatic: bool
):
    try:
        frappe.enqueue(
            "calco_erp.calco_purchase.purchase_order_supplier_dispatch.dispatch_purchase_order_email",
            queue="short",
            timeout=300,
            deduplicate=True,
            job_id=f"calco-po-supplier-dispatch-{communication}",
            purchase_order=purchase_order,
            communication=communication,
            automatic=automatic,
        )
    except Exception as exc:
        reason = f"Email queue unavailable: {exc}"
        _mark_dispatch_failure(communication, reason)
        frappe.log_error(
            title=f"Purchase Order supplier dispatch queue failed: {purchase_order}",
            message=frappe.get_traceback(),
        )
        _commit_failure_audit()


def _dispatch_lock(lock_name: str):
    return frappe.cache.lock(lock_name, timeout=300, blocking_timeout=1)


def _commit_failure_audit():
    frappe.db.commit()


def _find_automatic_communication(purchase_order: str) -> str | None:
    return frappe.db.get_value(
        "Communication",
        {
            "reference_doctype": "Purchase Order",
            "reference_name": purchase_order,
            "communication_medium": "Email",
            "message_id": _automatic_message_id(purchase_order),
        },
        "name",
    )


def _lock_purchase_order_dispatch(purchase_order: str):
    frappe.db.sql(
        "select name from `tabPurchase Order` where name = %s for update",
        purchase_order,
    )


def _email_queue_exists(communication: str) -> bool:
    return bool(frappe.db.exists("Email Queue", {"communication": communication}))


def _build_pdf_attachment(doc) -> dict:
    internal_url = _get_pdf_internal_url()
    original_host_name = frappe.conf.get("host_name")
    try:
        frappe.conf.host_name = internal_url
        pdf = frappe.get_print(
            "Purchase Order", doc.name, print_format=PRINT_FORMAT, as_pdf=True
        )
    finally:
        frappe.conf.host_name = original_host_name
    return {"fname": f"{_subject(doc.name)}.pdf", "fcontent": pdf}


def _get_pdf_internal_url() -> str:
    internal_url = (frappe.conf.get(PDF_INTERNAL_URL_CONFIG) or "").rstrip("/")
    parsed = urlparse(internal_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        frappe.throw(
            _("Set {0} to a valid internal HTTP(S) URL before dispatching Purchase Orders.").format(
                PDF_INTERNAL_URL_CONFIG
            )
        )
    return internal_url


def _mark_dispatch_failure(communication: str, reason: str) -> dict:
    if frappe.db.exists("Communication", communication):
        comm = frappe.get_doc("Communication", communication)
        comm.db_set("delivery_status", "Error", update_modified=False)
        comm.db_set(
            "content",
            f"{comm.content}<hr><strong>Dispatch failed:</strong> {escape(reason)}",
            update_modified=False,
        )
    return {"status": "Failed", "communication": communication, "reason": reason}


def _subject(purchase_order: str) -> str:
    return f"Purchase Order - {purchase_order}"


def _automatic_message_id(purchase_order: str) -> str:
    return f"calco-po-auto-{purchase_order}@calco.local"


def _email_body_html() -> str:
    return "<div>" + escape(EMAIL_BODY).replace("\n", "<br>") + "</div>"
