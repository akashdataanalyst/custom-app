from __future__ import annotations

import frappe
from decimal import Decimal, ROUND_HALF_UP
from functools import partial

from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property
from frappe.utils import cint, flt, nowdate

from calco_erp.calco_purchase.import_shipment import (
    get_import_shipment_gate_for_purchase_order,
    unique_non_empty,
)


IMPORT_FLAG = "custom_is_import_purchase_receipt"
# Retained only so pre-RC Recovery records and explicit rollback tooling remain
# readable. Final release setup does not install or require this Finance metadata.
INITIAL_LCV_PR_FIELD = "custom_calco_initial_import_purchase_receipt"
SYSTEM_PREPARED_FIELD = "custom_calco_system_prepared"
PREPARATION_NOTES_FIELD = "custom_calco_preparation_notes"
ELIGIBLE_CHARGES = (
    ("custom_basic_customs_duty", "Basic Customs Duty / BCD", "custom_import_bcd_account"),
    ("custom_social_welfare_surcharge", "Social Welfare Surcharge / SWS", "custom_import_sws_account"),
    ("custom_import_freight", "Import Freight", "custom_import_freight_account"),
    ("custom_transit_insurance", "Transit Insurance", "custom_import_insurance_account"),
    ("custom_cha_clearing_charges", "CHA / Clearing", "custom_import_cha_account"),
    ("custom_port_airport_handling", "Port / Airport Handling", "custom_import_port_handling_account"),
    ("custom_other_eligible_landed_cost", "Other Eligible Landed Cost", "custom_import_other_landed_cost_account"),
)

# Deprecated Finance/LCV compatibility policy. It is deliberately not invoked by
# hooks, patches, or ensure_import_landed_cost_setup in the 1.1.0 release path.
LCV_PERMISSIONS = {
    "Purchase User": {"read": 1},
    "Purchase Manager": {"read": 1},
    "Accounts User": {"read": 1, "write": 1, "create": 1},
    "Accounts Manager": {
        "read": 1,
        "write": 1,
        "create": 1,
        "submit": 1,
        "cancel": 1,
        "amend": 1,
    },
}


def ensure_import_landed_cost_setup():
    create_custom_fields(get_import_landed_cost_custom_fields(), update=True, ignore_validate=True)
    frappe.clear_cache(doctype="Purchase Receipt")


def get_import_landed_cost_custom_fields():
    import_depends = f"eval:doc.{IMPORT_FLAG}"
    return {
        "Purchase Receipt": _purchase_receipt_fields(import_depends),
    }


def _field(fieldname, fieldtype, label, insert_after, depends_on=None, **values):
    result = {
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": label,
        "insert_after": insert_after,
    }
    if depends_on:
        result["depends_on"] = depends_on
    result.update(values)
    return result


def _currency_field(fieldname, label, insert_after, depends_on=None, **values):
    return _field(
        fieldname,
        "Currency",
        label,
        insert_after,
        depends_on,
        options="Company:company:default_currency",
        **values,
    )


def _purchase_receipt_fields(import_depends):
    fields = [
        {
            "fieldname": IMPORT_FLAG,
            "fieldtype": "Check",
            "label": "Import Purchase Receipt",
            "insert_after": "custom_rm_expiry_date",
            "read_only": 1,
            "no_copy": 1,
            "hidden": 1,
            "default": "0",
        },
        {
            "fieldname": "custom_import_clearance_section",
            "fieldtype": "Section Break",
            "label": "Deprecated Import Clearance Evidence",
            "insert_after": IMPORT_FLAG,
            "hidden": 1,
        },
        _field("custom_bill_of_entry_no", "Data", "Bill of Entry No. (Deprecated)", "custom_import_clearance_section", hidden=1),
        _field("custom_bill_of_entry_date", "Date", "Bill of Entry Date (Deprecated)", "custom_bill_of_entry_no", hidden=1),
        _field(
            "custom_bill_of_entry_attachment",
            "Attach",
            "Bill of Entry / Customs Attachment (Deprecated)",
            "custom_bill_of_entry_date",
            hidden=1,
        ),
        {
            "fieldname": "custom_import_clearance_column",
            "fieldtype": "Column Break",
            "insert_after": "custom_bill_of_entry_attachment",
            "hidden": 1,
        },
        {
            **_field(
                "custom_customs_exchange_rate",
                "Float",
                "Customs Exchange Rate (Deprecated)",
                "custom_import_clearance_column",
                hidden=1,
            ),
            "precision": "9",
        },
        _currency_field(
            "custom_customs_assessable_value",
            "Customs Assessable Value (Deprecated)",
            "custom_customs_exchange_rate",
            hidden=1,
        ),
        {
            "fieldname": "custom_import_charges_section",
            "fieldtype": "Section Break",
            "label": "Import Charges & Operational RM Inward Cost",
            "insert_after": "custom_customs_assessable_value",
            "depends_on": import_depends,
        },
    ]
    previous = "custom_import_charges_section"
    for fieldname, label, _account_field in ELIGIBLE_CHARGES:
        fields.append(_currency_field(fieldname, label, previous, import_depends, non_negative=1))
        previous = fieldname
    fields.extend(
        [
            _currency_field(
                "custom_import_igst",
                "Import IGST (Recoverable - Excluded from Landed Cost)",
                previous,
                import_depends,
                non_negative=1,
            ),
            {
                "fieldname": "custom_import_landed_cost_summary_section",
                "fieldtype": "Section Break",
                "label": "Operational RM Inward Cost",
                "insert_after": "custom_import_igst",
                "depends_on": import_depends,
                "collapsible": 0,
            },
        ]
    )
    previous = "custom_import_landed_cost_summary_section"
    for fieldname, label in (
        ("custom_total_eligible_landed_charges", "Total Import Charges"),
        ("custom_estimated_landed_value", "Final RM Inward Value"),
        ("custom_estimated_landed_cost_per_stock_uom", "RM Inward Cost / Stock UOM"),
    ):
        fields.append(_currency_field(fieldname, label, previous, import_depends, read_only=1, no_copy=1))
        previous = fieldname
    fields.append(
        {
            "fieldname": "custom_import_landed_cost_summary_html",
            "fieldtype": "HTML",
            "label": "Import RM Inward Cost Summary",
            "insert_after": previous,
            "depends_on": import_depends,
        }
    )
    return fields


def _landed_cost_voucher_fields():
    return [
        _field(
            INITIAL_LCV_PR_FIELD,
            "Link",
            "Initial Import Purchase Receipt",
            "posting_date",
            options="Purchase Receipt",
            read_only=1,
            no_copy=1,
            unique=1,
        ),
        _field(
            SYSTEM_PREPARED_FIELD,
            "Check",
            "Calco System Prepared",
            INITIAL_LCV_PR_FIELD,
            read_only=1,
            no_copy=1,
            default="0",
        ),
        _field(
            PREPARATION_NOTES_FIELD,
            "Small Text",
            "Preparation Notes",
            SYSTEM_PREPARED_FIELD,
            read_only=1,
            no_copy=1,
        ),
    ]


def _company_account_fields():
    fields = [
        {
            "fieldname": "custom_import_landed_cost_accounts_section",
            "fieldtype": "Section Break",
            "label": "Import Landed Cost Account Mapping",
            "insert_after": "default_expense_account",
            "collapsible": 1,
        }
    ]
    previous = "custom_import_landed_cost_accounts_section"
    for _amount_field, label, fieldname in ELIGIBLE_CHARGES:
        fields.append(
            _field(
                fieldname,
                "Link",
                f"{label} Account",
                previous,
                options="Account",
                description="Finance-approved account used only for the initial Draft Landed Cost Voucher.",
            )
        )
        previous = fieldname
    return fields


def ensure_lcv_permissions():
    if not frappe.db.exists("DocType", "Landed Cost Voucher"):
        return
    for role, permissions in LCV_PERMISSIONS.items():
        if not frappe.db.exists("Role", role):
            continue
        filters = {
            "parent": "Landed Cost Voucher",
            "role": role,
            "permlevel": 0,
            "if_owner": 0,
        }
        if not frappe.db.exists("Custom DocPerm", filters):
            add_permission("Landed Cost Voucher", role, 0, "read")
        for permission_type, value in permissions.items():
            update_permission_property(
                "Landed Cost Voucher",
                role,
                0,
                permission_type,
                value,
                validate=False,
            )


def get_purchase_orders(doc) -> list[str]:
    return unique_non_empty(
        row.get("purchase_order")
        for row in (doc.get("items") or [])
        if row.get("purchase_order")
    )


def get_import_receipt_context(doc) -> dict:
    purchase_orders = get_purchase_orders(doc)
    gates = [get_import_shipment_gate_for_purchase_order(name) for name in purchase_orders]
    return {
        "is_import": bool(gates and any(gate.get("required") for gate in gates)),
        "purchase_orders": purchase_orders,
        "import_shipments": unique_non_empty(
            row.get("name")
            for gate in gates
            for row in gate.get("submitted_docs", [])
        ),
    }


def sync_import_purchase_receipt(doc, method=None):
    context = (
        get_import_receipt_context(doc)
        if not cint(doc.get("is_return"))
        else {"is_import": False}
    )
    doc.set(IMPORT_FLAG, 1 if context.get("is_import") else 0)
    if context.get("is_import"):
        calculate_estimated_landed_cost(doc)


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _currency_quantum(doc) -> Decimal:
    precision = 2
    if hasattr(doc, "precision"):
        try:
            precision = cint(doc.precision("base_net_total")) or precision
        except Exception:
            pass
    return Decimal("1").scaleb(-precision)


def _as_float(value: Decimal) -> float:
    return float(value)


def calculate_estimated_landed_cost(doc) -> dict:
    """Derive operational inward cost without changing stock/accounting valuation."""
    eligible_charges = sum(
        (
            _decimal(doc.get(fieldname))
            for fieldname, _label, _account_field in ELIGIBLE_CHARGES
        ),
        Decimal("0"),
    )
    quantum = _currency_quantum(doc)
    eligible_rows = []
    excluded_rows = []
    for position, row in enumerate(doc.get("items") or []):
        accepted_qty = _decimal(row.get("stock_qty"))
        material_value = _decimal(
            row.get("base_net_amount") or row.get("base_amount")
        )
        values = {
            "row": row,
            "position": position,
            "accepted_qty": accepted_qty,
            "material_value": material_value,
        }
        if accepted_qty <= 0:
            excluded_rows.append(
                {
                    "item_code": row.get("item_code"),
                    "row_name": row.get("name"),
                    "reason": "No accepted stock quantity.",
                }
            )
        elif material_value <= 0:
            excluded_rows.append(
                {
                    "item_code": row.get("item_code"),
                    "row_name": row.get("name"),
                    "reason": "Accepted item has no positive base material value.",
                }
            )
        else:
            eligible_rows.append(values)

    base_material_value = sum(
        (row["material_value"] for row in eligible_rows), Decimal("0")
    )
    accepted_stock_qty = sum(
        (row["accepted_qty"] for row in eligible_rows), Decimal("0")
    )
    calculable = bool(
        eligible_rows and base_material_value > 0 and accepted_stock_qty > 0
    )
    calculation_error = ""
    if not calculable:
        calculation_error = (
            "Operational RM inward cost requires positive accepted quantity "
            "and base material value."
        )

    allocated_total = Decimal("0")
    item_costs = []
    for index, values in enumerate(eligible_rows):
        row = values["row"]
        if index == len(eligible_rows) - 1:
            allocated_charges = eligible_charges - allocated_total
        else:
            allocated_charges = (
                eligible_charges * values["material_value"] / base_material_value
            ).quantize(quantum, rounding=ROUND_HALF_UP)
            allocated_total += allocated_charges
        inward_value = values["material_value"] + allocated_charges
        inward_cost = inward_value / values["accepted_qty"]
        conversion_factor = _decimal(row.get("conversion_factor")) or Decimal("1")
        item_costs.append(
            {
                "item_code": row.get("item_code"),
                "row_name": row.get("name"),
                "row_index": row.get("idx") or values["position"] + 1,
                "stock_uom": row.get("stock_uom"),
                "accepted_stock_qty": _as_float(values["accepted_qty"]),
                "supplier_rate": _as_float(
                    _decimal(row.get("rate")) / conversion_factor
                ),
                "supplier_amount": flt(row.get("amount")),
                "material_rate_company_currency": _as_float(
                    _decimal(row.get("base_rate")) / conversion_factor
                ),
                "material_amount_company_currency": _as_float(
                    values["material_value"]
                ),
                "allocated_import_charges": _as_float(allocated_charges),
                "rm_inward_value": _as_float(inward_value),
                "rm_inward_cost_per_stock_uom": _as_float(inward_cost),
            }
        )

    inward_value = (
        base_material_value + eligible_charges if calculable else Decimal("0")
    )
    inward_per_uom = (
        inward_value / accepted_stock_qty
        if calculable and len(item_costs) == 1
        else Decimal("0")
    )
    doc.set(
        "custom_total_eligible_landed_charges", _as_float(eligible_charges)
    )
    doc.set("custom_estimated_landed_value", _as_float(inward_value))
    doc.set(
        "custom_estimated_landed_cost_per_stock_uom", _as_float(inward_per_uom)
    )
    return {
        "calculation_status": "Calculated" if calculable else "Not Calculable",
        "calculation_error": calculation_error,
        "allocation_method": "By Material INR Value",
        "supplier_transaction_value": flt(doc.get("net_total")),
        "commercial_exchange_rate": flt(doc.get("conversion_rate")),
        "base_material_value": _as_float(base_material_value),
        "accepted_stock_qty": _as_float(accepted_stock_qty),
        "eligible_charges": _as_float(eligible_charges),
        "rm_inward_value": _as_float(inward_value),
        "rm_inward_cost_per_stock_uom": _as_float(inward_per_uom),
        # Compatibility aliases for existing persisted field names and callers.
        "estimated_landed_value": _as_float(inward_value),
        "estimated_landed_cost_per_stock_uom": _as_float(inward_per_uom),
        "import_igst": flt(doc.get("custom_import_igst")),
        "charges": [
            {
                "fieldname": fieldname,
                "label": label,
                "amount": flt(doc.get(fieldname)),
            }
            for fieldname, label, _account_field in ELIGIBLE_CHARGES
        ],
        "item_costs": item_costs,
        "excluded_rows": excluded_rows,
    }


def validate_import_purchase_receipt_submission(doc, method=None):
    sync_import_purchase_receipt(doc)
    if not cint(doc.get(IMPORT_FLAG)):
        return

    company_currency = frappe.get_cached_value(
        "Company", doc.company, "default_currency"
    )
    if doc.currency != company_currency and flt(doc.conversion_rate) <= 1:
        frappe.throw(
            _(
                "Valid foreign-currency to INR exchange rate is required before submitting this Import Purchase Receipt."
            )
        )

def schedule_initial_import_lcv(doc, method=None):
    if cint(doc.get("is_return")) or not cint(doc.get(IMPORT_FLAG)):
        return
    frappe.db.after_commit.add(partial(_enqueue_initial_lcv_after_commit, doc.name))


def _enqueue_initial_lcv_after_commit(purchase_receipt: str):
    try:
        frappe.enqueue(
            "calco_erp.calco_purchase.import_landed_cost.prepare_initial_import_lcv",
            queue="short",
            timeout=300,
            deduplicate=True,
            job_id=f"calco-import-lcv-{purchase_receipt}",
            purchase_receipt=purchase_receipt,
        )
    except Exception:
        frappe.log_error(
            title=f"Import landed cost queue failed: {purchase_receipt}",
            message=frappe.get_traceback(),
        )


def _existing_initial_lcv(purchase_receipt: str):
    return frappe.db.get_value(
        "Landed Cost Voucher",
        {INITIAL_LCV_PR_FIELD: purchase_receipt},
        ["name", "docstatus"],
        as_dict=True,
    )


def prepare_initial_import_lcv(purchase_receipt: str) -> dict:
    try:
        return _prepare_initial_import_lcv(purchase_receipt)
    except Exception:
        frappe.log_error(
            title=f"Import landed cost preparation failed: {purchase_receipt}",
            message=frappe.get_traceback(),
        )
        return {
            "status": "attention_required",
            "reason": "Draft Landed Cost Voucher preparation failed.",
        }


def _prepare_initial_import_lcv(purchase_receipt: str) -> dict:
    existing = _existing_initial_lcv(purchase_receipt)
    if existing:
        return {"status": "existing", "landed_cost_voucher": existing.name}

    pr = frappe.get_doc("Purchase Receipt", purchase_receipt)
    if cint(pr.docstatus) != 1 or cint(pr.get("is_return")):
        return {
            "status": "not_applicable",
            "reason": "Purchase Receipt is not a submitted inward receipt.",
        }
    if not get_import_receipt_context(pr).get("is_import"):
        return {
            "status": "not_applicable",
            "reason": "Purchase Receipt is not an import receipt.",
        }

    mapped, unresolved = _mapped_lcv_charges(pr)
    preparation_notes = _unresolved_mapping_message(unresolved)
    if not mapped and not unresolved:
        preparation_notes = "No positive eligible import charges were entered."

    lcv = frappe.new_doc("Landed Cost Voucher")
    lcv.company = pr.company
    lcv.posting_date = pr.posting_date or nowdate()
    lcv.distribute_charges_based_on = "Amount"
    lcv.set(INITIAL_LCV_PR_FIELD, pr.name)
    lcv.set(SYSTEM_PREPARED_FIELD, 1)
    lcv.append(
        "purchase_receipts",
        {
            "receipt_document_type": "Purchase Receipt",
            "receipt_document": pr.name,
            "supplier": pr.supplier,
            "posting_date": pr.posting_date,
            "grand_total": pr.base_grand_total,
        },
    )
    lcv.get_items_from_purchase_receipts()
    for charge in mapped:
        lcv.append(
            "taxes",
            {
                "expense_account": charge["account"],
                "description": charge["label"],
                "amount": charge["amount"],
                "exchange_rate": 1,
            },
        )
    if preparation_notes:
        lcv.set(PREPARATION_NOTES_FIELD, preparation_notes)
    lcv.insert(ignore_permissions=True)
    return {
        "status": "prepared",
        "landed_cost_voucher": lcv.name,
        "unresolved_charges": unresolved,
        "attention": preparation_notes,
    }


def _mapped_lcv_charges(pr):
    company = frappe.get_cached_doc("Company", pr.company)
    mapped = []
    unresolved = []
    for amount_field, label, account_field in ELIGIBLE_CHARGES:
        amount = flt(pr.get(amount_field))
        if amount <= 0:
            continue
        account = company.get(account_field)
        if not account or not _valid_lcv_account(account, pr.company):
            unresolved.append(
                {
                    "label": label,
                    "amount": amount,
                    "mapping_field": account_field,
                }
            )
            continue
        mapped.append({"label": label, "amount": amount, "account": account})
    return mapped, unresolved


def _valid_lcv_account(account, company):
    values = frappe.get_cached_value(
        "Account", account, ["company", "is_group", "disabled"], as_dict=True
    )
    return bool(
        values
        and values.company == company
        and not cint(values.is_group)
        and not cint(values.disabled)
    )


def _unresolved_mapping_message(unresolved):
    if not unresolved:
        return ""
    labels = ", ".join(row["label"] for row in unresolved)
    return (
        f"Finance account mapping required for: {labels}. "
        "Amounts remain preserved on the Purchase Receipt."
    )


def get_lcv_summary(purchase_receipt: str) -> dict:
    names = frappe.get_all(
        "Landed Cost Purchase Receipt",
        filters={
            "receipt_document_type": "Purchase Receipt",
            "receipt_document": purchase_receipt,
        },
        pluck="parent",
        distinct=True,
        limit_page_length=0,
    )
    rows = (
        frappe.get_all(
            "Landed Cost Voucher",
            filters={"name": ("in", names)},
            fields=[
                "name",
                "docstatus",
                "total_taxes_and_charges",
                INITIAL_LCV_PR_FIELD,
                PREPARATION_NOTES_FIELD,
            ],
            order_by="creation asc",
            limit_page_length=0,
        )
        if names
        else []
    )
    active = [row for row in rows if cint(row.docstatus) < 2]
    submitted = [row for row in rows if cint(row.docstatus) == 1]
    drafts = [row for row in rows if cint(row.docstatus) == 0]
    if len(active) > 1:
        status = "Multiple LCVs"
    elif submitted:
        status = "Submitted"
    elif drafts:
        status = "Draft"
    else:
        status = "Not Prepared"
    return {
        "status": status,
        "lcvs": rows,
        "draft_lcvs": [row.name for row in drafts],
        "submitted_lcvs": [row.name for row in submitted],
        "submitted_charges": sum(
            flt(row.total_taxes_and_charges) for row in submitted
        ),
        "pending_charges": sum(flt(row.total_taxes_and_charges) for row in drafts),
        "attention": " ".join(
            row.get(PREPARATION_NOTES_FIELD)
            for row in drafts
            if row.get(PREPARATION_NOTES_FIELD)
        ),
    }


def _add_preparation_attention(pr, lcv):
    if lcv["lcvs"] or cint(pr.docstatus) != 1:
        return lcv
    _mapped, unresolved = _mapped_lcv_charges(pr)
    error_exists = any(
        frappe.db.exists("Error Log", {"method": ("like", f"%{title}: {pr.name}%")})
        for title in (
            "Import landed cost queue failed",
            "Import landed cost preparation failed",
            "Import landed cost requires attention",
        )
    )
    if unresolved or error_exists:
        lcv["status"] = "Preparation Requires Attention"
        lcv["attention"] = (
            _unresolved_mapping_message(unresolved)
            if unresolved
            else "Purchase Receipt submitted. Draft Landed Cost preparation requires attention."
        )
    return lcv


@frappe.whitelist()
def get_import_purchase_receipt_summary(doc=None, purchase_receipt=None):
    if purchase_receipt:
        pr = frappe.get_doc("Purchase Receipt", purchase_receipt)
    else:
        values = frappe.parse_json(doc) if isinstance(doc, str) else doc
        pr = frappe.get_doc(values)
    context = (
        get_import_receipt_context(pr)
        if not cint(pr.get("is_return"))
        else {"is_import": False}
    )
    estimate = calculate_estimated_landed_cost(pr) if context.get("is_import") else {}
    return {
        **context,
        "company_currency": frappe.get_cached_value(
            "Company", pr.company, "default_currency"
        ),
        "estimate": estimate,
    }


def warn_purchase_invoice_landed_cost_status(doc, method=None):
    receipt_names = unique_non_empty(
        row.get("purchase_receipt")
        for row in (doc.get("items") or [])
        if row.get("purchase_receipt")
    )
    pending = []
    for name in receipt_names:
        pr = frappe.get_doc("Purchase Receipt", name)
        if not get_import_receipt_context(pr).get("is_import"):
            continue
        summary = get_lcv_summary(name)
        if not summary["submitted_lcvs"]:
            pending.append(f"{name} ({summary['status']})")
    if pending:
        frappe.msgprint(
            _(
                "Finance warning: landed cost is not submitted for Import Purchase Receipt(s): {0}. "
                "Purchase Invoice submission is not blocked."
            ).format(", ".join(pending)),
            indicator="orange",
            alert=True,
        )


@frappe.whitelist()
def get_suspicious_foreign_purchase_receipts(limit=200):
    frappe.only_for(
        ("Accounts User", "Accounts Manager", "Purchase Manager", "System Manager")
    )
    rows = frappe.get_all(
        "Purchase Receipt",
        filters={"docstatus": 1},
        fields=[
            "name",
            "company",
            "supplier",
            "posting_date",
            "currency",
            "conversion_rate",
            "base_net_total",
            "net_total",
        ],
        order_by="posting_date desc, name desc",
        limit_page_length=min(cint(limit) or 200, 1000),
    )
    output = []
    for row in rows:
        company_currency = frappe.get_cached_value(
            "Company", row.company, "default_currency"
        )
        if row.currency == company_currency:
            continue
        lcv = get_lcv_summary(row.name)
        issues = []
        if flt(row.conversion_rate) <= 1:
            issues.append("Suspicious foreign-currency conversion rate")
        if not lcv["submitted_lcvs"]:
            issues.append("No submitted Landed Cost Voucher")
        if issues:
            row["lcv_status"] = lcv["status"]
            row["issues"] = issues
            output.append(row)
    return output
