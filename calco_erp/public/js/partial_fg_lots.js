(() => {
  const api="calco_erp.calco_production.partial_fg_lots";
  const esc=v=>frappe.utils.escape_html(String(v??""));
  const call=(method,args)=>frappe.call({method:`${api}.${method}`,args,freeze:true}).then(r=>r.message);
  const openDoc=(dt,name)=>frappe.set_route("Form",dt,name);
  const simple="calco_erp.calco_production.fg_confirmation";
  const request=(method,args)=>frappe.call({method:`${simple}.${method}`,args,freeze:true}).then(r=>r.message);
  async function showReceipt(frm,r) {
    if(r.received){frappe.msgprint({title:__("FG Received"),indicator:"green",message:`${esc(r.qty)} Kg · ${esc(r.batch)} · ${__("FG Quarantine")}<br>${__("Lot-specific Quality Inspection is next.")}`});await frm.reload_doc();return;}
    const d=new frappe.ui.Dialog({title:__("Review & Confirm"),fields:[{fieldtype:"HTML",options:`<p>${esc(r.qty)} Kg · ${esc(r.batch)} → ${__("FG Quarantine")}</p><p>${__("Receipt is prepared and awaits existing stock approval authority.")}</p>`}],primary_action_label:r.can_submit?__("Confirm Receipt"):__("Open Authorized Review"),primary_action:async()=>{
      if(!r.can_submit){openDoc("Stock Entry",r.stock_entry);d.hide();return;}
      const done=await request("submit_receipt",{lot_name:r.lot});d.hide();await showReceipt(frm,done);
    }});d.show();
  }
  async function receive(frm, reviewOnly=false) {
    if(frm.is_dirty()){frappe.msgprint(__("Save/reload the Job Card first."));return;}
    const handover=await frappe.call({method:"calco_erp.calco_production.fg_handover.context",args:{job_card:frm.doc.name}});
    if(handover.message.notes.some(n=>n.docstatus===1)){
      const notes=handover.message.notes.filter(n=>n.docstatus===1&&!n.partial_fg_lot);
      if(!notes.length){frappe.msgprint(__("All submitted handovers already have a receipt. Open FG Delivery Notes for the linked receipt."));return;}
      if(notes.length===1){openDoc("FG Delivery Note",notes[0].name);return;}
      frappe.prompt({fieldname:"name",fieldtype:"Select",label:__("FG Delivery Note"),options:notes.map(n=>n.name).join("\n"),reqd:1},v=>openDoc("FG Delivery Note",v.name),__("Receive FG"));return;
    }
    let state=await request("receipt_preview",{job_card:frm.doc.name});
    let d;
    const render=()=>{
      const fields=[["FG / Grade",`${state.item} / ${state.grade}`],["WO Planned Qty",state.planned_qty],["Cumulative FG Produced",state.cumulative_fg_qty],["FG Already Received",state.received_qty],["FG to Receive",state.qty],["Type",state.type],["Proposed FG Lot / Batch",state.proposed_batch||"—"],["Destination",state.destination]];
      d.fields_dict.evidence.$wrapper.html(`<table class="table table-bordered">${fields.map(([label,value])=>`<tr><th>${__(label)}</th><td>${esc(value)}</td></tr>`).join("")}</table>${state.status!=="Ready"?`<p class="text-warning"><strong>${__(state.status)}</strong><br>${esc(state.review_reason||"")}</p>`:""}`);
      d.get_primary_btn().prop("disabled",!state.token||state.can_confirm===false);
      if(state.can_confirm===false)d.fields_dict.evidence.$wrapper.append(`<p>${__("Receipt requires an authorized production reviewer with the existing Batch and Stock Entry permissions.")}</p>`);
      if(state.status==="Consumption requires review")d.fields_dict.evidence.$wrapper.append(`<p>${__("Use Advanced / Exceptions → Review Consumption Attribution.")}</p>`);
    };
    const review=async()=>{
      const proposal=await request("receipt_review_preview",{job_card:frm.doc.name});
      const r=new frappe.ui.Dialog({title:__("Review Consumption Attribution"),size:"extra-large",fields:[
        {fieldname:"rows",fieldtype:"Table",label:__("Unallocated Actual Consumption"),cannot_add_rows:true,cannot_delete_rows:true,in_place_edit:true,fields:[
          {fieldname:"consumption_entry",fieldtype:"Data",label:__("Consumption"),read_only:1,in_list_view:1},{fieldname:"consumption_detail",fieldtype:"Data",hidden:1},
          {fieldname:"item_code",fieldtype:"Data",label:__("RM"),read_only:1,in_list_view:1},{fieldname:"batch_no",fieldtype:"Data",label:__("Batch"),read_only:1,in_list_view:1},
          {fieldname:"available_qty",fieldtype:"Float",label:__("Available"),read_only:1,in_list_view:1},{fieldname:"qty",fieldtype:"Float",label:__("Attribute Qty"),in_list_view:1}],data:proposal.rows.map(x=>({...x,qty:0}))},
        {fieldname:"reason",fieldtype:"Small Text",label:__("Attribution Review Reason"),reqd:1}
      ],primary_action_label:__("Use Reviewed Attribution"),primary_action:async v=>{state=await request("reviewed_receipt_proposal",{job_card:frm.doc.name,selection:v.rows.filter(x=>x.qty>0),reason:v.reason});r.hide();render();}});r.show();
    };
    d=new frappe.ui.Dialog({title:__("Receive FG"),size:"large",fields:[
      {fieldname:"evidence",fieldtype:"HTML"},
      {fieldname:"note",fieldtype:"Small Text",label:__("Production Note")}
    ],primary_action_label:__("Receive FG"),primary_action:async v=>{if(!state.token)return;d.get_primary_btn().prop("disabled",true);try{const result=await request("receive_fg",{token:state.token,note:v.note||""});d.hide();await showReceipt(frm,result);}catch(e){d.get_primary_btn().prop("disabled",false);throw e;}}});d.show();render();if(reviewOnly&&state.can_review)await review();
  }
  async function consume(frm) {
    const state=await call("preview",{job_card:frm.doc.name});
    const res=await frappe.call({method:"calco_erp.calco_production.wip_consumption.get_wip_consumption_preview",args:{work_order:state.work_order}});
    const d=new frappe.ui.Dialog({title:__("Confirm Actual WIP Consumption"),size:"extra-large",fields:[
      {fieldtype:"HTML",options:`<p>${__("Current cumulative FG")}: <strong>${esc(state.cumulative_fg_qty)} Kg</strong></p><p>${__("Submitted consumption — recorded FG quantity basis")}: ${(state.recorded_consumption_bases||[]).map(r=>`<a href="/app/stock-entry/${encodeURIComponent(r.name)}">${esc(r.name)}</a>: ${esc(r.fg_completed_qty)} Kg`).join("; ")||__("None")}</p>`},
      {fieldname:"basis_qty",fieldtype:"Float",label:__("FG Quantity Covered (enter manually)"),reqd:1,description:__("Not calculated as remaining FG. Review existing submitted consumption before recording additional measured RM consumption.")},
      {fieldname:"rows",fieldtype:"Table",label:__("Measured Actual Consumption — enter quantities; no BOM inference"),cannot_add_rows:true,cannot_delete_rows:true,in_place_edit:true,
       fields:[{fieldname:"item_code",fieldtype:"Data",label:__("RM"),read_only:1,in_list_view:1},{fieldname:"batch_no",fieldtype:"Data",label:__("Batch"),read_only:1,in_list_view:1},{fieldname:"available_qty",fieldtype:"Float",label:__("Available WIP"),read_only:1,in_list_view:1},{fieldname:"qty",fieldtype:"Float",label:__("Actual Consumed"),in_list_view:1}],data:(res.message.rows||[]).map(r=>({...r,qty:0}))}
    ],primary_action_label:__("Open Standard Consumption"),primary_action:async values=>{
      const result=await call("make_live_consumption",{job_card:frm.doc.name,rows:values.rows.filter(r=>r.qty>0),basis_qty:values.basis_qty});
      frappe.model.sync(result);d.hide();openDoc("Stock Entry",result.name);
    }});d.show();
  }
  frappe.ui.form.on("Job Card",{refresh:async frm=>{
    if(frm.is_new()||frm.doc.operation!=="Compounding / Extrusion")return;
    const s=await call("summary",{job_card:frm.doc.name});if(!s?.enabled)return;
    const labels=[["planned_qty","WO Planned Qty"],["cumulative_fg_qty","Cumulative FG Qty"],["unreceived_qty","FG Produced but Not Yet Received"],["quarantine_qty","FG Quarantine Qty"],["released_qty","FG Released Qty"],["dispatched_qty","FG Dispatched Qty"],["balance_to_produce","Balance to Produce"]];
    const html=`<div class="calco-partial-fg-panel"><div style="display:flex;flex-wrap:wrap;gap:12px">${labels.map(([k,l])=>`<div style="padding:10px;border:1px solid var(--border-color);border-radius:6px"><small>${__(l)}</small><div><strong>${esc(s[k])} Kg</strong></div></div>`).join("")}</div><p>${__("Recovery / yield (not FG stock)")}: ${Object.entries(s.recovery).map(([k,v])=>`${esc(({spy_qty:"SPY",tpy_qty:"TPY",loose_quantity:"LB",lab_samples:"LS",metal_separator_qty:"Metal Separator"})[k])}: ${esc(v)}`).join(" · ")}</p><p>${s.lots.map(l=>`<a href="/app/partial-fg-lot/${encodeURIComponent(l.name)}">${esc(l.name)}</a> — ${esc(l.fg_qty)} Kg (${l.docstatus===2?__("Cancelled"):__("Confirmed")})`).join("<br>")}</p></div>`;
    const existing=$(frm.wrapper).find('.calco-partial-fg-panel');if(existing.length)existing.replaceWith(html);else frm.dashboard.add_section(html,__("Partial FG Production"));
    const handovers=await frappe.call({method:"calco_erp.calco_production.fg_handover.context",args:{job_card:frm.doc.name}});
    const h=handovers.message;
    $(frm.wrapper).find('.calco-fg-handover-panel').remove();
    frm.dashboard.add_section(`<div class="calco-fg-handover-panel"><strong>${__("Packed FG Delivered")}: ${esc(h.delivered_fg)} Kg · ${__("Handover FG Received")}: ${esc(h.received_fg)} Kg</strong><p>${h.notes.map(n=>`<a href="/app/fg-delivery-note/${encodeURIComponent(n.name)}">${esc(n.name)}</a> — ${esc(n.prime_fg_qty)} Kg · ${esc(n.status)}${(n.quality_inspections||[]).map(q=>` · <a href="/app/quality-inspection/${encodeURIComponent(q.name)}">${esc(q.name)}: ${esc(q.status)}</a>`).join('')}${(n.releases||[]).map(r=>` · <a href="/app/final-qc-release/${encodeURIComponent(r.name)}">${esc(r.name)}: ${esc(r.status)}</a>`).join('')}`).join('<br>')}</p></div>`,__("FG Handover"));
    const production=frappe.user_roles.some(r=>["Production Engineer","Production Head","Manufacturing Manager"].includes(r));
    if(production&&h.can_create)frm.add_custom_button(__("New FG Delivery Note"),()=>frappe.new_doc("FG Delivery Note",{compounding_job_card:frm.doc.name}),__("Production"));
    const endEvents=JSON.parse(frm.doc.custom_physical_completion_audit||"[]").filter(e=>["Physical End","Reopen"].includes(e.action));
    const physicallyEnded=endEvents.length&&endEvents[endEvents.length-1].action==="Physical End";
    if(production&&frm.doc.docstatus===0&&((frm.doc.status==="Work In Progress"&&!frm.doc.is_paused)||physicallyEnded)){
      frm.add_custom_button(__("Confirm Actual WIP Consumption"),()=>consume(frm),__("Advanced / Exceptions"));
      frm.add_custom_button(__(s.unreceived_qty>0?"Receive FG":"No unreceived FG"),()=>receive(frm),__("Production"));
    } else if(production&&frm.doc.docstatus===1&&frm.doc.status==="Completed")frm.add_custom_button(__(s.unreceived_qty>0?"Receive FG":"No unreceived FG"),()=>receive(frm),__("Production"));
    if(production&&frappe.user_roles.includes("Production Head"))frm.add_custom_button(__("Review Consumption Attribution"),()=>receive(frm,true),__("Advanced / Exceptions"));
    // Finalized cards retain history; do not rerun actionable WIP/finalization gates.
    if(frm.doc.docstatus===1&&frm.doc.status==="Completed")return;
    const progress=await request("status",{job_card:frm.doc.name});
    const old=$(frm.wrapper).find('.calco-production-reconciliation');old.remove();
    frm.dashboard.add_section(`<div class="calco-production-reconciliation"><strong>${esc(progress.message)}</strong>${progress.exceptions.length?`<ul>${progress.exceptions.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:""}</div>`,__("Production Reconciliation"));
    for(const receipt of progress.receipts){
      if(!receipt.received&&production)frm.add_custom_button(__("Review & Confirm")+": "+receipt.batch,()=>showReceipt(frm,receipt),__("Production"));
      else if(receipt.can_quality)frm.add_custom_button(__("Lot Quality Inspection")+": "+receipt.batch,async()=>{const r=await call("make_quality_inspection",{name:receipt.lot});openDoc("Quality Inspection",r.name);},__("Quality"));
    }
    frm.add_custom_button(__("Reconciliation Details"),async()=>{const r=await frappe.call({method:"calco_erp.calco_production.physical_completion.finalization_preview",args:{job_card:frm.doc.name}});frappe.msgprint(r.message.ready?__("Production reconciliation complete"):r.message.blockers.map(esc).join("<br>"));},__("Advanced / Exceptions"));
  }});
})();
