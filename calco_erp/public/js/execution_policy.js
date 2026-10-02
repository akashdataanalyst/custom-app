frappe.ui.form.on('Job Card', {
  refresh(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.doc.work_order || frm.doc.custom_stopped_execution_closure) return;
    if ((frm.doc.time_logs || []).some(r => r.from_time && !r.to_time)) return;
    const action = frm.doc.is_paused ? 'Resume' : 'Start';
    frm.add_custom_button(__(`Check ${action}`), () => {
      if (frm.is_dirty()) { frappe.msgprint(__('Save/reload the Job Card before checking execution.')); return; }
      frappe.call({method:'calco_erp.calco_production.execution_policy.get_execution_preflight',
        args:{job_card:frm.doc.name,action},callback(result) {
          const data=result.message;
          if (!data?.allowed) return;
          const esc=frappe.utils.escape_html;
          const warnings=(data.warnings || []).map(w => `<li>${esc(Object.values(w).filter(v => v != null).join(' | '))}</li>`).join('');
          frappe.msgprint({title:__(`Check ${action}`),indicator:warnings?'orange':'green',
            message:`<p>${esc(data.message)}</p>${warnings?`<ul>${warnings}</ul>`:''}`});
        }});
    }, __('Advanced / Exceptions'));
  }
});
