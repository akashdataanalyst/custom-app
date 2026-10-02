frappe.ui.form.on("Item", {
  refresh(frm) {
    calco_show_item_code(frm);
    if (frm.is_new()) {
      return;
    }

    frappe.call({
      method:
        "calco_erp.calco_purchase.existing_rm_readiness.get_existing_rm_technical_context",
      args: { item_code: frm.doc.name },
      callback(response) {
        const context = response.message || {};
        const fields = [
          "custom_existing_rm_technical_section",
          "custom_existing_rm_technical_status",
          "custom_existing_rm_technical_decision",
          "custom_existing_rm_technical_approved_by",
          "custom_existing_rm_technical_approved_on",
          "custom_existing_rm_technical_remarks",
          "custom_existing_rm_technical_evidence",
        ];
        fields.forEach((fieldname) =>
          frm.toggle_display(fieldname, Boolean(context.eligible))
        );

        if (!context.eligible || !context.can_decide) {
          return;
        }
        if (context.status === "Approved") {
          frm.add_custom_button(
            __("Revoke Technical Certification"),
            () => show_decision_dialog(frm, "Revoked"),
            __("Technical Certification")
          );
          return;
        }
        frm.add_custom_button(
          __("Approve Technical Certification"),
          () => show_decision_dialog(frm, "Approved"),
          __("Technical Certification")
        );
        frm.add_custom_button(
          __("Reject Technical Certification"),
          () => show_decision_dialog(frm, "Rejected"),
          __("Technical Certification")
        );
      },
    });
  },
});

function show_decision_dialog(frm, decision) {
  const approving = decision === "Approved";
  const dialog = new frappe.ui.Dialog({
    title: __("{0} Existing RM Technical Certification", [decision]),
    fields: [
      {
        fieldname: "remarks",
        label: __("Remarks"),
        fieldtype: "Small Text",
        reqd: 1,
      },
      {
        fieldname: "evidence",
        label: __("Evidence / Reference"),
        fieldtype: "Small Text",
        reqd: approving ? 1 : 0,
        hidden: approving ? 0 : 1,
      },
    ],
    primary_action_label: __(decision),
    primary_action(values) {
      dialog.disable_primary_action();
      frappe.call({
        method:
          "calco_erp.calco_purchase.existing_rm_readiness.apply_existing_rm_technical_decision",
        args: {
          item_code: frm.doc.name,
          decision,
          remarks: values.remarks,
          evidence: values.evidence || "",
        },
        freeze: true,
        callback() {
          dialog.hide();
          frm.reload_doc();
        },
        always() {
          dialog.enable_primary_action();
        },
      });
    },
  });
  dialog.show();
}

// Frappe v16 refresh_fields() runs cleanup_refresh() before the supported
// refresh event. Restore presentation here; do not wrap Form internals or
// assign doc.item_code/name. New and duplicated documents use native metadata.
function calco_show_item_code(frm) {
  const field = frm.fields_dict.item_code;
  if (!field) return;
  const native_field = frappe.meta.get_docfield("Item", "item_code");
  frm.set_df_property(
    "item_code", "read_only", frm.is_new() ? (native_field.read_only || 0) : 1
  );
  frm.toggle_display("item_code", true);
}
