function calcoSetRmQcDecisionQueries(frm) {
  frm.set_query("quality_inspection", function () {
    return {
      query: "calco_erp.calco_quality.rm_quality_flow_queries.pending_rm_quality_inspection_query",
    };
  });
}

function calcoSyncRmQcDecisionFromInspection(frm) {
  if (!frm.doc.quality_inspection) {
    return;
  }

  frappe.db
    .get_value("Quality Inspection", frm.doc.quality_inspection, [
      "reference_name",
      "item_code",
      "batch_no",
      "status",
      "custom_overall_result",
    ])
    .then((response) => {
      const inspection = response.message || {};
      frm.set_value("purchase_receipt", inspection.reference_name || "");
      frm.set_value("item_code", inspection.item_code || "");
      frm.set_value("batch_no", inspection.batch_no || "");
    });
}

function calcoAddDeviationAction(frm) {
  if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.decision !== "Deviation Required") {
    return;
  }

  frm.add_custom_button(__("RM Deviation Approval"), () => {
    frappe.call({
      method: "calco_erp.calco_quality.doctype.rm_qc_decision.rm_qc_decision.create_rm_deviation_from_decision",
      args: { name: frm.doc.name },
      freeze: true,
      callback: (response) => {
        const deviationName = response.message;
        if (!deviationName) {
          return;
        }
        frappe.set_route("Form", "RM Deviation Approval", deviationName);
      },
    });
  });
}

frappe.ui.form.on("RM QC Decision", {
  setup(frm) {
    calcoSetRmQcDecisionQueries(frm);
  },

  refresh(frm) {
    calcoSetRmQcDecisionQueries(frm);
    calcoAddDeviationAction(frm);
  },

  quality_inspection(frm) {
    calcoSyncRmQcDecisionFromInspection(frm);
  },
});
