"""Explicit uploaded-manifest metadata executor; no generic definition discovery.

Native DocType/Custom Field DDL may commit independently. This is not an atomic
schema migration. Back up first; stop on native failure rather than forcing retry.
"""
import copy
import hashlib
import json
from pathlib import Path
import frappe
from calco_erp.release_20260917.metadata import canonical, project, field_conflicts

SCOPE = 'aws_consolidated_20260928'
SOURCE_COMMIT = '0c3f3f494ff2fcf4b61841bd6c48382832189c5d'
COUNT = 688
HARD_EXCLUSIONS = {('Workspace Sidebar', 'Production'), ('Workspace', 'Production'),
    ('Workspace Sidebar', 'Calco Production Workspace'), ('Role', 'Calco New UI'), ('Page', 'calco-home')}
METADATA_TYPES = {'Role', 'DocType', 'Custom Field', 'Property Setter', 'Page', 'Report',
    'Print Format', 'Client Script', 'Server Script', 'Notification', 'Number Card',
    'Custom HTML Block', 'Workspace', 'Workspace Sidebar', 'Dashboard', 'Dashboard Chart', 'Desktop Icon'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()

def _key(row):
    if not isinstance(row, dict) or not isinstance(row.get('doctype'), str) or not isinstance(row.get('name'), str) or not row['doctype'] or not row['name']:
        raise ValueError('Metadata key requires non-empty doctype/name')
    return row['doctype'], row['name']

def _activation(row):
    dt, name = _key(row)
    if dt not in METADATA_TYPES:
        return True  # Business/master records are never metadata installation.
    doc = row['document']
    if dt == 'Server Script' and doc.get('disabled') != 1:
        return True
    if dt in ('Custom Field', 'Property Setter'):
        parent = str(doc.get('dt') or doc.get('doc_type') or '').lower()
        field = str(doc.get('fieldname') or doc.get('field_name') or '').lower()
        value = doc.get('default') if dt == 'Custom Field' else doc.get('value') if doc.get('property') == 'default' else None
        if ('control plan' in parent or 'ipqc' in parent or 'ipqc' in field) and value not in (None, '', 0, '0'):
            if any(x in field for x in ('enable', 'activ', 'interval', 'window', 'trigger', 'timing')):
                return True
    return False

def _load(manifest_path, preservation_path, scope, source_commit):
    if scope != SCOPE or source_commit != SOURCE_COMMIT:
        raise ValueError('Exact release scope/source_commit required')
    raw = Path(manifest_path).read_bytes(); preserved_raw = Path(preservation_path).read_bytes()
    data = json.loads(raw); preservation = json.loads(preserved_raw)
    if data.get('scope') != SCOPE or data.get('source_commit') != SOURCE_COMMIT:
        raise ValueError('Manifest scope/source_commit mismatch')
    rows = data.get('records')
    if not isinstance(rows, list) or len(rows) != COUNT:
        raise ValueError('Exactly 688 records required')
    if preservation.get('default') != 'SKIP/HOLD' or not isinstance(preservation.get('objects'), list):
        raise ValueError('Preservation default must be SKIP/HOLD')
    dispositions = {}
    for obj in preservation['objects']:
        key = _key(obj)
        if key in dispositions:raise ValueError('Duplicate preservation key: '+str(key))
        dispositions[key] = obj.get('classification')
    seen = set()
    for row in rows:
        key = _key(row)
        if key in seen:raise ValueError('Duplicate target key: '+str(key))
        seen.add(key)
        if key in HARD_EXCLUSIONS:raise ValueError('Hard-excluded metadata: '+str(key))
        if dispositions.get(key, 'SKIP/HOLD') != 'APPLY':raise ValueError('Preservation contract blocks: '+str(key))
        doc = row.get('document')
        if not isinstance(doc, dict) or doc.get('doctype') != key[0] or doc.get('name', key[1]) != key[1]:
            raise ValueError('Embedded document identity mismatch: '+str(key))
        if key[0] == 'Custom Field' and key[1] != str(doc.get('dt')) + '-' + str(doc.get('fieldname')):
            raise ValueError('Custom Field native identity differs from target key')
        if type(row.get('baseline_available')) is not bool or 'captured_baseline' not in row:
            raise ValueError('Explicit baseline evidence required: '+str(key))
        if row['captured_baseline'] is not None and not isinstance(row['captured_baseline'], dict):
            raise ValueError('Invalid captured baseline')
        if _activation(row):raise ValueError('Business/master activation or enabled script forbidden: '+str(key))
        if row.get('source'):raise ValueError('External source reload forbidden; embedded document is authority')
    return rows, {'targeted_sha256':hashlib.sha256(raw).hexdigest(), 'preservation_sha256':hashlib.sha256(preserved_raw).hexdigest()}

def _desired(row):
    doc = copy.deepcopy(row['document'])
    if row['doctype'] == 'DocType':
        doc['fields'] = [dict(f, default=str(f['default'])) if isinstance(f.get('default'), (int,float)) else f for f in doc.get('fields', [])]
        if 'field_order' in doc:
            order = {name:i for i,name in enumerate(doc['field_order'])}
            doc['fields'].sort(key=lambda f:order.get(f['fieldname'],len(order)))
    if row['doctype'] == 'Report':doc.pop('disable_prepared_report', None)  # Native non-persisted serialization key.
    return doc

def _projection(value, desired, dt):
    if value is None:return None
    value = copy.deepcopy(value)
    if dt == 'DocType' and 'field_order' in desired:value['field_order']=[f['fieldname'] for f in value.get('fields',[])]
    return canonical(project(value, desired))

def _current(dt, name):
    return frappe.get_doc(dt, name).as_dict() if frappe.db.exists(dt, name) else None

def _plan(rows, hashes):
    result=[]
    for row in rows:
        dt,name=_key(row);desired=_desired(row);old=_current(dt,name)
        current=_projection(old,desired,dt);baseline=_projection(row['captured_baseline'],desired,dt);target=canonical(desired)
        conflicts=[]
        if old and dt=='DocType':conflicts=field_conflicts(old,desired)
        if old and dt=='Custom Field':conflicts=field_conflicts({'fields':[old]},{'fields':[desired]})
        if dt=='Custom Field' and frappe.db.exists('DocType',desired['dt']) and frappe.db.exists('DocField',{'parent':desired['dt'],'fieldname':desired['fieldname']}):
            conflicts.append('Conflicts with native DocField')
        if conflicts:classification='Conflict';reason='; '.join(conflicts)
        elif old is not None and current==target:classification='Already Current';reason='Relevant state equals embedded desired document'
        elif old is None:
            if row['baseline_available'] and row['captured_baseline'] is not None:
                classification='Conflict';reason='Drifted: captured object is now absent'
            else:classification='Create';reason='Absent object; safe prospective metadata creation'
        elif not row['baseline_available']:
            classification='Conflict';reason='Drifted: unexpected existing object without reviewed baseline'
        elif row['captured_baseline'] is None or current!=baseline:
            classification='Conflict';reason='Drifted: current relevant state differs from captured baseline'
        else:classification='Update';reason='Current relevant state matches reviewed baseline'
        result.append(dict(doctype=dt,name=name,classification=classification,current_hash=digest(current),baseline_hash=digest(baseline),desired_hash=digest(target),reason=reason))
    counts={k:sum(r['classification']==k for r in result) for k in ('Already Current','Create','Update','Conflict')}
    payload=dict(site=frappe.local.site,scope=SCOPE,source_commit=SOURCE_COMMIT,total_records=len(result),counts=counts,
        schema_required=any(r['doctype'] in ('DocType','Custom Field') and r['classification'] in ('Create','Update') for r in result),records=result,manifest_hashes=hashes)
    return dict(payload,fingerprint=digest(payload))

def plan(manifest_path, preservation_path, scope=SCOPE, source_commit=SOURCE_COMMIT):
    """Read-only metadata inspection. Does not initialize/migrate/synchronize anything."""
    return _plan(*_load(manifest_path,preservation_path,scope,source_commit))

def _order(row):
    dt=row['doctype']
    priority=row.get('apply_priority', -1 if dt=='Role' else 0 if dt=='DocType' and row.get('child') else 1 if dt=='DocType' else 2 if dt=='Custom Field' else 3 if dt in ('Number Card','Custom HTML Block','Report') else 5 if dt=='Workspace Sidebar' else 4)
    return priority,row['name']

def _write(row):
    dt,name=_key(row);values=_desired(row)
    if dt=='Custom Field':
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
        create_custom_fields({values['dt']:[{k:v for k,v in values.items() if k not in ('doctype','name','dt')}]},update=True)
    elif frappe.db.exists(dt,name):
        doc=frappe.get_doc(dt,name);doc.update({k:v for k,v in values.items() if k not in ('doctype','name')});doc.save()
    else:
        values.setdefault('name',name);frappe.get_doc(values).insert(set_name=name)

def apply(manifest_path, preservation_path, expected_site, reviewed_fingerprint, approval_reference, source_commit, scope):
    if frappe.session.user!='Administrator':raise frappe.PermissionError('Administrator required')
    if expected_site!=frappe.local.site or not str(approval_reference or '').strip():raise ValueError('Exact expected_site and approval_reference required')
    rows,hashes=_load(manifest_path,preservation_path,scope,source_commit)
    review=_plan(rows,hashes)  # Fresh target/baseline validation immediately before any mutation.
    if review['fingerprint']!=reviewed_fingerprint:raise ValueError('Reviewed fingerprint no longer matches PLAN')
    if review['counts']['Conflict']:raise ValueError('Conflict/Drifted: no metadata applied')
    changes={(r['doctype'],r['name']) for r in review['records'] if r['classification']!='Already Current'}
    applied=[];old_patch=frappe.flags.in_patch;old_import=frappe.flags.in_import
    try:
        # Standard metadata document APIs need native schema-import context. This
        # does not run patch handlers, fixtures, migration hooks or business setup.
        frappe.flags.in_patch=True;frappe.flags.in_import=True
        for row in sorted(rows,key=_order):
            if _key(row) in changes:_write(row);applied.append(dict(doctype=row['doctype'],name=row['name']))
        after=_plan(rows,hashes)
        if after['counts']['Already Current']!=COUNT:raise RuntimeError('Post-APPLY target mismatch; stop for native metadata inspection')
        return dict(site=frappe.local.site,scope=SCOPE,source_commit=SOURCE_COMMIT,applied=applied,approval_reference=approval_reference,after=after,
            ddl_warning='Native schema DDL may commit independently; no automatic schema rollback. No migration/patch/fixture/business setup executed.')
    except Exception:
        frappe.db.rollback();raise
    finally:
        frappe.flags.in_patch=old_patch;frappe.flags.in_import=old_import
