(() => {
 const api='calco_erp.calco_production.fg_handover';
 const call=(m,a)=>frappe.call({method:`${api}.${m}`,args:a,freeze:true}).then(r=>r.message);
 const esc=v=>frappe.utils.escape_html(String(v??''));
 const open=(dt,n)=>frappe.set_route('Form',dt,n);
 const production=()=>frappe.user_roles.some(r=>['Production Engineer','Production Head','Manufacturing Manager'].includes(r));
 async function receipt(frm,review=false){
  let state=await call('receipt_preview',{name:frm.doc.name});
  if(state.existing){open('Stock Entry',state.existing.stock_entry);return;}
  let d;
  const draw=()=>{
   d.fields_dict.summary.$wrapper.html(`<table class="table table-bordered">${[
    ['FG Delivery Note',frm.doc.name],['WO Planned Qty',state.planned_qty],['Cumulative FG Produced',state.cumulative_fg_qty],['FG Already Received',state.received_qty],['Packed FG to Receive',state.qty],['Proposed FG Batch',state.proposed_batch],['Destination','FG Quarantine']
   ].map(([l,v])=>`<tr><th>${__(l)}</th><td>${esc(v)}</td></tr>`).join('')}</table><p>${esc(state.status)} ${esc(state.review_reason||'')}</p>`);
   d.get_primary_btn().prop('disabled',!state.token||!state.can_confirm);
  };
  d=new frappe.ui.Dialog({title:__('Receive FG'),fields:[{fieldname:'summary',fieldtype:'HTML'}],primary_action_label:__('Receive FG'),primary_action:async()=>{
   if(!state.token)return;const r=await call('receive',{token:state.token});d.hide();await frm.reload_doc();
   frappe.msgprint(r.received?__('FG received into Quarantine. Lot-specific Quality Inspection is next.'):__('Receipt prepared. Open the linked Manufacture Stock Entry for authorized review.'));
  }});d.show();draw();
  if(review&&state.can_review){
   const res=await frappe.call({method:'calco_erp.calco_production.fg_confirmation.review_preview',args:{job_card:frm.doc.compounding_job_card,qty:frm.doc.prime_fg_qty}});
   const r=new frappe.ui.Dialog({title:__('Review Consumption Attribution'),size:'extra-large',fields:[
    {fieldname:'rows',fieldtype:'Table',cannot_add_rows:true,cannot_delete_rows:true,in_place_edit:true,fields:[
     {fieldname:'consumption_entry',fieldtype:'Data',label:'Consumption',read_only:1,in_list_view:1},{fieldname:'consumption_detail',fieldtype:'Data',hidden:1},
     {fieldname:'item_code',fieldtype:'Data',label:'RM',read_only:1,in_list_view:1},{fieldname:'batch_no',fieldtype:'Data',label:'Batch',read_only:1,in_list_view:1},
     {fieldname:'available_qty',fieldtype:'Float',label:'Available',read_only:1,in_list_view:1},{fieldname:'qty',fieldtype:'Float',label:'Attribute Qty',in_list_view:1}],data:res.message.rows.map(x=>({...x,qty:0}))},
    {fieldname:'reason',fieldtype:'Small Text',label:__('Attribution Review Reason'),reqd:1}
   ],primary_action_label:__('Use Reviewed Attribution'),primary_action:async v=>{state=await call('receipt_preview',{name:frm.doc.name,selection:v.rows.filter(x=>x.qty>0),reason:v.reason});r.hide();draw();}});r.show();
  }
 }
 async function acknowledge(frm,action){
  if(action==='QA Accepted')frappe.prompt({fieldname:'observation',fieldtype:'Small Text',label:__('QA Handover Observation'),reqd:1},async v=>{await call('acknowledge',{name:frm.doc.name,action,observation:v.observation});frm.reload_doc();},__('QA Accepted'),__('Accept Handover'));
  else{await call('acknowledge',{name:frm.doc.name,action});frm.reload_doc();}
 }
 async function source(frm){
  if(frm.doc.docstatus!==0||!frm.doc.compounding_job_card)return;
  const card=frm.doc.compounding_job_card;
  const values=await call('entry_context',{job_card:card});
  if(frm.doc.compounding_job_card===card)await frm.set_value(values);
 }
 function totals(frm){
  const packed=flt(frm.doc.bags)*flt(frm.doc.kg_per_bag);
  const other=['loose_qty','spy_qty','tpy_qty','samples_qty','metal_separator_qty','others_qty'].reduce((n,k)=>n+flt(frm.doc[k]),0);
  return frm.set_value({prime_fg_qty:packed,total_output_qty:packed+other});
 }
 frappe.ui.form.on('FG Delivery Note',{
  refresh:async frm=>{
   if(!frm.doc.compounding_job_card)return;
   if(frm.doc.docstatus===0&&!frm.doc.item_code)await source(frm);
   frm.set_intro(__('Shift Report is production evidence. Only packed FG is received as FG stock. QA handover acceptance does not replace lot Quality Inspection.'));
   if(frm.doc.docstatus!==1)return;
   if(frappe.user_roles.some(r=>['Production Head','Manufacturing Manager'].includes(r)))frm.add_custom_button(__('Authorize Handover Reversal'),()=>frappe.prompt({fieldname:'reason',fieldtype:'Small Text',label:__('Reversal Reason'),reqd:1},async v=>{await call('authorize_reversal',{name:frm.doc.name,reason:v.reason});frm.reload_doc();}),__('Advanced / Exceptions'));
   if(frm.doc.status==='Reversal Pending'){frm.set_intro(__('Reversal authorized. Use the existing downstream reversal workflows, then cancel/amend this note. No stock has been reversed automatically.'),'orange');return;}

   if(!frm.doc.qa_by&&frappe.user_roles.some(r=>['Quality Calco','Quality User','Quality Manager'].includes(r)))frm.add_custom_button(__('QA Accepted'),()=>acknowledge(frm,'QA Accepted'),__('Handover'));
   if(frm.doc.qa_by&&!frm.doc.partial_fg_lot&&frm.doc.prime_fg_qty>0&&production())frm.add_custom_button(__('Receive FG'),()=>receipt(frm),__('Handover'));
   if(frm.doc.qa_by&&!frm.doc.partial_fg_lot&&frappe.user_roles.includes('Production Head'))frm.add_custom_button(__('Review Consumption Attribution'),()=>receipt(frm,true),__('Advanced / Exceptions'));
   if(!frm.doc.stores_by&&frappe.user_roles.some(r=>['Stock User','Stock Manager'].includes(r)))frm.add_custom_button(__('Stores Received'),()=>acknowledge(frm,'Stores Received'),__('Handover'));
   if(frm.doc.partial_fg_lot){
    frm.add_custom_button(__('Partial FG Lot'),()=>open('Partial FG Lot',frm.doc.partial_fg_lot),__('Linked Documents'));
    if(frappe.model.can_create('Quality Inspection'))frm.add_custom_button(__('Lot Quality Inspection'),async()=>{const r=await frappe.call({method:'calco_erp.calco_production.partial_fg_lots.make_quality_inspection',args:{name:frm.doc.partial_fg_lot}});open('Quality Inspection',r.message.name);},__('Quality'));
   }
   const c=await call('context',{job_card:frm.doc.compounding_job_card});const row=c.notes.find(x=>x.name===frm.doc.name);
   for(const r of row?.releases||[])frm.add_custom_button((frm.doc.is_final?__('Final Production / Close Batch via Release'):__('Final QC Release'))+': '+r.name,()=>open('Final QC Release',r.name),__('Linked Documents'));
  },
  compounding_job_card:source,
  bags:totals,kg_per_bag:totals,loose_qty:totals,spy_qty:totals,tpy_qty:totals,
  samples_qty:totals,metal_separator_qty:totals,others_qty:totals
 });
})();
