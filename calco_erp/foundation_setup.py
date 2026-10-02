import frappe


QUALITY_PARAMETER_GROUP = "Calco PolyTechnik Pvt Ltd"

QUALITY_PARAMETERS = [
    ("Moisture", "Moisture percentage"),
    ("MFI", "Melt Flow Index"),
    ("Ash", "Ash content percentage"),
    ("Density", "Density"),
]

QUALITY_TEMPLATES = {
    "Calco Incoming RM QC": [
        {"specification": "Moisture", "numeric": 1},
        {"specification": "MFI", "numeric": 1},
        {"specification": "Ash", "numeric": 1},
        {"specification": "Density", "numeric": 1},
    ],
    "Calco Final FG QC": [
        {"specification": "Moisture", "numeric": 1},
        {"specification": "MFI", "numeric": 1},
        {"specification": "Ash", "numeric": 1},
        {"specification": "Density", "numeric": 1},
    ],
}


def ensure_foundation_records():
    rename_legacy_quality_parameter_group()
    ensure_quality_parameter_group()
    ensure_quality_parameters()
    ensure_quality_templates()
    frappe.clear_cache()


def rename_legacy_quality_parameter_group():
    legacy_name = "Calco Polymer"
    if not frappe.db.exists("Quality Inspection Parameter Group", legacy_name):
        return

    if not frappe.db.exists("Quality Inspection Parameter Group", QUALITY_PARAMETER_GROUP):
        frappe.rename_doc(
            "Quality Inspection Parameter Group",
            legacy_name,
            QUALITY_PARAMETER_GROUP,
            force=True,
        )
        return

    frappe.db.sql(
        """
        update `tabQuality Inspection Parameter`
        set parameter_group = %s
        where parameter_group = %s
        """,
        (QUALITY_PARAMETER_GROUP, legacy_name),
    )


def ensure_quality_parameter_group():
    if frappe.db.exists("Quality Inspection Parameter Group", QUALITY_PARAMETER_GROUP):
        return

    frappe.get_doc(
        {
            "doctype": "Quality Inspection Parameter Group",
            "group_name": QUALITY_PARAMETER_GROUP,
        }
    ).insert(ignore_permissions=True)


def ensure_quality_parameters():
    for parameter, description in QUALITY_PARAMETERS:
        if frappe.db.exists("Quality Inspection Parameter", parameter):
            continue

        frappe.get_doc(
            {
                "doctype": "Quality Inspection Parameter",
                "parameter": parameter,
                "parameter_group": QUALITY_PARAMETER_GROUP,
                "description": description,
            }
        ).insert(ignore_permissions=True)


def ensure_quality_templates():
    for template_name, readings in QUALITY_TEMPLATES.items():
        if frappe.db.exists("Quality Inspection Template", template_name):
            template = frappe.get_doc("Quality Inspection Template", template_name)
        else:
            template = frappe.new_doc("Quality Inspection Template")
            template.quality_inspection_template_name = template_name

        template.quality_inspection_template_name = template_name
        template.set("item_quality_inspection_parameter", [])
        for reading in readings:
            template.append(
                "item_quality_inspection_parameter",
                {
                    "specification": reading["specification"],
                    "parameter_group": QUALITY_PARAMETER_GROUP,
                    "numeric": reading["numeric"],
                },
            )

        if template.is_new():
            template.insert(ignore_permissions=True)
        else:
            template.save(ignore_permissions=True)
