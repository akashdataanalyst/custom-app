frappe.ui.form.on("Purchase Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;

        frm.add_custom_button(
            __("Resend to Supplier"),
            () => frappe.call({
                method: "calco_erp.calco_purchase.purchase_order_supplier_dispatch.resend_purchase_order_to_supplier",
                args: { purchase_order: frm.doc.name },
                freeze: true,
                freeze_message: __("Queueing Purchase Order email..."),
                callback: () => {
                    frappe.show_alert({
                        message: __("Purchase Order email queued."),
                        indicator: "green",
                    });
                    frm.reload_doc();
                },
            }),
            __("Supplier Communication")
        );
    },
});
