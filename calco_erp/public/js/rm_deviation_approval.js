frappe.ui.form.on("RM Deviation Approval", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		updateAttachmentLink(frm);
		updateApprovalFieldState(frm);

		if (frm.doc.docstatus === 1 && (frm.doc.approval_status === "Pending" || frm.doc.approval_status === "Pending Operations Approval")) {
			const roles = frappe.user_roles || [];
			if (roles.includes("Operations Head") || roles.includes("System Manager")) {
				frm.add_custom_button(__("Approve Deviation"), () => approveDeviation(frm), __("Actions"));
				frm.add_custom_button(__("Reject Deviation"), () => rejectDeviation(frm), __("Actions"));
			}
		}

		if (frm.doc.docstatus === 1 && frm.doc.approval_status === "Rejected") {
			frm.add_custom_button(__("Request Supplier CAPA"), () => requestSupplierCapa(frm), __("Actions"));
		}
	},
});

function updateApprovalFieldState(frm) {
	const isPendingApproval = frm.doc.docstatus === 1
		&& (frm.doc.approval_status === "Pending" || frm.doc.approval_status === "Pending Operations Approval");
	frm.set_df_property("approval_status", "read_only", 1);
	frm.set_df_property("deviation_attachment", "reqd", frm.doc.docstatus === 0);
	frm.set_df_property(
		"operations_head",
		"description",
		frm.doc.docstatus === 1
			? __("Populated automatically when an Operations Head approves or rejects this deviation.")
			: __("Will be captured when the deviation is approved or rejected.")
	);
	frm.set_df_property(
		"approval_status",
		"description",
		isPendingApproval
			? __("Awaiting Operations Head approval.")
			: ""
	);
}

function updateAttachmentLink(frm) {
	const url = frm.doc.deviation_attachment;
	if (!url) {
		frm.set_df_property("deviation_attachment", "description", "");
		return;
	}

	const filename = url.split("/").pop() || __("Open attachment");
	const safeFilename = frappe.utils.escape_html(filename);
	const safeUrl = frappe.utils.escape_html(url);
	frm.set_df_property(
		"deviation_attachment",
		"description",
		`<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeFilename}</a>`
	);
}

function approveDeviation(frm) {
	frappe.prompt(
		[
			{
				fieldname: "approval_remarks",
				fieldtype: "Small Text",
				label: __("Approval Remarks"),
			},
		],
		(values) => {
			frappe.call({
				method: "calco_erp.calco_quality.doctype.rm_deviation_approval.rm_deviation_approval.approve_deviation",
				args: {
					name: frm.doc.name,
					approval_remarks: values.approval_remarks,
				},
			}).then(() => frm.reload_doc());
		},
		__("Approve RM Deviation"),
		__("Approve")
	);
}

function rejectDeviation(frm) {
	frappe.prompt(
		[
			{
				fieldname: "approval_remarks",
				fieldtype: "Small Text",
				label: __("Rejection Remarks"),
				reqd: 1,
			},
		],
		(values) => {
			frappe.call({
				method: "calco_erp.calco_quality.doctype.rm_deviation_approval.rm_deviation_approval.reject_deviation",
				args: {
					name: frm.doc.name,
					approval_remarks: values.approval_remarks,
				},
			}).then(() => frm.reload_doc());
		},
		__("Reject RM Deviation"),
		__("Reject")
	);
}

function requestSupplierCapa(frm) {
	frappe.call({
		method: "calco_erp.calco_quality.doctype.rm_deviation_approval.rm_deviation_approval.request_supplier_capa",
		args: {
			name: frm.doc.name,
		},
	}).then((response) => {
		if (response.message) {
			frappe.set_route("Form", "Supplier CAPA Request", response.message);
		}
	});
}
