frappe.ui.form.on('Job Card', {
  refresh(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.doc.work_order ||
        frm.doc.operation !== 'Compounding / Extrusion' ||
        !['Production Head','Manufacturing Manager'].some(r => frappe.user.has_role(r))) return;
    frm.add_custom_button(__('Set / Correct FG Batch'), () => {
      if (frm.is_dirty()) { frappe.msgprint(__('Save or reload the Job Card first.')); return; }
      const d = new frappe.ui.Dialog({title:__('Set / Correct FG Batch'),fields:[
        {fieldname:'current',label:__('Current FG Batch'),fieldtype:'Data',read_only:1,default:frm.doc.custom_fg_batch_no||''},
        {fieldname:'batch_no',label:__('Physical FG Batch'),fieldtype:'Data',reqd:1,description:__('An existing matching Batch is reused. A new identity is created only after genealogy checks.')},
        {fieldname:'reason',label:__('Reason'),fieldtype:'Small Text',reqd:1}
      ],primary_action_label:__('Set / Correct FG Batch'),async primary_action(values) {
        d.disable_primary_action();
        try {
          const r=await frappe.call({method:'calco_erp.calco_production.parallel_production.correct_batch',args:{job_card:frm.doc.name,batch_no:values.batch_no,reason:values.reason,expected_batch:frm.doc.custom_fg_batch_no||''},freeze:true});
          d.hide(); await frm.reload_doc();
          if(r.message.audit) frappe.msgprint({title:__('FG Batch Updated'),message:__('Batch correction audit: {0}',[frappe.utils.escape_html(r.message.audit)])});
        } finally {d.enable_primary_action();}
      }}); d.show();
    },__('Production'));
  }
});
