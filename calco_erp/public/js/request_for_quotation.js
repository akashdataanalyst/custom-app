frappe.ui.form.on("Request for Quotation", {
	refresh(frm) {
		const warning = frm.doc.custom_supplier_matrix_warning;
		if (warning) {
			frm.dashboard.set_headline_alert(warning, "orange");
		}
	},
});
