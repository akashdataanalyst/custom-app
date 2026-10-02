"""Prospective successor adoption; legacy comparisons are audit information only."""
from decimal import Decimal, InvalidOperation
import json
import frappe
from calco_erp.calco_quality import manufacturing_master_authority as authority
from calco_erp.release_20260917.master_sync import digest

VERSION = 'fg-standard-prospective-successor-v2'
SUCCESSOR = 'Legacy Authority Present - Approved Prospective Successor'
AMBIGUOUS = 'Legacy Ambiguous - Conflict'


def numeric(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value))
        if result == 0:
            return '0'
        if not result.is_finite():
            raise ValueError('Non-finite specification')
        text = format(result, 'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
    except InvalidOperation as exc:
        raise ValueError('Non-numeric specification limit') from exc


def flag(value):
    if value in (0, '0', False):
        return False
    if value in (1, '1', True):
        return True
    raise ValueError('Applicability/active flag is not explicit')


def compare(legacy, payload, fg):
    """All raw rows retained; only explicit active authority is compared.

    Numeric limits use exact Decimal equality, not tolerance. Text and units are
    exact (no invented case/unit/rating equivalence). Existing controlled aliases
    alone resolve comparison parameter identities. The independent source
    inspector has already validated the candidate; comparison warnings never
    become source holds. Control-only fields are never inherited.
    """
    # Predecessor is the complete named row set belonging to this exact FG,
    # not a single inferred winning legacy parameter/version. Parameter conflicts
    # remain reproducible evidence; only grade/record identity can block adoption.
    identity_issues = []
    if not isinstance(fg, str) or not fg.strip():
        identity_issues.append('Missing candidate FG identity')
    names = set()
    for row in legacy:
        if row.get('fg_item_code') != fg:
            identity_issues.append('Legacy row FG identity differs from candidate: ' + str(row.get('name')))
        name = row.get('name')
        if not isinstance(name, str) or not name or name in names:
            identity_issues.append('Missing/duplicate predecessor record identity: ' + str(name))
        if isinstance(name, str):
            names.add(name)
    legacy = sorted(legacy, key=lambda row: str(row.get('name') or ''))
    if not legacy:
        identity_issues.append('No predecessor records to fingerprint')
    issues, differences, compared = [], [], []
    extra, absent, inverted = [], [], []
    from calco_erp.calco_quality.fg_quality_setup import build_fg_control_plan_payload
    for row in legacy:
        try:
            lo, hi = numeric(row.get('minimum_value')), numeric(row.get('maximum_value'))
            if lo is not None and hi is not None and Decimal(lo) > Decimal(hi):
                effective = build_fg_control_plan_payload(row, 1)
                inverted.append({'legacy_row': row.get('name'), 'parameter': row.get('parameter'),
                    'stored_minimum': lo, 'stored_maximum': hi,
                    'effective_minimum': numeric(effective['min_value']), 'effective_maximum': numeric(effective['max_value']),
                    'explanation': 'Legacy Manual test ignores numeric limit fields' if row.get('test_type') == 'Manual' else 'Existing legacy minimum-only convention: zero maximum means no upper bound' if effective['max_value'] is None else 'Legacy data remains inverted under existing runtime semantics',
                    'rule': 'fg_quality_setup.build_fg_control_plan_payload'})
        except ValueError as exc:
            issues.append('Legacy limit representation: ' + str(exc))
    for review in payload.get('property_review', []):
        issues.append('Unresolved source mapping/specification: ' + str(review.get('reason')))
    active, props = {}, {}
    duplicate_parameters = set()
    for name, prop in payload.get('properties', {}).items():
        identity = authority.normalized(authority.canonical(name))
        if identity in props:
            issues.append('Ambiguous candidate parameter mapping: ' + name)
        props[identity] = prop
        if prop.get('conflicts'):
            issues.append('Conflicting candidate specification: ' + name)
    for row in legacy:
        try:
            if not flag(row.get('is_active')):
                continue
            identity = authority.normalized(authority.canonical(row.get('parameter')))
            if not identity:
                raise ValueError('Missing legacy parameter identity')
            if identity in active:
                # Fingerprint the whole predecessor set; do not choose a winner.
                duplicate_parameters.add(identity)
                issues.append('Duplicate active legacy parameter authority: ' + identity)
            active[identity] = row
        except ValueError as exc:
            issues.append(str(exc) + ': ' + str(row.get('name')))
    if not active:
        issues.append('No determinable active legacy authority')
    for identity in sorted(set(active) | set(props)):
        row, prop = active.get(identity), props.get(identity)
        if identity in duplicate_parameters:
            continue  # Raw named rows remain in audit evidence, without guessed precedence.
        if row is None:
            # Blank source columns are not additional acceptance specifications.
            if prop.get('minimum_value') is not None or prop.get('maximum_value') is not None or prop.get('target_values') or prop.get('not_applicable'):
                differences.append({'parameter': identity, 'field': 'presence', 'legacy': None, 'candidate': prop})
            continue
        if prop is None:
            extra.append(identity)
            absent.append(identity)
            issues.append('Unmapped/extra legacy parameter: ' + identity)
            continue
        try:
            applicable = flag(row.get('applicable'))
            targets = prop.get('target_values') or []
            meaningful = prop.get('minimum_value') is not None or prop.get('maximum_value') is not None or bool(targets)
            if not meaningful and not prop.get('not_applicable'):
                absent.append(identity)
                issues.append(identity + ': Blank prospective specification retained; no inheritance')
            if meaningful and prop.get('not_applicable'):
                raise ValueError('Candidate N/A contradicts specification')
            # Explicit source N/A is comparison evidence, never approved applicability.
            candidate = {'applicable': False if prop.get('not_applicable') else True if meaningful else None,
                         'minimum_value': numeric(prop.get('minimum_value')),
                         'maximum_value': numeric(prop.get('maximum_value')),
                         'target_values': targets, 'unit': prop.get('unit') or '',
                         'test_type': 'Numeric' if prop.get('minimum_value') is not None or prop.get('maximum_value') is not None else 'Manual'}
            effective = build_fg_control_plan_payload(row, 1)
            old = {'applicable': applicable, 'minimum_value': numeric(effective['min_value']),
                   'maximum_value': numeric(effective['max_value']),
                   'target_values': [row['target_value']] if row.get('target_value') else [],
                   'unit': row.get('unit') or '', 'test_type': row.get('test_type')}
            if old['test_type'] not in ('Numeric', 'Manual'):
                issues.append(identity + ': Unresolvable legacy test type')
            if candidate['applicable'] and (bool(old['unit']) != bool(candidate['unit'])):
                issues.append(identity + ': Legacy/new-source unit presence differs; no unit conversion')
            for origin, spec in (('legacy', old), ('candidate', candidate)):
                if spec['minimum_value'] is not None and spec['maximum_value'] is not None and Decimal(spec['minimum_value']) > Decimal(spec['maximum_value']):
                    issues.append(identity + ': Raw ' + origin + ' limits appear inverted; no reinterpretation')
            fields = ('applicable', 'minimum_value', 'maximum_value', 'target_values', 'unit', 'test_type')
            for field in fields:
                if old[field] != candidate[field]:
                    differences.append({'parameter': identity, 'field': field, 'legacy': old[field], 'candidate': candidate[field]})
            if prop.get('conditions'):
                # No proven legacy condition equivalent is available in this schema.
                issues.append('Unresolved legacy test condition: ' + identity)
            if 'critical_test' in prop:
                if flag(row.get('critical_test')) != flag(prop['critical_test']):
                    differences.append({'parameter': identity, 'field': 'critical_test', 'legacy': row.get('critical_test'), 'candidate': prop['critical_test']})
            compared.append({'parameter': identity, 'legacy_row': row.get('name'), 'stored_legacy_limits': {'minimum': row.get('minimum_value'), 'maximum': row.get('maximum_value')}, 'legacy': old, 'candidate': candidate,
                             'control_criticality_preserved': row.get('critical_test'),
                             'criticality_authority': 'Control Plan; not replaced by FG Standard'})
        except ValueError as exc:
            issues.append(identity + ': ' + str(exc))
    result = {'version': VERSION, 'classification': AMBIGUOUS if identity_issues else SUCCESSOR,
              'comparison_classification': 'Warnings' if issues else 'Different' if differences else 'Equivalent',
              'fg': fg, 'legacy_row_count': len(legacy),
              'extra_legacy_parameters': sorted(set(extra)), 'duplicate_legacy_parameters': sorted(duplicate_parameters),
              'parameters_without_new_specification': sorted(set(absent)), 'inverted_limit_evidence': inverted,
              'legacy_fingerprint': digest(legacy), 'candidate_fingerprint': digest(payload),
              'legacy_rows': legacy, 'comparisons': compared, 'differences': differences,
              'warnings': issues, 'issues': identity_issues,
              'scope': 'Prospective FG specification only; legacy controls and N/A approval remain unchanged'}
    result['comparison_fingerprint'] = digest(result)
    return result


def inspect(fg, payload):
    rows = frappe.get_all('FG Control Plan', filters={'fg_item_code': fg}, fields=['*'], order_by='name', limit_page_length=0)
    return compare(rows, payload, fg) if rows else None


def approvals_for(plan, approvals, approval_reference):
    """Per-grade, exact-comparison approval. No blanket legacy override."""
    approvals = approvals or {}
    if not isinstance(approvals, dict):
        raise ValueError('Reviewed successors must be a per-FG object')
    evidence = {}
    expected = {r['fg'] for r in plan['records'] if r['classification'] == SUCCESSOR}
    if set(approvals) != expected:
        raise ValueError('Reviewed successor approval required for exactly: ' + ', '.join(sorted(expected)))
    for row in plan['records']:
        comparison = row.get('legacy_comparison')
        if row['classification'] != SUCCESSOR:
            continue
        approval = approvals.get(row['fg'])
        if not isinstance(approval, dict) or approval.get('comparison_fingerprint') != comparison['comparison_fingerprint'] or not str(approval.get('approval_reference') or '').strip():
            raise ValueError('Missing/stale explicit prospective successor approval: ' + row['fg'])
        audit_comparison = {k: v for k, v in comparison.items() if k != 'legacy_rows'}
        audit_comparison['legacy_row_names'] = [r['name'] for r in comparison['legacy_rows']]
        evidence[row['candidate']] = {'kind': VERSION, 'fg': row['fg'], 'candidate': row['candidate'],
            'comparison': audit_comparison, 'reviewed_successor': approval,
            'release_approval_reference': approval_reference, 'reviewed_plan_fingerprint': plan['fingerprint'],
            'effective_authority': 'New start-time freezes only; no historical rebinding',
            'actor': frappe.session.user, 'site': frappe.local.site}
        if len(json.dumps(evidence[row['candidate']], ensure_ascii=True).encode()) > 60000:
            raise ValueError('Predecessor audit exceeds existing review-notes capacity: ' + row['fg'])
    return evidence
