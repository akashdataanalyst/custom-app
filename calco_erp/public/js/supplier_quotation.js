function set_supplier_quotation_query(frm) {
	const itemCodes = (frm.doc.items || [])
		.map((row) => row.item_code)
		.filter(Boolean);
	const rfqNames = (frm.doc.items || [])
		.map((row) => row.request_for_quotation)
		.filter(Boolean);

	frm.set_query("supplier", () => ({
		query: "calco_erp.calco_purchase.supplier_approval_matrix.supplier_quotation_supplier_query",
		filters: {
			docname: frm.doc.name || "",
			item_codes: JSON.stringify(itemCodes),
			request_for_quotations: JSON.stringify(rfqNames),
		},
	}));
}

function apply_supplier_quotation_supplier_filter(frm) {
	set_supplier_quotation_query(frm);

	frappe.call({
		method: "calco_erp.calco_purchase.supplier_approval_matrix.get_supplier_quotation_supplier_options",
		args: {
			doc: frm.doc,
		},
		callback: ({ message }) => {
			const info = message || {};
			const allowedSuppliers = info.allowed_suppliers || [];
			const allowedRows = info.rows || [];

			frm._supplierQuotationSupplierRows = allowedRows;
			frm._supplierQuotationAllowedSuppliers = allowedSuppliers;
			console.log("Supplier Quotation filter loaded", frm.doc.name || "(new)", allowedSuppliers);
			frappe.show_alert({
				message: __("Supplier Quotation filter loaded"),
				indicator: "green",
			});

			if (allowedSuppliers.length === 1 && !frm.doc.supplier) {
				frm.set_value("supplier", allowedSuppliers[0]);
			}
			if (frm.doc.supplier && allowedSuppliers.length && !allowedSuppliers.includes(frm.doc.supplier)) {
				frm.set_value("supplier", "");
			}
			frm.refresh_field("supplier");

			const source = info.source;
			if (source === "request_for_quotation" && info.request_for_quotations?.length) {
				frm.dashboard.set_headline_alert(
					`Supplier restricted to linked RFQ supplier list: ${info.request_for_quotations.join(", ")}`,
					"blue"
				);
			} else if (source === "supplier_approval_matrix" && info.item_codes?.length) {
				frm.dashboard.set_headline_alert(
					`Supplier restricted to approved supplier matrix for item(s): ${info.item_codes.join(", ")}`,
					"blue"
				);
			}
		},
	});
}

frappe.ui.form.on("Supplier Quotation", {
	setup(frm) {
		set_supplier_quotation_query(frm);
		if (!frm.__supplierQueryRealtimeBound) {
			frm.__supplierQueryRealtimeBound = true;
			frappe.realtime.on("supplier_filter_debug", (payload) => {
				console.log("Custom Supplier Query Executed", payload);
				frappe.show_alert({
					message: __("Custom Supplier Query Executed"),
					indicator: "green",
				});
			});
		}
	},
	refresh(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
	onload(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
	supplier(frm) {
		const allowed = frm._supplierQuotationAllowedSuppliers || [];
		if (frm.doc.supplier && allowed.length && !allowed.includes(frm.doc.supplier)) {
			frappe.msgprint(__("Selected supplier is not allowed for this Supplier Quotation."));
			frm.set_value("supplier", null);
		}
	},
});

frappe.ui.form.on("Supplier Quotation Item", {
	item_code(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
	request_for_quotation(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
	items_add(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
	items_remove(frm) {
		apply_supplier_quotation_supplier_filter(frm);
	},
});
