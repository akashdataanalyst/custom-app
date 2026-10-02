"""Targeted native metadata installation, with a reviewed target-state plan.

Does not execute migration hooks, patches, fixture import, or business setup.
DDL is not transactionally reversible: inspect the plan and backup first.
"""
import json
from pathlib import Path

import frappe
from calco_erp.release_20260917.master_sync import digest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).with_name('metadata.json')
VOLATILE = {'creation', 'modified', 'modified_by', 'owner', 'idx', 'docstatus',
            'parent', 'parentfield', 'parenttype', '__islocal', '__unsaved'}


def canonical(value, child=False):
    if isinstance(value, list):
        return [canonical(v, True) for v in value]
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()
                if k not in VOLATILE and not (child and k in ('name', 'doctype'))}
    return value


def project(current, desired):
    if isinstance(desired, dict):
        return {k: project((current or {}).get(k), v) for k, v in desired.items() if k not in VOLATILE}
    if isinstance(desired, list):
        if not isinstance(current, list) or len(current) != len(desired):
            return current
        return [project(old, new) for old, new in zip(current, desired)]
    return current

def field_conflicts(before, after):
    conflicts = []
    old = {r['fieldname']: r for r in before.get('fields', [])}
    new = {r['fieldname']: r for r in after.get('fields', [])}
    for name, row in old.items():
        if name not in new:
            conflicts.append('Existing field absent from package: ' + name)
        elif row.get('fieldtype') != new[name].get('fieldtype'):
            conflicts.append('Field type changes: ' + name)
        elif row.get('fieldtype') in ('Link', 'Table', 'Table MultiSelect') and row.get('options') != new[name].get('options'):
            conflicts.append('Field linkage changes: ' + name)
    return conflicts


def live_purchase_definitions(include_workspace=True):
    """Approved, snapshot-derived UI/report metadata only; no Server Scripts or business data."""
    data = json.loads((PACKAGE.parent / 'live_purchase_20260927.json').read_text(encoding='utf-8'))
    entries = [dict(r, child=False) for r in data['records']]
    if include_workspace:
        workspace = json.loads((ROOT / 'calco_purchase/workspace/purchase/purchase.json').read_text(encoding='utf-8'))
        entries.append(dict(doctype='Workspace', name='Purchase', child=False, document=dict(
            doctype='Workspace', name='Purchase', **{field: workspace[field]
                for field in ('content', 'shortcuts', 'custom_blocks')})))
    return entries


def definitions(scope=None):
    if scope == "bom_formulation_20260929":
        from calco_erp.planning_upgrade.formulation import definitions as formulation_definitions
        return formulation_definitions()
    if scope == "final_four_20260928":
        from calco_erp.final_four.metadata import definitions as final_definitions
        return final_definitions()
    if scope == "holistic_ui_20260928":
        from calco_erp.holistic_ui.metadata import definitions as holistic_definitions
        return holistic_definitions()
    if scope == "planning_upgrade_20260927":
        from calco_erp.planning_upgrade.metadata import definitions as upgrade_definitions
        return upgrade_definitions()
    if scope == "purchase_live_20260927":
        return live_purchase_definitions()
    if scope is not None:
        if scope != "enterprise_ui":
            raise ValueError("Unknown targeted metadata scope")
        source = "calco_production/page/calco_home/calco_home.json"
        return [dict(doctype="Role", name="Calco New UI", child=False,
                     document=dict(doctype="Role", name="Calco New UI", role_name="Calco New UI", desk_access=1)),
                dict(doctype="Page", name="calco-home", child=False, source=source,
                     document=json.loads((ROOT / source).read_text(encoding="utf-8-sig")))]
    data = json.loads(PACKAGE.read_text(encoding='utf-8'))
    result = []
    for r in data['source_metadata']:
        path = (ROOT / r['path']).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Invalid metadata source path')
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        result.append(dict(doctype=r['doctype'], name=r['name'], source=r['path'], document=document,
                           child=r['child']))
    for key, dt in [('custom_fields', 'Custom Field'), ('client_scripts', 'Client Script'),
                    ('property_setters', 'Property Setter')]:
        for r in data[key]:
            name = r.get('name') or r['dt'] + '-' + r['fieldname']
            result.append(dict(doctype=dt, name=name, document=dict(r, doctype=dt, name=name), child=False))
    result.extend(live_purchase_definitions(include_workspace=False))
    # Metadata dependencies are derived from the same approved application
    # definitions; no Recovery users, permissions assignments or transactions.
    roles = sorted({row['role'] for entry in result for key in ('permissions', 'roles')
                    for row in entry['document'].get(key, []) if row.get('role')})
    for role in roles:
        result.append(dict(doctype='Role', name=role, child=False,
            document=dict(doctype='Role', name=role, role_name=role,
                          **({'desk_access':1} if role=='Calco New UI' else {}))))
    from calco_erp import workspace_setup, workspace_presentation
    referenced = {r['number_card_name'] for e in result if e['doctype']=='Workspace'
                  for r in e['document'].get('number_cards', []) if r.get('number_card_name')}
    for card in workspace_setup.ALL_NUMBER_CARDS:
        if card['label'] not in referenced:
            continue
        name = card['label']
        result.append(dict(doctype='Number Card', name=name, child=False, document=dict(
            doctype='Number Card', name=name, label=name, module=card.get('module') or 'Calco Maintenance',
            type='Document Type', document_type=card['document_type'], function='Count',
            is_public=1, show_full_number=1, filters_json=json.dumps(card.get('filters') or []),
            dynamic_filters_json=json.dumps(card.get('dynamic_filters') or []))))
    for config in workspace_setup.WORKSPACES:
        if config['name'] not in workspace_presentation.WORKSPACES:
            continue
        name = config['name']
        result.append(dict(doctype='Workspace Sidebar', name=name, child=False, document=dict(
            doctype='Workspace Sidebar', name=name, title=name, header_icon=config['icon'], module=config['module'],
            items=workspace_presentation.sidebar_items(name, workspace_presentation.definition(name)))))
    from calco_erp.planning_upgrade.metadata import definitions as planning_definitions
    from calco_erp.holistic_ui.metadata import definitions as holistic_definitions
    result.extend(holistic_definitions())
    indexed = {(r['doctype'], r['name']): r for r in result}
    for entry in planning_definitions():
        indexed[(entry['doctype'], entry['name'])] = entry
    from calco_erp.final_four.metadata import definitions as final_definitions
    for entry in final_definitions():
        indexed[(entry["doctype"], entry["name"])] = entry
    # Keep the consolidated manifest aligned with the scoped formulation update.
    # Merge existing category metadata rather than dropping unrelated field controls.
    from calco_erp.planning_upgrade.formulation import definitions as formulation_definitions
    for entry in formulation_definitions():
        key = (entry['doctype'], entry['name'])
        if key in indexed:
            entry = dict(entry, document=dict(indexed[key]['document'], **entry['document']))
        indexed[key] = entry
    return list(indexed.values())


def plan(scope=None):
    result = []
    for entry in definitions(scope):
        dt, name, desired = entry['doctype'], entry['name'], entry['document']
        old = frappe.get_doc(dt, name).as_dict() if frappe.db.exists(dt, name) else None
        # field_order is a source serialization directive, stored as child idx.
        # disable_prepared_report is not a persisted Report field in this native
        # version; prepared_report is the actual stored control and is compared.
        desired = dict(desired)
        # Native DocField.default is a text column, including numeric defaults.
        if dt == 'DocType':
            desired['fields'] = [dict(f, default=str(f['default'])) if isinstance(f.get('default'), (int, float)) else dict(f) for f in desired.get('fields', [])]
        if dt == 'DocType' and 'field_order' in desired:
            order = {name: index for index, name in enumerate(desired['field_order'])}
            desired['fields'] = sorted(desired.get('fields', []), key=lambda f: order.get(f['fieldname'], len(order)))
            if old:
                old['field_order'] = [f['fieldname'] for f in old.get('fields', [])]
        if dt == 'Report':
            desired.pop('disable_prepared_report', None)
        conflicts = []
        if old and dt == 'DocType':
            conflicts = field_conflicts(old, desired)
        if old and dt == 'Custom Field':
            conflicts = field_conflicts({'fields': [old]}, {'fields': [desired]})
        native = False
        if dt == 'Custom Field' and frappe.db.exists('DocType', desired['dt']):
            native = bool(frappe.db.exists('DocField', {'parent': desired['dt'], 'fieldname': desired['fieldname']}))
            if native:
                conflicts.append('Already a native DocField; reconcile duplicate Custom Field definition')
        projection = project(old, desired) if old else None
        same = old is not None and canonical(projection) == canonical(desired)
        result.append(dict(doctype=dt, name=name, source=entry.get('source'),
                           classification='Conflict' if conflicts else 'Already Current' if same else 'Update' if old else 'Create',
                           conflicts=conflicts, desired_hash=digest(desired), current_hash=digest(old)))
    payload = dict(site=frappe.local.site, records=result,
                   schema_required=any(r['doctype'] in ('DocType', 'Custom Field') and
                                       r['classification'] != 'Already Current' for r in result))
    if scope is not None:
        payload["scope"] = scope
    return dict(payload, fingerprint=digest(payload))


def apply(expected_site, reviewed_fingerprint, approval_reference, scope=None):
    if frappe.session.user != 'Administrator':
        frappe.throw('Administrator required for targeted release metadata.', frappe.PermissionError)
    if expected_site != frappe.local.site or not str(approval_reference or '').strip():
        raise ValueError('Exact site and review reference required')
    review = plan(scope)
    if review['fingerprint'] != reviewed_fingerprint:
        raise ValueError('Metadata changed since review; regenerate plan')
    if any(r['classification'] == 'Conflict' for r in review['records']):
        raise ValueError('Metadata conflict: no targeted installation performed')
    current = {(r['doctype'], r['name']): r for r in review['records']}
    entries = sorted(definitions(scope), key=lambda r: (r.get('apply_priority', -1 if r['doctype'] == 'Role' else 0 if r['doctype'] == 'DocType' and r['child']
                        else 1 if r['doctype'] == 'DocType' else 2 if r['doctype'] == 'Custom Field' else 3 if r['doctype'] in ('Number Card', 'Custom HTML Block', 'Report') else 4 if r['doctype'] != 'Workspace Sidebar' else 5),
                        r['name']))
    applied = []
    for entry in entries:
        dt, name = entry['doctype'], entry['name']
        if current[(dt, name)]['classification'] == 'Already Current':
            continue
        if entry.get('source'):
            parts = Path(entry['source']).parts
            frappe.reload_doc(parts[0], parts[1], parts[2], force=True)
        elif dt == 'Custom Field':
            from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
            values = {k: v for k, v in entry['document'].items() if k not in ('doctype', 'name', 'dt')}
            create_custom_fields({entry['document']['dt']: [values]}, update=True)
        else:
            values = entry['document']
            if frappe.db.exists(dt, name):
                doc = frappe.get_doc(dt, name)
                doc.update({k: v for k, v in values.items() if k not in ('doctype', 'name')})
                doc.save()
            else:
                frappe.get_doc(values).insert()
        applied.append(dict(doctype=dt, name=name))
    # Exact existing Gate 1 uniqueness invariant; no activation or routing.
    for dt in (() if scope is not None else ('Production Receipt Policy Boundary', 'Production Batch Closure', 'Production Settlement Preparation')):
        frappe.db.add_unique(dt, ['job_card', 'revision'], constraint_name='unique_run_revision')
    return dict(applied=applied, approval_reference=approval_reference,
                schema_rollback_warning='Native DDL may commit; retain live DB and stop on any failure')
