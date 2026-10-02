frappe.ui.form.on("Preventive Maintenance Plan", {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button("Create Maintenance Ticket", () => {
                frappe.call({
                    method: "calco_erp.calco_maintenance.doctype.preventive_maintenance_plan.preventive_maintenance_plan.create_maintenance_ticket",
                    args: { plan_name: frm.doc.name },
                    freeze: true,
                    freeze_message: "Creating maintenance ticket...",
                    callback(r) {
                        if (r.message && r.message.name) {
                            frappe.set_route("Form", "Maintenance Ticket", r.message.name);
                        }
                    },
                });
            });
        }
    },
});
