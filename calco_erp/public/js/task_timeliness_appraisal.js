// Native Appraisal UI remains; only the managed KRA has extra governance.
frappe.ui.form.on("Appraisal", {
    refresh(frm) {
        const admin = frappe.session.user === "Administrator" || ["HR Manager", "System Manager"].some(r => frappe.user_roles.includes(r));
        const rows = (frm.doc.appraisal_kra || []).filter(r => r.kra === "Task Timeliness");
        if (!rows.length) return;
        const total = (frm.doc.appraisal_kra || []).reduce((n, r) => n + flt(r.per_weightage), 0);
        const ev = (frm.doc.custom_task_timeliness_evidence || [])[0];
        frm.dashboard.set_headline_alert(__("KRA total: {0}%. Task Timeliness: {1}. Other weights are never rebalanced automatically.", [total, ev ? (ev.finalized_on ? __("Finalized evidence") : __("Calculated evidence")) : __("Not calculated")]), total === 100 ? "blue" : "orange");
        const grid = frm.fields_dict.appraisal_kra.grid;
        (grid.grid_rows || []).filter(r => r.doc.kra === "Task Timeliness").forEach(r => r.toggle_editable("per_weightage", admin && frm.doc.docstatus === 0));
        if (admin && frm.doc.docstatus === 0 && !frm.is_new()) {
            frm.add_custom_button(__("Refresh Task Timeliness"), () => {
                if (frm.is_dirty()) { frappe.msgprint(__("Save and review the KRA weights before refreshing.")); return; }
                frappe.call({method: "calco_erp.task_timeliness.appraisal.refresh", args: {appraisal: frm.doc.name}, freeze: true}).then(() => frm.reload_doc());
            });
        }
    }
});
