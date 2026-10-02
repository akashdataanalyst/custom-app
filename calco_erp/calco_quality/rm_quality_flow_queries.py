from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def _get_search_txt(txt):
    return f"%{(txt or '').strip()}%"


def _get_limit_clause(start, page_len):
    return f"limit {cint(start)}, {cint(page_len) or 20}"


def _get_pending_rm_quality_inspection_conditions(
    qi_alias="qi",
    exclude_rm_qc_decision_param=None,
    exclude_rm_release_note_param=None,
):
    decision_exclusion = ""
    release_exclusion = ""

    if exclude_rm_qc_decision_param:
        decision_exclusion = f" and rqd.name != %({exclude_rm_qc_decision_param})s"

    if exclude_rm_release_note_param:
        release_exclusion = f" and rrn.name != %({exclude_rm_release_note_param})s"

    return f"""
        {qi_alias}.docstatus = 1
        and {qi_alias}.inspection_type = 'Incoming'
        and {qi_alias}.reference_type = 'Purchase Receipt'
        and not (
            upper(ifnull({qi_alias}.custom_overall_result, '')) = 'ACCEPTED'
            or ifnull({qi_alias}.status, '') = 'Accepted'
        )
        and (
            ifnull({qi_alias}.quality_inspection_template, '') like 'Calco RM QC -%%'
            or exists (
                select 1
                from `tabRM Testing Standard` rts
                where rts.rm_item = {qi_alias}.item_code
                  and rts.is_active = 1
            )
        )
        and not exists (
            select 1
            from `tabRM QC Decision` rqd
            where rqd.docstatus < 2
              and rqd.quality_inspection = {qi_alias}.name
              {decision_exclusion}
        )
        and not exists (
            select 1
            from `tabRM Release Note` rrn
            inner join `tabRM QC Decision` rqd_release on rqd_release.name = rrn.rm_qc_decision
            where rrn.docstatus < 2
              and rqd_release.quality_inspection = {qi_alias}.name
              {release_exclusion}
        )
    """


def _get_pending_rm_qc_decision_conditions(
    rqd_alias="rqd",
    exclude_rm_release_note_param=None,
):
    release_exclusion = ""

    if exclude_rm_release_note_param:
        release_exclusion = f" and rrn.name != %({exclude_rm_release_note_param})s"

    return f"""
        {rqd_alias}.docstatus = 1
        and {rqd_alias}.decision = 'Deviation Required'
        and not exists (
            select 1
            from `tabRM Release Note` rrn
            where rrn.docstatus < 2
              and rrn.rm_qc_decision = {rqd_alias}.name
              {release_exclusion}
        )
    """


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pending_rm_quality_inspection_query(doctype, txt, searchfield, start, page_len, filters):
    return frappe.db.sql(
        f"""
        select
            qi.name,
            concat_ws(
                ' | ',
                coalesce(nullif(s.supplier_name, ''), pr.supplier, {frappe.db.escape(_('Unknown Supplier'))}),
                date_format(coalesce(pr.posting_date, date(qi.creation)), '%%d %%b %%Y'),
                qi.item_code,
                case
                    when ifnull(qi.batch_no, '') != '' then qi.batch_no
                    else {frappe.db.escape(_('No Batch'))}
                end
            ) as description
        from `tabQuality Inspection` qi
        left join `tabPurchase Receipt` pr
            on pr.name = qi.reference_name
        left join `tabSupplier` s
            on s.name = pr.supplier
        where {_get_pending_rm_quality_inspection_conditions('qi')}
          and (
                qi.name like %(txt)s
             or qi.reference_name like %(txt)s
             or qi.item_code like %(txt)s
             or ifnull(qi.batch_no, '') like %(txt)s
             or ifnull(s.supplier_name, pr.supplier) like %(txt)s
          )
        order by coalesce(pr.posting_date, date(qi.creation)) desc, qi.modified desc, qi.name desc
        {_get_limit_clause(start, page_len)}
        """,
        {"txt": _get_search_txt(txt)},
    )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pending_rm_qc_decision_query(doctype, txt, searchfield, start, page_len, filters):
    return frappe.db.sql(
        f"""
        select
            rqd.name,
            concat_ws(
                ' | ',
                coalesce(nullif(s.supplier_name, ''), pr.supplier, {frappe.db.escape(_('Unknown Supplier'))}),
                date_format(coalesce(pr.posting_date, date(rqd.creation)), '%%d %%b %%Y'),
                rqd.item_code,
                case
                    when ifnull(rqd.batch_no, '') != '' then rqd.batch_no
                    else {frappe.db.escape(_('No Batch'))}
                end
            ) as description
        from `tabRM QC Decision` rqd
        left join `tabPurchase Receipt` pr
            on pr.name = rqd.purchase_receipt
        left join `tabSupplier` s
            on s.name = pr.supplier
        where {_get_pending_rm_qc_decision_conditions('rqd')}
          and (
                rqd.name like %(txt)s
             or ifnull(rqd.purchase_receipt, '') like %(txt)s
             or rqd.item_code like %(txt)s
             or ifnull(rqd.batch_no, '') like %(txt)s
             or ifnull(s.supplier_name, pr.supplier) like %(txt)s
          )
        order by coalesce(pr.posting_date, date(rqd.creation)) desc, rqd.modified desc, rqd.name desc
        {_get_limit_clause(start, page_len)}
        """,
        {"txt": _get_search_txt(txt)},
    )


def is_pending_rm_quality_inspection(
    quality_inspection,
    exclude_rm_qc_decision=None,
    exclude_rm_release_note=None,
):
    if not quality_inspection:
        return False

    params = {"quality_inspection": quality_inspection}

    exclude_decision_param = None
    if exclude_rm_qc_decision:
        params["exclude_rm_qc_decision"] = exclude_rm_qc_decision
        exclude_decision_param = "exclude_rm_qc_decision"

    exclude_release_param = None
    if exclude_rm_release_note:
        params["exclude_rm_release_note"] = exclude_rm_release_note
        exclude_release_param = "exclude_rm_release_note"

    rows = frappe.db.sql(
        f"""
        select qi.name
        from `tabQuality Inspection` qi
        where qi.name = %(quality_inspection)s
          and {_get_pending_rm_quality_inspection_conditions(
                'qi',
                exclude_rm_qc_decision_param=exclude_decision_param,
                exclude_rm_release_note_param=exclude_release_param,
            )}
        limit 1
        """,
        params,
    )

    return bool(rows)


def is_pending_rm_qc_decision(rm_qc_decision, exclude_rm_release_note=None):
    if not rm_qc_decision:
        return False

    params = {"rm_qc_decision": rm_qc_decision}
    exclude_release_param = None

    if exclude_rm_release_note:
        params["exclude_rm_release_note"] = exclude_rm_release_note
        exclude_release_param = "exclude_rm_release_note"

    rows = frappe.db.sql(
        f"""
        select rqd.name
        from `tabRM QC Decision` rqd
        where rqd.name = %(rm_qc_decision)s
          and {_get_pending_rm_qc_decision_conditions(
                'rqd',
                exclude_rm_release_note_param=exclude_release_param,
            )}
        limit 1
        """,
        params,
    )

    return bool(rows)


def get_pending_rm_quality_inspection_validation_message():
    return _(
        "Selected Quality Inspection is already decided or released and is no longer pending RM QC Decision."
    )


def get_pending_rm_qc_decision_validation_message():
    return _(
        "Selected RM QC Decision is already released or no longer pending RM Release Note."
    )
