frappe.pages["fg-standard-review"].on_page_load = function(wrapper) {
 const page = frappe.ui.make_app_page({parent: wrapper, title: __("FG Standard Review"), single_column: true});
 const method = "calco_erp.calco_quality.fg_standard_review.";
 const esc = value => frappe.utils.escape_html(String(value ?? ""));
 const call = async (name, args = {}) => (await frappe.call({method: method + name, args, freeze: true})).message;
 const link = (type, name) => name ? `<a href="/app/${frappe.router.slug(type)}/${encodeURIComponent(name)}">${esc(name)}</a>` : "Missing";
 const spec = p => !p ? "—" : p.not_applicable ? "N/A" : [p.minimum_value != null ? "Min " + p.minimum_value : "", p.maximum_value != null ? "Max " + p.maximum_value : "", ...(p.target_values || []), ...(p.conditions || []), p.unit || ""].filter(Boolean).map(esc).join(" · ");
 const area = $('<div class="fg-review-body p-3"></div>').appendTo(page.main);
 const filters = {};
 let canApprove = false;
 for (const df of [
  {fieldname:'fg',label:'FG Code / Description',fieldtype:'Data'},
  {fieldname:'line',label:'Line',fieldtype:'Link',options:'Workstation'},
  {fieldname:'source_status',label:'Source Status',fieldtype:'Select',options:'\nProduction\nPreLaunch\nDD'},
  {fieldname:'criticality',label:'Criticality',fieldtype:'Select',options:'\nCritical\nMajor\nMinor'},
  {fieldname:'readiness',label:'Readiness',fieldtype:'Select',options:'\nFully Ready\nBlocked'},
  {fieldname:'state',label:'Missing Specification Type',fieldtype:'Select',options:'\nBlank\nExplicit N/A\nMissing FG Standard\nMissing Parameter\nAmbiguous\nApproved Not Applicable'}
 ]) filters[df.fieldname] = page.add_field({...df, change: () => load()});
 let generation = 0;
 async function load() {
  const seq = ++generation;
  const data = await call('list_reviews', {filters:Object.fromEntries(Object.entries(filters).map(([k,v])=>[k,v.get_value()]))});
  if(seq!==generation)return;
  canApprove = data.can_approve;
  area.html(`<p class="text-muted">${data.grades.length} grades · Production-source grades first. Quality-approved revisions apply to future runs. Existing frozen specifications remain unchanged.</p><div class="table-responsive"><table class="table table-bordered table-hover"><thead><tr><th>FG Code / Description</th><th>Current FG Standard</th><th>Source Status</th><th>Lines</th><th>Unresolved Tests</th><th>Readiness</th><th></th></tr></thead><tbody>${data.grades.map((g,i)=>`<tr><td><b>${esc(g.fg)}</b><br>${esc(g.description)}</td><td>${link('Manufacturing Quality Master Version',g.standard)}<br>${esc(g.revision)}</td><td>${esc(g.source_status)}</td><td>${g.lines.map(esc).join(', ')}</td><td>${g.unresolved}</td><td>${g.fully_ready?'Fully Ready':'Blocked'}</td><td><button class="btn btn-sm btn-default review-grade" data-index="${i}">Review</button></td></tr>`).join('')}</tbody></table></div>`);
  area.find('.review-grade').on('click',function(){review(data.grades[Number(this.dataset.index)].fg);});
 }
 async function review(fg) {
  const g = await call('get_grade',{fg});
  const parameters = [...new Set(g.requirements.map(r=>r.parameter))].sort();
  const d = new frappe.ui.Dialog({title:__('FG Standard Review: {0}',[fg]),size:'extra-large',fields:[
   {fieldname:'context',fieldtype:'HTML'},
   {fieldname:'changes',label:'Proposed Specification / Applicability Changes',fieldtype:'Table',cannot_add_rows:false,in_place_edit:true,fields:[
    {fieldname:'parameter',label:'Parameter',fieldtype:'Select',options:parameters.join('\n'),reqd:1,in_list_view:1,columns:2},
    {fieldname:'decision',label:'Decision',fieldtype:'Select',options:'\nSpecification\nNot Applicable',reqd:1,in_list_view:1,columns:2},
    {fieldname:'minimum_value',label:'Min',fieldtype:'Data',in_list_view:1,columns:1},
    {fieldname:'maximum_value',label:'Max',fieldtype:'Data',in_list_view:1,columns:1},
    {fieldname:'target_value',label:'Rating / Text',fieldtype:'Data',in_list_view:1,columns:2},
    {fieldname:'conditions',label:'Test Condition / Thickness / Temperature',fieldtype:'Small Text',in_list_view:1,columns:2},
    {fieldname:'unit',label:'Unit',fieldtype:'Data'}
   ]},
   {fieldname:'revision_reference',label:'Controlled Revision / Approval Reference',fieldtype:'Data',reqd:1},
   {fieldname:'reason',label:'Quality Revision Reason / Evidence',fieldtype:'Small Text',reqd:1}
  ],primary_action_label:'Preview Revision',primary_action:async values=>{
   const args={fg,expected_revision:g.standard,changes:values.changes,reason:values.reason,revision_reference:values.revision_reference};
   const result=await call('preview_revision',args);
   const preview=new frappe.ui.Dialog({title:'Review Prospective FG Standard Revision',size:'large',fields:[{fieldname:'result',fieldtype:'HTML'}]});
   preview.fields_dict.result.$wrapper.html(`<p>Previous: ${link('Manufacturing Quality Master Version',g.standard)}</p><p>New revision: ${esc(result.revision)}</p><table class="table table-bordered"><thead><tr><th>Parameter</th><th>Decision</th><th>Specification</th></tr></thead><tbody>${result.changes.map(c=>`<tr><td>${esc(c.parameter)}</td><td>${esc(c.decision)}</td><td>${spec({...c,target_values:c.target_value?[c.target_value]:[],conditions:c.conditions?[c.conditions]:[]})}</td></tr>`).join('')}</tbody></table><p>${esc(result.scope)}. No stock or QI is created.</p><p><b>Reason:</b> ${esc(values.reason)}</p>`);
   if(canApprove)preview.set_primary_action('Approve New FG Standard Revision',async()=>{
    preview.get_primary_btn().prop('disabled',true);
    try {const saved=await call('approve_revision',args);preview.hide();d.hide();frappe.msgprint({title:'FG Standard Revision Approved',message:`${link('Manufacturing Quality Master Version',saved.name)}<br>${saved.readiness.map(r=>esc(r.line)+': '+(r['Fully Manufacturing Ready']?'Fully Manufacturing Ready':r['QC Ready']?'QC Ready; other requirements remain':'Blocked')).join('<br>')}`});await load();}
    finally {preview.get_primary_btn().prop('disabled',false);}
   });
   else preview.fields_dict.result.$wrapper.append('<p>Quality Manager approval is required. No revision has been saved.</p>');
   preview.show();
  }});
  d.fields_dict.context.$wrapper.html(`<p><b>${esc(g.description)}</b> · ${link('Manufacturing Quality Master Version',g.standard)} · ${esc(g.source_status)}</p>${!g.item_exists?'<p class="text-danger">Missing FG Item — Item master authority must be established first.</p>':''}<details open><summary>Requirements and affected Control Plans</summary><div style="max-height:280px;overflow:auto"><table class="table table-bordered"><thead><tr><th>Parameter</th><th>Line / Control Plan</th><th>Criticality</th><th>Source State</th><th>Existing Specification</th></tr></thead><tbody>${g.requirements.map(r=>`<tr><td>${esc(r.source_parameter)}</td><td>${esc(r.line)}<br>${link('Manufacturing Quality Master Version',r.control_plan)}</td><td>${esc(r.criticality)}</td><td>${esc(r.state)}<br><small>${esc(r.reason)}</small></td><td>${spec(r.specification||r.existing)}</td></tr>`).join('')}</tbody></table></div></details><details><summary>Readiness impact by line</summary>${g.readiness.map(r=>`<p><b>${esc(r.line)}</b>: ${r['Fully Manufacturing Ready']?'Fully Ready':esc((r.blockers||[]).join('; '))}</p>`).join('')}</details><p class="text-muted">Enter only approved Quality specifications. Blank is not zero. N/A requires an explicit decision and reason. Conditions include applicable thickness and temperature. Existing conditions and units are retained when omitted.</p>`);
  if(!g.item_exists)d.get_primary_btn().prop('disabled',true);
  d.show();
 }
 async function mappings() {
  const rows=await call('parameter_mapping_review');
  const d=new frappe.ui.Dialog({title:'Parameter Mapping Review',size:'extra-large',fields:[{fieldname:'rows',fieldtype:'HTML'}]});
  d.fields_dict.rows.$wrapper.html(`<p>Approve aliases centrally only where acceptance criteria are equivalent. Conflicting limits require an FG Standard revision.</p><table class="table table-bordered"><thead><tr><th>Parameter</th><th>Issue</th><th>Affected Grades / Lines</th><th>Action</th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td>${esc(r.parameter)}</td><td>${esc(r.reason)}</td><td>${r.grades.length} grades · ${r.lines.map(esc).join(', ')}<details><summary>Grades</summary>${r.grades.map(esc).join(', ')}</details></td><td>${r.alias_candidate&&canApprove?`<button class="btn btn-default btn-sm alias-review" data-index="${i}">Review Alias</button>`:'Quality specification review'}</td></tr>`).join('')}</tbody></table>`);
  d.fields_dict.rows.$wrapper.find('.alias-review').on('click',function(){
   const r=rows[Number(this.dataset.index)];
   const entry=new frappe.ui.Dialog({title:'Approve Central Parameter Alias',fields:[
    {fieldname:'alias',label:'Source Parameter',fieldtype:'Data',default:r.parameter,read_only:1},
    {fieldname:'target_parameter',label:'Equivalent Controlled Parameter',fieldtype:'Link',options:'Quality Inspection Parameter',reqd:1},
    {fieldname:'reference',label:'Approval Reference',fieldtype:'Data',reqd:1},
    {fieldname:'reason',label:'Evidence of Equivalent Acceptance Criteria',fieldtype:'Small Text',reqd:1}
   ],primary_action_label:'Review and Approve Alias',primary_action:values=>frappe.confirm(`Apply this prospective alias to ${r.grades.length} affected grades? Existing frozen runs remain unchanged.`,async()=>{await call('approve_mapping',{...values,expected_revision:r.mapping_revision||''});entry.hide();d.hide();await load();})});entry.show();
  });d.show();
 }
 page.add_inner_button('Parameter Mapping Review',mappings);
 page.add_inner_button('Refresh',load);
 load();
};
