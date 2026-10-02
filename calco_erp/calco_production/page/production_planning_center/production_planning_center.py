from __future__ import annotations

import frappe

from calco_erp.calco_production import (
    fg_planning,
    planning_release,
    planning_work_order,
    production_planning,
)


def get_context(context):
    context.no_cache = 1


@frappe.whitelist()
def get_planning_data(*args, **kwargs):
    return production_planning.get_planning_data(*args, **kwargs)


@frappe.whitelist()
def search_fg_items(txt="", *args, **kwargs):
    return fg_planning.search_fg_items(txt=txt, *args, **kwargs)


@frappe.whitelist()
def set_planning_review(
    recommendation_key="",
    decision="",
    reviewed_qty=0,
    remarks="",
    from_date=None,
    to_date=None,
    **_rpc_kwargs,
):
    return production_planning.set_planning_review(
        recommendation_key=recommendation_key,
        decision=decision,
        reviewed_qty=reviewed_qty,
        remarks=remarks,
        from_date=from_date,
        to_date=to_date,
    )


@frappe.whitelist()
def search_draft_production_plans(*args, **kwargs):
    return production_planning.search_draft_production_plans(*args, **kwargs)


@frappe.whitelist()
def create_or_update_draft_production_plan(*args, **kwargs):
    return production_planning.create_or_update_draft_production_plan(*args, **kwargs)


@frappe.whitelist()
def get_release_context(*args, **kwargs):
    return planning_release.get_release_context(*args, **kwargs)


@frappe.whitelist()
def release_to_production(*args, **kwargs):
    return planning_release.release_to_production(*args, **kwargs)


@frappe.whitelist()
def get_work_order_creation_context(*args, **kwargs):
    return planning_work_order.get_work_order_creation_context(*args, **kwargs)


@frappe.whitelist()
def create_work_order_from_planning_center(*args, **kwargs):
    return planning_work_order.create_work_order_from_planning_center(*args, **kwargs)
