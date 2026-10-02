(() => {
 const api='calco_erp.calco_production.production_settlement';
 const allowed=()=>frappe.user_roles.some(r=>['Manufacturing Manager','Accounts Manager'].includes(r));
 const call=(method,args)=>frappe.call({method:`${api}.${method}`,args,freeze:true}).then(r=>r.message);
 const esc=v=>frappe.utils.escape_html(String(v??''));
 function prepare(frm){
  const d=new frappe.ui.Dialog({title:__('Prepare Run Settlement'),size:'extra-large',fields:[
   {fieldtype:'HTML',options:`<p>${__('Immutable physical confirmation')}: ${esc(frm.doc.name)} / ${__('Version')} ${esc(frm.doc.revision)}</p><p>${__('Approve the conversion and disposition policy. Missing or ambiguous evidence creates a Management Review case. No stock posting or automatic LCV submission occurs.')}</p>`},
   {fieldname:'method',fieldtype:'Select',label:__('Conversion Policy'),options:'\nsubmitted-additional-costs\nper-fg-unit\nexplicit-zero',reqd:1},
   {fieldname:'version',fieldtype:'Data',label:__('Approved Policy Version'),reqd:1},
   {fieldname:'rate',fieldtype:'Data',label:__('Approved Conversion Rate per FG Stock Unit'),depends_on:'eval:doc.method=="per-fg-unit"'},
   {fieldname:'evidence',fieldtype:'Small Text',label:__('Conversion Approval / Source Evidence'),reqd:1},
   {fieldname:'treatment',fieldtype:'Select',label:__('Recovery / Loss Treatment'),options:'\nno-recovery-no-loss\nprocess-loss-absorbed\nseparate-recovery-loss-absorbed',reqd:1},
   {fieldname:'advanced',fieldtype:'Section Break',label:__('Approved Attribution / Stock Evidence (Exceptions Only)'),collapsible:1},
   {fieldname:'attribution',fieldtype:'Code',options:'JSON',label:__('Additional Actual Source Attribution'),description:__('Array: lot, consumption_entry, consumption_detail, item_code, batch_no, qty, evidence. No BOM allocation.')},
   {fieldname:'restorations',fieldtype:'Code',options:'JSON',label:__('Posted Restoration References'),description:__('Array: lot, stock_entry, detail, batch_no, original_source [entry, detail, item, batch], evidence. References existing submitted stock only.')},
   {fieldname:'recovery',fieldtype:'Code',options:'JSON',label:__('Separately Posted Recovery References'),description:__('Array: lot, stock_entry, detail, batch_no, evidence. Existing submitted recovery receipt; full exact batch allocation.')},
   {fieldname:'approval_section',fieldtype:'Section Break'},
   {fieldname:'reason',fieldtype:'Small Text',label:__('Preparation / Revision Reason'),reqd:1},
   {fieldname:'approved',fieldtype:'Check',label:__('I approve this conversion policy and the referenced quantity/disposition evidence for this closure version.'),reqd:1}
  ],primary_action_label:__('Prepare Settlement'),primary_action:async v=>{
   if(!v.approved)return;
   const parse=x=>x?JSON.parse(x):[];
   let options;try{options={conversion:{method:v.method,version:v.version,rate:v.rate,approved:true,evidence:v.evidence},treatment:v.treatment,attribution:parse(v.attribution),restorations:parse(v.restorations),recovery:parse(v.recovery)};}catch(e){frappe.msgprint(__('Enter valid JSON arrays for exception evidence.'));return;}
   const r=await call('prepare',{closure:frm.doc.name,options,approved:1,reason:v.reason});d.hide();frappe.set_route('Form','Production Settlement Preparation',r.name);
  }});d.show();
 }
 async function review(frm){
  const result=await call('details',{name:frm.doc.name});
  if(result.exceptions){frappe.msgprint({title:__('Management Review Required'),message:result.exceptions.map(esc).join('<br>')});return;}
  const rows=result.calculation.targets.map(t=>({...t,...result.reconciliation.valuation_reconciliation.find(v=>v.lot===t.lot)}));
  const d=new frappe.ui.Dialog({title:__('Lot Settlement Review'),size:'extra-large',fields:[
   {fieldtype:'HTML',options:`<p>${esc(result.closure)} / ${__('Closure version')} ${esc(result.closure_version)} · ${__('Preparation version')} ${esc(result.preparation_version)}</p><table class="table table-bordered"><thead><tr>${['FG Lot','Manufacture','Material Value','Conversion','Recovery','Current Accounted','Prior Adjustments','Required FG Value','Residual'].map(x=>`<th>${__(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${[r.lot,r.stock_entry,r.material_value,r.conversion_value,r.recovery_value,r.current_accounted_value,r.previous_value_adjustments,r.target_value,r.residual_value_delta].map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table><p>${__('Values are in company currency. Quantity evidence, attribution and the approved policy are retained in this preparation. LCV submission remains manual.')}</p>`},
   {fieldname:'lot',fieldtype:'Select',label:__('Lot to Review for Draft LCV'),options:rows.map(r=>r.lot),reqd:1}
  ],primary_action_label:__('Review Draft LCV Eligibility'),primary_action:async v=>{
   const r=await call('draft_preview',{name:frm.doc.name,lot:v.lot});
   if(r.existing_draft){frappe.set_route('Form','Landed Cost Voucher',r.existing_draft.name);return;}
   if(!r.token){frappe.msgprint(r.blockers.map(esc).join('<br>'));return;}
   frappe.confirm(__('Create a Draft LCV for residual adjustment {0}?',[r.data.valuation.residual_value_delta]),async()=>{
    const made=await frappe.call({method:'calco_erp.calco_production.fg_true_up_draft.create_draft',args:{token:r.token},freeze:true});frappe.set_route('Form','Landed Cost Voucher',made.message.name);
   });
  }});d.show();
 }
 frappe.ui.form.on('Production Batch Closure',{refresh(frm){if(!frm.is_new()&&allowed())frm.add_custom_button(__('Prepare Settlement'),()=>prepare(frm),__('Management / Accounts'));}});
 frappe.ui.form.on('Production Settlement Preparation',{refresh(frm){if(!frm.is_new()&&allowed())frm.add_custom_button(__('Review Settlement'),()=>review(frm));}});
})();
