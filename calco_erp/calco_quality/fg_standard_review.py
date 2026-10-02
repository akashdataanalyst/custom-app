"""Quality-owned prospective specification completion; never an import or stock API."""
from __future__ import annotations
import copy,json,math
from collections import defaultdict
import frappe
from frappe.utils import now_datetime
from calco_erp.calco_quality import quality_master_versions as q
VERSION='quality-fg-standard-review-v1'
MAPPING='Quality Parameter Mapping Revision'

def read_authority():
    if frappe.session.user!='Administrator' and not set(frappe.get_roles()).intersection({'Quality User','Quality Manager','System Manager'}):
        frappe.throw('Quality access required.',frappe.PermissionError)
    frappe.has_permission(q.DOCTYPE,'read',throw=True)

def approve_authority():
    from calco_erp.calco_quality.manufacturing_master_authority import quality_authority
    quality_authority()

def mapping_records():
    if not hasattr(frappe.local,'quality_review_aliases'):
        rows=frappe.get_all(MAPPING,filters={'is_active':1},fields=['name','alias_key','target_parameter','reason','approved_by','approved_on'],limit_page_length=0) if frappe.db.exists('DocType',MAPPING) else []
        frappe.local.quality_review_aliases={r.alias_key:r for r in rows}
    return frappe.local.quality_review_aliases

def approved_property(prop):
    evidence=(prop or {}).get('quality_review') or {}
    return bool(evidence.get('decision') in ('Specification','Not Applicable') and all(evidence.get(k) for k in ('revision','reason','approved_by','approved_on','reference')))

def number(value):
    if value is None or value=='':return None
    if isinstance(value,bool):frappe.throw('A specification limit must be numeric.')
    try:result=float(value)
    except (ValueError,TypeError):frappe.throw('A specification limit must be numeric.')
    if not math.isfinite(result):frappe.throw('Specification limits must be finite.')
    return result

def normalize_changes(changes,allowed):
    from calco_erp.calco_quality import manufacturing_master_authority as a
    changes=frappe.parse_json(changes) if isinstance(changes,str) else changes
    if not isinstance(changes,list) or not changes:frappe.throw('Enter at least one specification or applicability decision.')
    result=[];seen=set()
    for row in changes:
        if not isinstance(row,dict):frappe.throw('Invalid specification row.')
        name=a.canonical(str(row.get('parameter') or '').strip())
        if name not in allowed or name in seen:frappe.throw('Select each applicable parameter once from this grade review.')
        seen.add(name);decision=row.get('decision')
        if decision not in ('Specification','Not Applicable'):frappe.throw('Confirm Specification or Not Applicable explicitly.')
        lo=number(row.get('minimum_value'));hi=number(row.get('maximum_value'));target=str(row.get('target_value') or '').strip()
        if decision=='Specification':
            if lo is None and hi is None and not target:frappe.throw('Enter Min, Max or a rating/text specification; blank is not zero.')
            if lo is not None and hi is not None and lo>hi:frappe.throw('Minimum cannot exceed maximum.')
            if target and (lo is not None or hi is not None):frappe.throw('Use numeric limits or a rating/text criterion, not conflicting types.')
        elif lo is not None or hi is not None or target:frappe.throw('Not Applicable cannot also contain acceptance limits.')
        result.append({'parameter':name,'decision':decision,'minimum_value':lo,'maximum_value':hi,'target_value':target,'conditions':str(row.get('conditions') or '').strip(),'unit':str(row.get('unit') or '').strip()})
    return sorted(result,key=lambda x:x['parameter'])

def grade_controls(fg):
    from calco_erp.calco_quality import manufacturing_master_authority as a
    docs=[frappe.get_doc(q.DOCTYPE,n) for n in frappe.get_all(q.DOCTYPE,filters={'master_kind':'Control Plan','fg_item':fg,'is_active':1},pluck='name',limit_page_length=0)]
    controls={a.canonical(c['parameter']) for d in docs for c in a.controls(json.loads(d.payload)) if a.required_final(c)}
    return docs,controls

def proposal(fg,expected_revision,changes,reason,revision_reference):
    read_authority();frappe.get_doc('Item',fg).check_permission('read')
    reason=str(reason or '').strip();reference=str(revision_reference or '').strip()
    if not reason or not reference:frappe.throw('Revision reference and approval reason/evidence are mandatory.')
    current=q.current('FG Standard',fg);docs,allowed=grade_controls(fg)
    normalized=normalize_changes(changes,allowed)
    expected_revision=str(expected_revision or '')
    key='QMV-REVIEW-'+q.source.fingerprint([VERSION,fg,expected_revision,normalized,reason,reference])[:32]
    if frappe.db.exists(q.DOCTYPE,key):
        if not current or current.name!=key:frappe.throw('This revision has already been superseded; refresh the review.')
        return {'key':key,'reused':True,'current':current,'changes':normalized}
    if (current.name if current else '')!=expected_revision:frappe.throw('The FG Standard changed. Refresh and review the latest revision before approval.')
    return {'key':key,'reused':False,'current':current,'changes':normalized,'reason':reason,'reference':reference,'plans':docs}

@frappe.whitelist()
def preview_revision(fg,expected_revision='',changes=None,reason='',revision_reference=''):
    p=proposal(fg,expected_revision,changes,reason,revision_reference)
    return {'revision':p['key'],'previous':p['current'].name if p['current'] else None,'changes':p['changes'],'reused':p['reused'],'stock_effect':'None','scope':'Prospective FG Standard only; existing frozen runs unchanged'}

def revision_property(change,prior,evidence):
    return {'parameter':change['parameter'],'minimum_value':change['minimum_value'],'maximum_value':change['maximum_value'],'target_values':[change['target_value']] if change['target_value'] else [],'conditions':[change['conditions']] if change['conditions'] else copy.deepcopy((prior or {}).get('conditions',[])),'unit':change['unit'] or (prior or {}).get('unit',''),'not_applicable':change['decision']=='Not Applicable','source_columns':copy.deepcopy((prior or {}).get('source_columns',[])),'conflicts':[],'quality_review':{**evidence,'decision':change['decision'],'previous_property':copy.deepcopy(prior)}}


@frappe.whitelist()
def approve_revision(fg,expected_revision='',changes=None,reason='',revision_reference=''):
    approve_authority()
    frappe.db.sql('select name from `tabItem` where name=%s for update',(fg,))
    p=proposal(fg,expected_revision,changes,reason,revision_reference)
    if p['reused']:return {'name':p['key'],'reused':True,'readiness':affected_readiness(fg)}
    old=p['current']
    if old:old.check_permission('write')
    payload=json.loads(old.payload) if old else {'kind':'FG Standard','properties':{},'cells':[],'property_review':[]}
    at=str(now_datetime());evidence={'revision':p['key'],'previous':old.name if old else None,'reason':p['reason'],'reference':p['reference'],'approved_by':frappe.session.user,'approved_on':at,'version':VERSION}
    for change in p['changes']:
        param=change['parameter'];prior=copy.deepcopy(payload.get('properties',{}).get(param))
        prop=revision_property(change,prior,evidence)
        payload.setdefault('properties',{})[param]=prop
    payload.setdefault('quality_revision_history',[]).append({**evidence,'parameters':[r['parameter'] for r in p['changes']]})
    values={'doctype':q.DOCTYPE,'version_key':p['key'],'master_kind':'FG Standard','fg_item':fg,'production_line':'','is_active':1,'business_revision':p['reference'],'issue_date_text':at,'material_status':old.material_status if old else '', 'source_file':old.source_file if old else '', 'source_sha256':old.source_sha256 if old else '', 'source_sheet':old.source_sheet if old else '', 'source_row':old.source_row if old else 0,'source_version':VERSION,'source_fingerprint':q.source.fingerprint([old.source_fingerprint if old else '',evidence,p['changes']]),'payload_hash':q.source.fingerprint(payload),'payload':q.encoded(payload),'review_notes':'Quality-approved prospective specification/applicability revision; source cells and prior history retained.','approval_reference':p['reason'],'supersedes':old.name if old else '', 'imported_by':frappe.session.user,'imported_on':at}
    flag=getattr(frappe.flags,'controlled_quality_master_sync',False);frappe.flags.controlled_quality_master_sync=True
    try:
        if old:old.is_active=0;old.save()
        doc=frappe.get_doc(values);doc.insert()
    finally:frappe.flags.controlled_quality_master_sync=flag
    return {'name':doc.name,'reused':False,'readiness':affected_readiness(fg)}

def affected_readiness(fg):
    from calco_erp.calco_quality import manufacturing_master_authority as a
    docs,_=grade_controls(fg)
    return [{'line':d.production_line,**{k:v for k,v in a.readiness(fg,d.production_line).items() if k in ('QC Ready','IPQC Ready','Fully Manufacturing Ready','blockers')}} for d in docs]

def category(res,standard):
    if not standard:return 'Missing FG Standard'
    if res['class']=='N':return 'Approved Not Applicable'
    if res['class']=='D':return 'Explicit N/A' if res['reason']=='Not Applicable' else 'Blank'
    return {'A':'Resolved','B':'Resolved','C':'Resolved','E':'Missing Parameter','F':'Ambiguous'}[res['class']]

def catalog():
    from calco_erp.calco_quality import manufacturing_master_authority as a
    from calco_erp.calco_production import mpds_master as mm
    masters=frappe.get_all(q.DOCTYPE,filters={'is_active':1},fields=['name','master_kind','fg_item','production_line','payload_hash','material_status','business_revision','payload','modified'],limit_page_length=0)
    mpds=frappe.get_all(mm.MPDS_DOCTYPE,filters={'status':mm.CURRENT_STATUS},fields=['name','fg_item','production_line','import_identity_key','imported_payload_hash','modified'],limit_page_length=0)
    native=frappe.get_all('FG Control Plan',fields=['name','fg_item_code','modified'],limit_page_length=0)
    parameters=frappe.get_all('Quality Inspection Parameter',fields=['name','modified'],limit_page_length=0)
    dependency=q.source.fingerprint([dict(mapping_records()),parameters])
    cache_key='fg-standard-review:v2:'+q.source.fingerprint([[d.name,d.payload_hash,str(d.modified)] for d in masters]+[[d.name,d.imported_payload_hash,str(d.modified)] for d in mpds]+[native,dependency])
    cached=frappe.cache.get_value(cache_key)
    if cached is not None:return cached
    standards={d.fg_item:d for d in masters if d.master_kind=='FG Standard'}
    items={r.name:r for r in frappe.get_all('Item',fields=['name','item_name','description','disabled'],limit_page_length=0)}
    production={(d.fg_item,d.production_line) for d in mpds if (d.import_identity_key or '').startswith('manufacturing-masters-v1:')}
    result={}
    for cp in (d for d in masters if d.master_kind=='Control Plan'):
        std=standards.get(cp.fg_item);sp=json.loads(std.payload) if std else None;payload=json.loads(cp.payload)
        item=items.get(cp.fg_item) or {};g=result.setdefault(cp.fg_item,{'fg':cp.fg_item,'description':item.get('item_name') or item.get('description') or cp.fg_item,'item_exists':bool(item),'disabled':item.get('disabled',0),'standard':std.name if std else '', 'revision':std.business_revision if std else '', 'source_status':std.material_status if std else cp.material_status,'lines':[],'plans':[],'requirements':[],'readiness':[]})
        g['lines'].append(cp.production_line);g['plans'].append(cp.name)
        pair_key='fg-standard-review:pair-v1:'+q.source.fingerprint([dependency,cp.name,cp.payload_hash,std.name if std else '',std.payload_hash if std else '',[[d.name,d.imported_payload_hash,str(d.modified)] for d in mpds if d.fg_item==cp.fg_item and d.production_line==cp.production_line],[dict(d) for d in native if d.fg_item_code==cp.fg_item]])
        pair=frappe.cache.get_value(pair_key)
        if pair is not None:
            g['readiness'].append(pair['state']);g['requirements'].extend(pair['requirements']);continue
        initial=len(g['requirements'])
        state=a.readiness(cp.fg_item,cp.production_line)
        g['readiness'].append({'line':cp.production_line,**{k:state[k] for k in ('Production Ready','QC Ready','IPQC Ready','Fully Manufacturing Ready','blockers')}})
        for control in a.controls(payload):
            if not a.required_final(control):continue
            r=a.resolve(control,sp);g['requirements'].append({'parameter':r['identity'],'source_parameter':control['parameter'],'line':cp.production_line,'control_plan':cp.name,'criticality':control.get('criticality'),'state':category(r,std),'reason':r['reason'],'existing':(sp or {}).get('properties',{}).get(r['identity']),'specification':r.get('property')})
        frappe.cache.set_value(pair_key,{'state':g['readiness'][-1],'requirements':g['requirements'][initial:]},expires_in_sec=86400)
    for g in result.values():
        g['lines']=sorted(set(g['lines']));g['unresolved']=len({r['parameter'] for r in g['requirements'] if r['state'] not in ('Resolved','Approved Not Applicable')});g['fully_ready']=all(r['Fully Manufacturing Ready'] for r in g['readiness'])
    rows=sorted(result.values(),key=lambda g:(0 if str(g['source_status']).casefold() in ('production','commercial') else 1,g['fg']))
    frappe.cache.set_value(cache_key,rows,expires_in_sec=120)
    return rows

@frappe.whitelist()
def list_reviews(filters=None):
    read_authority();filters=frappe.parse_json(filters) if isinstance(filters,str) else (filters or {})
    rows=[]
    for g in catalog():
        if filters.get('fg') and filters['fg'].casefold() not in (g['fg']+' '+g['description']).casefold():continue
        if filters.get('line') and filters['line'] not in g['lines']:continue
        if filters.get('source_status') and filters['source_status'].casefold() not in str(g['source_status']).casefold():continue
        if filters.get('readiness')=='Fully Ready' and not g['fully_ready']:continue
        if filters.get('readiness')=='Blocked' and g['fully_ready']:continue
        requirements=[r for r in g['requirements'] if not filters.get('line') or r['line']==filters['line']]
        if filters.get('state'):requirements=[r for r in requirements if r['state']==filters['state']]
        if filters.get('criticality'):requirements=[r for r in requirements if str(r['criticality']).casefold()==filters['criticality'].casefold()]
        if (filters.get('state') or filters.get('criticality')) and not requirements:continue
        rows.append({k:v for k,v in g.items() if k!='requirements'})
    return {'grades':rows,'can_approve':frappe.session.user=='Administrator' or 'Quality Manager' in frappe.get_roles()}

@frappe.whitelist()
def get_grade(fg):
    read_authority()
    for g in catalog():
        if g['fg']==fg:return g
    frappe.throw('No source Control Plan requirements found for this grade.')

@frappe.whitelist()
def parameter_mapping_review():
    read_authority();groups={}
    for g in catalog():
        for r in g['requirements']:
            if r['state'] not in ('Missing Parameter','Ambiguous'):continue
            key=(r['source_parameter'],r['reason']);entry=groups.setdefault(key,{'parameter':key[0],'reason':key[1],'grades':set(),'lines':set(),'alias_candidate':r['state']=='Missing Parameter' or 'alias' in r['reason'].casefold()})
            entry['grades'].add(g['fg']);entry['lines'].add(r['line'])
    return [{**v,'grades':sorted(v['grades']),'lines':sorted(v['lines'])} for v in groups.values()]

@frappe.whitelist()
def approve_mapping(alias,target_parameter,reason,reference,expected_revision=''):
    approve_authority();read_authority()
    alias=' '.join(str(alias or '').split());target_parameter=str(target_parameter or '').strip();reason=str(reason or '').strip();reference=str(reference or '').strip()
    if not alias or not target_parameter or not reason or not reference:frappe.throw('Alias, target parameter, reason and reference are required.')
    if not frappe.db.exists('Quality Inspection Parameter',target_parameter):frappe.throw('Select an existing controlled Quality Inspection Parameter.')
    from calco_erp.calco_quality.fg_quality_setup import get_true_fg_testing_method_name_set
    if target_parameter not in get_true_fg_testing_method_name_set() | {'Surface Appearance','Laser Marking','Viscosity Number'}:
        frappe.throw('Select a canonical product-quality parameter; an alias cannot remove a required test by mapping it to a process or packing control.')
    key_alias=alias.casefold()
    if key_alias==target_parameter.casefold():frappe.throw('Alias and target must differ.')
    frappe.db.sql('select name from `tabDocType` where name=%s for update',(MAPPING,))
    frappe.local.quality_review_aliases=None;del frappe.local.quality_review_aliases
    existing=mapping_records().get(key_alias);key='QPMR-'+q.source.fingerprint([key_alias,target_parameter,reason,reference,expected_revision])[:32]
    if frappe.db.exists(MAPPING,key):
        if not existing or existing.name!=key:frappe.throw('This mapping revision has been superseded.')
        return {'name':key,'reused':True}
    if (existing.name if existing else '')!=expected_revision:frappe.throw('Mapping changed; refresh before approving.')
    if target_parameter.casefold() in mapping_records() or any(r.target_parameter.casefold()==key_alias for r in mapping_records().values()):frappe.throw('Chained aliases are not allowed; select the final canonical parameter.')
    candidates=parameter_mapping_review()
    if not existing and not any(r['parameter'].casefold()==key_alias and r['alias_candidate'] for r in candidates):frappe.throw('Use a grade specification revision for conflicting limits; this is not an unresolved alias candidate.')
    values={'doctype':MAPPING,'revision_key':key,'alias':alias,'alias_key':key_alias,'target_parameter':target_parameter,'is_active':1,'reason':reason,'reference':reference,'approved_by':frappe.session.user,'approved_on':now_datetime(),'supersedes':existing.name if existing else ''}
    values['fingerprint']=q.source.fingerprint({k:str(values.get(k) or '') for k in ('revision_key','alias','alias_key','target_parameter','reason','reference','approved_by','approved_on','supersedes')})
    flag=getattr(frappe.flags,'quality_mapping_review',False);frappe.flags.quality_mapping_review=True
    try:
        if existing:
            old=frappe.get_doc(MAPPING,existing.name);old.is_active=0;old.save()
        doc=frappe.get_doc(values);doc.insert()
    finally:frappe.flags.quality_mapping_review=flag
    del frappe.local.quality_review_aliases
    return {'name':doc.name,'reused':False,'scope':'Prospective parameter resolution; frozen runs unchanged'}
