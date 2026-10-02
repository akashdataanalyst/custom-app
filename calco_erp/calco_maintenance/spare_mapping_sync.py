import frappe

from calco_erp.calco_maintenance.machine_master_sync import resolve_machine_docname
from calco_erp.calco_maintenance.master_data_utils import cstr, get_reference_groups


def sync_machine_spare_mapping_after_migrate():
    if not frappe.db.exists("DocType", "Maintenance Spare Mapping"):
        return

    if not frappe.db.has_column("Maintenance Spare Mapping", "machine"):
        return

    sync_mapping_rows()
    frappe.clear_cache(doctype="Maintenance Spare Mapping")


def sync_mapping_rows():
    rows = frappe.get_all(
        "Maintenance Spare Mapping",
        fields=[
            "name",
            "machine",
            "equipment",
            "spare_item",
            "source_machine_name",
            "equipment_group",
            "critical",
            "standard_qty",
            "is_active",
        ],
        limit_page_length=0,
    )

    item_critical_cache = {}

    for row in rows:
        critical = row.critical if row.critical is not None else get_item_critical(row.spare_item, item_critical_cache)
        standard_qty = row.standard_qty or 1
        is_active = 1 if row.is_active is None else row.is_active
        machine = cstr(row.machine) or cstr(row.equipment)
        candidates = get_candidate_machines(row.source_machine_name, row.equipment_group, machine)

        if machine:
            upsert_mapping_row(
                row.name,
                machine=machine,
                spare_item=row.spare_item,
                source_machine_name=row.source_machine_name,
                equipment_group=row.equipment_group,
                critical=critical,
                standard_qty=standard_qty,
                is_active=is_active,
            )
            if machine in candidates:
                candidates.remove(machine)
        elif len(candidates) == 1:
            machine = candidates.pop(0)
            upsert_mapping_row(
                row.name,
                machine=machine,
                spare_item=row.spare_item,
                source_machine_name=row.source_machine_name,
                equipment_group=row.equipment_group,
                critical=critical,
                standard_qty=standard_qty,
                is_active=is_active,
            )
        else:
            updates = {}
            if row.critical != critical:
                updates["critical"] = critical
            if row.standard_qty != standard_qty:
                updates["standard_qty"] = standard_qty
            if row.is_active != is_active:
                updates["is_active"] = is_active
            if updates:
                frappe.db.set_value("Maintenance Spare Mapping", row.name, updates, update_modified=False)

        for candidate in candidates:
            existing_name = frappe.db.get_value(
                "Maintenance Spare Mapping",
                {
                    "machine": candidate,
                    "spare_item": row.spare_item,
                    "source_machine_name": row.source_machine_name,
                },
                "name",
            )

            if existing_name:
                upsert_mapping_row(
                    existing_name,
                    machine=candidate,
                    spare_item=row.spare_item,
                    source_machine_name=row.source_machine_name,
                    equipment_group=row.equipment_group,
                    critical=critical,
                    standard_qty=standard_qty,
                    is_active=is_active,
                )
                continue

            frappe.get_doc(
                {
                    "doctype": "Maintenance Spare Mapping",
                    "machine": candidate,
                    "equipment": candidate,
                    "spare_item": row.spare_item,
                    "source_machine_name": row.source_machine_name,
                    "equipment_group": row.equipment_group,
                    "critical": critical,
                    "standard_qty": standard_qty,
                    "is_active": is_active,
                }
            ).insert(ignore_permissions=True)


def get_item_critical(item_code, cache):
    if item_code not in cache:
        cache[item_code] = int(frappe.db.get_value("Item", item_code, "custom_maintenance_critical") or 0)
    return cache[item_code]


def get_candidate_machines(source_machine_name, equipment_group, machine):
    candidates = []

    machine = cstr(machine)
    if machine and frappe.db.exists("Maintenance Equipment", machine):
        return [machine]

    exact_machine = resolve_machine_docname(source_machine_name)
    if exact_machine:
        return [exact_machine]

    groups = get_reference_groups(source_machine_name, equipment_group)
    if not groups:
        return []

    for group in sorted(groups):
        rows = frappe.get_all(
            "Maintenance Equipment",
            filters={"equipment_group": group, "active": 1},
            fields=["name"],
            limit_page_length=0,
            order_by="name asc",
        )
        candidates.extend(row.name for row in rows)

    return list(dict.fromkeys(candidates))


def upsert_mapping_row(name, machine, spare_item, source_machine_name, equipment_group, critical, standard_qty, is_active):
    updates = {
        "machine": machine,
        "equipment": machine,
        "spare_item": spare_item,
        "source_machine_name": source_machine_name,
        "equipment_group": equipment_group,
        "critical": critical,
        "standard_qty": standard_qty,
        "is_active": is_active,
    }
    frappe.db.set_value("Maintenance Spare Mapping", name, updates, update_modified=False)
