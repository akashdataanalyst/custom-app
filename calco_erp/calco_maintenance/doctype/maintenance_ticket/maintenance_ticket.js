frappe.ui.form.on("Maintenance Ticket", {
    refresh(frm) {
        frm.set_query("machine_name", () => ({
            filters: { active: 1 },
        }));

        frm.set_query("requested_spare_item", () => ({
            query: "calco_erp.calco_maintenance.doctype.maintenance_ticket.maintenance_ticket.get_machine_spare_query",
            filters: { machine: frm.doc.machine_name },
        }));

        frm.set_query("pm_plan", () => {
            const filters = { is_active: 1 };
            if (frm.doc.machine_name) {
                filters.equipment = frm.doc.machine_name;
            }
            return { filters };
        });

        if (frm.doc.machine_name) {
            frm.add_custom_button("Suggested Spares", () => {
                frappe.call({
                    method: "calco_erp.calco_maintenance.doctype.maintenance_ticket.maintenance_ticket.get_suggested_spares",
                    args: { equipment: frm.doc.machine_name },
                    freeze: true,
                    freeze_message: "Loading spare suggestions...",
                    callback(r) {
                        const rows = r.message || [];
                        if (!rows.length) {
                            frappe.msgprint("No spare suggestions were found for the selected equipment.");
                            return;
                        }

                        const itemsHtml = rows
                            .map(
                                (row) =>
                                    `<li><strong>${frappe.utils.escape_html(row.name)}</strong> - ${frappe.utils.escape_html(
                                        row.item_name || row.name
                                    )} (${frappe.utils.escape_html(row.stock_uom || "Nos")}) | Std Qty: ${frappe.utils.escape_html(
                                        String(row.standard_qty || 1)
                                    )} | Critical: ${row.critical ? "Yes" : "No"}</li>`
                            )
                            .join("");

                        frappe.msgprint({
                            title: "Suggested Spares",
                            message: `<ul>${itemsHtml}</ul>`,
                            wide: true,
                        });
                    },
                });
            }, "Lookup");
        }
    },

    machine_name(frm) {
        if (!frm.doc.machine_name) {
            frm.set_value("requested_spare_item", null);
            frm.set_value("requested_spare_qty", 1);
            return;
        }

        frappe.db
            .get_value("Maintenance Equipment", frm.doc.machine_name, ["machine_name", "location"])
            .then(({ message }) => {
                if (!message) {
                    return;
                }

                frm.set_value("equipment", frm.doc.machine_name);

                if (!frm.doc.location && message.location) {
                    frm.set_value("location", message.location);
                }
            });

        frm.set_value("requested_spare_item", null);
        frm.set_value("requested_spare_qty", 1);
    },

    pm_plan(frm) {
        if (!frm.doc.pm_plan) {
            return;
        }

        frappe.db
            .get_value("Preventive Maintenance Plan", frm.doc.pm_plan, ["equipment"])
            .then(({ message }) => {
                if (message && message.equipment) {
                    frm.set_value("machine_name", message.equipment);
                    frm.set_value("equipment", message.equipment);
                }
            });
    },

    requested_spare_item(frm) {
        if (!frm.doc.requested_spare_item) {
            return;
        }

        frappe.call({
            method: "calco_erp.calco_maintenance.doctype.maintenance_ticket.maintenance_ticket.get_suggested_spares",
            args: { equipment: frm.doc.machine_name },
            callback(r) {
                const rows = r.message || [];
                const match = rows.find((row) => row.name === frm.doc.requested_spare_item);
                if (match && !frm.doc.requested_spare_qty) {
                    frm.set_value("requested_spare_qty", match.standard_qty || 1);
                }
            },
        });
    },
});
