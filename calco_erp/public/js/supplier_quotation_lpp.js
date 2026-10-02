var SUPPLIER_LPP_FIELDS = [
	"custom_last_purchase_price",
	"custom_last_purchase_po",
	"custom_last_purchase_date",
	"custom_last_purchase_currency",
	"custom_last_purchase_uom",
	"custom_price_difference",
	"custom_price_difference_percent",
];

function supplierLppValuesEqual(currentValue, resolvedValue) {
	const currentBlank = currentValue === null || currentValue === undefined || currentValue === "";
	const resolvedBlank = resolvedValue === null || resolvedValue === undefined || resolvedValue === "";
	if (currentBlank || resolvedBlank) return currentBlank && resolvedBlank;
	return currentValue === resolvedValue || String(currentValue) === String(resolvedValue);
}

function setSupplierLppValueIfChanged(row, fieldname, value) {
	if (supplierLppValuesEqual(row[fieldname], value)) return false;
	frappe.model.set_value(row.doctype, row.name, fieldname, value);
	return true;
}

function calculateSupplierQuotationVariance(row) {
	const currentRate = flt(row.rate || 0);
	const lpp = flt(row.custom_last_purchase_price || 0);
	if (currentRate <= 0 || lpp <= 0) {
		setSupplierLppValueIfChanged(row, "custom_price_difference", null);
		setSupplierLppValueIfChanged(row, "custom_price_difference_percent", null);
		return;
	}

	const difference = currentRate - lpp;
	setSupplierLppValueIfChanged(row, "custom_price_difference", difference);
	setSupplierLppValueIfChanged(row, "custom_price_difference_percent", (difference / lpp) * 100);
}

function getSupplierQuotationItemPriceContext(frm, row) {
	return JSON.stringify([
		frm.doc.supplier || "",
		frm.doc.currency || "",
		row.item_code || "",
		row.uom || row.stock_uom || "",
	]);
}

function clearSupplierNeutralPriceListDefault(frm, row) {
	if (!row.__islocal) return false;
	const context = getSupplierQuotationItemPriceContext(frm, row);
	if (row.__calcoCurrentPriceCheckedContext === context) return false;
	row.__calcoCurrentPriceCheckedContext = context;

	const currentRate = flt(row.rate || 0);
	const priceListRate = flt(row.price_list_rate || 0);
	if (currentRate <= 0 || priceListRate <= 0) return false;
	if (Math.abs(currentRate - priceListRate) > 0.000001) return false;

	setSupplierLppValueIfChanged(row, "rate", null);
	return true;
}

function invalidateSupplierQuotationCurrentPrices(frm) {
	(frm.doc.items || []).forEach((row) => {
		row.__calcoCurrentPriceCheckedContext = null;
		setSupplierLppValueIfChanged(row, "rate", null);
		setSupplierLppValueIfChanged(row, "custom_price_difference", null);
		setSupplierLppValueIfChanged(row, "custom_price_difference_percent", null);
	});
}

function applySupplierQuotationLppResponse(frm, rows) {
	const byKey = {};
	(rows || []).forEach((row) => {
		byKey[row.row_key] = row;
		byKey[`idx:${row.idx}`] = row;
	});

	(frm.doc.items || []).forEach((row) => {
		const resolved = byKey[row.name] || byKey["idx:" + row.idx];
		if (!resolved) return;
		SUPPLIER_LPP_FIELDS.slice(0, 5).forEach((fieldname) => {
			setSupplierLppValueIfChanged(row, fieldname, resolved[fieldname.replace("custom_", "")]);
		});
		if (clearSupplierNeutralPriceListDefault(frm, row)) {
			setSupplierLppValueIfChanged(row, "custom_price_difference", null);
			setSupplierLppValueIfChanged(row, "custom_price_difference_percent", null);
		} else {
			setSupplierLppValueIfChanged(row, "custom_price_difference", resolved.price_difference);
			setSupplierLppValueIfChanged(
				row,
				"custom_price_difference_percent",
				resolved.price_difference_percent
			);
		}
	});

	const missing = (rows || []).filter((row) => row.item_code && !row.comparable).map((row) => row.item_code);
	if (missing.length && frm.doc.supplier && frm.doc.currency) {
		frm.dashboard.set_headline_alert(
			__(
				"Last Purchase Price not available for this supplier: {0}",
				[Array.from(new Set(missing)).join(", ")]
			),
			"orange"
		);
		frm.__calcoLppHeadlineVisible = true;
	} else if (frm.__calcoLppHeadlineVisible) {
		frm.dashboard.clear_headline();
		frm.__calcoLppHeadlineVisible = false;
	}
}

function getSupplierQuotationLppContext(frm) {
	const itemContext = (frm.doc.items || []).map((row) => [row.item_code || "", row.uom || row.stock_uom || ""]);
	return JSON.stringify([frm.doc.supplier || "", frm.doc.currency || "", itemContext]);
}

function refreshSupplierQuotationLpp(frm, expectedContext) {
	if (frm.doc.docstatus !== 0) return;
	const requestContext = expectedContext || getSupplierQuotationLppContext(frm);
	if (requestContext !== getSupplierQuotationLppContext(frm)) return;
	const requestVersion = (frm.__calcoLppRequestVersion || 0) + 1;
	frm.__calcoLppRequestVersion = requestVersion;
	frm.__calcoLppPendingContext = requestContext;
	if (!frm.doc.supplier || !frm.doc.currency) {
		const emptyRows = (frm.doc.items || []).map((row) => ({
			row_key: row.name,
			idx: row.idx,
			item_code: row.item_code,
			comparable: false,
			last_purchase_price: null,
			last_purchase_po: null,
			last_purchase_date: null,
			last_purchase_currency: null,
			last_purchase_uom: null,
			price_difference: null,
			price_difference_percent: null,
		}));
		applySupplierQuotationLppResponse(frm, emptyRows);
		frm.__calcoLppResolvedContext = requestContext;
		frm.__calcoLppPendingContext = null;
		return;
	}
	frappe.call({
		method: "calco_erp.calco_purchase.supplier_last_purchase_price.preview_supplier_quotation_lpp",
		args: { doc: frm.doc },
		freeze: false,
		callback: ({ message }) => {
			if (frm.doc.docstatus !== 0) return;
			if (requestVersion !== frm.__calcoLppRequestVersion) return;
			if (requestContext !== getSupplierQuotationLppContext(frm)) return;
			applySupplierQuotationLppResponse(frm, message?.rows || []);
			frm.__calcoLppResolvedContext = requestContext;
			frm.__calcoLppPendingContext = null;
		},
		error: () => {
			if (requestVersion === frm.__calcoLppRequestVersion) frm.__calcoLppPendingContext = null;
		},
	});
}

function queueSupplierQuotationLppRefresh(frm) {
	if (frm.doc.docstatus !== 0) return;
	const context = getSupplierQuotationLppContext(frm);
	if (context === frm.__calcoLppResolvedContext || context === frm.__calcoLppPendingContext) return;
	clearTimeout(frm.__calcoLppRefreshTimer);
	frm.__calcoLppRefreshTimer = setTimeout(() => refreshSupplierQuotationLpp(frm, context), 250);
}

frappe.ui.form.on("Supplier Quotation", {
	refresh(frm) {
		frm.fields_dict.items.grid.update_docfield_property("rate", "label", __("Current Price"));
		if (frm.__calcoCurrentPriceSupplier === undefined) {
			frm.__calcoCurrentPriceSupplier = frm.doc.supplier || "";
		}
		if (frm.__calcoCurrentPriceCurrency === undefined) {
			frm.__calcoCurrentPriceCurrency = frm.doc.currency || "";
		}
		if (frm.doc.docstatus === 0) queueSupplierQuotationLppRefresh(frm);
	},
	supplier(frm) {
		const supplier = frm.doc.supplier || "";
		if (
			frm.__calcoCurrentPriceSupplier !== undefined &&
			frm.__calcoCurrentPriceSupplier !== supplier
		) {
			invalidateSupplierQuotationCurrentPrices(frm);
		}
		frm.__calcoCurrentPriceSupplier = supplier;
		queueSupplierQuotationLppRefresh(frm);
	},
	currency(frm) {
		const currency = frm.doc.currency || "";
		if (
			frm.__calcoCurrentPriceCurrency !== undefined &&
			frm.__calcoCurrentPriceCurrency !== currency
		) {
			invalidateSupplierQuotationCurrentPrices(frm);
		}
		frm.__calcoCurrentPriceCurrency = currency;
		queueSupplierQuotationLppRefresh(frm);
	},
});

frappe.ui.form.on("Supplier Quotation Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		row.__calcoCurrentPriceCheckedContext = null;
		setSupplierLppValueIfChanged(row, "rate", null);
		queueSupplierQuotationLppRefresh(frm);
	},
	uom(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		row.__calcoCurrentPriceCheckedContext = null;
		setSupplierLppValueIfChanged(row, "rate", null);
		queueSupplierQuotationLppRefresh(frm);
	},
	items_add(frm) {
		queueSupplierQuotationLppRefresh(frm);
	},
	items_remove(frm) {
		queueSupplierQuotationLppRefresh(frm);
	},
	rate(frm, cdt, cdn) {
		calculateSupplierQuotationVariance(locals[cdt][cdn]);
	},
});
