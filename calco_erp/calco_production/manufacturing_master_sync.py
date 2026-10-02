"""Pinned, opt-in manufacturing master sync. Never invoked by migrate or scheduler.

Problem source rows are excluded, never deleted from ERP. Quality sources remain
held until their complete revision/checkpoint authority can be established.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

PACKAGE = Path(__file__).with_name('master_data_20260915')
LINE_MAP = {f'L-{n}': f'Line {n}' for n in (1, 2, 3, 5, 6)}
MACHINE_MAP = {f'MEGAMachine {n}': f'Line {n}' for n in (1, 2, 3, 5, 6)}
IDENTITY_PREFIX = 'manufacturing-masters-v1:'


def text(value):
    return '' if value is None else str(value).strip()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def operational_display_text(kind, header, value, number_format):
    """Preserve exact Excel display, never infer an original range from a date."""
    supported = {'Air pressure', 'Vaccum Valve On Time', 'Granuel Size (mm)', 'Die'}
    if kind == 'mpds' and header in supported and number_format in ('m\\-d', 'm-d'):
        return f'{value.month}-{value.day}'
    return None


def build_mpds_payload(sheet, record):
    from calco_erp.calco_production import mpds_import
    originals = []
    try:
        for conversion in record.get('normalizations', []):
            cell = sheet.cell(record['row'], conversion['column'])
            originals.append((cell, cell.value, cell.number_format))
            cell.value = conversion['display_text']
            cell.number_format = '@'
        payload = mpds_import._build_payload(sheet, record['row'], record['source_sha256'])
        for conversion in record.get('normalizations', []):
            cell_address = sheet.cell(record['row'], conversion['column']).coordinate
            row = next(row for row in payload['specifications'] if row['source_cell'] == cell_address or row['source_cell'].endswith('!' + cell_address))
            row['source_value_type'] = 'Excel Date Display Text'
            row['source_raw_value'] = json.dumps(conversion, sort_keys=True)
            row['mapping_note'] = (row.get('mapping_note') or '') + ' Exact source Excel display retained as text; no inferred numeric limits.'
        payload['source_payload_hash'] = mpds_import._source_payload_hash(payload)
        return payload
    finally:
        for cell, value, number_format in originals:
            cell.value = value
            cell.number_format = number_format


def inspect_sources(items, workstations, package=PACKAGE):
    """Pure read-only classification. No inferred dates, revisions, or timing rules."""
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    result = {'version': manifest['version'], 'sources': {}, 'records': []}
    for kind, source in manifest['sources'].items():
        path = package / source['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError(f'Source fingerprint mismatch: {source["file"]}')
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook[source['sheet']]
            rows = list(sheet.values)
            date_formats = {(cell.row, cell.column - 1): cell.number_format for cells in sheet.iter_rows() for cell in cells if isinstance(cell.value, (date, datetime))}
        finally:
            workbook.close()
        indices = {'mpds': (0, 1), 'fg_standard': (1,), 'control_plan': (3, 4)}[kind]
        groups = defaultdict(list)
        for number, row in enumerate(rows[1:], 2):
            if all(text(row[i]) for i in indices):
                groups[tuple(text(row[i]) for i in indices)].append((number, row))
        for key, group in groups.items():
            for number, values in group:
                issues = []
                if len(group) > 1:
                    issues.append('Duplicate/revision: active authority unresolved')
                if key[0] not in items:
                    issues.append('Missing FG master')
                elif items[key[0]].get('disabled'):
                    issues.append('Disabled FG master')
                line = None
                if kind != 'fg_standard':
                    line = (LINE_MAP if kind == 'mpds' else MACHINE_MAP).get(key[1])
                    if not line or line not in workstations:
                        issues.append('Invalid production line/machine')
                if kind == 'mpds' and text(values[80]) and text(values[80]) != line:
                    issues.append('Conflicting primary/secondary line')
                required = {'mpds': (66, 67, 68), 'fg_standard': (31, 65), 'control_plan': (2, 5, 6)}[kind]
                if any(not text(values[i]) for i in required):
                    issues.append('Missing revision/issue identity')
                allowed_dates = {'mpds': {68}, 'fg_standard': {65}, 'control_plan': {0, 6}}[kind]
                date_cells = []
                normalizations = []
                for i, value in enumerate(values):
                    if not isinstance(value, (date, datetime)) or i in allowed_dates:
                        continue
                    number_format = date_formats.get((number, i), '')
                    displayed = operational_display_text(kind, rows[0][i], value, number_format)
                    if displayed is None:
                        date_cells.append(i)
                    else:
                        normalizations.append({'column': i + 1, 'raw_date': value.isoformat(), 'number_format': number_format, 'display_text': displayed})
                if date_cells:
                    issues.append('Date-typed operational value')
                if kind == 'fg_standard' and isinstance(values[46], (int, float)) and values[46] > 1000:
                    issues.append('Drying time requires source review')
                if kind == 'control_plan':
                    issues.append('Unsupported/unmapped: source frequencies do not establish IPQC checkpoint timing')
                record = {'source': kind, 'sheet': source['sheet'], 'row': number, 'key': key,
                          'line': line, 'issues': issues, 'source_sha256': source['sha256'],
                          'source_fingerprint': fingerprint({'headers': rows[0], 'values': values}),
                          'classification': 'Excluded' if issues else 'Candidate', 'normalizations': normalizations,
                          'values': values, 'headers': rows[0]}
                result['records'].append(record)
        result['sources'][kind] = {**source, 'populated_rows': sum(map(len, groups.values())),
                                   'unique_keys': len(groups), 'duplicate_keys': sum(len(g) > 1 for g in groups.values())}
    return result


def sync(dry_run=True, expected_site=None, approval_reference=None):
    """Explicit site-bound invocation; all writes remain in caller's transaction.

    Apply creates a new version through the existing review/approval workflow.
    The caller must supply the explicit source-review approval reference.
    """
    import frappe
    from calco_erp.calco_production import mpds_import, mpds_master
    if not isinstance(dry_run, bool):
        raise ValueError('dry_run must be a boolean')
    if not dry_run:
        if not expected_site or expected_site != frappe.local.site:
            raise ValueError('An exact expected_site is required for explicit apply')
        if not text(approval_reference):
            raise ValueError('Source-review approval reference is required')
        mpds_master._check_roles(mpds_master.APPROVAL_ROLES, 'Manufacturing master sync requires controlled authority.')
    items = {r.name: r for r in frappe.get_all('Item', fields=['name', 'disabled'], limit_page_length=0)}
    lines = set(frappe.get_all('Workstation', pluck='name', limit_page_length=0))
    audit = inspect_sources(items, lines)
    if not dry_run:
        for item in sorted({r['key'][0] for r in audit['records'] if r['source'] == 'mpds' and not r['issues']}):
            frappe.db.sql('select name from `tabItem` where name=%s for update', (item,))
    existing = {r.import_identity_key: r for r in frappe.get_all(mpds_master.MPDS_DOCTYPE,
                fields=['name', 'import_identity_key', 'source_payload_hash', 'status'], limit_page_length=0)}
    counts = Counter()
    workbook = None
    try:
        for record in audit['records']:
            if record['issues']:
                counts['Excluded'] += 1
                continue
            if record['source'] != 'mpds':
                record['issues'].append('Unsupported/unmapped: complete versioned FG specification authority unresolved')
                record['classification'] = 'Excluded'
                counts['Excluded'] += 1
                continue
            if workbook is None:
                workbook = load_workbook(PACKAGE / audit['sources']['mpds']['file'], data_only=True)
            payload = build_mpds_payload(workbook['Line2'], record)
            identity = IDENTITY_PREFIX + record['source_fingerprint']
            payload['import_identity_key'] = identity
            old = existing.get(identity)
            if old:
                stored = frappe.get_doc(mpds_master.MPDS_DOCTYPE, old.name)
                if old.source_payload_hash != payload['source_payload_hash'] or mpds_master.get_authority_payload_hash(stored) != stored.imported_payload_hash:
                    raise ValueError(f'Existing imported authority differs from frozen source: {old.name}')
                record['classification'] = 'Unchanged'
                record['document'] = old.name
                counts['Unchanged'] += 1
                continue
            record['classification'] = 'Create'
            counts['Create'] += 1
            if not dry_run:
                # No bypass of review, approval, history, or source validation.
                current = frappe.db.get_value(mpds_master.MPDS_DOCTYPE, {'fg_item': payload['fg_item'], 'production_line': payload['production_line'], 'status': mpds_master.CURRENT_STATUS}, 'name')
                payload['supersedes'] = current or ''
                payload['revision_reason'] = text(approval_reference)
                original_flag = getattr(frappe.flags, 'mpds_import', False)
                frappe.flags.mpds_import = True
                try:
                    document = frappe.get_doc({'doctype': mpds_master.MPDS_DOCTYPE, **payload})
                    document.insert()
                finally:
                    frappe.flags.mpds_import = original_flag
                # Match native importer: hash persisted field precision/null defaults.
                stored = frappe.get_doc(mpds_master.MPDS_DOCTYPE, document.name)
                frappe.db.set_value(mpds_master.MPDS_DOCTYPE, document.name, 'imported_payload_hash', mpds_master.get_authority_payload_hash(stored), update_modified=False)
                mpds_master.mark_reviewed(document.name, text(approval_reference))
                mpds_master.submit_for_approval(document.name)
                approved = mpds_master.approve_and_make_current(document.name, text(approval_reference))
                record['document'] = document.name
                record['status'] = approved['status']
    finally:
        if workbook:
            workbook.close()
    audit['dry_run'] = dry_run
    audit['counts'] = dict(counts)
    for record in audit['records']:
        record.pop('values', None)
        record.pop('headers', None)
    return audit

def sync_all(dry_run=True,expected_site=None,approval_reference=None):
    """Combined explicit release entry point; caller owns commit/rollback."""
    from calco_erp.calco_quality.quality_master_versions import sync as sync_quality
    mpds=sync(dry_run=dry_run,expected_site=expected_site,approval_reference=approval_reference)
    quality=sync_quality(dry_run=dry_run,expected_site=expected_site,approval_reference=approval_reference)
    mpds['records']=[r for r in mpds['records'] if r['source']=='mpds']
    mpds['counts']=dict(Counter(r['classification'] for r in mpds['records']))
    from calco_erp.calco_quality.manufacturing_master_authority import sync_timing
    timing=sync_timing(dry_run=dry_run,expected_site=expected_site,approval_reference=approval_reference)
    return {'mpds':mpds,'quality':quality,'timing':timing}
