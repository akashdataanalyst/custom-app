from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def _coerce_filters(filters):
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    return frappe._dict(filters or {})


def _get_pending_rm_line_condition(pr_alias="pr", pri_alias="pri"):
    return f"""
        and not exists (
            select 1
            from `tabQuality Inspection` qi
            where qi.docstatus < 2
              and qi.inspection_type = 'Incoming'
              and qi.reference_type = 'Purchase Receipt'
              and qi.reference_name = {pr_alias}.name
              and qi.item_code = {pri_alias}.item_code
              and ifnull(qi.batch_no, '') = ifnull({pri_alias}.batch_no, '')
        )
        and not exists (
            select 1
            from `tabRM QC Decision` rqd
            where rqd.docstatus = 1
              and rqd.purchase_receipt = {pr_alias}.name
              and rqd.item_code = {pri_alias}.item_code
              and ifnull(rqd.batch_no, '') = ifnull({pri_alias}.batch_no, '')
        )
        and not exists (
            select 1
            from `tabRM Release Note` rrn
            inner join `tabRM QC Decision` rqd_release on rqd_release.name = rrn.rm_qc_decision
            where rrn.docstatus = 1
              and rqd_release.docstatus = 1
              and rqd_release.purchase_receipt = {pr_alias}.name
              and rqd_release.item_code = {pri_alias}.item_code
              and ifnull(rqd_release.batch_no, '') = ifnull({pri_alias}.batch_no, '')
        )
    """


def _get_search_txt(txt):
    return f"%{(txt or '').strip()}%"


def _get_limit_clause(start, page_len):
    return f"limit {cint(start)}, {cint(page_len) or 20}"


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def unsupported_rm_reference_query(doctype, txt, searchfield, start, page_len, filters):
    return []


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pending_rm_reference_name_query(doctype, txt, searchfield, start, page_len, filters):
    filters = _coerce_filters(filters)
    params = {"txt": _get_search_txt(txt)}
    company_condition = ""

    if filters.company:
        params["company"] = filters.company
        company_condition = "and pr.company = %(company)s"

    return frappe.db.sql(
        f"""
        select
            pr.name,
            concat_ws(
                ' | ',
                coalesce(nullif(s.supplier_name, ''), pr.supplier, {frappe.db.escape(_('Unknown Supplier'))}),
                date_format(pr.posting_date, '%%d %%b %%Y'),
                left(
                    group_concat(
                        distinct coalesce(nullif(pri.item_name, ''), pri.item_code)
                        order by pri.item_code separator ', '
                    ),
                    140
                )
            ) as description
        from `tabPurchase Receipt` pr
        inner join `tabPurchase Receipt Item` pri
            on pri.parent = pr.name
           and pri.parenttype = 'Purchase Receipt'
        left join `tabSupplier` s
            on s.name = pr.supplier
        where pr.docstatus = 1
          {company_condition}
          and (
                pr.name like %(txt)s
             or ifnull(s.supplier_name, pr.supplier) like %(txt)s
             or ifnull(pr.supplier, '') like %(txt)s
             or pri.item_code like %(txt)s
             or ifnull(pri.item_name, '') like %(txt)s
             or ifnull(pri.batch_no, '') like %(txt)s
          )
          {_get_pending_rm_line_condition('pr', 'pri')}
        group by pr.name, s.supplier_name, pr.supplier, pr.posting_date, pr.modified
        order by pr.posting_date desc, pr.modified desc, pr.name desc
        {_get_limit_clause(start, page_len)}
        """,
        params,
    )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pending_rm_item_query(doctype, txt, searchfield, start, page_len, filters):
    filters = _coerce_filters(filters)
    if not filters.reference_name:
        return []

    return frappe.db.sql(
        f"""
        select
            pri.item_code,
            concat_ws(
                ' | ',
                coalesce(max(nullif(pri.item_name, '')), pri.item_code),
                concat(
                    {frappe.db.escape(_('Pending Batches'))},
                    ': ',
                    count(distinct case when ifnull(pri.batch_no, '') != '' then pri.batch_no end)
                )
            ) as description
        from `tabPurchase Receipt` pr
        inner join `tabPurchase Receipt Item` pri
            on pri.parent = pr.name
           and pri.parenttype = 'Purchase Receipt'
        where pr.docstatus = 1
          and pr.name = %(reference_name)s
          and (
                pri.item_code like %(txt)s
             or ifnull(pri.item_name, '') like %(txt)s
          )
          {_get_pending_rm_line_condition('pr', 'pri')}
        group by pri.item_code
        order by max(pr.posting_date) desc, pri.item_code asc
        {_get_limit_clause(start, page_len)}
        """,
        {
            "reference_name": filters.reference_name,
            "txt": _get_search_txt(txt),
        },
    )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pending_rm_batch_query(doctype, txt, searchfield, start, page_len, filters):
    filters = _coerce_filters(filters)
    if not filters.reference_name:
        return []

    params = {
        "reference_name": filters.reference_name,
        "txt": _get_search_txt(txt),
    }
    item_condition = ""
    if filters.item_code:
        params["item_code"] = filters.item_code
        item_condition = "and pri.item_code = %(item_code)s"

    return frappe.db.sql(
        f"""
        select
            pri.batch_no,
            concat_ws(
                ' | ',
                pri.item_code,
                coalesce(nullif(pri.item_name, ''), pri.item_code),
                date_format(max(pr.posting_date), '%%d %%b %%Y')
            ) as description
        from `tabPurchase Receipt` pr
        inner join `tabPurchase Receipt Item` pri
            on pri.parent = pr.name
           and pri.parenttype = 'Purchase Receipt'
        where pr.docstatus = 1
          and pr.name = %(reference_name)s
          and ifnull(pri.batch_no, '') != ''
          {item_condition}
          and (
                pri.batch_no like %(txt)s
             or pri.item_code like %(txt)s
             or ifnull(pri.item_name, '') like %(txt)s
          )
          {_get_pending_rm_line_condition('pr', 'pri')}
        group by pri.batch_no, pri.item_code, pri.item_name
        order by max(pr.posting_date) desc, pri.batch_no asc
        {_get_limit_clause(start, page_len)}
        """,
        params,
    )


@frappe.whitelist()
def get_single_pending_rm_batch(reference_name, item_code):
    if not reference_name or not item_code:
        return None

    rows = frappe.db.sql(
        f"""
        select distinct pri.batch_no
        from `tabPurchase Receipt` pr
        inner join `tabPurchase Receipt Item` pri
            on pri.parent = pr.name
           and pri.parenttype = 'Purchase Receipt'
        where pr.docstatus = 1
          and pr.name = %(reference_name)s
          and pri.item_code = %(item_code)s
          and ifnull(pri.batch_no, '') != ''
          {_get_pending_rm_line_condition('pr', 'pri')}
        order by pri.batch_no asc
        limit 2
        """,
        {
            "reference_name": reference_name,
            "item_code": item_code,
        },
    )

    if len(rows) == 1:
        return rows[0][0]

    return None


def is_pending_rm_purchase_receipt_candidate(reference_name, item_code=None, batch_no=None):
    if not reference_name:
        return False

    params = {"reference_name": reference_name}
    item_condition = ""
    batch_condition = ""

    if item_code:
        params["item_code"] = item_code
        item_condition = "and pri.item_code = %(item_code)s"

    if batch_no is not None and batch_no != "":
        params["batch_no"] = batch_no
        batch_condition = "and ifnull(pri.batch_no, '') = %(batch_no)s"

    result = frappe.db.sql(
        f"""
        select pri.name
        from `tabPurchase Receipt` pr
        inner join `tabPurchase Receipt Item` pri
            on pri.parent = pr.name
           and pri.parenttype = 'Purchase Receipt'
        where pr.docstatus = 1
          and pr.name = %(reference_name)s
          {item_condition}
          {batch_condition}
          {_get_pending_rm_line_condition('pr', 'pri')}
        limit 1
        """,
        params,
    )

    return bool(result)


def get_existing_rm_quality_inspection(reference_name, item_code=None, batch_no=None):
    if not reference_name:
        return None

    filters = {
        "reference_type": "Purchase Receipt",
        "reference_name": reference_name,
        "inspection_type": "Incoming",
        "docstatus": ("<", 2),
    }
    if item_code:
        filters["item_code"] = item_code
    if batch_no is not None:
        filters["batch_no"] = batch_no or ""

    rows = frappe.get_all(
        "Quality Inspection",
        filters=filters,
        fields=["name", "docstatus", "status", "custom_overall_result", "modified"],
        order_by="docstatus desc, modified desc, name desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def get_rm_purchase_receipt_pending_status(reference_name, item_code=None, batch_no=None):
    if not reference_name:
        return "missing_reference"

    pr_meta = frappe.db.get_value("Purchase Receipt", reference_name, ["name", "docstatus"], as_dict=True)
    if not pr_meta:
        return "missing_reference"

    if pr_meta.docstatus == 0:
        return "draft_purchase_receipt"

    if pr_meta.docstatus == 2:
        return "cancelled_purchase_receipt"

    if is_pending_rm_purchase_receipt_candidate(reference_name, item_code, batch_no):
        return "pending"

    existing_qi = get_existing_rm_quality_inspection(reference_name, item_code, batch_no)
    if existing_qi and cint(existing_qi.docstatus) == 0:
        return "draft_quality_inspection_exists"

    if frappe.db.exists(
        "RM QC Decision",
        {
            "purchase_receipt": reference_name,
            **({"item_code": item_code} if item_code else {}),
            **({"batch_no": batch_no or ""} if batch_no is not None else {}),
            "docstatus": 1,
        },
    ):
        return "rm_qc_decision_exists"

    if frappe.db.exists(
        "Quality Inspection",
        {
            "reference_type": "Purchase Receipt",
            "reference_name": reference_name,
            **({"item_code": item_code} if item_code else {}),
            **({"batch_no": batch_no or ""} if batch_no is not None else {}),
            "docstatus": 1,
        },
    ):
        return "submitted_quality_inspection_exists"

    return "not_pending"


def get_pending_rm_reference_validation_message(reference_name=None, item_code=None, batch_no=None):
    status = get_rm_purchase_receipt_pending_status(reference_name, item_code, batch_no)
    if status == "draft_purchase_receipt":
        return _("Submit the Purchase Receipt before creating Incoming Quality Inspection.")
    if status == "draft_quality_inspection_exists":
        existing_qi = get_existing_rm_quality_inspection(reference_name, item_code, batch_no)
        if existing_qi:
            return _("Incoming Quality Inspection draft {0} already exists for this Purchase Receipt. Open the draft and continue editing it.").format(existing_qi.name)
        return _("An Incoming Quality Inspection draft already exists for this Purchase Receipt. Open the draft and continue editing it.")
    if status == "cancelled_purchase_receipt":
        return _("The selected Purchase Receipt is cancelled and cannot be used for Incoming Quality Inspection.")
    if status == "rm_qc_decision_exists":
        return _("The selected Purchase Receipt already has an RM QC Decision and is no longer pending inspection.")
    if status == "submitted_quality_inspection_exists":
        return _("A submitted Incoming Quality Inspection already exists for the selected Purchase Receipt reference.")
    return _("Selected Purchase Receipt reference is already processed for RM QC or no longer pending inspection.")
