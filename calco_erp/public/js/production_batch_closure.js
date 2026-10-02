(() => {
  const presentation='calco_erp.calco_production.production_closure_presentation';
  async function refresh(frm) {
    if(frm.is_new() || frm.doc.docstatus===2 || !frappe.user_roles.some(r=>['Production Engineer','Production Head','Manufacturing Manager'].includes(r))) return;
    const job=frm.doctype==='Job Card';
    if(!job && frm.doc.docstatus!==1) return;
    if(job && (!frm.doc.custom_physical_completion_audit || frm.doc.operation!=='Compounding / Extrusion')) return;
    const name=frm.doc.name;
    const response=await frappe.call({method:presentation+'.'+(job?'preview_for_job_card':'preview'),args:job?{job_card:name}:{final_release:name}});
    const ctx=response.message;
    if(frm.doc.name!==name || !ctx?.available) return;
    frm.add_custom_button(__('Final RM Consumption'),async()=>{
      if(frm.is_dirty()){frappe.msgprint(__('Save or reload this document first.'));return;}
      const result=await frappe.call({method:'calco_erp.calco_production.final_consumption.open_entry',args:{final_release:ctx.final_release},freeze:true});
      frappe.set_route('Form','Production Consumption Entry',result.message);
    },job?__('Production'):undefined);
  }
  frappe.ui.form.on('Job Card',{refresh});
  frappe.ui.form.on('Final QC Release',{refresh});
})();
