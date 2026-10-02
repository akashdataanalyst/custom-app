(() => {
  window.calco_erp = window.calco_erp || {};
  calco_erp.grade_change_control = calco_erp.grade_change_control || {};
  const METHODS = {
    create: "calco_erp.calco_production.grade_change_control.create_grade_change_clearance",
    generate: "calco_erp.calco_production.grade_change_control.confirm_classifications_and_generate",
    submit: "calco_erp.calco_production.grade_change_control.submit_for_approval",
    approve: "calco_erp.calco_production.grade_change_control.approve_clearance",
    reject: "calco_erp.calco_production.grade_change_control.reject_clearance",
    requestReset: "calco_erp.calco_production.grade_change_control.request_reset",
    approveReset: "calco_erp.calco_production.grade_change_control.approve_reset",
    rejectReset: "calco_erp.calco_production.grade_change_control.reject_reset",
  };

  const callAndReload = async (frm, method, args = {}) => {
    const response = await frappe.call({ method, args, freeze: true });
    await frm.reload_doc();
    return response.message || {};
  };

  const saveThenCall = async (frm, method, args = {}) => {
    if (frm.is_dirty()) await frm.save();
    return callAndReload(frm, method, args);
  };

  const promptReason = (title, label, callback) => {
    frappe.prompt(
      [{ fieldname: "reason", fieldtype: "Small Text", label, reqd: 1 }],
      (values) => callback(values.reason),
      title,
      __("Continue")
    );
  };

  const openOrCreateClearance = async (frm, args = {}) => {
    const existing = frm.doc.custom_grade_change_clearance;
    if (existing) {
      frappe.set_route("Form", "Grade Change Clearance", existing);
      return;
    }
    const response = await frappe.call({ method: METHODS.create, args, freeze: true });
    const result = response.message || {};
    if (!result.name) {
      frappe.throw(__("Grade Change Clearance was not returned by the server."));
    }
    frappe.set_route(...(result.route || ["Form", "Grade Change Clearance", result.name]));
  };

  const setupSourceDocument = (frm) => {
    if (frm.is_new()) return;
    if (frm.doctype === "Job Card" && frm.doc.operation !== "Compounding / Extrusion") return;
    const clearance = frm.doc.custom_grade_change_clearance;
    frm.add_custom_button(
      clearance ? __("Open Grade Change Clearance") : __("Create Grade Change Clearance"),
      async () => {
        const args = frm.doctype === "Work Order"
          ? { work_order: frm.doc.name }
          : { job_card: frm.doc.name, work_order: frm.doc.work_order };
        await openOrCreateClearance(frm, args);
      },
      __("Production")
    );
  };

  const LEGACY_CLEARANCE_FIELDS = [
    "job_card", "previous_grade", "new_grade", "critical_grade_change",
    "prepared_by", "prepared_on", "line_clearance_done", "hopper_cleaned",
    "die_cleaned", "screen_changed", "purging_confirmation_required",
    "purging_confirmation_done",
  ];

  const AUDIT_ONLY_FIELDS = [
    "previous_override", "current_override",
    "previous_classification_changed_by", "previous_classification_changed_on",
    "current_classification_changed_by", "current_classification_changed_on",
    "classification_confirmed_by", "classification_confirmed_on",
    "controlled_document_no", "controlled_revision", "evaluation_revision",
    "checklist_generated_by", "checklist_generated_on",
    "approved_by", "approved_on", "rejection_reason",
  ];

  const CLEANING_CODES = [
    [0, "Cleaning Not Required"],
    [1, "Emptying / Replacing If Required"],
    [2, "Emptying / Clean With Air / Cloth"],
    [3, "Emptying / Wash With Water And Dry Cloth"],
  ];

  const escape = (value) => frappe.utils.escape_html(value || "-");

  const renderHtmlField = (frm, fieldname, html) => {
    const field = frm.fields_dict[fieldname];
    if (field && field.$wrapper) field.$wrapper.html(html);
  };

  const renderEvaluationSummary = (frm) => {
    if (!frm.doc.transition || !frm.doc.cleaning_level) {
      renderHtmlField(
        frm,
        "evaluation_summary_html",
        `<div class="alert alert-info mb-3">${__("Select the actual previous and current classifications, then confirm to generate the controlled checklist.")}</div>`
      );
      return;
    }
    renderHtmlField(
      frm,
      "evaluation_summary_html",
      `<div class="border rounded p-3 mb-3 bg-light">
        <div class="row">
          <div class="col-sm-6"><div class="text-muted small">${__("Transition")}</div><strong>${escape(frm.doc.transition)}</strong></div>
          <div class="col-sm-6"><div class="text-muted small">${__("Cleaning Level")}</div><strong>${escape(frm.doc.cleaning_level)} — ${escape(frm.doc.cleaning_level_description)}</strong></div>
          <div class="col-sm-6 mt-3"><div class="text-muted small">${__("Controlled Standard")}</div><strong>${escape(frm.doc.controlled_document_no)} ${escape(frm.doc.controlled_revision)}</strong></div>
          <div class="col-sm-6 mt-3"><div class="text-muted small">${__("Evaluation Revision")}</div><strong>${frm.doc.evaluation_revision || 1}</strong></div>
        </div>
      </div>`
    );
  };

  const renderCleaningCodeLegend = (frm) => {
    const rows = CLEANING_CODES.map(([code, description]) =>
      `<div class="py-1"><strong>${__("Code")} ${code}</strong> — ${__(description)}</div>`
    ).join("");
    renderHtmlField(
      frm,
      "cleaning_code_legend_html",
      `<div class="border rounded p-3 mb-3 bg-light">
        <div class="font-weight-bold mb-2">${__("CLEANING CODE DETAILS")}</div>${rows}
      </div>`
    );
  };

  const renderAuditDetails = (frm) => {
    renderHtmlField(
      frm,
      "audit_details_html",
      `<div class="small text-muted mb-3">
        ${__("Evaluation Revision")}: <strong>${frm.doc.evaluation_revision || 1}</strong> &nbsp;|&nbsp;
        ${__("Generated By")}: <strong>${escape(frm.doc.checklist_generated_by)}</strong> &nbsp;|&nbsp;
        ${__("Generated On")}: <strong>${escape(frm.doc.checklist_generated_on)}</strong><br>
        ${__("Classification Confirmed By")}: <strong>${escape(frm.doc.classification_confirmed_by)}</strong> &nbsp;|&nbsp;
        ${__("Confirmed On")}: <strong>${escape(frm.doc.classification_confirmed_on)}</strong>
      </div>`
    );
  };

  const configureChecklistGrid = (frm) => {
    const field = frm.fields_dict.checklist;
    if (!field || !field.grid) return;
    const grid = field.grid;
    const canExecute = frappe.user_roles.some((role) =>
      ["Production Engineer", "System Manager"].includes(role)
    );
    const executionEditable = canExecute
      && frm.doc.docstatus === 0
      && ["Draft Checklist", "In Progress"].includes(frm.doc.status);
    const showAudit = frappe.user_roles.some((role) =>
      ["Production Head", "System Manager"].includes(role)
    );
    const revision = Number(frm.doc.evaluation_revision || 1);
    const rows = (frm.doc.checklist || [])
      .filter((row) => Number(row.evaluation_revision || 1) === revision && !Number(row.superseded || 0))
      .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0));
    const text = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
    const body = rows.map((row) => {
      const audit = showAudit && (row.checked_by || row.checked_on)
        ? `<div class="calco-gcc-audit text-muted small">
            ${__("Checked By")}: ${text(row.checked_by || "-")}<br>
            ${__("Checked On")}: ${text(row.checked_on || "-")}
          </div>`
        : "";
      return `<tr data-row-name="${text(row.name)}">
        <td class="calco-gcc-sequence">${text(row.sequence)}</td>
        <td class="calco-gcc-checkpoint">${text(row.check_item)}</td>
        <td class="calco-gcc-code">${text(row.required_cleaning_code)}</td>
        <td class="calco-gcc-method">${text(row.cleaning_code_description)}</td>
        <td class="calco-gcc-observation-cell">
          <textarea class="form-control calco-gcc-observation" rows="1"
            aria-label="${__("Observation")}" ${executionEditable ? "" : "disabled"}>${text(row.observation)}</textarea>
        </td>
        <td class="calco-gcc-done-cell">
          <input class="calco-gcc-done" type="checkbox" aria-label="${__("Done")}"
            ${Number(row.completed || 0) ? "checked" : ""} ${executionEditable ? "" : "disabled"}>
          ${audit}
        </td>
      </tr>`;
    }).join("");
    const table = $(`<div class="calco-gcc-controlled-table-wrap table-responsive">
      <table class="table table-bordered calco-gcc-controlled-table">
        <thead><tr>
          <th>${__("Sr. No.")}</th>
          <th>${__("Check Point")}</th>
          <th>${__("Code")}</th>
          <th>${__("Required Cleaning Method")}</th>
          <th>${__("Observation")}</th>
          <th>${__("Done")}</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`);

    grid.wrapper.addClass("calco-grade-change-checklist-grid");
    grid.cannot_add_rows = true;
    grid.cannot_delete_rows = true;
    grid.wrapper.find(".form-grid").hide();
    grid.wrapper.find(
      ".grid-add-row, .grid-add-multiple-rows, .grid-remove-rows, .grid-footer, "
      + ".grid-buttons, .grid-row-open, .btn-open-row, .row-check, .row-index"
    ).hide();
    grid.wrapper.find(".calco-gcc-controlled-table-wrap").remove();
    grid.wrapper.append(table);
    const resizeObservation = (element) => {
      element.style.height = "auto";
      element.style.height = `${Math.max(element.scrollHeight, 28)}px`;
    };
    table.find(".calco-gcc-observation").each((index, element) => resizeObservation(element));
    table.on("input.calcoGradeChange", ".calco-gcc-observation", function () {
      const rowName = $(this).closest("tr").data("row-name");
      const row = (frm.doc.checklist || []).find((entry) => entry.name === rowName);
      if (!row || !executionEditable) return;
      row.observation = $(this).val();
      resizeObservation(this);
      frm.dirty();
    });
    table.on("change.calcoGradeChange", ".calco-gcc-done", function () {
      const rowName = $(this).closest("tr").data("row-name");
      const row = (frm.doc.checklist || []).find((entry) => entry.name === rowName);
      if (!row || !executionEditable) return;
      row.completed = this.checked ? 1 : 0;
      frm.dirty();
    });
    if (!document.getElementById("calco-grade-change-checklist-grid-style")) {
      $("<style>", {
        id: "calco-grade-change-checklist-grid-style",
        text: `
          .calco-gcc-controlled-table {
            width: 100%;
            min-width: 0;
            table-layout: fixed;
            margin-bottom: 0;
            font-size: 12.5px;
            line-height: 1.3;
          }
          .calco-gcc-controlled-table th {
            background: var(--subtle-fg);
            vertical-align: middle;
            padding: 4px 5px !important;
            line-height: 1.25;
          }
          .calco-gcc-controlled-table td {
            vertical-align: middle;
            white-space: normal;
            overflow-wrap: anywhere;
            padding: 4px 6px !important;
            line-height: 1.3;
          }
          .calco-gcc-controlled-table th:nth-child(1), .calco-gcc-controlled-table td:nth-child(1) { width: 5%; text-align: center; padding-left: 3px !important; padding-right: 3px !important; }
          .calco-gcc-controlled-table th:nth-child(2), .calco-gcc-controlled-table td:nth-child(2) { width: 27%; }
          .calco-gcc-controlled-table th:nth-child(3), .calco-gcc-controlled-table td:nth-child(3) { width: 6%; text-align: center; padding-left: 3px !important; padding-right: 3px !important; }
          .calco-gcc-controlled-table th:nth-child(4), .calco-gcc-controlled-table td:nth-child(4) { width: 30%; }
          .calco-gcc-controlled-table th:nth-child(5), .calco-gcc-controlled-table td:nth-child(5) { width: 26%; }
          .calco-gcc-controlled-table th:nth-child(6), .calco-gcc-controlled-table td:nth-child(6) { width: 6%; text-align: center; padding-left: 2px !important; padding-right: 2px !important; }
          .calco-gcc-controlled-table .calco-gcc-checkpoint { font-weight: 600; }
          .calco-grade-change-checklist-grid .grid-add-row,
          .calco-grade-change-checklist-grid .grid-add-multiple-rows,
          .calco-grade-change-checklist-grid .grid-remove-rows,
          .calco-grade-change-checklist-grid .grid-footer,
          .calco-grade-change-checklist-grid .grid-buttons,
          .calco-grade-change-checklist-grid .grid-row-open,
          .calco-grade-change-checklist-grid .btn-open-row,
          .calco-grade-change-checklist-grid .row-check,
          .calco-grade-change-checklist-grid .row-index { display: none !important; }
          .calco-gcc-controlled-table .calco-gcc-observation {
            width: 100%;
            min-height: 28px;
            line-height: 1.25;
            padding: 3px 5px;
            resize: none;
            overflow-y: hidden;
          }
          .calco-gcc-controlled-table .calco-gcc-done { width: 16px; height: 16px; margin: 0; }
          .calco-gcc-controlled-table .calco-gcc-audit { margin-top: 3px; line-height: 1.25; }
        `,
      }).appendTo(document.head);
    }
  };

  const configureExecutionFields = (frm) => {
    AUDIT_ONLY_FIELDS.forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.toggle_display(fieldname, false);
    });
    frm.set_df_property("remarks", "label", __("Engineer Remarks"));
    frm.set_df_property("completion_section", "label", __("H - Execution Summary"));
    ["execution_started_on", "execution_completed_on", "time_taken_minutes"].forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.set_df_property(fieldname, "read_only", 1);
    });
    frm.set_df_property("system_evidence_section", "description", __("Read-only ERP evidence supporting the Engineer classification decision."));
    frm.set_df_property("line_material_removed", "description", __("Unchecked means unconfirmed. The Engineer must explicitly confirm this control."));
    frm.set_df_property("feeder_connection_confirmed", "description", __("Unchecked means unconfirmed. The Engineer must explicitly confirm this control."));
    frm.set_df_property("cotton_cloth_available", "description", __("Select Yes or No. No requires a Cotton Cloth Observation before approval submission."));
    frm.toggle_reqd("cotton_cloth_observation", frm.doc.cotton_cloth_available === "No");

    const generated = frm.doc.status !== "Pending Evaluation" || Boolean(frm.doc.checklist_generated_on);
    ["checklist_section", "additional_controls_section", "completion_section"].forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.toggle_display(fieldname, generated);
    });
    if (frm.fields_dict.regeneration_reason) {
      frm.toggle_display("regeneration_reason", Boolean(frm.doc.checklist_generated_on));
    }
  };

  const configureClearanceLayout = (frm) => {
    LEGACY_CLEARANCE_FIELDS.forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.toggle_display(fieldname, false);
    });
    const labels = {
      authority_section: "B - Current Production",
      system_evidence_section: "C - System Evidence - For Reference",
      engineer_decision_section: "D - Production Engineer Evaluation",
      evaluation_section: "E - Derived Cleaning Requirement",
      checklist_section: "F - F-GCL-01 Checklist",
      additional_controls_section: "G - Additional Controls",
      completion_section: "H - Execution Summary",
      reset_section: "Audit / Revision Details",
      status: "Workflow Status",
    };
    Object.entries(labels).forEach(([fieldname, label]) => {
      if (frm.fields_dict[fieldname]) frm.set_df_property(fieldname, "label", __(label));
    });
    frm.layout.message.find(".calco-grade-change-controlled-document")
      .closest(".form-message").remove();
    frm.set_intro(
      `<div class="calco-grade-change-controlled-document"><strong>${__("A - Controlled Document")}</strong><br>${__("Document")}: F-GCL-01 &nbsp; ${__("Revision")}: REV-02 &nbsp; ${__("Status")}: ${frappe.utils.escape_html(frm.doc.status || "Pending Evaluation")} &nbsp; ${__("Evaluation Revision")}: ${frm.doc.evaluation_revision || 1}</div>`,
      "blue"
    );
    frm.dashboard.parent.find(".calco-grade-change-decision").remove();
    frm.dashboard.add_section(
      `<div class="calco-grade-change-approval-summary"><strong>${__("I - Approval")}</strong><br>${__("Workflow Status")}: ${frappe.utils.escape_html(frm.doc.status || "Pending Evaluation")}<br>${__("Approved / Rejected By")}: ${frappe.utils.escape_html(frm.doc.approved_by || "-")}<br>${__("Approved / Rejected On")}: ${frappe.utils.escape_html(frm.doc.approved_on || "-")}</div>`,
      __("F-GCL-01 Decision"),
      "custom calco-grade-change-decision"
    );
    renderEvaluationSummary(frm);
    renderCleaningCodeLegend(frm);
    renderAuditDetails(frm);
    configureChecklistGrid(frm);
    configureExecutionFields(frm);
  };

  const setGridLocked = (frm, fieldname, locked) => {
    if (!frm.fields_dict[fieldname]) return;
    frm.set_df_property(fieldname, "read_only", locked ? 1 : 0);
    const grid = frm.fields_dict[fieldname].grid;
    if (!grid) return;
    grid.cannot_add_rows = locked;
    grid.wrapper.find(".grid-add-row, .grid-add-multiple-rows, .grid-remove-rows").toggle(!locked);
    grid.refresh();
  };

  const applyJobCardUi = (frm, payload = {}) => {
    if (frm.doctype !== "Job Card" || frm.doc.operation !== "Compounding / Extrusion") return;
    [
      "custom_grade_change_required",
      "custom_grade_change_status",
      "custom_grade_change_section",
      "custom_grade_change_checklist",
    ].forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.toggle_display(fieldname, false);
    });
    const summary = payload.summary || {};
    const approved = summary.grade_change_approved === true;
    setGridLocked(frm, "custom_rm_loading_details", !approved);
    if (frm.fields_dict.custom_rm_loading_status) {
      frm.set_df_property("custom_rm_loading_status", "read_only", approved ? 0 : 1);
    }
    if (!approved && frm.fields_dict.custom_rm_loading_section) {
      frm.set_df_property(
        "custom_rm_loading_section",
        "description",
        __("Blocked until the authoritative F-GCL-01 Grade Change Clearance is Approved.")
      );
    }
  };

  const setAuthorityFields = (frm) => {
    const locked = ["In Progress", "Pending Approval", "Reset Approval Required", "Approved", "Rejected"].includes(frm.doc.status);
    [
      "actual_previous_classification", "actual_current_classification",
      "previous_override_reason", "current_override_reason", "not_required",
      "not_required_reason", "regeneration_reason",
    ].forEach((fieldname) => frm.set_df_property(fieldname, "read_only", locked ? 1 : 0));
  };

  const setupClearance = (frm) => {
    if (frm.is_new()) return;
    configureClearanceLayout(frm);
    setAuthorityFields(frm);
    const status = frm.doc.status;

    if (["Pending Evaluation", "Draft Checklist"].includes(status)) {
      frm.add_custom_button(__("Confirm Classification and Generate Checklist"), () =>
        callAndReload(frm, METHODS.generate, {
          clearance: frm.doc.name,
          actual_previous_classification: frm.doc.actual_previous_classification,
          actual_current_classification: frm.doc.actual_current_classification,
          previous_override_reason: frm.doc.previous_override_reason,
          current_override_reason: frm.doc.current_override_reason,
          regeneration_reason: frm.doc.regeneration_reason,
          remarks: frm.doc.remarks,
          not_required: frm.doc.not_required,
          not_required_reason: frm.doc.not_required_reason,
        }), __("Grade Change"));
    }
    if (["Draft Checklist", "In Progress"].includes(status)) {
      frm.add_custom_button(__("Submit for Approval"), () =>
        saveThenCall(frm, METHODS.submit, { clearance: frm.doc.name }), __("Grade Change"));
    }
    if (["In Progress", "Pending Approval", "Approved"].includes(status)) {
      frm.add_custom_button(__("Request Reset"), () =>
        promptReason(__("Request Controlled Reset"), __("Reset Reason"), (reason) =>
          callAndReload(frm, METHODS.requestReset, { clearance: frm.doc.name, reset_reason: reason })), __("Grade Change"));
    }
    const canApprove = frappe.user_roles.includes("Production Head") || frappe.user_roles.includes("System Manager");
    if (status === "Pending Approval" && canApprove) {
      frm.add_custom_button(__("Approve"), () =>
        callAndReload(frm, METHODS.approve, { clearance: frm.doc.name }), __("Production Head"));
      frm.add_custom_button(__("Reject"), () =>
        promptReason(__("Reject Grade Change Clearance"), __("Rejection Reason"), (reason) =>
          callAndReload(frm, METHODS.reject, { clearance: frm.doc.name, rejection_reason: reason })), __("Production Head"));
    }
    if (status === "Reset Approval Required") {
      frm.add_custom_button(__("Approve Reset"), () =>
        callAndReload(frm, METHODS.approveReset, { clearance: frm.doc.name }), __("Production Head"));
      frm.add_custom_button(__("Reject Reset"), () =>
        promptReason(__("Reject Reset Request"), __("Reset Rejection Reason"), (reason) =>
          callAndReload(frm, METHODS.rejectReset, { clearance: frm.doc.name, rejection_reason: reason })), __("Production Head"));
    }
  };

  frappe.ui.form.on("Work Order", { refresh: setupSourceDocument });
  frappe.ui.form.on("Job Card", {
    refresh(frm) {
      setupSourceDocument(frm);
      applyJobCardUi(frm);
    },
  });
  frappe.ui.form.on("Grade Change Clearance", {
    refresh: setupClearance,
    cotton_cloth_available(frm) {
      frm.toggle_reqd("cotton_cloth_observation", frm.doc.cotton_cloth_available === "No");
    },
  });

  calco_erp.grade_change_control.open_or_create = openOrCreateClearance;
  calco_erp.grade_change_control.apply_job_card_ui = applyJobCardUi;
})();
