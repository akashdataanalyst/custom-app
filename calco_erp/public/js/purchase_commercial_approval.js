frappe.ui.form.on("Purchase Commercial Approval", {
	refresh(frm) {
		const terminal = ["Approved", "Rejected"].includes(frm.doc.approval_status);
		const canReopen = frappe.user.has_role("System Manager") || frappe.user.has_role("Commercial Admin");

		set_field_editability(frm, terminal);

		if (terminal && canReopen && !frm.is_new()) {
			frm.add_custom_button(__("Reopen Approval"), () => open_reopen_dialog(frm));
		}
	},
});

function set_field_editability(frm, terminal) {
	[
		"reason_for_higher_price",
		"decision",
		"reopen_reason",
	].forEach((fieldname) => {
		const df = frm.get_docfield(fieldname);
		if (!df) {
			return;
		}
		const isReopenField = fieldname === "reopen_reason";
		df.read_only = terminal || isReopenField;
	});
	frm.refresh_fields(["reason_for_higher_price", "decision", "reopen_reason"]);

	if (terminal) {
		frm.disable_save();
	} else {
		frm.enable_save();
	}
}

function open_reopen_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Reopen Approval"),
		fields: [
			{
				fieldname: "reopen_reason",
				fieldtype: "Small Text",
				label: __("Reopen Reason"),
				reqd: 1,
			},
		],
		primary_action_label: __("Reopen"),
		primary_action(values) {
			frappe.call({
				method: "calco_erp.calco_purchase.doctype.purchase_commercial_approval.purchase_commercial_approval.reopen_purchase_commercial_approval",
				args: {
					name: frm.doc.name,
					reopen_reason: values.reopen_reason,
				},
				callback: () => {
					dialog.hide();
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}
