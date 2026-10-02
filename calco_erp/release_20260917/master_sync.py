"""Reviewed release orchestration; existing master engines remain authoritative.

No scheduler hook, HTTP method, stock posting, or commit is provided here.
A conflict stops the component transaction. Resolve it through Quality governance
and generate a fresh plan; an old reviewed plan cannot authorize changed data.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

import frappe

COMPONENTS = ('fg_items', 'fg_standards', 'mpds', 'control_plans', 'ipqc_timing')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(',', ':')).encode()).hexdigest()


def classify(existing_key, intended_key, issues=(), existing_payload=None, intended_payload=None):
    if issues:
        return 'Held'
    if existing_key and existing_key != intended_key:
        return 'Conflict'
    if existing_key:
        return ('Already Current' if existing_payload == intended_payload else 'Conflict')
    return 'Create'


def _engine(component, dry_run=True, **kwargs):
    from calco_erp.calco_quality import fg_item_candidate_sync as items
    from calco_erp.calco_quality import fg_standard_candidate_sync as standards
    from calco_erp.calco_quality import quality_master_versions as versions
    from calco_erp.calco_quality import manufacturing_master_authority as authority
    from calco_erp.calco_production import manufacturing_master_sync as mpds
    if component == 'fg_items':
        return items.run(dry_run=dry_run, **kwargs)
    if component == 'fg_standards':
        manifest = json.loads((items.PACKAGE / 'manifest.json').read_text(encoding='utf-8'))
        return standards.sync(str(items.PACKAGE / manifest['file']), manifest['sha256'],
                              dry_run=dry_run, **kwargs)
    if component == 'mpds':
        return mpds.sync(dry_run=dry_run, **kwargs)
    if component == 'control_plans':
        return versions.sync(dry_run=dry_run, master_kind='Control Plan', **kwargs)
    if component == 'ipqc_timing':
        return authority.sync_timing(dry_run=dry_run, **kwargs)
    raise ValueError('Unknown release master component')


def _target_state():
    """Conservative drift detection includes native and versioned master evidence."""
    tables = ('Item', 'Item Default', 'Item Supplier', 'Item Customer Detail',
              'Item Barcode', 'Workstation', 'FG Control Plan', 'FG Testing Method',
              'Manufacturing Quality Master Version', 'Master Process Data Sheet',
              'Quality Parameter Mapping Revision')
    state = {}
    for dt in tables:
        if not frappe.db.exists('DocType', dt):
            state[dt] = None
            continue
        rows = frappe.get_all(dt, fields=['*'], order_by='name', limit_page_length=0)
        state[dt] = digest(rows)
    return state


def plan(component):
    if component not in COMPONENTS:
        raise ValueError('Unknown release master component')
    from calco_erp.calco_quality import quality_master_versions as q
    result = _engine(component)
    rows = []
    if component == 'fg_items':
        for r in result['records']:
            rows.append(dict(fg=r['fg'], classification=('Already Current' if r['category'] == 'Existing'
                        else 'Create' if r['category'] in ('A', 'C') else 'Held'), reasons=r['issues']))
    elif component == 'fg_standards':
        for r in result['records']:
            current = q.current('FG Standard', r['fg'])
            status = classify(current.name if current else None, r['key'], r['issues'],
                              json.loads(current.payload) if current else None, r['payload'])
            comparison = None
            if status == 'Create' and frappe.db.exists(q.DOCTYPE, r['key']):
                status = 'Conflict'  # An inactive version must never be silently reactivated.
            if status == 'Create':
                from . import legacy_standard_adoption as adoption
                comparison = adoption.inspect(r['fg'], r['payload'])
                if comparison:
                    status = comparison['classification']
            rows.append(dict(fg=r['fg'], candidate=r['key'], current=current.name if current else None,
                             classification=status, reasons=r['issues'] + (comparison['issues'] if comparison else []),
                             legacy_comparison=comparison))
    elif component == 'mpds':
        from calco_erp.calco_production import mpds_master as m
        for r in result['records']:
            if r['source'] != 'mpds':
                continue
            current = frappe.db.get_value(m.MPDS_DOCTYPE,
                {'fg_item': r['key'][0], 'production_line': r['line'], 'status': m.CURRENT_STATUS}, 'name')
            status = 'Held' if r['issues'] else 'Already Current' if r['classification'] == 'Unchanged' else 'Create'
            if not r['issues'] and current and current != r.get('document'):
                status = 'Conflict'
            rows.append(dict(fg=r['key'][0], line=r['line'], current=current,
                             classification=status, reasons=r['issues']))
    elif component == 'control_plans':
        candidates, held = q.candidates()
        for r in candidates:
            if r['master_kind'] != 'Control Plan':
                continue
            current = q.current('Control Plan', r['fg_item'], r['production_line'])
            stored = frappe.db.exists(q.DOCTYPE, r['version_key'])
            # A later controlled revision is retained, never reactivated/replaced.
            status = 'Already Current' if stored else 'Conflict' if current else 'Create'
            comparison = None
            if status == 'Create':
                from . import legacy_control_plan_adoption as adoption
                comparison = adoption.inspect(r['fg_item'], r['production_line'],
                                              r['version_key'], json.loads(r['payload']))
                if comparison:
                    status = comparison['classification']
            rows.append(dict(fg=r['fg_item'], line=r['production_line'], candidate=r['version_key'],
                             current=current.name if current else None, classification=status,
                             reasons=comparison['issues'] if comparison else [],
                             legacy_comparison=comparison))
        rows.extend(dict(fg=r['key'][0], classification='Held', reasons=r['reasons'])
                    for r in held if r['source'] == 'control_plan')
    else:
        candidates, _ = q.candidates()
        approved = {r['version_key'] for r in candidates if r['master_kind'] == 'Control Plan'}
        for r in result['created']:
            rows.append(dict(fg=r['fg'], line=r['line'], current=r['previous'], candidate=r['name'],
                             classification='New Revision' if r['previous'] in approved else 'Conflict',
                             reasons=r['review']))
        rows.extend(dict(current=name, classification='Already Current', reasons=[])
                    for name in result['unchanged'])
    payload = dict(component=component, site=frappe.local.site, records=rows,
                   counts=dict(Counter(r['classification'] for r in rows)), target_state=_target_state(),
                   engine_preview=result)
    return dict(payload, fingerprint=digest(payload))


def apply(component, expected_site, reviewed_fingerprint, approval_reference, reviewed_successors=None):
    if expected_site != frappe.local.site or not str(approval_reference or '').strip():
        raise ValueError('Exact site and approval reference required')
    if frappe.session.user != 'Administrator':
        frappe.throw('Release master application requires Administrator.', frappe.PermissionError)
    # Existing engines use the same Item locks; prevent approved targets drifting
    # between preflight and each native create/revision operation.
    frappe.db.sql('select name from `tabItem Group` where name=%s for update', ('Finished Goods',))
    frappe.db.sql('select name from `tabItem` order by name for update')
    if component in ('fg_standards', 'control_plans'):
        # Lock legacy rows and the range against concurrent authority insertion.
        frappe.db.sql('select name from `tabFG Control Plan` order by name for update')
        frappe.db.sql('select name from `tabManufacturing Quality Master Version` order by name for update')
    reviewed = plan(component)
    if reviewed['fingerprint'] != reviewed_fingerprint:
        raise ValueError('Target/source changed: generate and review a fresh dry-run')
    if reviewed['counts'].get('Conflict') or reviewed['counts'].get('Legacy Ambiguous - Conflict'):
        raise ValueError('Conflicting target authority: component held; Quality review required')
    options = {}
    if component == 'fg_standards':
        from .legacy_standard_adoption import approvals_for
        options['legacy_adoptions'] = approvals_for(reviewed, reviewed_successors, approval_reference)
        # Atomic component even when the caller catches an insertion error.
        frappe.db.savepoint('fg_standard_component')
        try:
            result = _engine(component, dry_run=False, expected_site=expected_site,
                             approval_reference=approval_reference, **options)
            return result
        except Exception:
            frappe.db.rollback(save_point='fg_standard_component')
            raise
    if component == 'control_plans':
        from .legacy_control_plan_adoption import approvals_for
        options['control_plan_adoptions'] = approvals_for(reviewed, reviewed_successors, approval_reference)
        frappe.db.savepoint('control_plan_component')
        try:
            return _engine(component, dry_run=False, expected_site=expected_site,
                           approval_reference=approval_reference, **options)
        except Exception:
            frappe.db.rollback(save_point='control_plan_component')
            raise
    if reviewed_successors:
        raise ValueError('Successor approvals apply only to FG Standards or Control Plans')
    return _engine(component, dry_run=False, expected_site=expected_site,
                   approval_reference=approval_reference)
