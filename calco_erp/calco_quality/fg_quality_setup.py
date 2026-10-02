from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

from calco_erp.calco_quality.fg_quantity_rules import (
    FG_KG_PER_MT,
    get_quantity_multiplier as calculate_fg_quantity_multiplier,
    get_required_test_count as calculate_fg_required_test_count,
)
from calco_erp.calco_quality.rm_quality_setup import is_rm_quality_context
from calco_erp.calco_quality.rm_testing_utils import normalize_text, parse_float


FG_TEST_TYPE_NUMERIC = "Numeric"
FG_TEST_TYPE_MANUAL = "Manual"
FG_INSPECTION_PENDING = "Pending"
FG_MANUAL_REVIEW_PARAMETERS = {
    "Color Variation (Black Spot Free)",
    "Finish (Shine/Dull)",
    "Long Cut",
    "Metal Contamination",
}

DEFAULT_FG_TESTING_METHODS = [
    "MFI",
    "Density",
    "Ash Content",
    "Moisture Content",
    "Tensile Strength at Yield",
    "Young Modulus",
    "Elongation at Yield",
    "Flexural Strength",
    "Flexural Modulus",
    "Notch Izod Impact",
    "Shrinkage - Linear",
    "Shrinkage - Transverse",
    "Glow Wire Test",
    "UL94 Burning Testing",
    "CTI (V)",
    "HDT (Deg C)",
    "L",
    "A",
    "B",
    "Water Absorption",
    "Melting Point",
    "Vicat Softening Point",
    "Coefficient of Linear Thermal Expansion",
    "Dielectric Strength",
    "Dielectric Constant",
    "Volume Resistivity",
    "Surface Resistivity",
    "Long Cut",
    "Color Variation (Black Spot Free)",
    "Finish (Shine/Dull)",
    "UL 94 Burning Test @0.8",
    "UL 94 Burning Test @1.6",
    "GWT @0.8",
    "GWT @1.6",
    "Un-annealed, HDT",
    "Un-Notched Izod Impact Strength",
    "Delta E",
    "Transmittance",
    "GWT or UL Seconds",
    "Metal Contamination",
]
FG_TESTING_METHOD_ALIASES = {
    "HDT (\u00B0C)": "HDT (Deg C)",
    "Ash": "Ash Content",
    "Ash Content (%)": "Ash Content",
    "CTI": "CTI (V)",
    "Comparative Tracking Index": "CTI (V)",
    "Dielectric Constant (Unit Less)": "Dielectric Constant",
    "Dielectric Strength (KV/mm)": "Dielectric Strength",
    "El@yl": "Elongation at Yield",
    "Elongation at Yield (%)": "Elongation at Yield",
    "FS @ PL": "Flexural Strength",
    "Flexural Strength @ PL": "Flexural Strength",
    "HDT": "HDT (Deg C)",
    "Izod imp": "Notch Izod Impact",
    "Mould Shrinkage, Linear": "Shrinkage - Linear",
    "Mould Shrinkage, Transverse": "Shrinkage - Transverse",
    "Moisture": "Moisture Content",
    "Moisture (%)": "Moisture Content",
    "Surface Resistivity (Ohm)": "Surface Resistivity",
    "TS @ yield": "Tensile Strength at Yield",
    "TS @ yield (MPa)": "Tensile Strength at Yield",
    "Un-Notched Izod Impact Strength @ 23\u00B0C, 3.2 mm": "Un-Notched Izod Impact Strength",
    "Volume Resistivity (Ohm.cm)": "Volume Resistivity",
    "Water Absorption (%)": "Water Absorption",
}
FG_CONTROL_PLAN_IMPORT_DESCRIPTION = "Imported from FG Control Plan workbook."


def ensure_fg_quality_setup():
    create_custom_fields(get_fg_quality_custom_fields(), update=True)
    ensure_fg_testing_methods()
    ensure_fg_item_template_links()
    frappe.clear_cache()


def ensure_fg_testing_methods():
    if not frappe.db.exists("DocType", "FG Testing Method"):
        return

    for method_name in get_true_fg_testing_method_names():
        if frappe.db.exists("FG Testing Method", method_name):
            continue

        frappe.get_doc(
            {
                "doctype": "FG Testing Method",
                "method_name": method_name,
            }
        ).insert(ignore_permissions=True)


def get_true_fg_testing_method_names() -> tuple[str, ...]:
    return tuple(DEFAULT_FG_TESTING_METHODS)


def get_true_fg_testing_method_name_set() -> set[str]:
    return set(get_true_fg_testing_method_names())


def canonicalize_fg_testing_method_name(method_name: str) -> str:
    normalized_name = normalize_text(method_name)
    if not normalized_name:
        return ""
    return FG_TESTING_METHOD_ALIASES.get(normalized_name, normalized_name)


def get_fg_quality_custom_fields():
    return {
        "Quality Inspection": [
            {
                "fieldname": "parameter_samples",
                "fieldtype": "Table",
                "label": "Parameter Samples",
                "options": "FG Inspection Parameter Sample",
                "insert_after": "readings",
            },
            {
                "fieldname": "custom_manufacturing_qty_mt",
                "fieldtype": "Float",
                "label": "Manufacturing Qty (MT)",
                "insert_after": "parameter_samples",
                "read_only": 1,
            },
            {
                "fieldname": "custom_quantity_multiplier",
                "fieldtype": "Int",
                "label": "Quantity Multiplier",
                "insert_after": "custom_manufacturing_qty_mt",
                "read_only": 1,
            },
        ],
        "Quality Inspection Reading": [
            {
                "fieldname": "custom_sample_size",
                "fieldtype": "Float",
                "label": "Sample",
                "insert_after": "custom_result_label",
                "read_only": 1,
            },
            {
                "fieldname": "custom_frequency",
                "fieldtype": "Data",
                "label": "Frequency",
                "insert_after": "custom_sample_size",
                "read_only": 1,
            },
            {
                "fieldname": "custom_required_tests",
                "fieldtype": "Int",
                "label": "Required Tests",
                "insert_after": "custom_frequency",
                "read_only": 1,
            },
            {
                "fieldname": "custom_test_sequence",
                "fieldtype": "Int",
                "label": "Test No",
                "insert_after": "custom_required_tests",
                "read_only": 1,
                "hidden": 1,
            },
            {
                "fieldname": "custom_target_value",
                "fieldtype": "Data",
                "label": "Target Value",
                "insert_after": "custom_test_sequence",
                "read_only": 1,
            },
            {
                "fieldname": "custom_parameter_result",
                "fieldtype": "Data",
                "label": "Parameter Result",
                "insert_after": "custom_target_value",
                "read_only": 1,
            },
            {
                "fieldname": "custom_parameter_key",
                "fieldtype": "Data",
                "label": "Parameter Key",
                "insert_after": "custom_parameter_result",
                "read_only": 1,
                "hidden": 1,
            },
        ],
    }


def ensure_fg_item_template_links():
    if not frappe.db.exists("DocType", "Item"):
        return

    for item_code, template_name in get_fg_item_template_map().items():
        if not item_code or not template_name:
            continue

        if not frappe.db.exists("Item", item_code):
            continue

        if not frappe.db.exists("Quality Inspection Template", template_name):
            continue

        current_template = normalize_text(frappe.db.get_value("Item", item_code, "quality_inspection_template"))
        if current_template:
            continue

        frappe.db.set_value(
            "Item",
            item_code,
            "quality_inspection_template",
            template_name,
            update_modified=False,
        )


def apply_fg_control_plan(doc, method=None):
    from calco_erp.calco_production.in_process_quality import apply_frozen_readings
    if apply_frozen_readings(doc):
        return
    if doc.doctype != "Quality Inspection" or not doc.item_code:
        return
    if doc.inspection_type == "Incoming" and doc.reference_type == "Purchase Receipt":
        return
    if is_rm_quality_context(doc):
        return

    if not normalize_text(doc.quality_inspection_template):
        template_name = resolve_fg_inspection_template(doc.item_code, update_item=True)
        if template_name:
            doc.quality_inspection_template = template_name

    from calco_erp.calco_quality.quality_master_versions import frozen_rows
    frozen_master_rows = frozen_rows(doc.item_code, doc.get("reference_type"), doc.get("reference_name"), doc.get("batch_no"))
    if frozen_master_rows is None and not uses_fg_control_plan(doc.item_code):
        set_fg_quantity_fields(
            doc,
            {
                "manufacturing_qty_mt": None,
                "quantity_multiplier": 0,
            },
        )
        if doc.meta.has_field("parameter_samples"):
            doc.set("parameter_samples", [])
        populate_fg_template_readings(doc)
        evaluate_fg_inspection_state(doc)
        return

    control_plan_rows = frozen_master_rows if frozen_master_rows is not None else get_applicable_fg_control_plan_rows(doc.item_code)
    quantity_context = resolve_fg_manufacturing_context(
        item_code=doc.item_code,
        reference_type=doc.get("reference_type"),
        reference_name=doc.get("reference_name"),
        batch_no=doc.get("batch_no"),
    )
    sync_fg_control_plan_readings(doc, control_plan_rows, quantity_context)
    evaluate_fg_inspection_state(doc)


@frappe.whitelist()
def get_fg_control_plan_context(
    item_code: str,
    reference_type: str | None = None,
    reference_name: str | None = None,
    batch_no: str | None = None,
) -> dict[str, object]:
    context = build_fg_inspection_context(item_code, reference_type, reference_name, batch_no)
    return {
        "use_control_plan": context["use_control_plan"],
        "has_control_plan": context["has_control_plan"],
        "empty_message": context["empty_message"],
        "readings": context["readings"],
        "parameter_samples": context["parameter_samples"],
        "manufacturing_qty_mt": context["manufacturing_qty_mt"],
        "quantity_multiplier": context["quantity_multiplier"],
        "quantity_source": context["quantity_source"],
    }


@frappe.whitelist()
def get_fg_inspection_context(
    item_code: str,
    reference_type: str | None = None,
    reference_name: str | None = None,
    batch_no: str | None = None,
) -> dict[str, object]:
    return build_fg_inspection_context(item_code, reference_type, reference_name, batch_no)


def build_fg_inspection_context(
    item_code: str,
    reference_type: str | None = None,
    reference_name: str | None = None,
    batch_no: str | None = None,
) -> dict[str, object]:
    item_code = normalize_text(item_code)
    if not item_code:
        return {
            "quality_inspection_template": "",
            "use_control_plan": False,
            "has_control_plan": False,
            "empty_message": "",
            "manufacturing_qty_mt": None,
            "quantity_multiplier": 0,
            "quantity_source": "",
            "readings": [],
            "parameter_samples": [],
        }

    template_name = resolve_fg_inspection_template(item_code, update_item=True)
    from calco_erp.calco_quality.quality_master_versions import frozen_rows
    frozen_master_rows = frozen_rows(item_code, reference_type, reference_name, batch_no)
    use_control_plan = frozen_master_rows is not None or uses_fg_control_plan(item_code)
    rows = frozen_master_rows if frozen_master_rows is not None else (get_applicable_fg_control_plan_rows(item_code) if use_control_plan else [])
    quantity_context = resolve_fg_manufacturing_context(
        item_code=item_code,
        reference_type=reference_type,
        reference_name=reference_name,
        batch_no=batch_no,
    )
    parent_rows = build_fg_parent_control_plan_rows(rows, quantity_context["quantity_multiplier"])
    sample_rows = build_fg_parameter_sample_payloads(parent_rows)

    empty_message = ""
    if use_control_plan and not rows:
        empty_message = _("No QC parameters defined for this grade.")
    elif use_control_plan and rows and not parent_rows:
        empty_message = _("Manufacturing quantity is required to calculate FG required tests.")

    return {
        "quality_inspection_template": template_name,
        "use_control_plan": use_control_plan,
        "has_control_plan": bool(rows),
        "empty_message": empty_message,
        "manufacturing_qty_mt": quantity_context["manufacturing_qty_mt"],
        "quantity_multiplier": quantity_context["quantity_multiplier"],
        "quantity_source": quantity_context["source_label"],
        "readings": parent_rows,
        "parameter_samples": sample_rows,
    }


def uses_fg_control_plan(item_code: str) -> bool:
    item_code = normalize_text(item_code)
    if not item_code:
        return False

    return bool(
        frappe.db.exists(
            "FG Control Plan",
            {
                "fg_item_code": item_code,
                "is_active": 1,
                "parameter": ["in", list(get_true_fg_testing_method_names())],
            **({"custom_inspection_scope": ["in", ["", "Final QC"]]} if frappe.get_meta("FG Control Plan").has_field("custom_inspection_scope") else {}),
            },
        )
    )


def resolve_fg_manufacturing_context(
    item_code: str,
    reference_type: str | None = None,
    reference_name: str | None = None,
    batch_no: str | None = None,
) -> dict[str, object]:
    item_code = normalize_text(item_code)
    reference_type = normalize_text(reference_type)
    reference_name = normalize_text(reference_name)
    batch_no = normalize_text(batch_no)

    context = {
        "manufacturing_qty": None,
        "manufacturing_qty_mt": None,
        "quantity_multiplier": 0,
        "source_label": "",
        "source_doctype": "",
        "source_name": "",
        "source_field": "",
    }
    if not item_code:
        return context

    stock_entry_qty = get_fg_stock_entry_finished_qty(item_code, reference_type, reference_name)
    if stock_entry_qty is not None:
        manufacturing_qty_mt = convert_fg_quantity_to_mt(item_code, stock_entry_qty)
        context.update(
            {
                "manufacturing_qty": stock_entry_qty,
                "manufacturing_qty_mt": manufacturing_qty_mt,
                "quantity_multiplier": get_fg_quantity_multiplier(manufacturing_qty_mt),
                "source_label": _("Stock Entry {0} finished item qty").format(reference_name),
                "source_doctype": "Stock Entry",
                "source_name": reference_name,
                "source_field": "items.qty",
            }
        )
        return context

    batch_record = get_fg_batch_production_source(item_code, batch_no, reference_type, reference_name)
    if batch_record:
        manufacturing_qty = get_fg_numeric_limit(batch_record.get("produced_qty"))
        manufacturing_qty_mt = convert_fg_quantity_to_mt(item_code, manufacturing_qty)
        context.update(
            {
                "manufacturing_qty": manufacturing_qty,
                "manufacturing_qty_mt": manufacturing_qty_mt,
                "quantity_multiplier": get_fg_quantity_multiplier(manufacturing_qty_mt),
                "source_label": _("Batch Production Record {0} produced_qty").format(batch_record.get("name")),
                "source_doctype": "Batch Production Record",
                "source_name": batch_record.get("name"),
                "source_field": "produced_qty",
            }
        )
        return context

    return context


def get_fg_batch_production_source(
    item_code: str,
    batch_no: str,
    reference_type: str,
    reference_name: str,
) -> frappe._dict | None:
    if not frappe.db.exists("DocType", "Batch Production Record"):
        return None

    filters = {"item_code": item_code, "docstatus": 1}
    if batch_no:
        filters["fg_batch_no"] = batch_no
    elif reference_type == "Stock Entry" and reference_name:
        filters["stock_entry"] = reference_name
    else:
        return None

    rows = frappe.get_all(
        "Batch Production Record",
        filters=filters,
        fields=["name", "produced_qty", "stock_entry"],
        order_by="modified desc, creation desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def get_fg_stock_entry_finished_qty(item_code: str, reference_type: str, reference_name: str) -> float | None:
    if reference_type != "Stock Entry" or not reference_name or not frappe.db.exists("Stock Entry", reference_name):
        return None

    stock_entry = frappe.get_doc("Stock Entry", reference_name)
    if cint(stock_entry.docstatus) != 1 or normalize_text(stock_entry.purpose) != "Manufacture":
        return None

    finished_row = next(
        (
            row
            for row in stock_entry.get("items", [])
            if cint(row.get("is_finished_item")) and normalize_text(row.get("item_code")) == item_code
        ),
        None,
    )
    if not finished_row:
        return None

    return get_fg_numeric_limit(finished_row.get("qty"))


def convert_fg_quantity_to_mt(item_code: str, quantity) -> float | None:
    quantity = get_fg_numeric_limit(quantity)
    if quantity is None:
        return None

    stock_uom = normalize_text(frappe.db.get_value("Item", item_code, "stock_uom"))
    if stock_uom.casefold() in {"kg", "kilogram", "kilograms"}:
        return quantity / FG_KG_PER_MT
    return quantity


def get_fg_quantity_multiplier(manufacturing_qty_mt) -> int:
    manufacturing_qty_mt = get_fg_numeric_limit(manufacturing_qty_mt)
    return calculate_fg_quantity_multiplier(manufacturing_qty_mt)


def build_fg_parent_control_plan_rows(
    control_plan_rows: list[frappe._dict],
    quantity_multiplier: int,
) -> list[dict[str, object]]:
    if not quantity_multiplier or quantity_multiplier <= 0:
        return []

    parent_rows: list[dict[str, object]] = []
    for row in control_plan_rows:
        payload = build_fg_control_plan_payload(row, quantity_multiplier)
        required_tests = cint(payload.get("custom_required_tests"))
        if required_tests <= 0:
            continue

        parent_rows.append(payload)

    return parent_rows


def build_fg_parameter_sample_payloads(plan_payloads: list[dict[str, object]]) -> list[dict[str, object]]:
    sample_rows: list[dict[str, object]] = []

    for plan_payload in plan_payloads:
        required_tests = cint(plan_payload.get("custom_required_tests"))
        for sample_no in range(1, required_tests + 1):
            sample_rows.append(
                {
                    "parameter_key": plan_payload.get("custom_parameter_key"),
                    "parameter": plan_payload.get("specification"),
                    "sample_no": sample_no,
                    "reading": "",
                    "result": "",
                }
            )

    return sample_rows


def get_applicable_fg_control_plan_rows(item_code: str) -> list[frappe._dict]:
    item_code = normalize_text(item_code)
    if not item_code or not frappe.db.exists("DocType", "FG Control Plan"):
        return []

    rows = frappe.get_all(
        "FG Control Plan",
        filters={
            "fg_item_code": item_code,
            "applicable": 1,
            "is_active": 1,
            "parameter": ["in", list(get_true_fg_testing_method_names())],
            **({"custom_inspection_scope": ["in", ["", "Final QC"]]} if frappe.get_meta("FG Control Plan").has_field("custom_inspection_scope") else {}),
        },
        fields=[
            "name",
            "parameter",
            "minimum_value",
            "maximum_value",
            "target_value",
            "unit",
            "size",
            "frequency",
            "critical_test",
            "test_type",
            "version",
        ],
        order_by="creation asc",
        limit_page_length=0,
    )
    method_rules = get_fg_testing_method_rule_map()
    return [
        row
        for row in rows
        if should_include_fg_control_plan_row(row, method_rules.get(normalize_text(row.get("parameter")), {}))
    ]


def populate_fg_template_readings(doc) -> None:
    if doc.doctype != "Quality Inspection" or not doc.item_code or doc.get("readings"):
        return

    template_name = normalize_text(doc.quality_inspection_template) or resolve_fg_inspection_template(
        doc.item_code,
        update_item=True,
    )
    if not template_name:
        return

    from erpnext.stock.doctype.quality_inspection_template.quality_inspection_template import (
        get_template_details,
    )

    doc.quality_inspection_template = template_name
    parameters = get_template_details(template_name)
    for parameter in parameters:
        child = doc.append("readings", {})
        child.update(parameter)
        child.status = ""
        child.custom_result_label = ""
        child.parameter_group = frappe.get_value(
            "Quality Inspection Parameter",
            parameter.specification,
            "parameter_group",
        )


def resolve_fg_inspection_template(item_code: str, update_item: bool = False) -> str:
    item_code = normalize_text(item_code)
    if not item_code:
        return ""

    item_template = normalize_text(frappe.db.get_value("Item", item_code, "quality_inspection_template"))
    if item_template and frappe.db.exists("Quality Inspection Template", item_template):
        return item_template

    fallback_template = get_fg_item_template_map().get(item_code, "")
    if not fallback_template or not frappe.db.exists("Quality Inspection Template", fallback_template):
        return ""

    if update_item and frappe.db.exists("Item", item_code) and not item_template:
        frappe.db.set_value(
            "Item",
            item_code,
            "quality_inspection_template",
            fallback_template,
            update_modified=False,
        )

    return fallback_template


@lru_cache(maxsize=1)
def get_fg_item_template_map() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / "data_foundation" / "generated" / "items_fg.csv"
    if not path.exists():
        return {}

    mapping: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            item_code = normalize_text(row.get("item_code")).upper()
            template_name = normalize_text(row.get("quality_template"))
            if item_code and template_name:
                mapping[item_code] = template_name

    return mapping


def build_fg_control_plan_payload(row: frappe._dict, quantity_multiplier: int) -> dict[str, object]:
    is_numeric = normalize_text(row.get("test_type")) != FG_TEST_TYPE_MANUAL
    sample_size = get_fg_numeric_limit(row.get("size"))
    frequency_value = get_fg_frequency_numeric_value(row.get("frequency"))
    required_tests = get_fg_required_test_count(quantity_multiplier, sample_size, frequency_value)
    min_value = get_fg_numeric_limit(row.get("minimum_value")) if is_numeric else None
    max_value = get_fg_numeric_limit(row.get("maximum_value")) if is_numeric else None
    if not row.get("source_exact_limits") and min_value not in (None, 0) and max_value == 0:
        max_value = None
    return {
        "specification": row.get("parameter"),
        "parameter": row.get("parameter"),
        "test_type": row.get("test_type") or FG_TEST_TYPE_NUMERIC,
        "numeric": 1 if is_numeric else 0,
        "manual_inspection": 0 if is_numeric else 1,
        "min_value": min_value,
        "max_value": max_value,
        "custom_target_value": normalize_text(row.get("target_value")),
        "custom_unit": normalize_text(row.get("unit")),
        "custom_sample_size": sample_size,
        "custom_frequency": normalize_text(row.get("frequency")),
        "custom_required_tests": required_tests,
        "custom_parameter_key": normalize_text(row.get("name")) or normalize_text(row.get("parameter")),
        "custom_critical_test": cint(row.get("critical_test")),
        "version": row.get("version"),
        "custom_test_condition": row.get("test_condition") or "",
    }


def get_fg_required_test_count(quantity_multiplier: int, sample_size, frequency_value) -> int:
    sample_size = get_fg_numeric_limit(sample_size)
    frequency_value = get_fg_numeric_limit(frequency_value)
    return calculate_fg_required_test_count(quantity_multiplier, sample_size, frequency_value)


def sync_fg_control_plan_readings(
    doc,
    control_plan_rows: list[frappe._dict],
    quantity_context: dict[str, object],
) -> None:
    set_fg_quantity_fields(doc, quantity_context)
    plan_payloads = build_fg_parent_control_plan_rows(control_plan_rows, cint(quantity_context.get("quantity_multiplier")))
    parameter_key_map = {normalize_text(payload.get("custom_parameter_key")): payload for payload in plan_payloads}
    parameter_spec_map = {normalize_text(payload.get("specification")): payload for payload in plan_payloads}

    invalid_rows = get_invalid_fg_legacy_rows(doc.get("readings", []), parameter_key_map, parameter_spec_map)
    invalid_samples = get_invalid_fg_sample_rows(doc.get("parameter_samples", []), parameter_key_map)
    if invalid_rows or invalid_samples:
        invalid_labels = sorted(set(invalid_rows + invalid_samples))
        frappe.throw(
            _("FG inspection rows must match the active FG Control Plan. Remove or remap extra row(s): {0}").format(
                ", ".join(invalid_labels)
            )
        )

    preserved_sample_entries = get_fg_preserved_sample_entries(
        doc.get("parameter_samples", []),
        doc.get("readings", []),
        parameter_key_map,
        parameter_spec_map,
    )

    rebuilt_rows = [build_fg_reading_row(payload) for payload in plan_payloads]
    rebuilt_samples = build_fg_parameter_sample_rows(plan_payloads, preserved_sample_entries)

    doc.set("readings", [])
    for row in rebuilt_rows:
        doc.append("readings", row)

    if doc.meta.has_field("parameter_samples"):
        doc.set("parameter_samples", [])
        for sample_row in rebuilt_samples:
            doc.append("parameter_samples", sample_row)

    required_tests = [
        cint(row.get("custom_required_tests"))
        for row in rebuilt_rows
        if cint(row.get("custom_required_tests")) > 0
    ]
    doc.sample_size = max(required_tests) if required_tests else None


def set_fg_quantity_fields(doc, quantity_context: dict[str, object]) -> None:
    if doc.meta.has_field("custom_manufacturing_qty_mt"):
        doc.custom_manufacturing_qty_mt = quantity_context.get("manufacturing_qty_mt")
    if doc.meta.has_field("custom_quantity_multiplier"):
        doc.custom_quantity_multiplier = cint(quantity_context.get("quantity_multiplier"))


def build_fg_reading_row(plan_payload: dict[str, object]) -> dict[str, object]:
    return {
        "specification": plan_payload["specification"],
        "numeric": cint(plan_payload["numeric"]),
        "manual_inspection": cint(plan_payload["manual_inspection"]),
        "min_value": plan_payload["min_value"],
        "max_value": plan_payload["max_value"],
        "custom_unit": plan_payload.get("custom_unit"),
        "custom_sample_size": plan_payload.get("custom_sample_size"),
        "custom_frequency": plan_payload.get("custom_frequency"),
        "custom_required_tests": cint(plan_payload.get("custom_required_tests")),
        "custom_test_sequence": 0,
        "custom_target_value": plan_payload.get("custom_target_value"),
        "custom_test_condition": plan_payload.get("custom_test_condition") or "",
        "custom_parameter_result": "",
        "custom_parameter_key": plan_payload.get("custom_parameter_key"),
        "custom_critical_test": cint(plan_payload["custom_critical_test"]),
        "acceptance_formula": "",
        "formula_based_criteria": 0,
    }

def fg_reading_has_user_input(reading) -> bool:
    if normalize_text(reading.get("value")):
        return True

    if normalize_text(reading.get("reading_value")):
        return True

    return any(normalize_text(reading.get(f"reading_{index}")) for index in range(1, 11))


def get_invalid_fg_legacy_rows(existing_rows, parameter_key_map, parameter_spec_map) -> list[str]:
    invalid_rows: list[str] = []

    for index, reading in enumerate(existing_rows or [], start=1):
        if not fg_reading_has_user_input(reading):
            continue

        if get_fg_parameter_payload_for_row(reading, parameter_key_map, parameter_spec_map):
            continue

        invalid_rows.append(normalize_text(reading.get("specification")) or _("(blank row {0})").format(index))

    return invalid_rows


def get_invalid_fg_sample_rows(existing_samples, parameter_key_map) -> list[str]:
    invalid_rows: list[str] = []

    for index, sample in enumerate(existing_samples or [], start=1):
        if not fg_parameter_sample_has_input(sample):
            continue

        sample_key = normalize_text(sample.get("parameter_key"))
        if sample_key and sample_key in parameter_key_map:
            continue

        invalid_rows.append(normalize_text(sample.get("parameter")) or _("(sample row {0})").format(index))

    return invalid_rows


def get_fg_parameter_payload_for_row(reading, parameter_key_map, parameter_spec_map):
    parameter_key = normalize_text(reading.get("custom_parameter_key"))
    if parameter_key and parameter_key in parameter_key_map:
        return parameter_key_map[parameter_key]

    specification = normalize_text(reading.get("specification"))
    return parameter_spec_map.get(specification)


def get_fg_preserved_sample_entries(
    existing_samples,
    existing_rows,
    parameter_key_map,
    parameter_spec_map,
) -> dict[str, list[dict[str, object]]]:
    preserved_entries = collect_fg_sample_entries(existing_samples, parameter_key_map)
    if fg_preserved_sample_entries_have_input(preserved_entries):
        return preserved_entries

    return collect_fg_legacy_sample_entries(existing_rows, parameter_key_map, parameter_spec_map)


def collect_fg_sample_entries(existing_samples, parameter_key_map) -> dict[str, list[dict[str, object]]]:
    entries = defaultdict(list)

    for sample in sorted(existing_samples or [], key=lambda row: (normalize_text(row.get("parameter_key")), cint(row.get("sample_no")) or 0, cint(row.get("idx")) or 0)):
        sample_key = normalize_text(sample.get("parameter_key"))
        if not sample_key or sample_key not in parameter_key_map:
            continue

        entries[sample_key].append(
            {
                "reading": normalize_text(sample.get("reading")),
                "result": normalize_text(sample.get("result")),
            }
        )

    return entries


def fg_preserved_sample_entries_have_input(entries: dict[str, list[dict[str, object]]]) -> bool:
    for sample_entries in entries.values():
        for entry in sample_entries:
            if normalize_text(entry.get("reading")) or normalize_text(entry.get("result")):
                return True
    return False


def collect_fg_legacy_sample_entries(existing_rows, parameter_key_map, parameter_spec_map) -> dict[str, list[dict[str, object]]]:
    entries = defaultdict(list)

    for reading in existing_rows or []:
        plan_payload = get_fg_parameter_payload_for_row(reading, parameter_key_map, parameter_spec_map)
        if not plan_payload:
            continue

        sample_key = normalize_text(plan_payload.get("custom_parameter_key"))
        for reading_value in extract_fg_legacy_sample_values(reading):
            entries[sample_key].append({"reading": reading_value, "result": ""})

    return entries


def extract_fg_legacy_sample_values(reading) -> list[str]:
    values = [normalize_text(reading.get(f"reading_{index}")) for index in range(1, 11)]
    values = [value for value in values if value]
    if values:
        return values

    for fieldname in ("reading_value", "value"):
        value = normalize_text(reading.get(fieldname))
        if value:
            return [value]

    return []


def build_fg_parameter_sample_rows(
    plan_payloads: list[dict[str, object]],
    preserved_sample_entries: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    sample_rows: list[dict[str, object]] = []

    for plan_payload in plan_payloads:
        parameter_key = normalize_text(plan_payload.get("custom_parameter_key"))
        required_tests = cint(plan_payload.get("custom_required_tests"))
        preserved_entries = preserved_sample_entries.get(parameter_key, [])
        preserve_upto = get_fg_preserved_sample_extent(preserved_entries)
        sample_count = max(required_tests, preserve_upto)

        for sample_no in range(1, sample_count + 1):
            preserved_entry = preserved_entries[sample_no - 1] if sample_no - 1 < len(preserved_entries) else {}
            sample_rows.append(
                {
                    "parameter_key": parameter_key,
                    "parameter": plan_payload.get("specification"),
                    "sample_no": sample_no,
                    "reading": normalize_text(preserved_entry.get("reading")),
                    "result": "",
                }
            )

    return sample_rows


def get_fg_preserved_sample_extent(preserved_entries: list[dict[str, object]]) -> int:
    last_non_empty_index = 0
    for index, entry in enumerate(preserved_entries or [], start=1):
        if normalize_text(entry.get("reading")) or normalize_text(entry.get("result")):
            last_non_empty_index = index
    return last_non_empty_index


def fg_parameter_sample_has_input(sample) -> bool:
    return bool(normalize_text(sample.get("reading")) or normalize_text(sample.get("result")))


def evaluate_fg_inspection_state(doc) -> None:
    if doc.doctype != "Quality Inspection" or not doc.item_code or is_rm_quality_context(doc):
        return

    if not doc.get("readings"):
        doc.manual_inspection = 0
        if doc.status != "Cancelled":
            doc.status = get_fg_incomplete_status(doc)
        if doc.meta.has_field("custom_overall_result"):
            doc.custom_overall_result = ""
        return

    pending_measurement_present = False
    critical_rejected_present = False
    non_critical_rejected_present = False
    review_required_present = False
    parameter_results: list[str] = []
    manual_row_present = False
    sample_map = get_fg_parameter_sample_map(doc)

    for reading in doc.readings:
        all_sample_rows = get_fg_sample_rows_for_reading(reading, sample_map)
        sample_rows = get_fg_relevant_sample_rows(reading, all_sample_rows)
        parameter_result = ""
        group_has_review = False
        group_has_rejected = False
        group_has_pending = False
        group_is_critical = bool(cint(reading.get("custom_critical_test")))
        if is_fg_manual_review_row(reading):
            manual_row_present = True

        if all_sample_rows:
            for sample_row in all_sample_rows:
                sample_row.result = ""

            for sample_row in sample_rows:
                if not fg_parameter_sample_has_reading(sample_row):
                    group_has_pending = True
                    pending_measurement_present = True
                    continue

                if is_fg_manual_review_row(reading):
                    sample_row.result = "Review Required"
                    group_has_review = True
                    continue

                sample_result = evaluate_fg_numeric_sample_result(reading, sample_row)
                if sample_result is None:
                    sample_row.result = "Review Required"
                    group_has_review = True
                    continue

                sample_row.result = "Pass" if sample_result else "Fail"
                if not sample_result:
                    group_has_rejected = True
        else:
            if not fg_reading_has_measurement(reading):
                group_has_pending = True
                pending_measurement_present = True
            elif is_fg_manual_review_row(reading):
                group_has_review = True
            else:
                numeric_result = evaluate_fg_numeric_row_status(reading)
                if numeric_result is None:
                    group_has_review = True
                elif not numeric_result:
                    group_has_rejected = True

        if group_has_rejected:
            parameter_result = "Rejected"
        elif group_has_review:
            parameter_result = "Review Required"
        elif group_has_pending:
            parameter_result = ""
        elif sample_rows or fg_reading_has_measurement(reading):
            parameter_result = "Accepted"

        set_fg_parent_row_result(reading, parameter_result)

        if parameter_result:
            parameter_results.append(parameter_result)
        if parameter_result == "Rejected":
            if group_is_critical:
                critical_rejected_present = True
            else:
                non_critical_rejected_present = True
        elif parameter_result == "Review Required":
            review_required_present = True

    doc.manual_inspection = 1 if manual_row_present else 0

    if doc.status != "Cancelled":
        if critical_rejected_present:
            doc.status = "Rejected"
        elif review_required_present or non_critical_rejected_present:
            doc.status = "Review Required"
        elif parameter_results and len(parameter_results) == len(doc.readings) and all(
            status == "Accepted" for status in parameter_results
        ):
            doc.status = "Accepted"
        else:
            doc.status = get_fg_incomplete_status(doc)

    if doc.meta.has_field("custom_overall_result"):
        if critical_rejected_present:
            doc.custom_overall_result = "REJECTED"
        elif review_required_present or non_critical_rejected_present:
            doc.custom_overall_result = "REVIEW REQUIRED"
        elif pending_measurement_present:
            doc.custom_overall_result = ""
        elif parameter_results and all(status == "Accepted" for status in parameter_results):
            doc.custom_overall_result = "ACCEPTED"
        else:
            doc.custom_overall_result = ""


def get_fg_incomplete_status(doc) -> str:
    if doc.get("custom_work_order_qc_stage") == "Final QC" and doc.docstatus == 0:
        return FG_INSPECTION_PENDING
    return ""


def get_fg_parameter_group_key(reading, row_index: int = 0) -> str:
    parameter_key = normalize_text(reading.get("custom_parameter_key"))
    if parameter_key:
        return parameter_key

    specification = normalize_text(reading.get("specification"))
    if specification:
        return specification

    return f"row-{row_index}"


def get_fg_parameter_sample_map(doc) -> dict[str, list]:
    grouped_rows: dict[str, list] = defaultdict(list)
    if not doc.meta.has_field("parameter_samples"):
        return grouped_rows

    sample_rows = sorted(
        doc.get("parameter_samples", []),
        key=lambda row: (
            normalize_text(row.get("parameter_key")),
            cint(row.get("sample_no")) or 0,
            cint(row.get("idx")) or 0,
        ),
    )
    for sample_row in sample_rows:
        grouped_rows[normalize_text(sample_row.get("parameter_key"))].append(sample_row)
    return grouped_rows


def get_fg_sample_rows_for_reading(reading, sample_map: dict[str, list]) -> list:
    return sample_map.get(get_fg_parameter_group_key(reading), [])


def get_fg_relevant_sample_rows(reading, sample_rows) -> list:
    if not sample_rows:
        return []

    required_tests = cint(reading.get("custom_required_tests"))
    sample_extent = get_fg_sample_row_extent(sample_rows)
    return list(sample_rows[: max(required_tests, sample_extent)])


def get_fg_sample_row_extent(sample_rows) -> int:
    last_non_empty_index = 0
    for index, sample_row in enumerate(sample_rows or [], start=1):
        if fg_parameter_sample_has_input(sample_row):
            last_non_empty_index = index
    return last_non_empty_index


def set_fg_parent_row_result(reading, parameter_result: str) -> None:
    reading.status = parameter_result
    if hasattr(reading, "custom_parameter_result"):
        reading.custom_parameter_result = parameter_result

    if not hasattr(reading, "custom_result_label"):
        return

    if parameter_result == "Accepted":
        reading.custom_result_label = "PASS"
    elif parameter_result == "Rejected":
        reading.custom_result_label = "FAIL"
    elif parameter_result == "Review Required":
        reading.custom_result_label = "REVIEW REQUIRED"
    else:
        reading.custom_result_label = ""


def fg_parameter_sample_has_reading(sample_row) -> bool:
    return bool(normalize_text(sample_row.get("reading")))


def fg_reading_has_measurement(reading) -> bool:
    if normalize_text(reading.get("value")) or normalize_text(reading.get("reading_value")):
        return True
    return any(normalize_text(reading.get(f"reading_{index}")) for index in range(1, 11))


def is_fg_manual_review_row(reading) -> bool:
    specification = normalize_text(reading.get("specification"))
    if specification in FG_MANUAL_REVIEW_PARAMETERS:
        return True

    if cint(reading.get("manual_inspection")):
        return True

    min_value = get_fg_numeric_limit(reading.get("min_value"))
    max_value = get_fg_numeric_limit(reading.get("max_value"))
    return min_value is None and max_value is None


def evaluate_fg_numeric_row_status(reading) -> bool | None:
    measurements = get_fg_measurements(reading)
    if measurements is None or not measurements:
        return None

    min_value = get_fg_numeric_limit(reading.get("min_value"))
    max_value = get_fg_numeric_limit(reading.get("max_value"))
    if min_value is None and max_value is None:
        return None

    for value in measurements:
        if min_value is not None and value < min_value:
            return False
        if max_value is not None and value > max_value:
            return False
    return True


def evaluate_fg_numeric_sample_result(reading, sample_row) -> bool | None:
    measurement = parse_float(normalize_text(sample_row.get("reading")))
    if measurement is None:
        return None

    min_value = get_fg_numeric_limit(reading.get("min_value"))
    max_value = get_fg_numeric_limit(reading.get("max_value"))
    if min_value is None and max_value is None:
        return None

    if min_value is not None and measurement < min_value:
        return False
    if max_value is not None and measurement > max_value:
        return False
    return True


def get_fg_measurements(reading) -> list[float] | None:
    measurements: list[float] = []
    for index in range(1, 11):
        raw_value = normalize_text(reading.get(f"reading_{index}"))
        if not raw_value:
            continue

        parsed_value = parse_float(raw_value)
        if parsed_value is None:
            return None
        measurements.append(parsed_value)

    if measurements:
        return measurements

    for fieldname in ("reading_value", "value"):
        raw_value = normalize_text(reading.get(fieldname))
        if not raw_value:
            continue
        parsed_value = parse_float(raw_value)
        if parsed_value is None:
            return None
        return [parsed_value]

    return []


def validate_fg_submission(doc, method=None) -> None:
    if doc.get("custom_work_order_qc_stage") == "In-Process QC":
        return
    if doc.doctype != "Quality Inspection" or not doc.item_code or is_rm_quality_context(doc):
        return

    if not uses_fg_control_plan(doc.item_code):
        return

    apply_fg_control_plan(doc)
    if not doc.get("readings"):
        frappe.throw(_("No applicable FG Control Plan parameters were loaded for this inspection."))

    missing_readings: list[str] = []
    invalid_readings: list[str] = []
    sample_map = get_fg_parameter_sample_map(doc)

    for index, reading in enumerate(doc.get("readings", []), start=1):
        sample_rows = get_fg_relevant_sample_rows(reading, get_fg_sample_rows_for_reading(reading, sample_map))
        if sample_rows:
            required_tests = cint(reading.get("custom_required_tests"))
            for sample_no in range(1, required_tests + 1):
                sample_row = sample_rows[sample_no - 1] if sample_no - 1 < len(sample_rows) else None
                reading_label = format_fg_required_reading_label(reading, sample_no)
                if not sample_row or not fg_parameter_sample_has_reading(sample_row):
                    missing_readings.append(reading_label)
                    continue

                if not is_fg_manual_review_row(reading) and evaluate_fg_numeric_sample_result(reading, sample_row) is None:
                    invalid_readings.append(reading_label)

            for sample_row in sample_rows[required_tests:]:
                if fg_parameter_sample_has_reading(sample_row) and not is_fg_manual_review_row(reading):
                    if evaluate_fg_numeric_sample_result(reading, sample_row) is None:
                        invalid_readings.append(
                            format_fg_required_reading_label(reading, cint(sample_row.get("sample_no")) or index)
                        )
            continue

        reading_label = format_fg_required_reading_label(reading, index)
        if not fg_reading_has_measurement(reading):
            missing_readings.append(reading_label)
            continue

        if not is_fg_manual_review_row(reading) and get_fg_measurements(reading) is None:
            invalid_readings.append(reading_label)

    if not missing_readings and not invalid_readings:
        return

    details = []
    if missing_readings:
        details.append(_("Missing required reading(s): {0}").format(", ".join(missing_readings[:10])))
    if invalid_readings:
        details.append(_("Invalid numeric reading(s): {0}").format(", ".join(invalid_readings[:10])))

    message = _("Enter all required FG readings before submitting the inspection.")
    if details:
        message += "<br>" + "<br>".join(details)
    frappe.throw(message)


def format_fg_required_reading_label(reading, sample_no: int) -> str:
    specification = normalize_text(reading.get("specification")) or _("Parameter")
    required_tests = cint(reading.get("custom_required_tests")) or 1
    return _("{0} [sample {1}/{2}]").format(specification, sample_no, required_tests)


def get_fg_numeric_limit(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return parse_float(value)


def get_fg_testing_method_rule_map() -> dict[str, dict[str, int]]:
    default_rules = {
        method_name: {
            "mandatory_qc": 0,
            "exclude_from_fg_inspection": 0,
        }
        for method_name in get_true_fg_testing_method_names()
    }
    if not frappe.db.exists("DocType", "FG Testing Method"):
        return default_rules

    meta = frappe.get_meta("FG Testing Method")
    fields = ["name"]
    if meta.has_field("mandatory_qc"):
        fields.append("mandatory_qc")
    if meta.has_field("exclude_from_fg_inspection"):
        fields.append("exclude_from_fg_inspection")

    for row in frappe.get_all("FG Testing Method", fields=fields, limit_page_length=0):
        method_name = normalize_text(row.get("name"))
        if not method_name:
            continue
        default_rules[method_name] = {
            "mandatory_qc": cint(row.get("mandatory_qc")),
            "exclude_from_fg_inspection": cint(row.get("exclude_from_fg_inspection")),
        }

    return default_rules


def should_include_fg_control_plan_row(row: frappe._dict, method_rules: dict[str, int] | None = None) -> bool:
    method_rules = method_rules or {}
    if cint(method_rules.get("exclude_from_fg_inspection")):
        return False

    if not has_positive_fg_control_requirement(row.get("size"), row.get("frequency")):
        return False

    if row_has_meaningful_fg_standard(row):
        return True

    return bool(cint(method_rules.get("mandatory_qc")))


def has_positive_fg_control_requirement(size, frequency) -> bool:
    size_value = get_fg_numeric_limit(size)
    frequency_value = get_fg_frequency_numeric_value(frequency)
    return (
        size_value is not None
        and size_value > 0
        and frequency_value is not None
        and frequency_value > 0
    )


def get_fg_frequency_numeric_value(value) -> float | None:
    parsed_value = get_fg_numeric_limit(value)
    if parsed_value is not None:
        return parsed_value

    cleaned_value = normalize_text(value)
    if not cleaned_value:
        return None

    for match in re.findall(r"\d+(?:\.\d+)?", cleaned_value):
        parsed_match = parse_float(match)
        if parsed_match is not None:
            return parsed_match

    return None


def row_has_meaningful_fg_standard(row: frappe._dict) -> bool:
    target_value = normalize_text(row.get("target_value"))
    min_present = row.get("minimum_value") not in (None, "")
    max_present = row.get("maximum_value") not in (None, "")
    min_value = get_fg_numeric_limit(row.get("minimum_value"))
    max_value = get_fg_numeric_limit(row.get("maximum_value"))

    if target_value:
        return True

    if min_present or max_present:
        if min_value not in (None, 0):
            return True
        if max_value not in (None, 0):
            return True

    return False
