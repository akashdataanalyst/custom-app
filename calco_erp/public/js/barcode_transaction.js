frappe.provide("calco_erp.barcode");

(function () {
	const SCAN_FIELD = "custom_scan_barcode";
	const QC_STATUS_FIELD = "custom_qc_status";
	const QC_ACCEPTED_QTY_FIELD = "custom_accepted_qty";
	const QC_REJECTED_QTY_FIELD = "custom_rejected_qty";
	const QC_INSPECTION_FIELD = "custom_quality_inspection";
	const QC_DEVIATION_FIELD = "custom_rm_deviation_approval";
	const REJECTED_STATUS = "Rejected";
	const PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD = "custom_supplier_purchase_invoice_attachment";
	const PR_SUPPLIER_TEST_CERTIFICATE_FIELD = "custom_supplier_test_certificate_attachment";
	const PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD = "custom_raw_material_storage_photo";
	const PR_RM_EXPIRY_DATE_FIELD = "custom_rm_expiry_date";
	const PRODUCTION_RM_WAREHOUSE = "Stores - CPPL";
	const PRODUCTION_WIP_WAREHOUSE = "Work In Progress - CPPL";
	const PR_RM_DOCUMENT_FIELDS = [
		PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD,
		PR_SUPPLIER_TEST_CERTIFICATE_FIELD,
		PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD,
		PR_RM_EXPIRY_DATE_FIELD,
	];

	const CONFIG = {
		"Purchase Receipt": {
			table: "items",
			default_qty: 1,
			qty_fields: ["qty", "received_qty"],
			warehouse_fields: ["warehouse"],
			parent_defaults: ["set_warehouse"],
		},
		"Stock Entry": {
			table: "items",
			default_qty: 1,
			qty_fields: ["qty", "transfer_qty"],
			warehouse_fields: ["s_warehouse", "t_warehouse"],
			parent_defaults: ["from_warehouse", "to_warehouse"],
		},
		"Delivery Note": {
			table: "items",
			default_qty: 1,
			qty_fields: ["qty"],
			warehouse_fields: ["warehouse"],
			parent_defaults: ["set_warehouse"],
		},
	};

	function get_config(frm) {
		return CONFIG[frm.doctype];
	}

	function set_work_order_batch_query(frm) {
		if (
			frm.doctype !== "Stock Entry" ||
			!frm.doc.work_order ||
			frm.doc.purpose !== "Material Transfer for Manufacture"
		) {
			return;
		}

		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			if (!row.item_code) {
				frappe.throw(__("Please enter Item Code to get Batch Number"));
			}

			return {
				query: "calco_erp.calco_production.stock_entry_batch_query.get_production_rm_batches",
				filters: {
					item_code: row.item_code,
					warehouse: row.s_warehouse || doc.from_warehouse || PRODUCTION_RM_WAREHOUSE,
					work_order: doc.work_order,
					posting_date: doc.posting_date || frappe.datetime.nowdate(),
				},
			};
		});
	}

	function is_work_order_material_transfer(frm) {
		return (
			frm.doctype === "Stock Entry" &&
			Boolean(frm.doc.work_order) &&
			frm.doc.purpose === "Material Transfer for Manufacture"
		);
	}

	function set_work_order_warehouse_read_only(frm, read_only) {
		if (!frm.fields_dict.items || !frm.fields_dict.items.grid) {
			return;
		}

		frm.set_df_property("from_warehouse", "read_only", read_only ? 1 : 0);
		frm.set_df_property("to_warehouse", "read_only", read_only ? 1 : 0);
		frm.fields_dict.items.grid.update_docfield_property(
			"s_warehouse",
			"read_only",
			read_only ? 1 : 0
		);
		frm.fields_dict.items.grid.update_docfield_property(
			"t_warehouse",
			"read_only",
			read_only ? 1 : 0
		);
		frm.__calco_work_order_warehouses_locked = read_only;
	}

	async function apply_work_order_warehouse_defaults(frm) {
		if (!is_work_order_material_transfer(frm)) {
			if (frm.__calco_work_order_warehouses_locked) {
				set_work_order_warehouse_read_only(frm, false);
			}
			return;
		}

		set_work_order_warehouse_read_only(frm, true);
		if (frm.doc.from_warehouse !== PRODUCTION_RM_WAREHOUSE) {
			await frm.set_value("from_warehouse", PRODUCTION_RM_WAREHOUSE);
		}
		if (frm.doc.to_warehouse !== PRODUCTION_WIP_WAREHOUSE) {
			await frm.set_value("to_warehouse", PRODUCTION_WIP_WAREHOUSE);
		}

		for (const row of frm.doc.items || []) {
			if (row.s_warehouse !== PRODUCTION_RM_WAREHOUSE) {
				await frappe.model.set_value(row.doctype, row.name, "s_warehouse", PRODUCTION_RM_WAREHOUSE);
			}
			if (row.t_warehouse !== PRODUCTION_WIP_WAREHOUSE) {
				await frappe.model.set_value(row.doctype, row.name, "t_warehouse", PRODUCTION_WIP_WAREHOUSE);
			}
		}
		frm.refresh_field("items");
	}

	function update_purchase_receipt_batch_ui(frm) {
		if (frm.doctype !== "Purchase Receipt" || !frm.fields_dict.items || !frm.fields_dict.items.grid) {
			return;
		}

		const grid = frm.fields_dict.items.grid;
		grid.update_docfield_property("batch_no", "in_list_view", 1);
		grid.update_docfield_property(
			"batch_no",
			"description",
			__("Batch No is required for batch-controlled RM items. It is auto-generated on save if left blank.")
		);
		if (field_exists("Purchase Receipt Item", QC_STATUS_FIELD)) {
			grid.update_docfield_property(QC_STATUS_FIELD, "in_list_view", 1);
			grid.update_docfield_property(QC_STATUS_FIELD, "read_only", 1);
			grid.update_docfield_property(QC_STATUS_FIELD, "description", __("Auto-updated from RM Quality Inspection status."));
		}
		if (field_exists("Purchase Receipt Item", QC_ACCEPTED_QTY_FIELD)) {
			grid.update_docfield_property(QC_ACCEPTED_QTY_FIELD, "in_list_view", 1);
			grid.update_docfield_property(QC_ACCEPTED_QTY_FIELD, "read_only", 1);
		}
		if (field_exists("Purchase Receipt Item", QC_REJECTED_QTY_FIELD)) {
			grid.update_docfield_property(QC_REJECTED_QTY_FIELD, "in_list_view", 1);
			grid.update_docfield_property(QC_REJECTED_QTY_FIELD, "read_only", 1);
		}
		if (field_exists("Purchase Receipt Item", QC_INSPECTION_FIELD)) {
			grid.update_docfield_property(QC_INSPECTION_FIELD, "read_only", 1);
		}
		if (field_exists("Purchase Receipt Item", QC_DEVIATION_FIELD)) {
			grid.update_docfield_property(QC_DEVIATION_FIELD, "read_only", 1);
		}

		if (frm.fields_dict[SCAN_FIELD]) {
			frm.set_df_property(
				SCAN_FIELD,
				"description",
				__("Scan a batch barcode or save the receipt to auto-generate Batch No on item rows.")
			);
		}
	}

	function update_purchase_receipt_qc_intro(frm) {
		if (frm.doctype !== "Purchase Receipt") {
			return;
		}

		const row_statuses = (frm.doc.items || [])
			.map((row) => row[QC_STATUS_FIELD])
			.filter((status) => !!status);

		if (!row_statuses.length) {
			frm.set_intro("");
			return;
		}

		if (row_statuses.some((status) => ["Pending", "In Progress"].includes(status))) {
			frm.set_intro(__("QC Pending"), "orange");
			return;
		}

		if (row_statuses.some((status) => status === REJECTED_STATUS)) {
			frm.set_intro(__("Rejected RM pending deviation approval or purchase return."), "red");
			return;
		}

		if (row_statuses.some((status) => status === "Hold")) {
			frm.set_intro(__("QC Hold"), "orange");
			return;
		}

		if (row_statuses.every((status) => ["Accepted", "Released", "Accepted Under Deviation"].includes(status))) {
			frm.set_intro(__("QC Completed"), "green");
			return;
		}

		frm.set_intro("");
	}

	async function update_purchase_receipt_supplier_document_requirements(frm) {
		if (frm.doctype !== "Purchase Receipt") {
			return;
		}

		for (const fieldname of PR_RM_DOCUMENT_FIELDS) {
			if (!frm.fields_dict[fieldname]) {
				continue;
			}
			frm.toggle_reqd(fieldname, false);
		}

		if (frm.doc.docstatus !== 0 || frm.doc.is_return) {
			return;
		}

		const response = await frappe.call({
			method: "calco_erp.calco_quality.purchase_receipt_qc.get_purchase_receipt_rm_document_requirement_state",
			args: {
				doc: frm.doc,
			},
		});
		const state = response.message || {};
		const isRequired = !!state.required;
		for (const fieldname of state.fieldnames || PR_RM_DOCUMENT_FIELDS) {
			if (!frm.fields_dict[fieldname]) {
				continue;
			}
			frm.toggle_reqd(fieldname, isRequired);
		}
	}

	function getRejectedRows(frm) {
		return (frm.doc.items || []).filter((row) => {
			return row[QC_STATUS_FIELD] === REJECTED_STATUS && flt(row[QC_REJECTED_QTY_FIELD] || 0) > 0;
		});
	}

	function removeRejectedFlowButtons(frm) {
		const createGroup = __("Create");
		[
			__("Purchase Return"),
			__("Create RM Deviation Approval"),
			__("Quality Inspection"),
			__("Purchase Invoice"),
			__("Landed Cost Voucher"),
		].forEach((label) => frm.remove_custom_button(label, createGroup));
	}

	function removeQualityInspectionButton(frm) {
		frm.remove_custom_button(__("Quality Inspection"), __("Create"));
	}

	async function addRejectedFlowButtons(frm) {
		if (frm.doctype !== "Purchase Receipt" || frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.is_return) {
			return;
		}

		removeRejectedFlowButtons(frm);
		const response = await frappe.call({
			method: "calco_erp.calco_quality.purchase_receipt_qc.get_purchase_receipt_rejected_action_state",
			args: {
				purchase_receipt: frm.doc.name,
			},
		});
		const state = response.message || {};
		const rejectedRows = state.rows || [];
		if (!rejectedRows.length) {
			return;
		}

		if (state.show_purchase_return) {
			frm.add_custom_button(
				__("Purchase Return"),
				() => {
					frappe.model.open_mapped_doc({
						method: "calco_erp.calco_quality.purchase_receipt_qc.make_rejected_qty_purchase_return",
						frm,
					});
				},
				__("Create")
			);
		}

		if (state.show_deviation) {
			frm.add_custom_button(
				__("Create RM Deviation Approval"),
				() => openDeviationApproval(frm, rejectedRows),
				__("Create")
			);
		}
	}

	async function addQualityInspectionButton(frm) {
		if (frm.doctype !== "Purchase Receipt" || frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.is_return) {
			return;
		}

		removeQualityInspectionButton(frm);
		const response = await frappe.call({
			method: "calco_erp.calco_quality.purchase_receipt_qc.get_purchase_receipt_quality_inspection_action_state",
			args: {
				purchase_receipt: frm.doc.name,
			},
		});
		const state = response.message || {};
		const rows = state.rows || [];
		if (!rows.length) {
			return;
		}

		frm.add_custom_button(
			__("Quality Inspection"),
			() => openQualityInspectionForReceipt(frm, rows),
			__("Create")
		);
	}

	function openQualityInspectionForReceipt(frm, rows) {
		if (rows.length === 1) {
			return handleQualityInspectionAction(frm, rows[0]);
		}

		frappe.prompt(
			[
				{
					fieldname: "purchase_receipt_item",
					fieldtype: "Select",
					label: __("Purchase Receipt Item"),
					reqd: 1,
					options: rows
						.map((row) => `${row.name}|${row.item_code} / ${row.batch_no || __("No Batch")} / ${row.qc_status || __("Pending")}`)
						.join("\n"),
				},
			],
			(values) => {
				const selected = (values.purchase_receipt_item || "").split("|")[0];
				const row = rows.find((entry) => entry.name === selected);
				if (row) {
					handleQualityInspectionAction(frm, row);
				}
			},
			__("Quality Inspection"),
			__("Open")
		);
	}

	function handleQualityInspectionAction(frm, row) {
		const actionType = row.action_type || "info";
		if (actionType === "open_existing" && row.quality_inspection) {
			frappe.set_route("Form", "Quality Inspection", row.quality_inspection);
			return;
		}

		if (actionType === "create_new") {
			frappe.route_options = {
				inspection_type: "Incoming",
				reference_type: "Purchase Receipt",
				reference_name: frm.doc.name,
				item_code: row.item_code,
				batch_no: row.batch_no || "",
			};
			frappe.set_route("Form", "Quality Inspection", "new-quality-inspection");
			return;
		}

		frappe.msgprint({
			title: __("Quality Inspection"),
			indicator: actionType === "blocked" ? "orange" : "blue",
			message: row.message || __("No Quality Inspection action is available for this Purchase Receipt row."),
		});
	}

	function openDeviationApproval(frm, rejectedRows) {
		if (rejectedRows.length === 1) {
			return createDeviationApproval(frm, rejectedRows[0].name);
		}

		frappe.prompt(
			[
				{
					fieldname: "purchase_receipt_item",
					fieldtype: "Select",
					label: __("Rejected Item"),
					reqd: 1,
					options: rejectedRows
						.map((row) => `${row.name}|${row.item_code} / ${row.batch_no || __("No Batch")} / ${row[QC_REJECTED_QTY_FIELD]}`)
						.join("\n"),
				},
			],
			(values) => {
				const selected = (values.purchase_receipt_item || "").split("|")[0];
				createDeviationApproval(frm, selected);
			},
			__("Create Deviation Approval"),
			__("Create")
		);
	}

	function createDeviationApproval(frm, purchaseReceiptItem) {
		frappe.call({
			method: "calco_erp.calco_quality.purchase_receipt_qc.create_rm_deviation_approval",
			args: {
				purchase_receipt: frm.doc.name,
				purchase_receipt_item: purchaseReceiptItem,
			},
		}).then((response) => {
			if (response.message) {
				frappe.set_route("Form", "RM Deviation Approval", response.message);
			}
		});
	}

	function field_exists(doctype, fieldname) {
		return !!frappe.meta.get_docfield(doctype, fieldname);
	}

	function find_matching_row(frm, data) {
		const config = get_config(frm);
		return (frm.doc[config.table] || []).find((row) => {
			if (row.item_code !== data.item_code) return false;
			if (data.batch_no && field_exists(row.doctype, "batch_no")) {
				return row.batch_no === data.batch_no;
			}
			return !row.batch_no;
		});
	}

	async function set_if_present(cdt, cdn, fieldname, value) {
		if (!field_exists(cdt, fieldname) || value === undefined || value === null || value === "") return;
		await frappe.model.set_value(cdt, cdn, fieldname, value);
	}

	async function apply_warehouse_defaults(frm, row) {
		const cdt = row.doctype;
		const cdn = row.name;

		if (frm.doctype === "Purchase Receipt") {
			if (!row.warehouse) {
				await set_if_present(cdt, cdn, "warehouse", frm.doc.set_warehouse);
			}
			return;
		}

		if (frm.doctype === "Delivery Note") {
			if (!row.warehouse) {
				await set_if_present(cdt, cdn, "warehouse", frm.doc.set_warehouse);
			}
			return;
		}

		if (frm.doctype === "Stock Entry") {
			if (frm.doc.purpose === "Material Receipt") {
				if (!row.t_warehouse) {
					await set_if_present(cdt, cdn, "t_warehouse", frm.doc.to_warehouse);
				}
				return;
			}

			if (!row.s_warehouse) {
				await set_if_present(cdt, cdn, "s_warehouse", frm.doc.from_warehouse);
			}
			if (!row.t_warehouse && frm.doc.purpose === "Material Transfer") {
				await set_if_present(cdt, cdn, "t_warehouse", frm.doc.to_warehouse);
			}
		}
	}

	async function update_qty(frm, row, increment) {
		const config = get_config(frm);
		const cdt = row.doctype;
		const cdn = row.name;

		for (const fieldname of config.qty_fields) {
			if (!field_exists(cdt, fieldname)) continue;
			const current = flt(row[fieldname]) || 0;
			await frappe.model.set_value(cdt, cdn, fieldname, current + increment);
		}
	}

	async function upsert_scanned_item(frm, data) {
		const config = get_config(frm);
		let row = find_matching_row(frm, data);

		if (!row) {
			row = frm.add_child(config.table);
			await frappe.model.set_value(row.doctype, row.name, "item_code", data.item_code);
		}

		if (data.batch_no && field_exists(row.doctype, "batch_no")) {
			await frappe.model.set_value(row.doctype, row.name, "batch_no", data.batch_no);
		}

		await apply_warehouse_defaults(frm, row);
		await update_qty(frm, row, config.default_qty);
		frm.refresh_field(config.table);
	}

	async function scan_barcode(frm) {
		const barcode = (frm.doc[SCAN_FIELD] || "").trim();
		if (!barcode) return;

		try {
			const response = await frappe.call({
				method: "calco_erp.barcode_setup.resolve_barcode",
				args: { barcode },
			});
			const data = response.message;
			if (!data) {
				frappe.throw(__("Barcode {0} could not be resolved.", [barcode]));
			}

			await upsert_scanned_item(frm, data);
			frappe.show_alert({
				message: __("Scanned {0}", [data.batch_no || data.item_code]),
				indicator: "green",
			});
		} catch (error) {
			frappe.msgprint({
				title: __("Barcode Scan Failed"),
				indicator: "red",
				message: error.message || error,
			});
		} finally {
			await frm.set_value(SCAN_FIELD, "");
		}
	}

	for (const doctype of Object.keys(CONFIG)) {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				set_work_order_batch_query(frm);
				void apply_work_order_warehouse_defaults(frm);
				update_purchase_receipt_batch_ui(frm);
				update_purchase_receipt_qc_intro(frm);
				update_purchase_receipt_supplier_document_requirements(frm);
				addQualityInspectionButton(frm);
				addRejectedFlowButtons(frm);
			},

			validate(frm) {
				return apply_work_order_warehouse_defaults(frm);
			},

			async [SCAN_FIELD](frm) {
				await scan_barcode(frm);
			},
		});
	}

	frappe.ui.form.on("Stock Entry", {
		work_order(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
		purpose(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
		items_add(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
	});

	frappe.ui.form.on("Stock Entry Detail", {
		item_code(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
		qty(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
		transfer_qty(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
		batch_no(frm) {
			void apply_work_order_warehouse_defaults(frm);
		},
	});

	frappe.ui.form.on("Purchase Receipt", {
		items_add(frm) {
			update_purchase_receipt_supplier_document_requirements(frm);
		},
		items_remove(frm) {
			update_purchase_receipt_supplier_document_requirements(frm);
		},
	});

	frappe.ui.form.on("Purchase Receipt Item", {
		item_code(frm) {
			update_purchase_receipt_supplier_document_requirements(frm);
		},
	});
})();
