(function () {
	const IMPORT_FIELD = "custom_is_import_purchase_receipt";
	const SUMMARY_FIELD = "custom_import_landed_cost_summary_html";
	const GRID_CLASS = "calco-import-pr-grid";
	const GRID_COLUMNS = {
		item_code: 2,
		qty: 1,
		rejected_qty: 1,
		rate: 1,
		amount: 1,
		base_rate: 1,
		base_amount: 1,
		warehouse: 2,
	};
	let refreshTimer = null;
	let standardGridState = null;

	function shouldPreserveImportConversionRate(frm, controller) {
		const companyCurrency = controller.get_company_currency();
		return frm.doc.docstatus === 0 &&
			cint(frm.doc[IMPORT_FIELD]) === 1 &&
			frm.doc.currency &&
			frm.doc.currency !== companyCurrency &&
			frm.doc.price_list_currency === frm.doc.currency &&
			flt(frm.doc.conversion_rate) > 0;
	}

	function installImportConversionRateOverride(frm) {
		const controller = frm.cscript;
		if (!controller || controller.__calco_import_conversion_rate_override ||
			typeof controller.conversion_rate !== "function") return;

		const standardConversionRate = controller.conversion_rate;
		controller.conversion_rate = function () {
			if (!shouldPreserveImportConversionRate(this.frm, this)) {
				return standardConversionRate.apply(this, arguments);
			}

			const conversionRate = flt(this.frm.doc.conversion_rate);
			// ERPNext clears this before repricing; keep the same-currency price list
			// on the user's standard transaction rate instead of fetching it again.
			this.frm.doc.plc_conversion_rate = conversionRate;
			this.frm.refresh_field("plc_conversion_rate");

			if (!this.in_apply_price_list) {
				return this.apply_price_list(null, true);
			}
		};
		controller.__calco_import_conversion_rate_override = true;
	}

	function money(value) {
		return format_currency(flt(value || 0), "INR");
	}

	function renderSummary(frm, state) {
		const field = frm.fields_dict[SUMMARY_FIELD];
		if (!field) return;
		if (!state.is_import) {
			field.$wrapper.empty();
			return;
		}
		const estimate = state.estimate || {};
		const companyCurrency = state.company_currency || frappe.boot.sysdefaults.currency || "INR";
		const itemCosts = estimate.item_costs || [];
		const materialRate = itemCosts.length === 1
			? format_currency(flt(itemCosts[0].material_rate_company_currency || 0), companyCurrency) + "/" + (itemCosts[0].stock_uom || "")
			: __("See item rows");
		const perUnitCharges = flt(estimate.accepted_stock_qty) > 0
			? flt(estimate.eligible_charges) / flt(estimate.accepted_stock_qty)
			: 0;
		const cells = [
			[__("Supplier Currency"), frm.doc.currency || ""],
			[__("Supplier Transaction Value"), format_currency(flt(estimate.supplier_transaction_value || 0), frm.doc.currency)],
			[__("Exchange Rate to {0}", [companyCurrency]), flt(estimate.commercial_exchange_rate || 0, 6)],
			[__("Material Rate {0} / Stock UOM", [companyCurrency]), materialRate],
			[__("Base Material Value INR"), money(estimate.base_material_value)],
			[__("Accepted Qty in Stock UOM"), flt(estimate.accepted_stock_qty || 0, 3)],
			[__("Total Import Charges"), money(estimate.eligible_charges)],
			[__("Import Charges / Stock UOM"), money(perUnitCharges)],
			[__("Final RM Inward Value INR"), money(estimate.rm_inward_value)],
			[__("Final RM Inward Cost INR / Stock UOM"), money(estimate.rm_inward_cost_per_stock_uom)],
			[__("Allocation Method"), __(estimate.allocation_method || "")],
			[__("Import IGST - Excluded From RM Inward Cost"), money(estimate.import_igst)],
		];
		(estimate.charges || []).forEach((charge) => {
			cells.splice(6, 0, [__(charge.label), money(charge.amount)]);
		});
		if (estimate.calculation_error) {
			cells.push([__("Calculation Status"), estimate.calculation_error]);
		}
		const html = cells.map(function (cell) {
			return "<div><strong>" + frappe.utils.escape_html(cell[0]) + "</strong><br>" +
				frappe.utils.escape_html(String(cell[1])) + "</div>";
		}).join("");
		const itemRows = itemCosts.map((row) => {
			return "<tr>" +
				"<td>" + frappe.utils.escape_html(row.item_code || "") + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.supplier_rate || 0), frm.doc.currency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.material_rate_company_currency || 0), companyCurrency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.material_amount_company_currency || 0), companyCurrency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.allocated_import_charges || 0), companyCurrency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.rm_inward_value || 0), companyCurrency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(format_currency(flt(row.rm_inward_cost_per_stock_uom || 0), companyCurrency)) + "</td>" +
				"<td>" + frappe.utils.escape_html(row.stock_uom || "") + "</td>" +
				"</tr>";
		}).join("");
		const itemTable = itemRows
			? '<div class="table-responsive calco-import-rate-table"><table class="table table-bordered table-sm">' +
				"<thead><tr><th>" + __("Item") + "</th><th>" + __("Supplier Rate ({0})", [frm.doc.currency]) +
				"</th><th>" + __("Material Rate ({0})", [companyCurrency]) + "</th><th>" +
				__("Material Amount ({0})", [companyCurrency]) + "</th><th>" +
				__("Allocated Import Charges ({0})", [companyCurrency]) + "</th><th>" +
				__("RM Inward Value ({0})", [companyCurrency]) + "</th><th>" +
				__("RM Inward Cost / Stock UOM ({0})", [companyCurrency]) + "</th><th>" + __("Stock UOM") +
				"</th></tr></thead><tbody>" + itemRows + "</tbody></table></div>"
			: "";
		field.$wrapper.html('<div class="calco-import-cost-summary">' + html + "</div>" + itemTable);
	}

	function configureItemGrid(frm, isImport, companyCurrency) {
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (!grid) return;
		if (!standardGridState) {
			standardGridState = {};
			Object.keys(GRID_COLUMNS).forEach((fieldname) => {
				const df = grid.docfields.find((row) => row.fieldname === fieldname);
				if (df) standardGridState[fieldname] = {
					label: df.label,
					columns: df.columns,
					in_list_view: df.in_list_view,
				};
			});
		}
		Object.keys(GRID_COLUMNS).forEach((fieldname) => {
			const df = grid.docfields.find((row) => row.fieldname === fieldname);
			if (!df) return;
			const original = standardGridState[fieldname];
			if (isImport) {
				df.in_list_view = 1;
				df.columns = GRID_COLUMNS[fieldname];
			} else if (original) {
				df.in_list_view = original.in_list_view;
				df.columns = original.columns;
				df.label = original.label;
			}
		});
		if (isImport) {
			const currency = frm.doc.currency || __("Transaction Currency");
			companyCurrency = companyCurrency || frappe.boot.sysdefaults.currency || "INR";
			grid.docfields.find((row) => row.fieldname === "rate").label = __("Rate ({0})", [currency]);
			grid.docfields.find((row) => row.fieldname === "amount").label = __("Amount ({0})", [currency]);
			grid.docfields.find((row) => row.fieldname === "base_rate").label = __("Rate ({0})", [companyCurrency]);
			grid.docfields.find((row) => row.fieldname === "base_amount").label = __("Amount ({0})", [companyCurrency]);
		}
		grid.wrapper.toggleClass(GRID_CLASS, isImport);
		grid.setup_visible_columns();
		grid.refresh();
	}

	function addImportShipmentActions(frm, state) {
		if (!state.is_import) return;
		const shipments = state.import_shipments || [];
		shipments.forEach((name) => {
			frm.add_custom_button(
				shipments.length === 1 ? __("Open LC Import Shipment") : __("Open {0}", [name]),
				function () { frappe.set_route("Form", "LC Import Shipment", name); },
				__("Import")
			);
		});
	}

	async function refreshImportState(frm) {
		if (!frm.fields_dict[IMPORT_FIELD]) return;
		const response = await frappe.call({
			method: "calco_erp.calco_purchase.import_landed_cost.get_import_purchase_receipt_summary",
			args: frm.is_new() ? {doc: frm.doc} : {purchase_receipt: frm.doc.name},
		});
		const state = response.message || {};
		if (frm.doc.docstatus === 0 && cint(frm.doc[IMPORT_FIELD]) !== cint(state.is_import)) {
			await frm.set_value(IMPORT_FIELD, state.is_import ? 1 : 0);
		}
		renderSummary(frm, state);
		frm.set_df_property("custom_import_charges_section", "label", __("Import Charges & Operational RM Inward Cost"));
		frm.set_df_property("custom_import_landed_cost_summary_section", "label", __("Operational RM Inward Cost"));
		frm.set_df_property("custom_total_eligible_landed_charges", "label", __("Total Import Charges"));
		frm.set_df_property("custom_estimated_landed_value", "label", __("Final RM Inward Value"));
		frm.set_df_property("custom_estimated_landed_cost_per_stock_uom", "label", __("RM Inward Cost / Stock UOM"));
		configureItemGrid(frm, Boolean(state.is_import), state.company_currency);
		addImportShipmentActions(frm, state);
	}

	function scheduleRefresh(frm) {
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(function () { refreshImportState(frm); }, 250);
	}

	frappe.ui.form.on("Purchase Receipt", {
		setup: installImportConversionRateOverride,
		refresh: function (frm) {
			installImportConversionRateOverride(frm);
			return refreshImportState(frm);
		},
		conversion_rate: scheduleRefresh,
		base_net_total: scheduleRefresh,
		company: scheduleRefresh,
		currency: scheduleRefresh,
	});

	frappe.ui.form.on("Purchase Receipt Item", {
		purchase_order: scheduleRefresh,
		qty: scheduleRefresh,
		rejected_qty: scheduleRefresh,
	});
})();
