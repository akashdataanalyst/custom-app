"""Prospective master authority: immutable specifications, controls and readiness."""
from __future__ import annotations
import json
from functools import lru_cache
import frappe
from calco_erp.calco_quality import quality_master_versions as versions

VERSION = 'manufacturing-spec-authority-v2'
PERIODIC_DEFAULT = {'version':'ipqc-active-time-default-8h-v1','trigger_basis':'Elapsed Minutes','display_trigger':'Active Production Time','interval_minutes':480,'interval_hours':8,'pause_time_excluded':True,'scope':'All applicable product-quality tests; prospective only','approval':'Direct user approval: all applicable product-quality tests every 8 active hours, permitted window 30 minutes','sampling_window':30,'sample_quantity_authority':'Control Plan sample_size; not final-QC multiplier','mandatory':True,'missed_escalation':'Existing Quality disposition; critical mandatory tests require escalation'}
LABELS = {'A':'Exact FG Standard match','B':'Deterministic alias match','C':'Explicit Control Plan-only specification','D':'FG Standard specification blank/not applicable','E':'No corresponding FG Standard parameter','F':'Ambiguous mapping','N':'Approved FG Standard Not Applicable'}


def normalized(value):
    return ' '.join(str(value or '').split()).casefold()


@lru_cache(maxsize=1)
def identities():
    from calco_erp.calco_quality.fg_quality_setup import FG_TESTING_METHOD_ALIASES,get_true_fg_testing_method_names
    result={normalized(n):n for n in get_true_fg_testing_method_names()}
    result.update({normalized(k):v for k,v in FG_TESTING_METHOD_ALIASES.items()})
    return result


def canonical(value):
    from calco_erp.calco_quality.fg_standard_review import mapping_records
    mapping=mapping_records().get(normalized(value))
    return mapping.target_parameter if mapping else identities().get(normalized(value), ' '.join(str(value or '').split()))


@lru_cache(maxsize=2048)
def header_mapping(header):
    from calco_erp.calco_quality.importers.fg_standard_min_max_import import parse_standard_header
    from calco_erp.calco_quality.fg_quality_setup import get_true_fg_testing_method_name_set
    if normalized(header)=='glow wire test @ 3.2mm (°c)':header='Glow Wire Test @ 3.2 mm'
    return parse_standard_header(str(header or ''),get_true_fg_testing_method_name_set())


def resolve(control,standard):
    requested=control['parameter'];identity=canonical(requested)
    result={'parameter':requested,'identity':identity,'class':None,'reason':'','property':None,'source_columns':[]}
    props=standard.get('properties',{}) if standard else {}
    keys=[k for k in props if normalized(canonical(k))==normalized(identity)]
    columns=[c for c in (standard or {}).get('cells',[]) if normalized(canonical(c.get('header')))==normalized(identity) or ((m:=header_mapping(c.get('header'))) and normalized(canonical(m['parameter']))==normalized(identity))]
    result['source_columns']=[c['column'] for c in columns]
    from calco_erp.calco_quality.fg_standard_review import approved_property
    reviewed=[k for k in keys if approved_property(props[k])]
    if len(reviewed)==1:
        prop=props[reviewed[0]]
        result.update({'class':'N' if prop.get('not_applicable') else ('A' if normalized(reviewed[0])==normalized(requested) else 'B'),'reason':'Quality-approved FG Standard applicability' if prop.get('not_applicable') else 'Quality-approved FG Standard specification revision','property':prop})
        return result
    if len(keys)>1:
        result.update({'class':'F','reason':'Multiple standard properties share a normalized identity'});return result
    if any(c.get('source_type') in ('date','datetime') for c in columns):
        result.update({'class':'F','reason':'Date-typed specification cannot become an acceptance limit'});return result
    if keys:
        prop=props[keys[0]]
        lo,hi=prop.get('minimum_value'),prop.get('maximum_value')
        if prop.get('conflicts') or (lo is not None and hi is not None and lo>hi):
            result.update({'class':'F','reason':'Conflicting or inverted specification limits'});return result
        meaningful=lo is not None or hi is not None or bool(prop.get('target_values'))
        if not prop.get('not_applicable') and meaningful:
            result.update({'class':'A' if normalized(keys[0])==normalized(requested) else 'B','reason':'Frozen FG Standard specification','property':prop});return result
        if columns or prop.get('not_applicable'):
            result.update({'class':'D','reason':'Not Applicable' if prop.get('not_applicable') else 'Corresponding source specification cells are blank/unspecified'});return result
    if columns:
        raw_prop={'parameter':identity,'minimum_value':None,'maximum_value':None,'target_values':[],'conditions':[],'unit':'','source_columns':result['source_columns']}
        for cell in columns:
            value=cell.get('value')
            if normalized(value) in ('','n/a','na','-'):continue
            mapping=header_mapping(cell.get('header'));field=(mapping or {}).get('fieldname','target_value')
            if field in ('minimum_value','maximum_value') and versions.numeric(value):
                if raw_prop[field] is not None and raw_prop[field]!=value:
                    result.update({'class':'F','reason':'Conflicting source limits'});return result
                raw_prop[field]=value;raw_prop['unit']=mapping.get('unit','')
            else:raw_prop['target_values'].append(str(value))
        if raw_prop['minimum_value'] is not None or raw_prop['maximum_value'] is not None or raw_prop['target_values']:
            result.update({'class':'A' if any(normalized(c.get('header'))==normalized(requested) for c in columns) else 'B','reason':'Deterministic resolution from preserved FG Standard source cells','property':raw_prop});return result
        result.update({'class':'D','reason':'Corresponding source specification cells are blank/unspecified'});return result
    # Only a separately identified explicit specification qualifies, never Major/Minor,
    # sample size, frequency, a column label, or a process-control description.
    explicit=control.get('explicit_specification')
    if explicit and control.get('specification_source_reference'):
        meaningful=explicit.get('minimum_value') is not None or explicit.get('maximum_value') is not None or bool(explicit.get('target_values'))
        if meaningful:
            result.update({'class':'C','reason':control['specification_source_reference'],'property':explicit});return result
    ambiguous={'laser marking':'Laser printing is present but no approved Calco alias establishes equivalent acceptance criteria', 'surface appearance':'Aesthetic function/Finish are not an approved equivalent to Surface Appearance'}
    if normalized(requested) in ambiguous:
        result.update({'class':'F','reason':ambiguous[normalized(requested)]});return result
    result.update({'class':'E','reason':'No specification column; test-condition-only evidence is not an acceptance criterion'});return result


def controls(payload):
    """Include source controls the historical 40-method importer did not support."""
    result=[dict(c) for c in payload.get('controls',[])]
    cells=payload.get('cells',[])
    known={c['source_columns'][0] for c in result if c.get('source_columns')}
    for i,c in enumerate(cells):
        if c['column'] in known:continue
        if i+2>=len(cells):continue
        size,freq=cells[i+1],cells[i+2]
        if 'size' not in normalized(size.get('header')) or 'frequency' not in normalized(freq.get('header')):continue
        result.append({'parameter':c['header'],'criticality':c['value'],'sample_size':size['value'],'frequency':freq['value'],'frequency_scope':versions.frequency_scope(freq['value']),'source_columns':[c['column'],size['column'],freq['column']]})
    return result


def approved_not_applicable(control):
    return control.get('applicability')=='Not Applicable' and all(control.get(k) for k in ('applicability_reason','applicability_approved_by','applicability_approved_on','applicability_approval_reference'))


def required_final(control):
    from calco_erp.calco_quality.fg_quality_setup import get_true_fg_testing_method_name_set
    product_names=get_true_fg_testing_method_name_set() | {'Surface Appearance','Laser Marking','Viscosity Number'}
    return canonical(control.get('parameter')) in product_names and not approved_not_applicable(control) and control.get('frequency_scope')=='Final QC sample multiplier' and versions.numeric(control.get('sample_size')) and control['sample_size']>0 and versions.numeric(control.get('frequency')) and control['frequency']>0


def reconcile(standard,control):
    return [resolve(row,standard) for row in controls(control) if required_final(row)]


def project(standard_doc,control_doc):
    standard=json.loads(standard_doc.payload);control=json.loads(control_doc.payload)
    resolutions=[];rows=[];exceptions=[]
    for test in controls(control):
        if not required_final(test):continue
        resolution=resolve(test,standard);resolutions.append(resolution)
        if resolution['class']=='N':continue
        if resolution['class'] not in ('A','B','C'):
            exceptions.append(test['parameter']+': '+LABELS[resolution['class']]+' — '+resolution['reason']);continue
        prop=resolution['property'];lo=prop.get('minimum_value');hi=prop.get('maximum_value')
        rows.append({'name':control_doc.name+':'+test['parameter'],'parameter':resolution['identity'],'minimum_value':lo,'maximum_value':hi,'target_value':' | '.join(prop.get('target_values',[])),'unit':prop.get('unit',''),'size':test['sample_size'],'frequency':test['frequency'],'critical_test':int(normalized(test.get('criticality'))=='major'),'test_type':'Numeric' if lo is not None or hi is not None else 'Manual','version':standard_doc.name+' / '+control_doc.name,'test_condition':' | '.join(prop.get('conditions',[])),'source_exact_limits':True,'specification_authority':resolution['class']})
    if not rows and not exceptions:exceptions.append('No executable final-QC tests configured')
    from calco_erp.calco_quality.fg_standard_review import mapping_records
    mapping_evidence=[dict(v) for k,v in mapping_records().items() if any(normalized(c['parameter'])==k for c in controls(control))]
    return {'parameter_mapping_revisions':mapping_evidence,'resolution_version':VERSION,'standard':standard_doc.name,'standard_hash':standard_doc.payload_hash,'control_plan':control_doc.name,'control_plan_hash':control_doc.payload_hash,'fg_item':standard_doc.fg_item,'line':control_doc.production_line,'rows':rows,'exceptions':exceptions,'specification_resolution':resolutions,'ipqc_timing_policy':control.get('ipqc_timing_policy'),'ipqc_timing_policy_fingerprint':control.get('ipqc_timing_policy_fingerprint'),'ipqc_timing':'Approved source timing' if control.get('ipqc_timing_policy') else 'Unconfigured'}


def assess(production,quality,ipqc_rows):
    from calco_erp.calco_production.in_process_quality import validate_plan_row,CHECKPOINTS
    reasons=[];timing=[]
    for row in ipqc_rows:
        try:validate_plan_row(frappe._dict(row))
        except frappe.ValidationError as e:reasons.append(str(e))
        timing.append({key:row.get(key) for key in ['name','parameter',*CHECKPOINTS.values(),'custom_ipqc_period_basis','custom_ipqc_period_interval','custom_ipqc_sampling_window','custom_ipqc_mandatory','custom_ipqc_missed_escalation','custom_ipqc_quantity_basis','custom_ipqc_samples']})
    pr=bool(production);qr=bool(quality and quality.get('rows') and not quality.get('exceptions'));ir=bool(ipqc_rows) and not reasons
    if not pr:reasons.append('MPDS not resolved for this FG/Line')
    if not qr:reasons.extend((quality or {}).get('exceptions') or ['FG Standard / Control Plan specification authority not resolved'])
    if not ipqc_rows:reasons.append('Explicit IPQC checkpoint timing is unconfigured')
    return {'Production Ready':pr,'QC Ready':qr,'IPQC Ready':ir,'Fully Manufacturing Ready':pr and qr and ir,'blockers':reasons,'checkpoint_configuration':timing,'resolution_version':VERSION,'periodic_default':dict(PERIODIC_DEFAULT),'periodic_default_fingerprint':versions.source.fingerprint(PERIODIC_DEFAULT)}


@lru_cache(maxsize=1)
def source_control_plan_scope():
    """Exact prospective source scope, including unresolved/held source keys."""
    from pathlib import Path
    package=Path(__file__).parents[1]/'calco_production'/'master_data_20260915'
    scope=json.loads((package/'control_plan_scope.json').read_text(encoding='utf-8'))
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    if scope['source_sha256']!=manifest['sources']['control_plan']['sha256']:
        frappe.throw('Control Plan source-scope fingerprint mismatch.')
    return frozenset(tuple(pair) for pair in scope['combinations'])


def readiness(fg_item,line,ipqc_rows=None):
    from calco_erp.calco_production import mpds_start_snapshot as mp,in_process_quality as ip
    production=mp.prepare(fg_item,line)
    standard=versions.current('FG Standard',fg_item);control=versions.current('Control Plan',fg_item,line)
    quality=project(standard,control) if standard and control else None
    if ipqc_rows is None:
        ipqc_rows=frappe.get_all('FG Control Plan',filters={'fg_item_code':fg_item,'is_active':1,'applicable':1,'custom_inspection_scope':ip.STAGE},fields=ip.PLAN_FIELDS,order_by='name',limit_page_length=0)
    timing_errors=[]
    if control:
        ipqc_rows,timing_errors=periodic_rows(control,ipqc_rows,json.loads(standard.payload) if standard else None)
    state=assess(production,quality,ipqc_rows)
    state['resolved_ipqc_rows']=ipqc_rows
    if timing_errors:
        state['IPQC Ready']=False;state['Fully Manufacturing Ready']=False;state['blockers'].extend(timing_errors)
    if quality:
        missing=[r['parameter'] for r in quality['rows'] if not frappe.db.exists('Quality Inspection Parameter',r['parameter'])]
        if missing:
            state['QC Ready']=False;state['Fully Manufacturing Ready']=False
            state['blockers'].append('QI parameter master not configured: '+', '.join(sorted(set(missing))))
    state.update({'fg_item':fg_item,'line':line,'mpds':production,'quality_snapshot':quality,'managed_source_scope':bool(control) or (fg_item,line) in source_control_plan_scope(), 'control_plan_in_source':(fg_item,line) in source_control_plan_scope()})
    if quality and standard:
        cp=json.loads(control.payload);sp=json.loads(standard.payload)
        cp_ids={normalized(canonical(c['parameter'])) for c in controls(cp)}
        for row in ipqc_rows:
            r=resolve({'parameter':row['parameter']},sp)
            if normalized(canonical(row['parameter'])) not in cp_ids or r['class'] not in ('A','B','C'):
                state['Fully Manufacturing Ready']=False;state['blockers'].append('IPQC parameter lacks resolved frozen specification/control authority: '+row['parameter'])
    return state


def guard_and_bind(fg_item,line,ipqc_rows):
    # Same Item lock used by the controlled master import; freeze one coherent revision set.
    frappe.db.sql('select name from `tabItem` where name=%s for update',(fg_item,))
    state=readiness(fg_item,line,ipqc_rows)
    if not state['managed_source_scope']:return None
    if not state['Fully Manufacturing Ready']:
        frappe.throw('Manufacturing master readiness required for '+fg_item+' / '+line+': '+'; '.join(state['blockers']))
    ipqc_rows[:]=state.get('resolved_ipqc_rows',ipqc_rows)
    snapshot=state['quality_snapshot'];standard=versions.current('FG Standard',fg_item);spec=json.loads(standard.payload)
    for row in ipqc_rows:
        result=resolve({'parameter':row['parameter']},spec);prop=result['property']
        row.update({'minimum_value':prop.get('minimum_value'),'maximum_value':prop.get('maximum_value'),'target_value':' | '.join(prop.get('target_values',[])),'unit':prop.get('unit',''),'test_type':'Numeric' if prop.get('minimum_value') is not None or prop.get('maximum_value') is not None else 'Manual','test_condition':' | '.join(prop.get('conditions',[])),'source_exact_limits':True,'specification_authority_version':standard.name,'specification_authority_hash':standard.payload_hash})
    return state


@frappe.whitelist()
def get_master_readiness(fg_item,line):
    frappe.get_doc('Item',fg_item).check_permission('read')
    frappe.get_doc('Workstation',line).check_permission('read')
    return readiness(fg_item,line)


def quality_authority():
    if frappe.session.user!='Administrator' and 'Quality Manager' not in frappe.get_roles():
        frappe.throw('Quality Manager authority required.',frappe.PermissionError)


@frappe.whitelist()
def not_applicable_candidates(name):
    quality_authority()
    doc=frappe.get_doc(versions.DOCTYPE,name);doc.check_permission('read')
    if doc.master_kind!='Control Plan' or not doc.is_active:return []
    standard=versions.current('FG Standard',doc.fg_item)
    if not standard:return []
    return [r['parameter'] for r in reconcile(json.loads(standard.payload),json.loads(doc.payload)) if r['class']=='D']


@frappe.whitelist()
def revise_not_applicable(name,parameters,reason):
    quality_authority()
    parameters=frappe.parse_json(parameters) if isinstance(parameters,str) else parameters
    if not isinstance(parameters,list) or not parameters or not all(isinstance(p,str) for p in parameters) or not str(reason or '').strip():
        frappe.throw('Select parameters and enter a controlled Not Applicable reason.')
    old=frappe.get_doc(versions.DOCTYPE,name);old.check_permission('write')
    frappe.db.sql('select name from `tabItem` where name=%s for update',(old.fg_item,));old.reload()
    selected=sorted(set(parameters));reason=str(reason).strip()
    key='QMV-NA-'+versions.source.fingerprint([VERSION,name,selected,reason])[:32]
    if frappe.db.exists(versions.DOCTYPE,key):
        if versions.current('Control Plan',old.fg_item,old.production_line).name!=key:frappe.throw('This applicability revision has been superseded.')
        return {'name':key,'reused':True}
    if old.master_kind!='Control Plan' or not old.is_active:frappe.throw('Revise the current active Control Plan source version.')
    eligible=set(not_applicable_candidates(name))
    if not set(selected).issubset(eligible):frappe.throw('Only unresolved blank / Not Applicable FG Standard requirements can use this action.')
    from frappe.utils import now_datetime
    at=now_datetime();payload=json.loads(old.payload);updated=controls(payload)
    for row in updated:
        if row['parameter'] in selected:
            row.update({'applicability':'Not Applicable','applicability_reason':reason,'applicability_approved_by':frappe.session.user,'applicability_approved_on':str(at),'applicability_approval_reference':key})
    payload['controls']=updated
    if payload.get('ipqc_timing_policy'):
        payload['ipqc_periodic_controls'],payload['ipqc_timing_errors']=compile_periodic(payload)
    payload['applicability_revision']={'previous':name,'parameters':selected,'reason':reason,'by':frappe.session.user,'on':str(at)}
    doc=frappe.copy_doc(old);doc.version_key=key;doc.name=key;doc.is_active=1;doc.supersedes=name
    doc.payload=versions.encoded(payload);doc.payload_hash=versions.source.fingerprint(payload)
    doc.source_version=VERSION;doc.source_fingerprint=versions.source.fingerprint([old.source_fingerprint,key])
    doc.approval_reference='Quality Not Applicable revision: '+reason;doc.imported_by=frappe.session.user;doc.imported_on=at
    flag=getattr(frappe.flags,'controlled_quality_master_sync',False);frappe.flags.controlled_quality_master_sync=True
    try:
        old.is_active=0;old.save();doc.insert()
    finally:frappe.flags.controlled_quality_master_sync=flag
    return {'name':doc.name,'reused':False}


def install_resolved_parameter_masters():
    """Explicit master setup only; never called by readiness/start or QI reads."""
    quality_authority()
    from calco_erp.calco_quality.importers.fg_control_plan_import import QUALITY_PARAMETER_GROUP
    created=[]
    for doctype,field in [('FG Testing Method','method_name'),('Quality Inspection Parameter','parameter')]:
        if frappe.db.exists(doctype,'Surface Appearance'):continue
        values={'doctype':doctype,field:'Surface Appearance'}
        if doctype=='Quality Inspection Parameter':
            values.update(parameter_group=QUALITY_PARAMETER_GROUP,description='Exact Surface Appearance source criterion from approved manufacturing master workbooks.')
            if frappe.get_meta(doctype).has_field('custom_result_type'):values['custom_result_type']='Manual Review'
        frappe.get_doc(values).insert()
        created.append(doctype)
    return created


def compile_periodic(payload):
    result=[];errors=[]
    for control in controls(payload):
        if not required_final(control):continue
        parameter=canonical(control['parameter']);size=control['sample_size']
        if int(size)!=size:
            errors.append(parameter+': sample quantity must define an integer number of IPQC samples');continue
        criticality=normalized(control.get('criticality'))
        if criticality not in ('major','minor'):
            errors.append(parameter+': Control Plan criticality requires Quality review');continue
        result.append({'parameter':parameter,'source_columns':control['source_columns'],'custom_ipqc_samples':int(size),'critical_test':int(criticality=='major'),'custom_inspection_scope':'In-Process QC','custom_ipqc_period_basis':'Elapsed Minutes','custom_ipqc_period_interval':480,'custom_ipqc_sampling_window':30,'custom_ipqc_mandatory':1,'custom_ipqc_missed_escalation':int(criticality=='major'),'custom_ipqc_quantity_basis':'','custom_ipqc_startup':0,'custom_ipqc_stabilization':0,'custom_ipqc_end_of_batch':0})
    if not result:errors.append('No applicable product-quality tests for the Periodic policy')
    return result,errors


def periodic_rows(control_doc,existing,standard=None):
    payload=json.loads(control_doc.payload)
    if not payload.get('ipqc_timing_policy'):return list(existing),[]
    if versions.source.fingerprint(payload['ipqc_timing_policy'])!=payload.get('ipqc_timing_policy_fingerprint'):
        frappe.throw('Frozen Control Plan timing policy fingerprint mismatch.')
    omitted={canonical(c['parameter']) for c in controls(payload) if approved_not_applicable(c)}
    if standard:
        omitted.update(canonical(c['parameter']) for c in controls(payload) if resolve(c,standard)['class']=='N')
    rows=[frappe._dict(dict(row)) for row in existing if canonical(row['parameter']) not in omitted]
    for configured in payload['ipqc_periodic_controls']:
        configured={**configured,'parameter':canonical(configured['parameter'])}
        if configured['parameter'] in omitted:continue
        row=next((r for r in rows if canonical(r['parameter'])==configured['parameter']),None)
        checkpoint_flags={k:row.get(k) for k in ('custom_ipqc_startup','custom_ipqc_stabilization','custom_ipqc_end_of_batch')} if row else {}
        if row is None:
            row=frappe._dict(name=control_doc.name+':IPQC:'+configured['parameter'],version=control_doc.name);rows.append(row)
        row.update(configured);row.update(checkpoint_flags)
    return rows,[error for error in payload.get('ipqc_timing_errors',[]) if canonical(error.split(':',1)[0]) not in omitted]


def sync_timing(dry_run=True,expected_site=None,approval_reference=None):
    if not isinstance(dry_run,bool):raise ValueError('dry_run must be boolean')
    quality_authority()
    if not dry_run and (expected_site!=frappe.local.site or not approval_reference):raise ValueError('Exact site and timing approval reference required')
    result={'created':[],'unchanged':[],'review':[],'dry_run':dry_run}
    names=frappe.get_all(versions.DOCTYPE,filters={'master_kind':'Control Plan','is_active':1},pluck='name',order_by='fg_item,production_line,name',limit_page_length=0)
    for name in names:
        old=frappe.get_doc(versions.DOCTYPE,name)
        if not dry_run:
            frappe.db.sql('select name from `tabItem` where name=%s for update',(old.fg_item,));old.reload()
            if not old.is_active:frappe.throw('Control Plan changed concurrently; retry timing synchronization.')
        payload=json.loads(old.payload);rows,errors=compile_periodic(payload)
        if errors:result['review'].append({'fg':old.fg_item,'line':old.production_line,'errors':errors})
        policy_hash=versions.source.fingerprint(PERIODIC_DEFAULT)
        if payload.get('ipqc_timing_policy_fingerprint')==policy_hash and payload.get('ipqc_periodic_controls')==rows and payload.get('ipqc_timing_errors')==errors:
            result['unchanged'].append(name);continue
        key='QMV-TIME-'+versions.source.fingerprint([name,policy_hash,rows,errors])[:32]
        result['created'].append({'name':key,'previous':name,'fg':old.fg_item,'line':old.production_line,'parameters':len(rows),'review':errors})
        if dry_run:continue
        payload.update(ipqc_timing_policy=dict(PERIODIC_DEFAULT),ipqc_timing_policy_fingerprint=policy_hash,ipqc_periodic_controls=rows,ipqc_timing_errors=errors)
        doc=frappe.copy_doc(old);doc.name=key;doc.version_key=key;doc.is_active=1;doc.supersedes=name
        doc.payload=versions.encoded(payload);doc.payload_hash=versions.source.fingerprint(payload)
        doc.source_version=PERIODIC_DEFAULT['version'];doc.source_fingerprint=versions.source.fingerprint([old.source_fingerprint,policy_hash])
        from frappe.utils import now_datetime
        doc.imported_by=frappe.session.user;doc.imported_on=now_datetime();doc.approval_reference=approval_reference
        doc.review_notes='Prospective 8 active-hour / 30-minute product-quality policy. Raw final-QC and operational controls unchanged. Specification exceptions remain blocked.'
        flag=getattr(frappe.flags,'controlled_quality_master_sync',False);frappe.flags.controlled_quality_master_sync=True
        try:old.is_active=0;old.save();doc.insert()
        finally:frappe.flags.controlled_quality_master_sync=flag
    return result
