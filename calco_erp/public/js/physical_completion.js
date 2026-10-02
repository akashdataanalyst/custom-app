(() => {
  const api = "calco_erp.calco_production.physical_completion";
  const active = frm => frm.doc.operation === "Compounding / Extrusion" && !!frm.doc.custom_shift_output_activation;
  const history = frm => JSON.parse(frm.doc.custom_physical_completion_audit || "[]");
  const ended = frm => { for (const e of history(frm).slice().reverse()) {if (e.action === "Reopen") return false;if (e.action === "Physical End") return true;}return false; };
  const call = (method,args) => frappe.call({method:`${api}.${method}`,args,freeze:true}).then(r=>r.message);
  const esc = v => frappe.utils.escape_html(String(v ?? ""));
  async function physicalEnd(frm) {
    if(frm.is_dirty()) {frappe.msgprint(__("Save/reload the Job Card first."));return;}
    const s=await call("preview",{job_card:frm.doc.name});
    const fields=[["WO Planned Qty",s.planned_qty],["Controlled Cumulative FG Qty",s.cumulative_fg_qty],["FG Already Received",s.received_qty],["FG Produced but Not Yet Received",s.unreceived_qty],["Short-production Qty",Math.max(s.planned_qty-s.cumulative_fg_qty,0)]];
    const d=new frappe.ui.Dialog({title:__("Confirm Physical End"),size:"large",fields:[
      {fieldtype:"HTML",options:`<table class="table table-bordered">${fields.map(([k,v])=>`<tr><th>${__(k)}</th><td>${esc(v)} Kg</td></tr>`).join("")}</table><p>${__("Unresolved material / QC exceptions")}: ${[...(s.remaining_wip?[`${s.remaining_wip} Kg WIP requires disposition before finalization`]:[]),...(Object.values(s.recovery).some(v=>v)?[__("Recovery disposition requires review")]:[]),...((s.qc.blockers||[]).length?[__("Unresolved Quality checkpoints")]:[]),...(s.reporting.length?[__("Production reporting / sign-off incomplete")]:[])].map(esc).join("<br>")||__("None")}</p><p>${__("This stops physical execution. QC, material reconciliation and final Job Card submission remain separate.")}</p>`},
      {fieldname:"confirmed",fieldtype:"Check",label:__("I confirm physical Compounding has ended"),reqd:1}
    ],primary_action_label:__("Confirm Physical End"),primary_action:async v=>{if(!v.confirmed)return;await call("confirm_physical_end",{job_card:frm.doc.name,confirmed:1});d.hide();await frm.reload_doc();}});d.show();
  }
  function decorate(frm) {
    if(!active(frm))return;
    const end=ended(frm);
    const wrapper=$(frm.wrapper);
    wrapper.find('.jcd-btn-complete').each(function(){if($(this).text().trim()!==__("Confirm Physical End"))$(this).text(__("Confirm Physical End"));}).toggle(!end);
    wrapper.find('.jcd-btn-resume,.jcd-btn-start').toggle(!end);
  }
  frappe.ui.form.on("Job Card",{
    setup(frm) {
      const trigger=frm.script_manager.trigger.bind(frm.script_manager);
      frm.script_manager.trigger=(event,...args)=>active(frm)&&event==="complete_job_card"?physicalEnd(frm):trigger(event,...args);
    },
    refresh(frm) {
      if(!active(frm)||frm.is_new())return;
      if(!frm.__physicalObserver){frm.__physicalObserver=new MutationObserver(()=>decorate(frm));frm.__physicalObserver.observe(frm.wrapper,{childList:true,subtree:true});}
      decorate(frm);
      if(frm.doc.docstatus!==0)return;
      const production=frappe.user_roles.some(r=>["Production Engineer","Production Head","Manufacturing Manager"].includes(r));
      const manager=frappe.user_roles.some(r=>["Production Head","Manufacturing Manager"].includes(r));
      if(!ended(frm)) {if(production&&(frm.doc.time_logs||[]).some(r=>r.from_time))frm.add_custom_button(__("Confirm Physical End"),()=>physicalEnd(frm),__("Production"));return;}
      frm.dashboard.set_headline_alert(__("Physically Ended — Finalization Pending"),"orange");
      frm.add_custom_button(__("Finalization Details"),async()=>{const s=await call("finalization_preview",{job_card:frm.doc.name});frappe.msgprint({title:__("Finalization Details"),message:`<p>${__("Controlled FG")}: ${esc(s.cumulative_fg_qty)} Kg · ${__("Received FG")}: ${esc(s.received_qty)} Kg · ${__("Remaining FG receipt")}: ${esc(s.unreceived_qty)} Kg · ${__("Remaining WIP")}: ${esc(s.remaining_wip)} Kg · ${__("Approved measured loss")}: ${esc(s.process_loss_qty)} Kg</p>${s.ready?__("QC and reconciliation passed. Ready for final Job Card submission."):s.blockers.map(esc).join("<br>")}`});},__("Advanced / Exceptions"));
      if(manager){
        frm.add_custom_button(__("Approve Measured Process Loss"),()=>frappe.prompt([{fieldname:"qty",fieldtype:"Float",label:__("Measured Process Loss Qty"),reqd:1},{fieldname:"reason",fieldtype:"Small Text",label:__("Measured Loss Evidence / Explanation"),reqd:1}],async v=>{await call("approve_process_loss",{job_card:frm.doc.name,measured_loss_qty:v.qty,reason:v.reason});await frm.reload_doc();},__("Approve Measured Process Loss")),__("Advanced / Exceptions"));
        frm.add_custom_button(__("Reopen Physical Execution"),()=>frappe.prompt({fieldname:"reason",fieldtype:"Small Text",label:__("Reason"),reqd:1},async v=>{await call("reopen",{job_card:frm.doc.name,reason:v.reason});await frm.reload_doc();},__("Reopen Physical Execution")),__("Advanced / Exceptions"));
        frm.add_custom_button(__("Finalize Job Card"),async()=>{if(frm.is_dirty()){frappe.msgprint(__("Save/reload first."));return;}const s=await call("finalization_preview",{job_card:frm.doc.name});if(!s.ready){frappe.msgprint(s.blockers.map(esc).join("<br>"));return;}frappe.confirm(__("Submit final Job Card with controlled FG quantity {0} Kg?",[s.cumulative_fg_qty]),async()=>{await call("finalize",{job_card:frm.doc.name});await frm.reload_doc();});},__("Advanced / Exceptions"));
      }
    }
  });
})();
