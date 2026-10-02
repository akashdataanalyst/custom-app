"""Immutable source revisions; property-level review is separate from activation scope."""
from __future__ import annotations
import json
import math
from datetime import date, datetime
import frappe
from frappe.utils import now_datetime
from calco_erp.calco_production import manufacturing_master_sync as source

DOCTYPE = 'Manufacturing Quality Master Version'
POLICY = 'manufacturing-quality-source-v1'
KINDS = {'fg_standard': 'FG Standard', 'control_plan': 'Control Plan'}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_version(doc):
    if not getattr(frappe.flags, 'controlled_quality_master_sync', False):
        frappe.throw('Use the controlled manufacturing master sync; source versions are immutable.')
    payload = json.loads(doc.payload)
    if source.fingerprint(payload) != doc.payload_hash:
        frappe.throw('Manufacturing master payload fingerprint mismatch.')
    previous = doc.get_doc_before_save()
    if previous:
        for field in ('master_kind','fg_item','production_line','business_revision','issue_date_text','material_status','source_file','source_sha256','source_sheet','source_row','source_version','source_fingerprint','payload_hash','payload','approval_reference','imported_by','imported_on','version_key','review_notes','supersedes'):
            if previous.get(field) != doc.get(field):
                frappe.throw('Historical manufacturing master source evidence cannot be rewritten.')


def source_cells(record):
    return [{'column':i+1,'header':record['headers'][i], 'value':v.isoformat() if isinstance(v,(date,datetime)) else v, 'source_type':type(v).__name__} for i,v in enumerate(record['values'])]


def standard_payload(record):
    from calco_erp.calco_quality.importers.fg_standard_min_max_import import parse_standard_header
    from calco_erp.calco_quality.fg_quality_setup import get_true_fg_testing_method_name_set
    properties = {}; review=[]
    for i,(header,value) in enumerate(zip(record['headers'],record['values'])):
        if header is None or value is None:
            continue
        if isinstance(value,(date,datetime)) and i != 65:
            review.append({'column':i+1,'header':header,'reason':'Date-typed operational property; retained as evidence, not a numeric specification'})
            continue
        if i == 46 and numeric(value) and value > 1000:
            review.append({'column':47,'header':header,'reason':'Drying-time serial/value requires review'})
            continue
        normalized_header=' '.join(str(header).split()).casefold()
        # Exact source spelling: the legacy regex misses the attached mm unit.
        mapped_header='Glow Wire Test @ 3.2 mm' if normalized_header=='glow wire test @ 3.2mm (°c)' else str(header)
        mapping=parse_standard_header(mapped_header,get_true_fg_testing_method_name_set())
        if not mapping:
            continue
        parameter=mapping['parameter'];prop=properties.setdefault(parameter,{'parameter':parameter,'minimum_value':None,'maximum_value':None,'target_values':[],'unit':mapping['unit'],'source_columns':[],'conditions':[],'not_applicable':False,'conflicts':[]})
        prop['source_columns'].append(i+1)
        text=source.text(value)
        if text.casefold() in ('n/a','na'):
            prop['not_applicable']=True
            continue
        if text in ('','-'):
            continue
        field=mapping['fieldname']
        if field in ('minimum_value','maximum_value') and numeric(value):
            if prop[field] is not None and prop[field] != value:
                prop['conflicts'].append('Competing source limits: '+field)
            else:prop[field]=value
        elif text not in prop['target_values']:
            prop['target_values'].append(text)
    conditions={52:'MFI',54:'Tensile Strength at Yield',55:'Young Modulus',56:'Elongation at Yield',57:'Flexural Strength'}
    for i,parameter in conditions.items():
        value=record['values'][i]
        if value is not None:
            prop=properties.setdefault(parameter,{'parameter':parameter,'minimum_value':None,'maximum_value':None,'target_values':[],'unit':'','source_columns':[],'conditions':[],'not_applicable':False,'conflicts':[]})
            prop['conditions'].append(source.text(value))
    for prop in properties.values():
        if prop['minimum_value'] is not None or prop['maximum_value'] is not None or prop['target_values']:
            prop['not_applicable']=False
    return {'kind':'FG Standard','cells':source_cells(record),'properties':properties,'property_review':review,'ipqc_timing':'Unconfigured - never inferred from FG specifications'}


def frequency_scope(value):
    if numeric(value) and value >= 0:
        return 'Final QC sample multiplier'
    text=' '.join(source.text(value).casefold().split())
    if text in ('every batch','each batch'):return 'Batch'
    if text in ('every consignment','consignment'):return 'Consignment'
    if text in ('n/a','na'):return 'Not Applicable'
    if text in ('','-'):return 'Unspecified'
    return 'Review Required'


def control_payload(record):
    from calco_erp.calco_quality.importers.fg_control_plan_import import derive_parameter_groups
    from calco_erp.calco_quality.fg_quality_setup import get_true_fg_testing_method_name_set
    allowed=get_true_fg_testing_method_name_set()
    allowed |= {source.text(h) for h in record['headers'] if source.text(h).startswith(('130 -','140 -','150 -')) and 'Size' not in source.text(h) and 'Frequency' not in source.text(h)}
    groups=derive_parameter_groups([source.text(h) for h in record['headers']],list(allowed));controls=[]
    for group in groups:
        size=record['values'][group['size_index']];frequency=record['values'][group['frequency_index']] if group['frequency_index'] is not None else None
        for parameter in group['parameters']:
            controls.append({'parameter':parameter['name'],'criticality':record['values'][parameter['header_index']],'sample_size':size,'frequency':frequency,'frequency_scope':frequency_scope(frequency),'source_columns':[parameter['header_index']+1,group['size_index']+1,(group['frequency_index']+1) if group['frequency_index'] is not None else None]})
    return {'kind':'Control Plan','cells':source_cells(record),'controls':controls,'ipqc_timing':'Unconfigured - existing explicit checkpoint configuration remains authoritative'}


def candidates():
    items={r.name:r for r in frappe.get_all('Item',fields=['name','disabled'],limit_page_length=0)}
    lines=set(frappe.get_all('Workstation',pluck='name',limit_page_length=0))
    audit=source.inspect_sources(items,lines);records=[];held=[]
    for record in audit['records']:
        kind=record['source']
        if kind not in KINDS:continue
        reasons=[reason for reason in record['issues'] if reason in ('Missing FG master','Disabled FG master','Invalid production line/machine','Duplicate/revision: active authority unresolved')]
        special=kind=='fg_standard' and record['key'][0]=='760C0001' and record['source_sha256']=='8c14d6168f9143e1860071cd218c68e63eeb2e1571800bff0feee2bbfaa5b924' and record['row'] in (814,1415)
        if special:reasons=[reason for reason in reasons if not reason.startswith('Duplicate/revision')]
        if reasons:
            held.append({'source':kind,'row':record['row'],'key':record['key'],'reasons':reasons});continue
        payload=standard_payload(record) if kind=='fg_standard' else control_payload(record)
        payload=json.loads(encoded(payload))
        values=record['values'];active=not(special and record['row']==814)
        records.append({'version_key':'QMV-'+('STD-' if kind=='fg_standard' else 'CP-')+source.fingerprint([POLICY,record['source_fingerprint']])[:32], 'master_kind':KINDS[kind],'fg_item':record['key'][0],'production_line':record['line'] or '', 'is_active':int(active),'business_revision':source.text(values[31 if kind=='fg_standard' else 5]),'issue_date_text':source.text(values[65 if kind=='fg_standard' else 6]), 'material_status':source.text(values[111 if kind=='fg_standard' else 147]),'source_file':audit['sources'][kind]['file'],'source_sha256':record['source_sha256'],'source_sheet':record['sheet'],'source_row':record['row'],'source_version':POLICY,'source_fingerprint':record['source_fingerprint'],'payload_hash':source.fingerprint(payload),'payload':encoded(payload),'review_notes':('Historical 2023 version; user selected DD / PreLaunch row 1415.' if not active else 'Property-level exceptions retained. No IPQC timing inferred. Blank business revisions remain blank; immutable source version supplies technical identity.')})
    records.sort(key=lambda r:(r["fg_item"],r["master_kind"],r["production_line"],r["source_row"]))
    return records,held


def sync(dry_run=True,expected_site=None,approval_reference=None,master_kind=None,control_plan_adoptions=None):
    if control_plan_adoptions is not None and master_kind != 'Control Plan':
        raise ValueError('Control Plan adoption evidence is restricted to Control Plans')
    if not isinstance(dry_run,bool):raise ValueError('dry_run must be boolean')
    if master_kind is not None and master_kind not in KINDS.values():raise ValueError('Unsupported master kind')
    if not dry_run:
        if expected_site != frappe.local.site or not approval_reference:raise ValueError('Explicit site and source approval reference required')
        if not set(frappe.get_roles()).intersection({'System Manager','Quality Manager'}):frappe.throw('Quality master authority required.',frappe.PermissionError)
        if not frappe.db.exists('DocType',DOCTYPE):raise ValueError('Install the targeted master-version schema first')
    records,held=candidates()
    if master_kind:
        records=[r for r in records if r['master_kind']==master_kind]
        held=[r for r in held if KINDS.get(r['source'])==master_kind]
    result={'created':[],'unchanged':[],'held':held,'dry_run':dry_run}
    exists=frappe.db.exists('DocType',DOCTYPE)
    for record in records:
        if not dry_run:frappe.db.sql('select name from `tabItem` where name=%s for update',(record['fg_item'],))
        name=frappe.db.exists(DOCTYPE,record['version_key']) if exists else None
        if name:
            old=frappe.get_doc(DOCTYPE,name)
            if old.payload_hash != record['payload_hash'] or source.fingerprint(json.loads(old.payload)) != old.payload_hash:raise ValueError('Existing quality source version mismatch: '+name)
            result['unchanged'].append(name);continue
        result['created'].append({'name':record['version_key'],'kind':record['master_kind'],'fg':record['fg_item'],'line':record['production_line'],'active':record['is_active'],'row':record['source_row']})
        if dry_run:continue
        record = dict(record)
        if control_plan_adoptions and record['version_key'] in control_plan_adoptions:
            audit = control_plan_adoptions[record['version_key']]
            from calco_erp.release_20260917.master_sync import digest
            if (audit['fg'] != record['fg_item'] or audit['line'] != record['production_line']
                    or audit['candidate'] != record['version_key']
                    or audit['comparison']['candidate_fingerprint'] != digest(json.loads(record['payload']))):
                raise ValueError('Control Plan adoption candidate identity mismatch')
            record['review_notes'] = encoded(audit)
        flag=getattr(frappe.flags,'controlled_quality_master_sync',False);frappe.flags.controlled_quality_master_sync=True
        try:
            current=frappe.db.get_value(DOCTYPE,{'master_kind':record['master_kind'],'fg_item':record['fg_item'],'production_line':record['production_line'],'is_active':1},'name') if record['is_active'] else None
            if current:
                old=frappe.get_doc(DOCTYPE,current);old.is_active=0;old.save();record['supersedes']=current
            frappe.get_doc({'doctype':DOCTYPE,**record,'approval_reference':approval_reference,'imported_by':frappe.session.user,'imported_on':now_datetime()}).insert()
        finally:frappe.flags.controlled_quality_master_sync=flag
    return result

def current(kind,fg_item,line=''):
    if not frappe.db.exists('DocType',DOCTYPE):return None
    name=frappe.db.get_value(DOCTYPE,{'master_kind':kind,'fg_item':fg_item,'production_line':line or '', 'is_active':1},'name')
    if not isinstance(name,str) or not name:return None
    doc=frappe.get_doc(DOCTYPE,name)
    if source.fingerprint(json.loads(doc.payload)) != doc.payload_hash:frappe.throw('Quality master fingerprint mismatch.')
    return doc


def prepare_for_start(fg_item,line):
    standard=current('FG Standard',fg_item)
    control=current('Control Plan',fg_item,line)
    if not standard or not control:return None
    from calco_erp.calco_quality.manufacturing_master_authority import project
    return project(standard,control)


def frozen_rows(item_code,reference_type=None,reference_name=None,batch_no=None):
    """Only transaction-linked start-time evidence; never upgrade historical QIs."""
    wo_name=None
    if reference_type=='Stock Entry' and reference_name:
        wo_name=frappe.db.get_value('Stock Entry',reference_name,'work_order')
    elif reference_type=='Job Card' and reference_name:
        wo_name=frappe.db.get_value('Job Card',reference_name,'work_order')
    elif reference_type=='Work Order':wo_name=reference_name
    if not isinstance(wo_name,str) or not wo_name:return None
    wo=frappe.get_doc('Work Order',wo_name)
    if not wo.get('custom_ipqc_plan_snapshot'):return None
    from calco_erp.calco_production.in_process_quality import frozen_plan
    snapshot=frozen_plan(wo).get('quality_master_snapshot')
    if not snapshot:return None
    if snapshot['fg_item'] != item_code:frappe.throw('Frozen FG master lineage mismatch.')
    if snapshot['exceptions']:
        frappe.throw('Quality specification review required: '+'; '.join(snapshot['exceptions']))
    return [frappe._dict(row) for row in snapshot['rows']]

def ensure_schema():
    """Targeted master schema only; no broad migration or Item/template updates."""
    if 'System Manager' not in frappe.get_roles():frappe.throw('System Manager authority required.',frappe.PermissionError)
    frappe.reload_doc('calco_quality','doctype','manufacturing_quality_master_version')
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    if not frappe.get_meta('Quality Inspection Reading').has_field('custom_test_condition'):
        create_custom_fields({'Quality Inspection Reading':[{'fieldname':'custom_test_condition','fieldtype':'Small Text','label':'Test Condition','read_only':1,'insert_after':'custom_target_value'}]},update=False)
