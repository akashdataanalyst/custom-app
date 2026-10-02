"""FG-level predecessor evidence for explicitly approved FG+Line successors.

Diagnostics are not equivalence gates. No source defaults, historical mutations,
line inference, timing activation, transaction writes or commits occur here.
"""
import json
from collections import defaultdict
import frappe
from frappe.utils import now_datetime
from calco_erp.calco_quality import manufacturing_master_authority as authority
from .master_sync import digest

VERSION = 'control-plan-prospective-successor-v1'
SUCCESSOR = 'Legacy Authority Present - Approved Prospective Successor'
AMBIGUOUS = 'Legacy Ambiguous - Conflict'


def compare(rows, payload, fg, line, candidate):
    rows = sorted(rows, key=lambda r: str(r.get('name') or ''))
    issues = []
    if not fg or not line or not candidate:
        issues.append('Missing prospective FG/Line/version identity')
    names = [r.get('name') for r in rows]
    if not rows or not all(names) or len(set(names)) != len(names):
        issues.append('Missing or duplicate predecessor row identity')
    if any(r.get('fg_item_code') != fg for r in rows):
        issues.append('Predecessor FG identity mismatch')
    legacy, new = defaultdict(list), defaultdict(list)
    for row in rows:
        legacy[authority.canonical(row.get('parameter') or '')].append(row)
    for control in authority.controls(payload):
        new[authority.canonical(control.get('parameter') or '')].append(control)
    # Record source fields exactly; absent fields remain absent, not inherited.
    legacy_fields = ('name','parameter','is_active','applicable','size','frequency',
                     'critical_test','test_type','unit','minimum_value','maximum_value',
                     'target_value','custom_test_condition','production_line',
                     'custom_ipqc_checkpoint','custom_ipqc_trigger_basis',
                     'custom_ipqc_interval','custom_ipqc_sampling_window')
    comparisons, differences, warnings = [], [], []
    for parameter in sorted(set(legacy) & set(new)):
        old = [{k:r[k] for k in legacy_fields if k in r} for r in legacy[parameter]]
        incoming = new[parameter]
        entry = dict(parameter=parameter, legacy=old, candidate=incoming)
        comparisons.append(entry)
        if len(old) != 1 or len(incoming) != 1:
            warnings.append(dict(parameter=parameter, reason='Multiple historical/source occurrences; no inferred winner'))
        # Differently typed semantics are reported literally, not normalized into
        # a fabricated equivalent specification or an approval/activation rule.
        for oldrow in old:
            for newrow in incoming:
                changes = {}
                for previous, prospective in (('size','sample_size'),('frequency','frequency'),
                                              ('unit','unit'),('applicable','applicable'),
                                              ('custom_test_condition','conditions'),
                                              ('critical_test','criticality')):
                    if previous in oldrow or prospective in newrow:
                        left, right = oldrow.get(previous), newrow.get(prospective)
                        if left != right:
                            changes[prospective] = dict(legacy=left,candidate=right)
                if changes:
                    differences.append(dict(parameter=parameter,legacy_row=oldrow.get('name'),fields=changes))
    result = dict(version=VERSION,classification=AMBIGUOUS if issues else SUCCESSOR,
                  fg=fg,line=line,candidate=candidate,
                  predecessor_scope='FG-only; historical line identity not inferred',
                  prospective_scope='FG + Production Line + Control Plan version',
                  legacy_fingerprint=digest(rows),legacy_row_count=len(rows),legacy_row_names=names,
                  candidate_fingerprint=digest(payload),comparisons=comparisons,differences=differences,
                  legacy_only_parameters=sorted(set(legacy)-set(new)),
                  new_only_parameters=sorted(set(new)-set(legacy)),warnings=warnings,issues=issues)
    result['comparison_fingerprint'] = digest(result)
    return result


def inspect(fg, line, candidate, payload):
    rows = frappe.get_all('FG Control Plan', filters={'fg_item_code':fg},
                          fields=['*'],order_by='name',limit_page_length=0)
    return compare(rows,payload,fg,line,candidate) if rows else None


def approvals_for(plan, approvals, approval_reference):
    approvals = approvals or {}
    expected = {r['candidate']:r for r in plan['records'] if r['classification']==SUCCESSOR}
    if not isinstance(approvals,dict) or set(approvals)!=set(expected):
        raise ValueError('Exact candidate-keyed FG+Line successor approvals required')
    evidence = {}
    for key, record in expected.items():
        given = approvals[key]
        comparison = record['legacy_comparison']
        binding = dict(fg=record['fg'],line=record['line'],candidate=key,
                       comparison_fingerprint=comparison['comparison_fingerprint'],
                       reviewed_plan_fingerprint=plan['fingerprint'])
        if not isinstance(given,dict) or any(given.get(k)!=v for k,v in binding.items()):
            raise ValueError('Stale or cross-line Control Plan successor approval: '+key)
        if not isinstance(given.get('approval_reference'),str) or not given['approval_reference'].strip():
            raise ValueError('Explicit successor approval reference required: '+key)
        audit = dict(kind=VERSION,**binding,comparison=comparison,
                     approval_reference=given['approval_reference'].strip(),
                     release_approval_reference=approval_reference,
                     actor=frappe.session.user,site=frappe.local.site,
                     approved_on=str(now_datetime()),
                     scope='Prospective only; no historical rebinding or timing activation')
        if len(json.dumps(audit,ensure_ascii=False,default=str).encode('utf-8'))>60000:
            raise ValueError('Control Plan adoption audit exceeds immutable review_notes capacity: '+key)
        evidence[key]=audit
    return evidence
