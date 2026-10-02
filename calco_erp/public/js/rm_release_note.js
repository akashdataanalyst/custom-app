function calcoSetRmReleaseNoteQueries(frm) {
  frm.set_query("rm_qc_decision", function () {
    return {
      query: "calco_erp.calco_quality.rm_quality_flow_queries.pending_rm_qc_decision_query",
    };
  });
}

function calcoSetRmQcDecisionUiState(frm, options) {
  const isDirectAccepted = Boolean(options && options.isDirectAccepted);
  const helperText = isDirectAccepted
    ? "Not required for directly accepted Incoming QC."
    : "";

  frm.toggle_reqd("rm_qc_decision", !isDirectAccepted);
  frm.set_df_property("rm_qc_decision", "description", helperText);
}

function calcoRefreshRmQcDecisionRequirement(frm) {
  if (frm.doc.custom_rm_deviation_approval) {
    calcoSetRmQcDecisionUiState(frm, { isDirectAccepted: false });
    return Promise.resolve();
  }

  if (frm.doc.rm_qc_decision) {
    calcoSetRmQcDecisionUiState(frm, { isDirectAccepted: false });
    return Promise.resolve();
  }

  if (!frm.doc.custom_quality_inspection) {
    calcoSetRmQcDecisionUiState(frm, { isDirectAccepted: false });
    return Promise.resolve();
  }

  return frappe.db
    .get_value("Quality Inspection", frm.doc.custom_quality_inspection, [
      "docstatus",
      "status",
      "custom_overall_result",
    ])
    .then((response) => {
      const inspection = response.message || {};
      const overallResult = (inspection.custom_overall_result || inspection.status || "")
        .trim()
        .toUpperCase();
      const isDirectAccepted =
        cint(inspection.docstatus) === 1 &&
        (overallResult === "ACCEPTED" || overallResult === "PASS");
      calcoSetRmQcDecisionUiState(frm, { isDirectAccepted });
    })
    .catch(() => {
      calcoSetRmQcDecisionUiState(frm, { isDirectAccepted: false });
    });
}

function cint(value) {
  return parseInt(value || 0, 10) || 0;
}

function calcoSyncRmReleaseNoteFromDecision(frm) {
  if (!frm.doc.rm_qc_decision) {
    return;
  }

  frappe.db
    .get_value("RM QC Decision", frm.doc.rm_qc_decision, [
      "item_code",
      "batch_no",
      "sample_qty",
    ])
    .then((response) => {
      const decision = response.message || {};
      frm.set_value("item_code", decision.item_code || "");
      frm.set_value("batch_no", decision.batch_no || "");
      if (!frm.doc.release_qty && decision.sample_qty) {
        frm.set_value("release_qty", decision.sample_qty);
      }
    });
}

function calcoSyncRmReleaseNoteFromInspection(frm) {
  if (!frm.doc.custom_quality_inspection) {
    return Promise.resolve();
  }

  return frappe.db
    .get_value("Quality Inspection", frm.doc.custom_quality_inspection, [
      "reference_name",
      "item_code",
      "batch_no",
    ])
    .then((response) => {
      const inspection = response.message || {};
      frm.set_value("custom_purchase_receipt", inspection.reference_name || "");
      frm.set_value("item_code", inspection.item_code || "");
      frm.set_value("batch_no", inspection.batch_no || "");
    });
}

function calcoSyncRmReleaseNoteFromDeviation(frm) {
  if (!frm.doc.custom_rm_deviation_approval) {
    return Promise.resolve();
  }

  return frappe.db
    .get_value("RM Deviation Approval", frm.doc.custom_rm_deviation_approval, [
      "purchase_receipt",
      "quality_inspection",
      "rm_qc_decision",
      "item_code",
      "batch_no",
      "approved_qty",
    ])
    .then((response) => {
      const deviation = response.message || {};
      frm.set_value("custom_purchase_receipt", deviation.purchase_receipt || "");
      frm.set_value("custom_quality_inspection", deviation.quality_inspection || "");
      frm.set_value("rm_qc_decision", deviation.rm_qc_decision || "");
      frm.set_value("item_code", deviation.item_code || "");
      frm.set_value("batch_no", deviation.batch_no || "");
      if (!frm.doc.release_qty && deviation.approved_qty) {
        frm.set_value("release_qty", deviation.approved_qty);
      }
    });
}

frappe.ui.form.on("RM Release Note", {
  setup(frm) {
    calcoSetRmReleaseNoteQueries(frm);
  },

  refresh(frm) {
    calcoSetRmReleaseNoteQueries(frm);
    calcoRefreshRmQcDecisionRequirement(frm);
  },

  rm_qc_decision(frm) {
    calcoSyncRmReleaseNoteFromDecision(frm);
    calcoRefreshRmQcDecisionRequirement(frm);
  },

  custom_quality_inspection(frm) {
    calcoSyncRmReleaseNoteFromInspection(frm).then(() =>
      calcoRefreshRmQcDecisionRequirement(frm)
    );
  },

  custom_rm_deviation_approval(frm) {
    calcoSyncRmReleaseNoteFromDeviation(frm).then(() =>
      calcoRefreshRmQcDecisionRequirement(frm)
    );
  },
});
