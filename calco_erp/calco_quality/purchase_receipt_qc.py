from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


ITEM_RM_QC_FLAG_FIELD = "custom_enable_rm_qc"
PR_ITEM_QC_STATUS_FIELD = "custom_qc_status"
PR_ITEM_ACCEPTED_QTY_FIELD = "custom_accepted_qty"
PR_ITEM_REJECTED_QTY_FIELD = "custom_rejected_qty"
PR_ITEM_QI_LINK_FIELD = "custom_quality_inspection"
PR_ITEM_DEVIATION_LINK_FIELD = "custom_rm_deviation_approval"
PENDING_STATUS = "Pending"
IN_PROGRESS_STATUS = "In Progress"
ACCEPTED_STATUS = "Accepted"
HOLD_STATUS = "Hold"
REJECTED_STATUS = "Rejected"
ACCEPTED_UNDER_DEVIATION_STATUS = "Accepted Under Deviation"
RELEASED_STATUS = "Released"
DECISION_DEVIATION_REQUIRED = "Deviation Required"
DECISION_RETURN_TO_SUPPLIER = "Return to Supplier"
DECISION_HOLD_FOR_REVIEW = "Hold for Review"
QC_NOTIFICATION_ROLES = ("Quality Manager", "QC User", "Quality User")
DEVIATION_REQUESTER_ROLES = ("Purchase User", "Purchase Manager", "Quality Manager", "QC User", "Quality User", "System Manager")
DEVIATION_APPROVER_ROLES = ("Operations Head", "System Manager")
DEFAULT_RM_TEMPLATE = "Calco Incoming RM QC"
PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD = "custom_supplier_purchase_invoice_attachment"
PR_SUPPLIER_TEST_CERTIFICATE_FIELD = "custom_supplier_test_certificate_attachment"
PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD = "custom_raw_material_storage_photo"
PR_RM_EXPIRY_DATE_FIELD = "custom_rm_expiry_date"


def handle_purchase_receipt_submit(doc, method=None):
    if doc.doctype != "Purchase Receipt" or cint(doc.docstatus) != 1 or cint(doc.get("is_return")):
        return

    created_inspections = []

    for row in doc.get("items", []):
        if not row.get("item_code"):
            continue

        item_qc_config = get_item_qc_config(row.item_code)
        if not item_qc_config.required:
            continue

        inspection = ensure_purchase_receipt_quality_inspection(doc, row, item_qc_config)
        if inspection:
            created_inspections.append(inspection)

    sync_purchase_receipt_qc_statuses(doc.name)

    if created_inspections:
        notify_qc_users(doc, created_inspections)


def validate_supplier_documents_and_rm_storage(doc, method=None):
    if doc.doctype != "Purchase Receipt" or cint(doc.get("is_return")):
        return

    missing_labels = []
    required_fields = [
        (PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD, _("Supplier Purchase Invoice Attachment")),
        (PR_SUPPLIER_TEST_CERTIFICATE_FIELD, _("Supplier Test Certificate Attachment")),
        (PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD, _("Raw Material Storage Photo")),
        (PR_RM_EXPIRY_DATE_FIELD, _("RM Expiry Date")),
    ]

    for fieldname, label in required_fields:
        value = doc.get(fieldname)
        if isinstance(value, str):
            value = value.strip()
        if not value:
            missing_labels.append(label)

    if missing_labels:
        frappe.throw(
            _("Purchase Receipt cannot be submitted until these fields are filled in Supplier Documents & RM Storage: {0}").format(
                ", ".join(missing_labels)
            )
        )

    posting_date = getdate(doc.get("posting_date"))
    expiry_date = getdate(doc.get(PR_RM_EXPIRY_DATE_FIELD))
    if expiry_date < posting_date:
        frappe.throw(_("RM Expiry Date cannot be earlier than the Purchase Receipt posting date."))


def purchase_receipt_requires_rm_documents(doc):
    for row in doc.get("items", []):
        if not row.get("item_code"):
            continue
        if get_item_qc_config(row.get("item_code")).required:
            return True
    return False


@frappe.whitelist()
def get_purchase_receipt_rm_document_requirement_state(doc=None, purchase_receipt=None):
    if purchase_receipt:
        doc = frappe.get_doc("Purchase Receipt", purchase_receipt)
    else:
        doc = frappe.parse_json(doc) if doc else {}

    if not isinstance(doc, dict):
        doc = doc.as_dict()

    is_return = cint(doc.get("is_return"))
    is_draft = cint(doc.get("docstatus")) == 0
    required = bool(doc) and not is_return
    fieldnames = [
        PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD,
        PR_SUPPLIER_TEST_CERTIFICATE_FIELD,
        PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD,
        PR_RM_EXPIRY_DATE_FIELD,
    ]
    return {
        "required": bool(required and is_draft),
        "required_for_rm_purchase_receipt": bool(required),
        "is_draft": bool(is_draft),
        "is_return": bool(is_return),
        "fieldnames": fieldnames,
    }


def ensure_purchase_receipt_quality_inspection(doc, row, item_qc_config):
    existing = get_matching_quality_inspections(
        doc.name,
        row.item_code,
        row.get("batch_no"),
        purchase_receipt_item=row.name,
    )
    if existing:
        return None

    inspection = frappe.get_doc(
        {
            "doctype": "Quality Inspection",
            "inspection_type": "Incoming",
            "reference_type": "Purchase Receipt",
            "reference_name": doc.name,
            "child_row_reference": row.name,
            "item_code": row.item_code,
            "batch_no": row.get("batch_no"),
            "sample_size": get_row_sample_size(row),
            "custom_accepted_qty": flt(row.get("received_qty") or row.get("qty") or 0),
            "custom_accepted_qty_uom": row.get("stock_uom") or row.get("uom") or "",
            "inspected_by": frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Administrator",
            "quality_inspection_template": item_qc_config.template,
            "report_date": doc.get("posting_date"),
            "status": get_initial_quality_inspection_status(),
        }
    )
    inspection.flags.calco_initialize_incoming_status = True
    inspection.insert(ignore_permissions=True)
    return inspection


def get_item_qc_config(item_code):
    if not item_code:
        return frappe._dict(required=False, template="")

    fields = ["inspection_required_before_purchase", "quality_inspection_template", "item_group"]
    if doctype_has_field("Item", ITEM_RM_QC_FLAG_FIELD):
        fields.append(ITEM_RM_QC_FLAG_FIELD)

    item_details = frappe.db.get_value("Item", item_code, fields, as_dict=True) or {}
    required = cint(item_details.get(ITEM_RM_QC_FLAG_FIELD))
    required = required or cint(item_details.get("inspection_required_before_purchase"))
    required = required or (
        (item_details.get("item_group") or "").strip() == "Raw Material"
        and bool((item_details.get("quality_inspection_template") or "").strip())
    )

    template = (item_details.get("quality_inspection_template") or "").strip()
    if not template:
        template = get_rm_testing_standard_template(item_code)
    if not template and frappe.db.exists("Quality Inspection Template", DEFAULT_RM_TEMPLATE):
        template = DEFAULT_RM_TEMPLATE

    return frappe._dict(required=bool(required), template=template)


def get_rm_testing_standard_template(item_code):
    if not frappe.db.exists("DocType", "RM Testing Standard"):
        return ""

    rows = frappe.get_all(
        "RM Testing Standard",
        filters={"rm_item": item_code, "is_active": 1},
        fields=["quality_inspection_template"],
        order_by="modified desc",
        limit_page_length=1,
    )
    if not rows:
        return ""

    return (rows[0].get("quality_inspection_template") or "").strip()


def get_row_sample_size(row):
    return flt(row.get("received_qty") or row.get("qty") or 0) or 1


def get_initial_quality_inspection_status():
    status_field = frappe.get_meta("Quality Inspection").get_field("status")
    if not status_field:
        return "Draft"

    option_list = [entry.strip() for entry in (status_field.options or "").splitlines() if entry.strip()]
    options = set(option_list)
    if not options or "Draft" in options:
        return "Draft"

    if "Review Required" in options:
        return "Review Required"

    if "Pending" in options:
        return "Pending"

    # Fall back to the first configured option so the auto-created draft stays valid.
    return option_list[0] if option_list else "Draft"


def initialize_auto_created_incoming_quality_inspection_status(doc, method=None):
    if doc.doctype != "Quality Inspection":
        return
    if not getattr(doc.flags, "calco_initialize_incoming_status", False):
        return
    if (
        cint(doc.docstatus) != 0
        or doc.inspection_type != "Incoming"
        or doc.reference_type != "Purchase Receipt"
    ):
        return
    if (doc.get("custom_overall_result") or "").strip():
        return

    doc.status = get_initial_quality_inspection_status()


def get_matching_quality_inspections(
    purchase_receipt,
    item_code,
    batch_no,
    purchase_receipt_item=None,
):
    return frappe.db.sql(
        """
        select
            qi.name,
            qi.docstatus,
            qi.status,
            qi.custom_overall_result,
            qi.custom_accepted_qty,
            qi.child_row_reference,
            qi.modified
        from `tabQuality Inspection` qi
        where qi.docstatus < 2
          and qi.inspection_type = 'Incoming'
          and qi.reference_type = 'Purchase Receipt'
          and qi.reference_name = %(purchase_receipt)s
          and qi.item_code = %(item_code)s
          and ifnull(qi.batch_no, '') = %(batch_no)s
          and (%(purchase_receipt_item)s = '' or qi.child_row_reference = %(purchase_receipt_item)s)
        order by qi.docstatus desc, qi.modified desc, qi.name desc
        """,
        {
            "purchase_receipt": purchase_receipt,
            "item_code": item_code,
            "batch_no": batch_no or "",
            "purchase_receipt_item": purchase_receipt_item or "",
        },
        as_dict=True,
    )


def get_latest_submitted_rm_qc_decision(purchase_receipt, item_code, batch_no):
    if not frappe.db.exists("DocType", "RM QC Decision"):
        return None

    rows = frappe.get_all(
        "RM QC Decision",
        filters={
            "purchase_receipt": purchase_receipt,
            "item_code": item_code,
            "batch_no": batch_no or "",
            "docstatus": 1,
        },
        fields=["name", "decision", "status", "sample_qty", "quality_inspection", "modified"],
        order_by="modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def get_latest_submitted_rm_release(
    purchase_receipt,
    purchase_receipt_item,
    item_code,
    batch_no,
):
    if not frappe.db.exists("DocType", "RM Release Note"):
        return None

    rows = frappe.db.sql(
        """
        select
            name,
            status,
            release_qty,
            rm_qc_decision,
            custom_rm_deviation_approval,
            custom_purchase_receipt_item,
            modified
        from `tabRM Release Note`
        where docstatus = 1
          and custom_purchase_receipt = %(purchase_receipt)s
          and item_code = %(item_code)s
          and ifnull(batch_no, '') = %(batch_no)s
          and (
                custom_purchase_receipt_item = %(purchase_receipt_item)s
                or ifnull(custom_purchase_receipt_item, '') = ''
              )
        order by
            (custom_purchase_receipt_item = %(purchase_receipt_item)s) desc,
            modified desc,
            name desc
        limit 1
        """,
        {
            "purchase_receipt": purchase_receipt,
            "purchase_receipt_item": purchase_receipt_item,
            "item_code": item_code,
            "batch_no": batch_no or "",
        },
        as_dict=True,
    )
    return rows[0] if rows else None


def get_latest_submitted_rm_deviation(
    purchase_receipt,
    purchase_receipt_item,
    item_code,
    batch_no,
):
    if not frappe.db.exists("DocType", "RM Deviation Approval"):
        return None

    rows = frappe.get_all(
        "RM Deviation Approval",
        filters={
            "purchase_receipt": purchase_receipt,
            "purchase_receipt_item": purchase_receipt_item,
            "item_code": item_code,
            "batch_no": batch_no or "",
            "docstatus": 1,
        },
        fields=[
            "name",
            "approval_status",
            "approved_qty",
            "quality_inspection",
            "rm_qc_decision",
            "approval_remarks",
            "modified",
        ],
        order_by="modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def sync_purchase_receipt_qc_statuses(purchase_receipt):
    if not purchase_receipt or not doctype_has_field("Purchase Receipt Item", PR_ITEM_QC_STATUS_FIELD):
        return

    rows = frappe.get_all(
        "Purchase Receipt Item",
        filters={"parent": purchase_receipt, "parenttype": "Purchase Receipt"},
        fields=["name", "item_code", "batch_no", "qty", "received_qty"],
        order_by="idx asc",
        limit_page_length=0,
    )

    qc_config_cache = {}
    detail_cache = {}

    for row in rows:
        if not row.item_code:
            continue

        if row.item_code not in qc_config_cache:
            qc_config_cache[row.item_code] = get_item_qc_config(row.item_code)

        cache_key = row.name
        if cache_key not in detail_cache:
            detail_cache[cache_key] = build_pr_row_qc_details(
                purchase_receipt=purchase_receipt,
                row=row,
                item_qc_config=qc_config_cache[row.item_code],
            )

        set_purchase_receipt_item_qc_details(row.name, detail_cache[cache_key])


def sync_purchase_receipt_qc_status_from_quality_inspection(doc, method=None):
    if doc.doctype != "Quality Inspection":
        return

    if doc.inspection_type != "Incoming" or doc.reference_type != "Purchase Receipt" or not doc.reference_name:
        return

    sync_purchase_receipt_qc_statuses(doc.reference_name)


def sync_purchase_receipt_qc_status_from_rm_qc_decision(doc, method=None):
    if doc.doctype != "RM QC Decision" or not doc.purchase_receipt:
        return

    sync_purchase_receipt_qc_statuses(doc.purchase_receipt)


def sync_purchase_receipt_qc_status_from_rm_release(doc, method=None):
    if doc.doctype != "RM Release Note":
        return

    purchase_receipt = ""
    if doc.get("rm_qc_decision"):
        purchase_receipt = frappe.db.get_value("RM QC Decision", doc.rm_qc_decision, "purchase_receipt") or ""
    if purchase_receipt:
        sync_purchase_receipt_qc_statuses(purchase_receipt)


def sync_purchase_receipt_qc_status_from_rm_deviation(doc, method=None):
    if doc.doctype != "RM Deviation Approval" or not doc.purchase_receipt:
        return

    sync_purchase_receipt_qc_statuses(doc.purchase_receipt)


def build_pr_row_qc_details(purchase_receipt, row, item_qc_config):
    inspections = get_matching_quality_inspections(
        purchase_receipt,
        row.item_code,
        row.batch_no,
        purchase_receipt_item=row.name,
    )
    latest_inspection = inspections[0] if inspections else None
    latest_decision = get_latest_submitted_rm_qc_decision(purchase_receipt, row.item_code, row.batch_no)
    latest_release = get_latest_submitted_rm_release(
        purchase_receipt,
        row.name,
        row.item_code,
        row.batch_no,
    )
    latest_deviation = get_latest_submitted_rm_deviation(
        purchase_receipt,
        row.name,
        row.item_code,
        row.batch_no,
    )
    row_qty = flt(row.get("received_qty") or row.get("qty") or 0)

    if latest_release and latest_release.get("status") == RELEASED_STATUS:
        accepted_qty = flt(latest_release.get("release_qty") or row_qty)
        return frappe._dict(
            status=RELEASED_STATUS,
            accepted_qty=min(accepted_qty, row_qty),
            rejected_qty=max(row_qty - accepted_qty, 0),
            quality_inspection=get_linked_quality_inspection_name(latest_inspection, latest_decision, latest_deviation),
            deviation_approval=(latest_deviation or {}).get("name") if latest_deviation else "",
        )

    if latest_deviation and (latest_deviation.get("approval_status") or "") == "Approved":
        approved_qty = flt(latest_deviation.get("approved_qty") or row_qty)
        return frappe._dict(
            status=ACCEPTED_UNDER_DEVIATION_STATUS,
            accepted_qty=min(approved_qty, row_qty),
            rejected_qty=max(row_qty - approved_qty, 0),
            quality_inspection=get_linked_quality_inspection_name(latest_inspection, latest_decision, latest_deviation),
            deviation_approval=latest_deviation.get("name"),
        )

    if latest_decision:
        decision = (latest_decision.get("decision") or latest_decision.get("status") or "").strip()
        if decision == DECISION_HOLD_FOR_REVIEW:
            return frappe._dict(
                status=HOLD_STATUS,
                accepted_qty=0,
                rejected_qty=0,
                quality_inspection=get_linked_quality_inspection_name(latest_inspection, latest_decision, latest_deviation),
                deviation_approval=(latest_deviation or {}).get("name") if latest_deviation else "",
            )
        if decision in (DECISION_DEVIATION_REQUIRED, DECISION_RETURN_TO_SUPPLIER):
            rejected_qty = flt(latest_decision.get("sample_qty") or row_qty) or row_qty
            rejected_qty = min(rejected_qty, row_qty)
            return frappe._dict(
                status=REJECTED_STATUS,
                accepted_qty=max(row_qty - rejected_qty, 0),
                rejected_qty=rejected_qty,
                quality_inspection=get_linked_quality_inspection_name(latest_inspection, latest_decision, latest_deviation),
                deviation_approval=(latest_deviation or {}).get("name") if latest_deviation else "",
            )

    derived_from_qi = derive_purchase_receipt_qc_status(item_qc_config.required, inspections)
    if derived_from_qi.status == ACCEPTED_STATUS:
        accepted_qty = flt(derived_from_qi.accepted_qty or row_qty)
        return frappe._dict(
            status=ACCEPTED_STATUS,
            accepted_qty=min(accepted_qty, row_qty),
            rejected_qty=max(row_qty - accepted_qty, 0),
            quality_inspection=derived_from_qi.quality_inspection,
            deviation_approval="",
        )
    if derived_from_qi.status == REJECTED_STATUS:
        return frappe._dict(
            status=REJECTED_STATUS,
            accepted_qty=0,
            rejected_qty=row_qty,
            quality_inspection=derived_from_qi.quality_inspection,
            deviation_approval="",
        )
    if derived_from_qi.status == HOLD_STATUS:
        return frappe._dict(
            status=HOLD_STATUS,
            accepted_qty=0,
            rejected_qty=0,
            quality_inspection=derived_from_qi.quality_inspection,
            deviation_approval="",
        )

    return frappe._dict(
        status=derived_from_qi.status,
        accepted_qty=0,
        rejected_qty=0,
        quality_inspection=derived_from_qi.quality_inspection,
        deviation_approval="",
    )


def get_linked_quality_inspection_name(latest_inspection, latest_decision, latest_deviation):
    for candidate in (
        (latest_deviation or {}).get("quality_inspection"),
        (latest_decision or {}).get("quality_inspection"),
        (latest_inspection or {}).get("name"),
    ):
        if candidate:
            return candidate
    return ""


def sync_pr_item_linked_quality_inspection(row_name, quality_inspection):
    if doctype_has_field("Purchase Receipt Item", "quality_inspection"):
        frappe.db.set_value(
            "Purchase Receipt Item",
            row_name,
            "quality_inspection",
            quality_inspection or "",
            update_modified=False,
        )

    if doctype_has_field("Purchase Receipt Item", PR_ITEM_QI_LINK_FIELD):
        frappe.db.set_value(
            "Purchase Receipt Item",
            row_name,
            PR_ITEM_QI_LINK_FIELD,
            quality_inspection or "",
            update_modified=False,
        )


def derive_purchase_receipt_qc_status(is_qc_required, inspections):
    latest_submitted = next((row for row in inspections if cint(row.docstatus) == 1), None)
    if latest_submitted:
        overall_result = normalize_overall_result(latest_submitted.get("custom_overall_result"))
        inspection_status = (latest_submitted.get("status") or "").strip()
        if overall_result == "ACCEPTED" or inspection_status == ACCEPTED_STATUS:
            return frappe._dict(
                status=ACCEPTED_STATUS,
                accepted_qty=flt(latest_submitted.get("custom_accepted_qty") or 0),
                rejected_qty=0,
                quality_inspection=latest_submitted.name,
            )
        if overall_result == "REJECTED" or inspection_status == REJECTED_STATUS:
            return frappe._dict(
                status=REJECTED_STATUS,
                accepted_qty=0,
                rejected_qty=0,
                quality_inspection=latest_submitted.name,
            )
        if overall_result == "REVIEW REQUIRED":
            return frappe._dict(
                status=HOLD_STATUS,
                accepted_qty=0,
                rejected_qty=0,
                quality_inspection=latest_submitted.name,
            )
        return frappe._dict(
            status=IN_PROGRESS_STATUS,
            accepted_qty=0,
            rejected_qty=0,
            quality_inspection=latest_submitted.name,
        )

    in_progress = next((row for row in inspections if cint(row.docstatus) == 0 and quality_inspection_has_progress(row)), None)
    if in_progress:
        return frappe._dict(
            status=IN_PROGRESS_STATUS,
            accepted_qty=0,
            rejected_qty=0,
            quality_inspection=in_progress.name,
        )

    if inspections or is_qc_required:
        latest = inspections[0] if inspections else None
        return frappe._dict(
            status=PENDING_STATUS,
            accepted_qty=0,
            rejected_qty=0,
            quality_inspection=(latest or {}).get("name", "") if latest else "",
        )

    return frappe._dict(status="", accepted_qty=0, rejected_qty=0, quality_inspection="")


def normalize_overall_result(value: str | None) -> str:
    mapping = {
        "PASS": "ACCEPTED",
        "FAIL": "REJECTED",
        "PENDING MANUAL REVIEW": "REVIEW REQUIRED",
    }
    normalized = (value or "").strip().upper()
    return mapping.get(normalized, normalized)


def quality_inspection_has_progress(row):
    current_status = (row.get("status") or "").strip().lower()
    overall_result = (row.get("custom_overall_result") or "").strip()
    return bool(overall_result or current_status not in ("", "draft"))


def set_purchase_receipt_item_qc_details(row_name, details):
    target_values = {
        PR_ITEM_QC_STATUS_FIELD: details.status or "",
        PR_ITEM_ACCEPTED_QTY_FIELD: flt(details.accepted_qty),
        PR_ITEM_REJECTED_QTY_FIELD: flt(details.rejected_qty),
    }
    if doctype_has_field("Purchase Receipt Item", "rejected_qty"):
        target_values["rejected_qty"] = flt(details.rejected_qty)

    existing = frappe.db.get_value("Purchase Receipt Item", row_name, list(target_values.keys()), as_dict=True) or {}
    dirty = False
    for fieldname, value in target_values.items():
        current = existing.get(fieldname)
        if isinstance(value, float):
            if flt(current) == flt(value):
                continue
        elif (current or "") == (value or ""):
            continue
        frappe.db.set_value("Purchase Receipt Item", row_name, fieldname, value, update_modified=False)
        dirty = True

    quality_inspection = details.quality_inspection or ""
    linked_fields = []
    if doctype_has_field("Purchase Receipt Item", "quality_inspection"):
        linked_fields.append("quality_inspection")
    if doctype_has_field("Purchase Receipt Item", PR_ITEM_QI_LINK_FIELD):
        linked_fields.append(PR_ITEM_QI_LINK_FIELD)

    if linked_fields:
        link_existing = frappe.db.get_value("Purchase Receipt Item", row_name, linked_fields, as_dict=True) or {}
        for fieldname in linked_fields:
            if (link_existing.get(fieldname) or "") == quality_inspection:
                continue
            frappe.db.set_value("Purchase Receipt Item", row_name, fieldname, quality_inspection, update_modified=False)
            dirty = True

    deviation_name = details.deviation_approval or ""
    if doctype_has_field("Purchase Receipt Item", PR_ITEM_DEVIATION_LINK_FIELD):
        current_deviation = frappe.db.get_value(
            "Purchase Receipt Item",
            row_name,
            PR_ITEM_DEVIATION_LINK_FIELD,
        ) or ""
        if current_deviation != deviation_name:
            frappe.db.set_value(
                "Purchase Receipt Item",
                row_name,
                PR_ITEM_DEVIATION_LINK_FIELD,
                deviation_name,
                update_modified=False,
            )
            dirty = True

    return dirty


def notify_qc_users(purchase_receipt, inspections):
    users = get_qc_notification_users()
    if not users or not frappe.db.exists("DocType", "Notification Log"):
        return

    sender = frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Administrator"

    for inspection in inspections:
        message = (
            f"New RM QC pending for PR {purchase_receipt.name}, "
            f"Item {inspection.item_code}, Batch {inspection.batch_no or 'No Batch'}"
        )
        for user in users:
            notification = frappe.get_doc(
                {
                    "doctype": "Notification Log",
                    "subject": message,
                    "email_content": message,
                    "for_user": user,
                    "type": "Alert",
                    "document_type": "Quality Inspection",
                    "document_name": inspection.name,
                    "from_user": sender,
                }
            )
            notification.insert(ignore_permissions=True)


def get_qc_notification_users():
    role_rows = frappe.get_all(
        "Has Role",
        filters={"role": ("in", QC_NOTIFICATION_ROLES), "parenttype": "User"},
        fields=["parent"],
        limit_page_length=0,
    )
    if not role_rows:
        return []

    users = sorted({row.parent for row in role_rows if row.parent})
    active_users = frappe.get_all(
        "User",
        filters={
            "name": ("in", users),
            "enabled": 1,
            "user_type": "System User",
        },
        pluck="name",
        limit_page_length=0,
    )
    return active_users


def validate_rejected_qty_purchase_return(doc, method=None):
    if doc.doctype != "Purchase Receipt" or not cint(doc.get("is_return")) or not doc.get("return_against"):
        return

    source_receipt = doc.get("return_against")
    source_rows = get_purchase_receipt_row_map(source_receipt)
    for row in doc.get("items", []):
        source_row = resolve_source_purchase_receipt_row(source_rows, row)
        if not source_row:
            continue

        rejected_qty = flt(source_row.get(PR_ITEM_REJECTED_QTY_FIELD) or source_row.get("rejected_qty") or 0)
        if rejected_qty <= 0:
            frappe.throw(
                _("Purchase Return is only allowed for rejected RM quantity. Row {0} has no rejected quantity.").format(
                    row.idx or row.name
                )
            )

        available_qty, available_warehouse = get_blocked_stock_for_return(
            source_receipt,
            source_row.get("item_code"),
            source_row.get("batch_no"),
        )
        requested_qty = abs(flt(row.get("qty") or 0))
        allowed_qty = min(rejected_qty, available_qty)
        if requested_qty - allowed_qty > 1e-9:
            frappe.throw(
                _(
                    "Purchase Return qty for item {0}, batch {1} cannot exceed rejected/available blocked stock ({2})."
                ).format(source_row.get("item_code"), source_row.get("batch_no") or _("No Batch"), allowed_qty)
            )

        if available_warehouse and row.get("warehouse") != available_warehouse:
            row.warehouse = available_warehouse


@frappe.whitelist()
def create_rm_deviation_approval(purchase_receipt, purchase_receipt_item=None):
    enforce_deviation_request_roles()
    rejected_rows = get_rejected_purchase_receipt_rows(purchase_receipt)
    if not rejected_rows:
        frappe.throw(_("No rejected Purchase Receipt rows are available for deviation approval."))

    if purchase_receipt_item:
        row = next((entry for entry in rejected_rows if entry.name == purchase_receipt_item), None)
        if not row:
            frappe.throw(_("The selected Purchase Receipt row is not rejected."))
    elif len(rejected_rows) == 1:
        row = rejected_rows[0]
    else:
        frappe.throw(_("Select a rejected Purchase Receipt row before creating a deviation approval."))

    existing = get_existing_rm_deviation_approval(row.name)
    if existing:
        return existing

    purchase_receipt_doc = frappe.get_doc("Purchase Receipt", purchase_receipt)
    quality_inspection = row.get("quality_inspection") or row.get(PR_ITEM_QI_LINK_FIELD) or ""
    inspection_context = get_quality_inspection_context(quality_inspection)
    operations_head = get_default_operations_head()

    approval = frappe.get_doc(
        {
            "doctype": "RM Deviation Approval",
            "purchase_receipt": purchase_receipt,
            "purchase_receipt_item": row.name,
            "purchase_order": row.get("purchase_order"),
            "supplier": purchase_receipt_doc.get("supplier"),
            "item_code": row.item_code,
            "item_name": row.get("item_name"),
            "batch_no": row.batch_no,
            "warehouse": row.get("warehouse"),
            "quality_inspection": quality_inspection,
            "qc_status": row.get(PR_ITEM_QC_STATUS_FIELD),
            "received_qty": flt(row.get("received_qty") or row.get("qty") or 0),
            "accepted_qty": flt(row.get(PR_ITEM_ACCEPTED_QTY_FIELD) or 0),
            "rejected_qty": flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0),
            "approved_qty": flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0),
            "rate": flt(row.get("rate") or 0),
            "amount": flt(row.get("amount") or 0),
            "rejection_reason_from_qc": inspection_context.get("rejection_reason_from_qc"),
            "failed_parameters": inspection_context.get("failed_parameters"),
            "approval_status": "Draft",
            "operations_head": operations_head,
        }
    )
    approval.insert(ignore_permissions=True)
    return approval.name


def get_existing_rm_deviation_approval(purchase_receipt_item):
    if not frappe.db.exists("DocType", "RM Deviation Approval"):
        return ""

    rows = frappe.get_all(
        "RM Deviation Approval",
        filters={"purchase_receipt_item": purchase_receipt_item, "docstatus": ("<", 2)},
        fields=["name", "approval_status"],
        order_by="modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0].name if rows else ""


def get_rejected_purchase_receipt_rows(purchase_receipt):
    rows = [
        row
        for row in get_purchase_receipt_row_map(purchase_receipt).values()
        if (row.get(PR_ITEM_QC_STATUS_FIELD) or "").strip() == REJECTED_STATUS
        and flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0) > 0
        and not has_completed_purchase_return_for_rejected_qty(purchase_receipt, row.name)
    ]
    rows.sort(key=lambda row: cint(row.get("idx") or 0))
    return rows


def get_quality_inspection_context(quality_inspection):
    context = frappe._dict(rejection_reason_from_qc="", failed_parameters="")
    if not quality_inspection or not frappe.db.exists("Quality Inspection", quality_inspection):
        return context

    inspection = frappe.get_doc("Quality Inspection", quality_inspection)
    remarks_fields = [
        "remarks",
        "report",
        "custom_rejection_reason",
        "custom_rejection_remarks",
        "custom_remarks",
    ]
    for fieldname in remarks_fields:
        value = (inspection.get(fieldname) or "").strip() if hasattr(inspection, "get") else ""
        if value:
            context.rejection_reason_from_qc = value
            break

    failed_lines = []
    for row in inspection.get("readings") or []:
        status = (row.get("status") or "").strip().lower()
        if status not in ("rejected", "fail", "failed"):
            continue
        label = row.get("specification") or row.get("parameter") or row.get("parameter_group") or _("Parameter")
        reading = row.get("reading_value") or row.get("numeric") or row.get("value") or ""
        failed_lines.append(f"{label}: {reading}".strip(": "))
    context.failed_parameters = "\n".join(failed_lines)
    return context


def get_default_operations_head():
    if not frappe.db.exists("DocType", "Has Role"):
        return ""

    role_rows = frappe.get_all(
        "Has Role",
        filters={"role": "Operations Head", "parenttype": "User"},
        fields=["parent"],
        order_by="modified desc",
        limit_page_length=0,
    )
    users = [row.parent for row in role_rows if row.parent]
    if not users:
        return ""

    active_users = frappe.get_all(
        "User",
        filters={"name": ("in", users), "enabled": 1, "user_type": "System User"},
        pluck="name",
        limit_page_length=1,
    )
    return active_users[0] if active_users else users[0]


def has_completed_purchase_return_for_rejected_qty(purchase_receipt, purchase_receipt_item):
    returned_qty = get_completed_purchase_return_qty(purchase_receipt, purchase_receipt_item)
    source_row = get_purchase_receipt_row_map(purchase_receipt).get(purchase_receipt_item) or {}
    rejected_qty = flt(source_row.get(PR_ITEM_REJECTED_QTY_FIELD) or source_row.get("rejected_qty") or 0)
    return rejected_qty > 0 and returned_qty + 1e-9 >= rejected_qty


def get_completed_purchase_return_qty(purchase_receipt, purchase_receipt_item):
    if not purchase_receipt_item:
        return 0.0

    rows = frappe.get_all(
        "Purchase Receipt Item",
        filters={
            "purchase_receipt_item": purchase_receipt_item,
            "docstatus": 1,
            "parenttype": "Purchase Receipt",
        },
        fields=["qty", "parent"],
        limit_page_length=0,
    )
    returned_qty = 0.0
    for row in rows:
        if frappe.db.get_value("Purchase Receipt", row.parent, "is_return"):
            returned_qty += abs(flt(row.qty))
    return returned_qty


@frappe.whitelist()
def get_purchase_receipt_rejected_action_state(purchase_receipt):
    rejected_rows = get_rejected_purchase_receipt_rows(purchase_receipt)
    rows = []
    has_approved_deviation = False
    for row in rejected_rows:
        latest_decision = get_latest_submitted_rm_qc_decision(
            purchase_receipt,
            row.item_code,
            row.batch_no,
        )
        if not latest_decision:
            continue

        decision = (latest_decision.get("decision") or latest_decision.get("status") or "").strip()
        deviation_name = get_existing_rm_deviation_approval(row.name)
        deviation_status = ""
        if deviation_name and frappe.db.exists("RM Deviation Approval", deviation_name):
            deviation_status = frappe.db.get_value("RM Deviation Approval", deviation_name, "approval_status") or ""
            if deviation_status == "Approved":
                has_approved_deviation = True
        rows.append(
            {
                "name": row.name,
                "item_code": row.item_code,
                "batch_no": row.batch_no,
                "rejected_qty": flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0),
                "decision": decision,
                "rm_qc_decision": latest_decision.get("name"),
                "deviation_name": deviation_name,
                "deviation_status": deviation_status,
            }
        )

    return {
        "rows": rows,
        "show_deviation": any(
            row.get("decision") == DECISION_DEVIATION_REQUIRED and not row.get("deviation_name") for row in rows
        ),
        "show_purchase_return": any(
            row.get("decision") == DECISION_RETURN_TO_SUPPLIER
            or row.get("deviation_status") == "Rejected"
            for row in rows
        ),
        "has_approved_deviation": has_approved_deviation,
    }


@frappe.whitelist()
def get_purchase_receipt_quality_inspection_action_state(purchase_receipt):
    if not purchase_receipt:
        return {"rows": []}

    rows = []
    for row in frappe.get_all(
        "Purchase Receipt Item",
        filters={"parent": purchase_receipt, "parenttype": "Purchase Receipt"},
        fields=[
            "name",
            "idx",
            "item_code",
            "item_name",
            "batch_no",
            "qty",
            "received_qty",
            "quality_inspection",
            PR_ITEM_QI_LINK_FIELD,
            PR_ITEM_QC_STATUS_FIELD,
        ],
        order_by="idx asc",
        limit_page_length=0,
    ):
        item_qc_config = get_item_qc_config(row.item_code)
        if not item_qc_config.required:
            continue

        linked_qi = row.get("quality_inspection") or row.get(PR_ITEM_QI_LINK_FIELD) or ""
        inspection_docstatus = None
        inspection_status = ""
        if linked_qi and frappe.db.exists("Quality Inspection", linked_qi):
            inspection_docstatus, inspection_status = frappe.db.get_value(
                "Quality Inspection",
                linked_qi,
                ["docstatus", "status"],
            ) or (None, "")

        row_qty = flt(row.get("received_qty") or row.get("qty") or 0)
        qc_status = (row.get(PR_ITEM_QC_STATUS_FIELD) or "").strip()
        action_type = "create_new"
        message = _("Create Incoming Quality Inspection from this Purchase Receipt row.")

        if linked_qi:
            action_type = "open_existing"
            if cint(inspection_docstatus) == 0:
                message = _("Open the existing draft Incoming Quality Inspection and continue editing it.")
            elif cint(inspection_docstatus) == 1:
                message = _("Open the submitted Incoming Quality Inspection linked to this Purchase Receipt row.")
            else:
                action_type = "blocked"
                message = _("The linked Incoming Quality Inspection is cancelled. Create a new inspection from the Purchase Receipt row.")
        elif qc_status in (RELEASED_STATUS, REJECTED_STATUS, ACCEPTED_UNDER_DEVIATION_STATUS):
            action_type = "blocked"
            message = _("This Purchase Receipt row is already finalized for RM QC and no longer pending inspection.")

        rows.append(
            {
                "name": row.name,
                "idx": row.idx,
                "item_code": row.item_code,
                "item_name": row.get("item_name"),
                "batch_no": row.batch_no or "",
                "qty": row_qty,
                "qc_status": qc_status,
                "quality_inspection": linked_qi,
                "inspection_docstatus": cint(inspection_docstatus) if inspection_docstatus is not None else None,
                "inspection_status": inspection_status or "",
                "action_type": action_type,
                "message": message,
            }
        )

    return {"rows": rows}


@frappe.whitelist()
def make_rejected_qty_purchase_return(source_name, target_doc=None):
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return

    return_doc = make_purchase_return(source_name, target_doc)
    source_rows = get_purchase_receipt_row_map(source_name)
    retained_rows = []

    for row in list(return_doc.get("items", [])):
        source_row = source_rows.get(row.get("purchase_receipt_item"))
        if not source_row:
            continue

        rejected_qty = flt(source_row.get(PR_ITEM_REJECTED_QTY_FIELD) or source_row.get("rejected_qty") or 0)
        if rejected_qty <= 0:
            continue

        available_qty, available_warehouse = get_blocked_stock_for_return(
            source_name,
            source_row.get("item_code"),
            source_row.get("batch_no"),
        )
        allowed_qty = min(rejected_qty, available_qty)
        if allowed_qty <= 0:
            continue

        negative_qty = -1 * allowed_qty
        row.qty = negative_qty
        if hasattr(row, "received_qty"):
            row.received_qty = negative_qty
        if hasattr(row, "stock_qty"):
            row.stock_qty = negative_qty
        if hasattr(row, "rejected_qty"):
            row.rejected_qty = 0
        if available_warehouse:
            row.warehouse = available_warehouse
        if hasattr(row, "rejected_warehouse"):
            row.rejected_warehouse = ""
        if hasattr(row, "return_qty_from_rejected_warehouse"):
            row.return_qty_from_rejected_warehouse = 1 if is_rejected_warehouse(source_name, available_warehouse) else 0

        retained_rows.append(row)

    return_doc.set("items", retained_rows)
    if not return_doc.get("items"):
        frappe.throw(_("No rejected RM quantity is available to return for Purchase Receipt {0}.").format(source_name))

    return_doc.run_method("calculate_taxes_and_totals")
    return return_doc


def is_rejected_warehouse(purchase_receipt, warehouse):
    if not warehouse:
        return False
    from calco_erp.calco_quality.rm_warehouse_flow import get_rm_flow_warehouses

    company = frappe.db.get_value("Purchase Receipt", purchase_receipt, "company") or ""
    return warehouse == get_rm_flow_warehouses(company).get("rejected")


def resolve_source_purchase_receipt_row(source_rows, row):
    source_row = source_rows.get(row.get("purchase_receipt_item"))
    if source_row:
        return source_row

    for candidate in source_rows.values():
        if candidate.get("item_code") != row.get("item_code"):
            continue
        if (candidate.get("batch_no") or "") != (row.get("batch_no") or ""):
            continue
        return candidate
    return None


def get_purchase_receipt_row_map(purchase_receipt):
    fields = [
        "name",
        "idx",
        "item_code",
        "item_name",
        "batch_no",
        "qty",
        "received_qty",
        "warehouse",
        "rejected_warehouse",
        "rejected_qty",
        "material_request",
        "material_request_item",
        "purchase_order",
        "purchase_order_item",
        "rate",
        "amount",
        PR_ITEM_REJECTED_QTY_FIELD,
        PR_ITEM_ACCEPTED_QTY_FIELD,
        PR_ITEM_QC_STATUS_FIELD,
    ]
    if doctype_has_field("Purchase Receipt Item", PR_ITEM_QI_LINK_FIELD):
        fields.append(PR_ITEM_QI_LINK_FIELD)
    if doctype_has_field("Purchase Receipt Item", "quality_inspection"):
        fields.append("quality_inspection")
    if doctype_has_field("Purchase Receipt Item", PR_ITEM_DEVIATION_LINK_FIELD):
        fields.append(PR_ITEM_DEVIATION_LINK_FIELD)

    rows = frappe.get_all(
        "Purchase Receipt Item",
        filters={"parent": purchase_receipt, "parenttype": "Purchase Receipt"},
        fields=fields,
        limit_page_length=0,
    )
    return {row.name: row for row in rows}


def get_blocked_stock_for_return(purchase_receipt, item_code, batch_no):
    from calco_erp.calco_quality.rm_warehouse_flow import get_batch_balance, get_rm_flow_warehouses

    company = frappe.db.get_value("Purchase Receipt", purchase_receipt, "company") or ""
    warehouses = get_rm_flow_warehouses(company)
    latest_decision = get_latest_submitted_rm_qc_decision(purchase_receipt, item_code, batch_no)

    if not latest_decision:
        source_row = frappe.get_all(
            "Purchase Receipt Item",
            filters={
                "parent": purchase_receipt,
                "parenttype": "Purchase Receipt",
                "item_code": item_code,
                "batch_no": batch_no or "",
            },
            fields=["warehouse", PR_ITEM_REJECTED_QTY_FIELD, "rejected_qty"],
            order_by="idx asc",
            limit_page_length=1,
        )
        if source_row:
            row = source_row[0]
            fallback_qty = flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0)
            fallback_warehouse = row.get("warehouse") or warehouses.get("quarantine") or ""
            if fallback_qty > 0 and fallback_warehouse:
                return fallback_qty, fallback_warehouse

    candidates = [
        warehouses.get("rejected"),
        warehouses.get("quarantine"),
        warehouses.get("hold"),
    ]
    best_qty = 0.0
    best_warehouse = ""
    for warehouse in candidates:
        if not warehouse:
            continue
        qty = flt(get_batch_balance(item_code, batch_no, warehouse))
        if qty > best_qty + 1e-9:
            best_qty = qty
            best_warehouse = warehouse

    if best_qty <= 0:
        source_row = frappe.get_all(
            "Purchase Receipt Item",
            filters={
                "parent": purchase_receipt,
                "parenttype": "Purchase Receipt",
                "item_code": item_code,
                "batch_no": batch_no or "",
            },
            fields=["warehouse", "rejected_warehouse", PR_ITEM_REJECTED_QTY_FIELD, "rejected_qty"],
            order_by="idx asc",
            limit_page_length=1,
        )
        if source_row:
            row = source_row[0]
            fallback_qty = flt(row.get(PR_ITEM_REJECTED_QTY_FIELD) or row.get("rejected_qty") or 0)
            fallback_warehouse = (
                row.get("rejected_warehouse")
                or row.get("warehouse")
                or warehouses.get("rejected")
                or warehouses.get("quarantine")
                or ""
            )
            if fallback_qty > 0 and fallback_warehouse:
                return fallback_qty, fallback_warehouse

    return best_qty, best_warehouse


def enforce_deviation_request_roles():
    user_roles = set(frappe.get_roles(frappe.session.user))
    if not user_roles.intersection(DEVIATION_REQUESTER_ROLES):
        frappe.throw(
            _("Only Purchase or Quality users can create RM Deviation Approval."),
            exc=frappe.PermissionError,
        )


def enforce_deviation_submit_roles():
    user_roles = set(frappe.get_roles(frappe.session.user))
    if not user_roles.intersection(DEVIATION_APPROVER_ROLES):
        frappe.throw(
            _("Only an Operations Head or System Manager can approve or reject RM Deviation Approval."),
            exc=frappe.PermissionError,
        )


def doctype_has_field(doctype, fieldname):
    return frappe.get_meta(doctype).has_field(fieldname)
