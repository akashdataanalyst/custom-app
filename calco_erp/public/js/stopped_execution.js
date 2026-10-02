frappe.ui.form.on("Job Card", {
  refresh(frm) {
    const roles = frappe.user_roles || [];
    const authorized = roles.some(role => ["Production Head", "Manufacturing Manager"].includes(role));
    const active = (!frm.doc.is_paused && ["Work In Progress", "In Process", "Running"].includes(frm.doc.status)) ||
      (frm.doc.time_logs || []).some(row => row.from_time && !row.to_time);
    if (!authorized || frm.is_new() || frm.doc.docstatus !== 0 || !active ||
        frm.doc.custom_stopped_execution_closure || !frm.doc.work_order) return;
    const name = frm.doc.name;
    frappe.db.get_value("Work Order", frm.doc.work_order, ["docstatus", "status"]).then(result => {
      if (frm.doc.name !== name || result.message?.docstatus !== 1 || result.message?.status !== "Stopped") return;
      frm.add_custom_button(__("Close Stopped Execution"), () => {
        if (frm.is_dirty()) {
          frappe.msgprint(__("Reload the saved Job Card before closing stopped execution. Unsaved changes cannot be included."));
          return;
        }
        const dialog = new frappe.ui.Dialog({
          title: __("Close Stopped Execution"),
          fields: [
            {fieldtype: "HTML", options: `<p>${__("Closes stale timers and releases execution. The Job Card remains On Hold. Production quantity and WIP remain unreconciled; no stock or QC transaction is created.")}</p>`},
            {fieldname: "reason", label: __("Abandonment / Closure Reason"), fieldtype: "Small Text", reqd: 1}
          ],
          primary_action_label: __("Close Stopped Execution"),
          primary_action(values) {
            frappe.call({method: "calco_erp.calco_production.stopped_execution.close_stopped_execution",
              args: {job_card: name, reason: values.reason}, freeze: true,
              callback() { dialog.hide(); frm.reload_doc(); }
            });
          }
        });
        dialog.show();
      }, __("Production"));
    });
  }
});
