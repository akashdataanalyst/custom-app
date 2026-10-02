frappe.ui.form.on("Maintenance Automation Settings", {
    refresh(frm) {
        frm.add_custom_button("Run PM Auto Ticket Check", () => {
            frappe.call({
                method: "calco_erp.calco_maintenance.automation.run_due_pm_ticket_generation_now",
                freeze: true,
                freeze_message: "Checking due preventive maintenance plans...",
                callback(r) {
                    const created = (r.message && r.message.created_tickets) || [];
                    frappe.msgprint(`PM automation completed. Created ${created.length} maintenance ticket(s).`);
                },
            });
        });

        frm.add_custom_button("Send Test Summary Email", () => {
            frappe.call({
                method: "calco_erp.calco_maintenance.automation.send_daily_summary_now",
                freeze: true,
                freeze_message: "Sending maintenance summary email...",
                callback(r) {
                    const status = r.message && r.message.status ? r.message.status : "unknown";
                    frappe.msgprint(`Daily maintenance summary email result: ${status}`);
                },
            });
        });

        frm.add_custom_button("Preview WhatsApp Message", () => {
            frappe.call({
                method: "calco_erp.calco_maintenance.automation.get_whatsapp_summary_preview",
                freeze: true,
                freeze_message: "Preparing WhatsApp summary preview...",
                callback(r) {
                    frappe.msgprint({
                        title: "WhatsApp Preview",
                        message: `<pre style="white-space: pre-wrap;">${frappe.utils.escape_html((r.message && r.message.message) || "")}</pre>`,
                    });
                },
            });
        });
    },
});
