frappe.ui.form.on("Manufacturing Quality Master Version", {
    refresh(frm) {
        if (frm.is_new() || !frm.doc.is_active || frm.doc.master_kind !== "Control Plan" ||
            !(frappe.session.user === "Administrator" || frappe.user.has_role("Quality Manager"))) return;
        frm.add_custom_button(__("Revise Not Applicable"), () => {
            frappe.call({method: "calco_erp.calco_quality.manufacturing_master_authority.not_applicable_candidates", args: {name: frm.doc.name}}).then(r => {
                const candidates = r.message || [];
                if (!candidates.length) { frappe.msgprint(__("No blank or Not Applicable requirements are eligible for this revision.")); return; }
                const dialog = new frappe.ui.Dialog({title: __("Controlled Not Applicable Revision"), fields: [
                    {fieldname: "parameters", fieldtype: "MultiCheck", label: __("Requirements"), options: candidates.map(p => ({label: p, value: p}))},
                    {fieldname: "reason", fieldtype: "Small Text", label: __("Quality Decision / Reason"), reqd: 1}
                ], primary_action_label: __("Create Approved Applicability Revision"), primary_action(values) {
                    if (!values.parameters || !values.parameters.length) { frappe.msgprint(__("Select at least one requirement.")); return; }
                    frappe.call({method: "calco_erp.calco_quality.manufacturing_master_authority.revise_not_applicable", args: {name: frm.doc.name, parameters: values.parameters, reason: values.reason}, freeze: true}).then(result => {
                        dialog.hide(); frappe.set_route("Form", "Manufacturing Quality Master Version", result.message.name);
                    });
                }}); dialog.show();
            });
        });
    }
});
