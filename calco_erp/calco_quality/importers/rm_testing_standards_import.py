from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import frappe
from calco_erp.calco_quality.rm_quality_setup import ensure_rm_quality_setup
from calco_erp.calco_quality.rm_testing_utils import (
    build_rm_template_name,
    derive_approval_rule,
    derive_target_value,
    infer_result_type_from_rules,
    normalize_text,
    parse_float,
    resolve_item_by_code_or_name,
)
from calco_erp.foundation_setup import QUALITY_PARAMETER_GROUP, ensure_quality_parameter_group


SHEET_TESTING_TYPES = "Testing Types"
SHEET_RM_STANDARDS = "RM Standards"
TEST_TYPE_ALIASES = {
    "mfi (gm/10min)": "MFI (gm/10 min)",
}


def import_workbook(path: str) -> dict[str, object]:
    workbook_path = Path(path)
    if not workbook_path.exists():
        frappe.throw(f"Workbook not found at {workbook_path}")

    ensure_quality_parameter_group()
    ensure_rm_quality_setup()

    workbook_data = parse_xlsx_workbook(workbook_path)
    testing_rows = workbook_data.get(SHEET_TESTING_TYPES, [])
    standard_rows = workbook_data.get(SHEET_RM_STANDARDS, [])

    rules_by_test = defaultdict(list)
    for row in standard_rows:
        rule = derive_approval_rule(
            row.get("min"),
            row.get("max"),
            row.get("target_value"),
        )
        rules_by_test[resolve_testing_type_name(row.get("test_type"))].append(rule)

    testing_summary = import_testing_types(testing_rows, rules_by_test)
    fallback_summary = import_missing_testing_types_from_standards(standard_rows, rules_by_test)
    testing_summary["created"] += fallback_summary["created"]
    testing_summary["imported_names"].extend(fallback_summary["imported_names"])
    testing_summary["fallback_created"] = fallback_summary["created"]
    standard_summary = import_rm_testing_standards(standard_rows)
    template_summary = sync_rm_quality_templates()

    frappe.db.commit()

    return {
        "testing_types": testing_summary,
        "rm_testing_standards": standard_summary,
        "templates": template_summary,
    }


def import_testing_types(rows, rules_by_test) -> dict[str, object]:
    created = 0
    updated = 0
    imported_names: list[str] = []

    for row in rows:
        test_name = resolve_testing_type_name(row.get("test_type"))
        if not test_name:
            continue

        result_type = infer_result_type_from_rules(rules_by_test.get(test_name, []))
        description_parts = [
            f"Test Standard: {normalize_text(row.get('test_standard'))}" if normalize_text(row.get("test_standard")) else "",
            f"CPPL Method: {normalize_text(row.get('cppl_method'))}" if normalize_text(row.get("cppl_method")) else "",
            f"Test Condition: {normalize_text(row.get('test_condition'))}" if normalize_text(row.get("test_condition")) else "",
            f"Unit: {normalize_text(row.get('unit'))}" if normalize_text(row.get("unit")) else "",
        ]
        description = "\n".join([part for part in description_parts if part])

        if frappe.db.exists("Quality Inspection Parameter", test_name):
            doc = frappe.get_doc("Quality Inspection Parameter", test_name)
            updated += 1
        else:
            doc = frappe.new_doc("Quality Inspection Parameter")
            doc.parameter = test_name
            doc.parameter_group = QUALITY_PARAMETER_GROUP
            created += 1

        doc.parameter = test_name
        doc.parameter_group = QUALITY_PARAMETER_GROUP
        doc.description = description
        doc.custom_is_raw_material_test = 1
        doc.custom_test_standard = normalize_text(row.get("test_standard"))
        doc.custom_cppl_method = normalize_text(row.get("cppl_method"))
        doc.custom_test_condition = normalize_text(row.get("test_condition"))
        doc.custom_unit = normalize_text(row.get("unit"))
        doc.custom_result_type = result_type

        if doc.is_new():
            doc.insert(ignore_permissions=True)
        else:
            doc.save(ignore_permissions=True)

        imported_names.append(test_name)

    return {
        "created": created,
        "updated": updated,
        "imported_names": imported_names,
    }


def import_missing_testing_types_from_standards(rows, rules_by_test) -> dict[str, object]:
    created = 0
    imported_names: list[str] = []

    for row in rows:
        test_name = resolve_testing_type_name(row.get("test_type"))
        if not test_name or frappe.db.exists("Quality Inspection Parameter", test_name):
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Quality Inspection Parameter",
                "parameter": test_name,
                "parameter_group": QUALITY_PARAMETER_GROUP,
                "description": "Imported from RM Standards because the Testing Types tab had no matching master row.",
                "custom_is_raw_material_test": 1,
                "custom_result_type": infer_result_type_from_rules(rules_by_test.get(test_name, [])),
            }
        )
        doc.insert(ignore_permissions=True)
        created += 1
        imported_names.append(test_name)

    return {"created": created, "imported_names": imported_names}


def import_rm_testing_standards(rows) -> dict[str, object]:
    created = 0
    updated = 0
    unmatched_items: list[str] = []
    unmatched_tests: list[str] = []

    for row in rows:
        rm_name = normalize_text(row.get("rm_name"))
        test_type = resolve_testing_type_name(row.get("test_type"))
        min_value = normalize_text(row.get("min"))
        max_value = normalize_text(row.get("max"))

        if not rm_name or not test_type:
            continue

        rm_item = resolve_item_by_code_or_name(rm_name)
        if not rm_item:
            if rm_name not in unmatched_items:
                unmatched_items.append(rm_name)
            continue

        if not frappe.db.exists("Quality Inspection Parameter", test_type):
            if test_type not in unmatched_tests:
                unmatched_tests.append(test_type)
            continue

        target_value = derive_target_value(min_value, max_value)
        approval_rule = derive_approval_rule(min_value, max_value, target_value)
        parameter_meta = frappe.db.get_value(
            "Quality Inspection Parameter",
            test_type,
            [
                "custom_unit",
                "custom_test_standard",
                "custom_cppl_method",
                "custom_test_condition",
            ],
            as_dict=True,
        ) or {}

        existing_name = frappe.db.get_value(
            "RM Testing Standard",
            {"rm_item": rm_item, "testing_type": test_type},
            "name",
        )
        if existing_name:
            doc = frappe.get_doc("RM Testing Standard", existing_name)
            updated += 1
        else:
            doc = frappe.new_doc("RM Testing Standard")
            created += 1

        doc.rm_item = rm_item
        doc.rm_code = rm_name
        doc.testing_type = test_type
        doc.acceptable_min = min_value
        doc.acceptable_max = max_value
        doc.target_value = target_value
        doc.unit = normalize_text(parameter_meta.get("custom_unit"))
        doc.test_standard = normalize_text(parameter_meta.get("custom_test_standard"))
        doc.cppl_method = normalize_text(parameter_meta.get("custom_cppl_method"))
        doc.test_condition = normalize_text(parameter_meta.get("custom_test_condition"))
        doc.approval_rule = approval_rule
        doc.is_active = 1

        if doc.is_new():
            doc.insert(ignore_permissions=True)
        else:
            doc.save(ignore_permissions=True)

    return {
        "created": created,
        "updated": updated,
        "unmatched_items": unmatched_items,
        "unmatched_tests": unmatched_tests,
    }


def sync_rm_quality_templates() -> dict[str, int]:
    created = 0
    updated = 0
    item_updates = 0

    standards = frappe.get_all(
        "RM Testing Standard",
        filters={"is_active": 1},
        fields=[
            "name",
            "rm_item",
            "testing_type",
            "acceptable_min",
            "acceptable_max",
            "target_value",
            "approval_rule",
        ],
        order_by="rm_item asc, testing_type asc",
    )

    standards_by_item = defaultdict(list)
    for row in standards:
        standards_by_item[row.rm_item].append(row)

    for rm_item, rows in standards_by_item.items():
        item_code = frappe.db.get_value("Item", rm_item, "item_code") or rm_item
        template_name = build_rm_template_name(item_code)

        if frappe.db.exists("Quality Inspection Template", template_name):
            template = frappe.get_doc("Quality Inspection Template", template_name)
            updated += 1
        else:
            template = frappe.new_doc("Quality Inspection Template")
            created += 1

        template.quality_inspection_template_name = template_name
        template.set("item_quality_inspection_parameter", [])

        for row in rows:
            row_data = {
                "specification": row.testing_type,
                "parameter_group": QUALITY_PARAMETER_GROUP,
            }

            if row.approval_rule == "Numeric Range":
                row_data.update(
                    {
                        "numeric": 1,
                        "min_value": parse_float(row.acceptable_min),
                        "max_value": parse_float(row.acceptable_max),
                    }
                )
            else:
                guidance = (
                    normalize_text(row.target_value)
                    or normalize_text(row.acceptable_max)
                    or normalize_text(row.acceptable_min)
                )
                row_data.update({"numeric": 0, "value": guidance})

            template.append("item_quality_inspection_parameter", row_data)

        if template.is_new():
            template.insert(ignore_permissions=True)
        else:
            template.save(ignore_permissions=True)

        if frappe.db.get_value("Item", rm_item, "quality_inspection_template") != template_name:
            frappe.db.set_value(
                "Item",
                rm_item,
                "quality_inspection_template",
                template_name,
                update_modified=False,
            )
            item_updates += 1

        for standard in rows:
            if standard.name:
                frappe.db.set_value(
                    "RM Testing Standard",
                    standard.name,
                    "quality_inspection_template",
                    template_name,
                    update_modified=False,
                )

    return {
        "templates_created": created,
        "templates_updated": updated,
        "items_updated": item_updates,
    }


def parse_xlsx_workbook(path: Path) -> dict[str, list[dict[str, str]]]:
    with ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        workbook_sheets = read_workbook_sheet_map(archive)
        return {
            sheet_name: read_sheet_rows(archive, sheet_path, shared_strings)
            for sheet_name, sheet_path in workbook_sheets.items()
        }


def read_shared_strings(archive: ZipFile) -> list[str]:
    try:
        xml = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for item in xml.findall("x:si", namespace):
        texts = ["".join(text.itertext()) for text in item.findall(".//x:t", namespace)]
        values.append(normalize_text(" ".join(filter(None, texts))))
    return values


def read_workbook_sheet_map(archive: ZipFile) -> dict[str, str]:
    namespace = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
        for rel in relationships.findall("rel:Relationship", namespace)
    }

    sheet_map = {}
    for sheet in workbook.findall("x:sheets/x:sheet", namespace):
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rel_id)
        if not target:
            continue
        sheet_map[sheet.attrib["name"]] = f"xl/{target}" if not target.startswith("xl/") else target
    return sheet_map


def read_sheet_rows(archive: ZipFile, sheet_path: str, shared_strings: list[str]) -> list[dict[str, str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(archive.read(sheet_path))
    rows = []
    headers = []

    for row in root.findall("x:sheetData/x:row", namespace):
        values_by_index = {}
        for cell in row.findall("x:c", namespace):
            cell_ref = cell.attrib.get("r", "")
            column_letters = "".join(character for character in cell_ref if character.isalpha())
            column_index = column_name_to_index(column_letters)
            values_by_index[column_index] = read_cell_value(cell, namespace, shared_strings)

        if not headers:
            if not values_by_index:
                continue
            max_index = max(values_by_index)
            headers = [normalize_header(values_by_index.get(index, "")) for index in range(1, max_index + 1)]
            continue

        row_data = {
            headers[index - 1]: normalize_text(values_by_index.get(index, ""))
            for index in range(1, len(headers) + 1)
            if headers[index - 1]
        }
        if any(row_data.values()):
            rows.append(row_data)

    return rows


def read_cell_value(cell, namespace, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        index_node = cell.find("x:v", namespace)
        if index_node is None:
            return ""
        index = int(index_node.text)
        return shared_strings[index] if index < len(shared_strings) else ""

    if cell_type == "inlineStr":
        text_node = cell.find(".//x:t", namespace)
        return normalize_text(text_node.text if text_node is not None else "")

    value_node = cell.find("x:v", namespace)
    return normalize_text(value_node.text if value_node is not None else "")


def column_name_to_index(column_name: str) -> int:
    result = 0
    for character in column_name:
        result = result * 26 + ord(character.upper()) - 64
    return result


def normalize_header(value: str) -> str:
    mapping = {
        "test type": "test_type",
        "test standard": "test_standard",
        "cppl method": "cppl_method",
        "test condition": "test_condition",
        "unit": "unit",
        "rm name": "rm_name",
        "min": "min",
        "max": "max",
    }
    return mapping.get(normalize_text(value).lower(), "")


def resolve_testing_type_name(value: str) -> str:
    normalized = normalize_text(value)
    return TEST_TYPE_ALIASES.get(normalized.lower(), normalized)
