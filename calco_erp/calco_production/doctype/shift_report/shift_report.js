const SHIFT_REPORT_METHODS = "calco_erp.calco_production.shift_reporting";
const SHIFT_REPORT_PARENT_EVIDENCE_FIELDS = [
  "spy_qty", "tpy_qty", "metal_separator_qty",
  "lab_samples", "other_new_output", "other_output_reason", "repacked_qty", "repacking_source",
  "line_operator", "mixer_operator", "packing_operator", "lineman", "number_of_casual",
  "bag_no_from", "bag_no_to", "total_no_of_bags", "quantity", "loose_quantity", "others",
  "total_quantity", "cumulative_production", "start_up", "lumps", "floor_sweeping",
  "ud_generated", "online_ud_used", "balance_ud", "shift_remarks", "handover_remarks",
  "pending_issues", "next_shift_instructions", "special_observations",
];
const SHIFT_REPORT_PENDING_INPUT_TYPES = new Set(["Data", "Int", "Float", "Small Text"]);

frappe.ui.form.on("Shift Report", {
  refresh(frm) {
    collapseShiftReportContext(frm);
    applyShiftReportFrozenState(frm);
    bindShiftReportParentEvidence(frm);
    renderShiftFeederSettings(frm);
    renderShiftDowntime(frm);
    addShiftReportActions(frm);
    refreshShiftProductionOutput(frm);
    ensureShiftReportStyles();
    if (frm.is_new()) {
      frm.disable_save();
      frm.dashboard.set_headline_alert(
        __("Create Shift Report from a running Compounding / Extrusion Job Card cockpit."),
        "orange"
      );
    }
  },
  before_save(frm) {
    return flushShiftReportPendingParentEvidence(frm);
  },
});

function shiftReportCanEngineerSign() {
  return ["Production Engineer", "Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function shiftReportCanSeniorSign() {
  return ["Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function collapseShiftReportContext(frm) {
  if (frm.is_new()) return;
  ["document_control_section", "context_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function applyShiftReportFrozenState(frm) {
  const frozen = ["Completed", "Superseded", "Abandoned"].includes(frm.doc.status);
  SHIFT_REPORT_PARENT_EVIDENCE_FIELDS.forEach(
    (fieldname) => frm.set_df_property(fieldname, "read_only", frozen ? 1 : 0)
  );
}

function bindShiftReportParentEvidence(frm) {
  SHIFT_REPORT_PARENT_EVIDENCE_FIELDS.forEach((fieldname) => {
    const control = frm.fields_dict[fieldname];
    if (
      !control?.$input ||
      !SHIFT_REPORT_PENDING_INPUT_TYPES.has(control.df.fieldtype)
    ) return;

    control.$input
      .off(".calcoShiftEvidence")
      .on("input.calcoShiftEvidence", () => frm.dirty());
  });
}

async function flushShiftReportPendingParentEvidence(frm) {
  await flushUniqueOutputRow(frm);
  for (const fieldname of SHIFT_REPORT_PARENT_EVIDENCE_FIELDS) {
    if (frm.__controlled_output?.unique_mode && ["quantity","spy_qty","tpy_qty","lab_samples","loose_quantity","metal_separator_qty"].includes(fieldname)) continue;
    const control = frm.fields_dict[fieldname];
    if (
      !control?.$input ||
      !SHIFT_REPORT_PENDING_INPUT_TYPES.has(control.df.fieldtype) ||
      typeof control.get_input_value !== "function" ||
      typeof control.parse_validate_and_set_in_model !== "function"
    ) continue;

    await control.parse_validate_and_set_in_model(control.get_input_value());
  }
}

function shiftReportEsc(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function shiftReportNumber(value, precision = 3) {
  return flt(value || 0).toFixed(precision);
}

function renderShiftFeederSettings(frm) {
  const wrapper = frm.fields_dict.feeder_settings_html?.$wrapper;
  if (!wrapper) return;
  const rows = (frm.doc.feeder_settings || []).map((row) => `<tr>
    <td>${shiftReportEsc(row.sequence)}</td>
    <td>${shiftReportEsc(frappe.datetime.str_to_user(row.event_time))}</td>
    ${[1, 2, 3, 4, 5, 6].map((feeder) => `<td>${shiftReportNumber(row[`f${feeder}_set_percent`])}</td><td>${shiftReportNumber(row[`f${feeder}_actual_kg`])}</td>`).join("")}
    <td>${shiftReportNumber(row.set_throughput_kg_hr)}</td>
    <td>${shiftReportNumber(row.actual_throughput_kg_hr)}</td>
    <td>${shiftReportEsc(row.observation || "-")}</td>
    <td>${shiftReportEsc(row.entered_by)}<br><span class="text-muted">${shiftReportEsc(frappe.datetime.str_to_user(row.entered_on))}</span></td>
  </tr>`).join("");
  const feederHeaders = [1, 2, 3, 4, 5, 6]
    .map((feeder) => `<th colspan="2">${__("Feeder {0}", [feeder])}</th>`).join("");
  const valueHeaders = [1, 2, 3, 4, 5, 6]
    .map(() => `<th>${__("Set %")}</th><th>${__("Actual Kg")}</th>`).join("");
  wrapper.html(`<div class="table-responsive calco-shift-table-wrap">
    <table class="table table-bordered calco-shift-table calco-shift-feeder-table">
      <thead><tr><th rowspan="2">${__("Sr.")}</th><th rowspan="2">${__("Time")}</th>${feederHeaders}
      <th rowspan="2">${__("Set Throughput kg/hr")}</th><th rowspan="2">${__("Actual Throughput kg/hr")}</th>
      <th rowspan="2">${__("Observation")}</th><th rowspan="2">${__("Audit")}</th></tr>
      <tr>${valueHeaders}</tr></thead>
      <tbody>${rows || `<tr><td colspan="18" class="text-muted text-center">${__("No Extruder HMI feeder settings recorded.")}</td></tr>`}</tbody>
    </table>
  </div>`);
}

function renderShiftDowntime(frm) {
  const wrapper = frm.fields_dict.downtime_events_html?.$wrapper;
  if (!wrapper) return;
  const labels = {P:"Process", Q:"Quality", M:"Maintenance", S:"Set Up", C:"Commercial", GC:"Grade Change", E:"External"};
  const rows = (frm.doc.downtime_events || []).map((row) => `<tr>
    <td>${shiftReportEsc(row.sequence)}</td><td>${shiftReportEsc(frappe.datetime.str_to_user(row.stop_time))}</td>
    <td>${shiftReportEsc(frappe.datetime.str_to_user(row.start_time))}</td>
    <td class="is-number">${shiftReportNumber(row.total_time_minutes)}</td>
    <td><strong>${shiftReportEsc(row.code)}</strong><br><span class="text-muted">${shiftReportEsc(labels[row.code] || "")}</span></td>
    <td>${shiftReportEsc(row.description || "-")}</td>
    <td>${shiftReportEsc(row.recorded_by)}<br><span class="text-muted">${shiftReportEsc(frappe.datetime.str_to_user(row.recorded_on))}</span></td>
  </tr>`).join("");
  wrapper.html(`<div class="table-responsive calco-shift-table-wrap">
    <table class="table table-bordered calco-shift-table calco-shift-downtime-table">
      <thead><tr><th>${__("Sr.")}</th><th>${__("Stop Time")}</th><th>${__("Start Time")}</th>
      <th>${__("Total Time (Minutes)")}</th><th>${__("Code")}</th><th>${__("Description")}</th><th>${__("Audit")}</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7" class="text-muted text-center">${__("No downtime events recorded.")}</td></tr>`}</tbody>
    </table>
  </div>
  <div class="small text-muted calco-shift-code-legend">${__("P = Process | Q = Quality | M = Maintenance | S = Set Up | C = Commercial | GC = Grade Change | E = External")}</div>`);
}

function addShiftReportActions(frm) {
  if (!frm.is_new() && frm.doc.correction_of && ["Draft","Active"].includes(frm.doc.status) &&
      ["Production Head","Manufacturing Manager"].some(role => frappe.user.has_role(role))) {
    frm.add_custom_button(__("Abandon Pending Correction"), () => {
      if (frm.is_dirty()) { frappe.msgprint(__("Save or reload pending changes before abandonment.")); return; }
      frappe.prompt([{fieldname:"reason",fieldtype:"Small Text",label:__("Abandonment Reason"),reqd:1}], values =>
        frappe.call({method:"calco_erp.calco_production.shift_correction_abandonment.abandon_pending_correction",
          args:{name:frm.doc.name,reason:values.reason},freeze:true}).then(() => frm.reload_doc()),
        __("Abandon Pending Correction"), __("Abandon"));
    });
  }
  if (frm.is_new()) return;
  if (["Draft", "Active"].includes(frm.doc.status)) {
    frm.add_custom_button(__("Add Feeder Setting"), () => showShiftFeederDialog(frm));
    frm.add_custom_button(__("Add Downtime Event"), () => showShiftDowntimeDialog(frm));
    if (!frm.doc.engineer_signed_by && shiftReportCanEngineerSign()) {
      frm.add_custom_button(__("Engineer Sign-off"), () => runShiftReportAction(frm, "sign_shift_report_engineer"));
    }
    if (frm.doc.engineer_signed_by && shiftReportCanSeniorSign()) {
      frm.add_custom_button(__("Sr. Engineer Complete"), () => runShiftReportAction(frm, "complete_shift_report"));
    }
  }
  if (frm.doc.status === "Completed" && shiftReportCanSeniorSign()) {
    frm.add_custom_button(__("Create Correction"), () => createShiftReportCorrection(frm));
  }
}

function feederDialogFields() {
  const fields = [];
  [1, 2, 3, 4, 5, 6].forEach((feeder) => {
    fields.push({fieldname:`feeder_${feeder}_section`, fieldtype:"Section Break", label:__("Feeder {0}", [feeder])});
    fields.push({fieldname:`f${feeder}_set_percent`, fieldtype:"Float", label:__("Set %"), reqd:1});
    fields.push({fieldname:`f${feeder}_actual_kg`, fieldtype:"Float", label:__("Actual Kg"), reqd:1});
  });
  fields.push({fieldname:"throughput_section", fieldtype:"Section Break", label:__("Throughput")});
  fields.push({fieldname:"set_throughput_kg_hr", fieldtype:"Float", label:__("Set Throughput (kg/hr)"), reqd:1});
  fields.push({fieldname:"actual_throughput_kg_hr", fieldtype:"Float", label:__("Actual Throughput (kg/hr)"), reqd:1});
  fields.push({fieldname:"observation", fieldtype:"Small Text", label:__("Observation / Remarks")});
  return fields;
}

function showShiftFeederDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Feeder Setting - Extruder HMI"),
    size: "large",
    fields: [
      {fieldname:"event_time", fieldtype:"Datetime", label:__("Event Time"), reqd:1},
      {fieldname:"event_note", fieldtype:"HTML", options:`<div class="text-muted">${__("Enter the operational event time shown by the paper/HMI record. Entered By and Entered On are captured automatically. Feeder Run data is not copied.")}</div>`},
      ...feederDialogFields(),
    ],
    primary_action_label: __("Record Setting"),
    async primary_action(values) {
      await frappe.call({
        method:`${SHIFT_REPORT_METHODS}.add_shift_feeder_setting`,
        args:{name:frm.doc.name, ...values},
        freeze:true,
        freeze_message:__("Recording controlled Extruder HMI evidence..."),
      });
      dialog.hide();
      await frm.reload_doc();
    },
  });
  dialog.show();
}

function showShiftDowntimeDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Downtime Event"),
    fields: [
      {fieldname:"stop_time", fieldtype:"Datetime", label:__("Stop Time"), reqd:1},
      {fieldname:"start_time", fieldtype:"Datetime", label:__("Start Time"), reqd:1},
      {fieldname:"code", fieldtype:"Select", label:__("Code"), options:"P\nQ\nM\nS\nC\nGC\nE", reqd:1},
      {fieldname:"description", fieldtype:"Small Text", label:__("Description")},
      {fieldname:"duration_preview", fieldtype:"HTML"},
    ],
    primary_action_label: __("Record Downtime"),
    async primary_action(values) {
      await frappe.call({
        method:`${SHIFT_REPORT_METHODS}.add_shift_downtime_event`,
        args:{name:frm.doc.name, ...values},
        freeze:true,
        freeze_message:__("Recording controlled downtime evidence..."),
      });
      dialog.hide();
      await frm.reload_doc();
    },
  });
  const updatePreview = () => {
    const start = dialog.get_value("stop_time");
    const end = dialog.get_value("start_time");
    let message = __("Total Time will be calculated by the server.");
    if (start && end) {
      const minutes = moment(end).diff(moment(start), "minutes", true);
      if (minutes > 0) message = __("Calculated Total Time: {0} minutes", [minutes.toFixed(3)]);
    }
    dialog.fields_dict.duration_preview.$wrapper.html(`<div class="text-muted">${shiftReportEsc(message)}</div>`);
  };
  dialog.fields_dict.stop_time.$input.on("change", updatePreview);
  dialog.fields_dict.start_time.$input.on("change", updatePreview);
  updatePreview();
  dialog.show();
}

async function runShiftReportAction(frm, method) {
  await flushShiftReportPendingParentEvidence(frm);
  if (frm.is_dirty()) {
    await frm.save();
  }
  await frappe.call({method:`${SHIFT_REPORT_METHODS}.${method}`, args:{name:frm.doc.name}, freeze:true});
  await frm.reload_doc();
}

function createShiftReportCorrection(frm) {
  frappe.prompt(
    {fieldname:"reason", fieldtype:"Small Text", label:__("Correction Reason"), reqd:1},
    async (values) => {
      const response = await frappe.call({
        method:`${SHIFT_REPORT_METHODS}.make_shift_report_correction`,
        args:{name:frm.doc.name, reason:values.reason},
        freeze:true,
      });
      frappe.set_route("Form", "Shift Report", response.message.name);
    },
    __("Create Controlled Correction"),
    __("Create")
  );
}

function ensureShiftReportStyles() {
  if (document.getElementById("calco-shift-report-style")) return;
  $("<style>", {id:"calco-shift-report-style", text:`
    .calco-shift-table-wrap { border:1px solid var(--border-color); }
    .calco-shift-table { margin:0; min-width:1180px; table-layout:fixed; font-size:12px; line-height:1.3; }
    .calco-shift-table th, .calco-shift-table td { padding:5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
    .calco-shift-table th { background:var(--subtle-fg); text-align:center; }
    .calco-shift-table .is-number { text-align:right; font-variant-numeric:tabular-nums; }
    .calco-shift-feeder-table th:nth-child(1), .calco-shift-feeder-table td:nth-child(1) { width:42px; text-align:center; }
    .calco-shift-feeder-table th:nth-child(2), .calco-shift-feeder-table td:nth-child(2) { width:130px; }
    .calco-shift-downtime-table { min-width:860px; }
    .calco-shift-code-legend { padding:6px 2px; }
  `}).appendTo(document.head);
}

async function refreshShiftProductionOutput(frm) {
  if (frm.is_new()) return;
  const response = await frappe.call({method:"calco_erp.calco_production.shift_production_output.get_shift_output", args:{shift_report:frm.doc.name}});
  const output = response.message || {};
  frm.__controlled_output = output;
  applyShiftSnapshotFields(frm, output);
  frm.toggle_display("cumulative_production", !output.enabled);
  const wrapper = frm.fields_dict.output_summary?.$wrapper;
  if (!wrapper) return;
  if (!output.enabled) { wrapper.empty(); return; }
  frm.set_df_property("cumulative_production", "read_only", 1);
  const rows = output.readings || [];
  const render = rows => rows.map(row => `<tr><td>${shiftReportEsc(row.sequence)}</td><td>${shiftReportEsc(row.reading_time)}</td><td>${shiftReportNumber(row.cumulative_qty)} ${shiftReportEsc(row.uom)}</td><td>${shiftReportEsc(row.shift)}</td><td>${shiftReportEsc(row.entered_by)}</td><td>${shiftReportEsc(row.name)}${row.calculation_version ? `<details><summary>${__("Snapshot details")}</summary>${shiftReportEsc(row.calculation_version)} · ${__("Revision")}: ${shiftReportEsc(row.source_revision)} · ${__("Shift Output")}: ${shiftReportNumber(row.shift_qty)} ${shiftReportEsc(row.uom)} · ${shiftReportEsc(row.event_type)}<pre>${shiftReportEsc(row.components_json || "")}</pre></details>` : ""}</td><td>${shiftReportEsc(row.correction_of || "—")} ${shiftReportEsc(row.correction_reason || "")}${row.report_correction_of && !["Completed","Superseded","Abandoned"].includes(row.report_status) ? " (Awaiting sign-off)" : ""}</td></tr>`).join("");
  const table = entries => `<table class="table table-bordered"><thead><tr><th>#</th><th>${__("Time")}</th><th>${__("Run Cumulative Output")}</th><th>${__("Shift")}</th><th>${__("Entered By")}</th><th>${__("Reading")}</th><th>${__("Correction")}</th></tr></thead><tbody>${render(entries)}</tbody></table>`;
  if (output.unique_mode) {
    renderUniqueOutputRow(frm, output);
    wrapper.html(`<details><summary>${__("Output history")}</summary><p>${__("This shift unique output")}: ${shiftReportNumber(output.shift_qty)} ${shiftReportEsc(output.uom)}</p>${table(rows)}${rows.map(r => r.source_pool_json ? `<details><summary>${shiftReportEsc(r.name)} — ${__("Source evidence")}</summary><pre>${shiftReportEsc(r.source_pool_json)}</pre></details>` : "").join("")}</details>`);
    return;
  }
  wrapper.html(`<h4>${__("Controlled Production Output")}</h4><p><b>${shiftReportNumber(output.current_cumulative)} ${shiftReportEsc(output.uom)}</b> ${__("cumulative")} · ${__("Planned")}: ${shiftReportNumber(output.planned_qty)} · ${__("Remaining")}: ${shiftReportNumber(output.remaining_qty)} · ${__("Current Shift")}: ${shiftReportEsc(output.current_shift)} · ${__("Last Reading")}: ${shiftReportEsc(output.last_reading?.reading_time || "—")}</p>${table(rows.slice(-8))}${rows.length > 8 ? `<details><summary>${__("Full output audit")} (${rows.length})</summary>${table(rows)}</details>` : ""}`);
  if (output.snapshot_mode) {
    wrapper.prepend(`<p><b>${__("Shift Output")}: ${shiftReportNumber(output.shift_qty)} ${shiftReportEsc(output.uom)}</b> · <b>${__("Run Cumulative Output")}: ${shiftReportNumber(output.current_cumulative)} ${shiftReportEsc(output.uom)}</b></p><p>${__("Enter this-shift quantities in Packing Details and Save. Totals update after Save. No separate cumulative entry is required.")}</p>`);
  }
  if (output.can_record) frm.add_custom_button(__("Record Production Output"), () => showProductionOutputDialog(frm));
  if (output.can_correct) frm.add_custom_button(__("Correct Production Output"), () => showProductionOutputDialog(frm, true));
}

function showProductionOutputDialog(frm, correction = false) {
  const output = frm.__controlled_output || {};
  const fields = [{fieldname:"cumulative_qty", fieldtype:"Float", label:__("Run Cumulative Output") + ` (${output.uom})`, reqd:1, default:output.current_cumulative, description:__("Total output since this Job Card started, including previous shifts. Remaining production is not process loss.")}];
  if (correction) fields.push(
    {fieldname:"correction_of", fieldtype:"Select", label:__("Effective Reading to Correct"), options:(output.effective || []).map(r => r.name).join("\n"), reqd:1},
    {fieldname:"reason", fieldtype:"Small Text", label:__("Correction Reason"), reqd:1});
  frappe.prompt(fields, values => frappe.call({method:"calco_erp.calco_production.shift_production_output.record_production_output", args:{shift_report:frm.doc.name,...values}, freeze:true}).then(() => frm.reload_doc()), __(correction ? "Correct Production Output" : "Record Production Output"));
}

function applyShiftSnapshotFields(frm, output) {
  ["spy_qty","tpy_qty","metal_separator_qty","unique_cumulative_html"].forEach(f => frm.toggle_display(f, !!output.unique_mode));
  if (output.unique_mode) { applyUniqueOutputFields(frm); return; }
  const active = !!output.snapshot_mode;
  const frozen = ["Completed", "Superseded", "Abandoned"].includes(frm.doc.status);
  const extra = ["lab_samples","other_new_output","other_output_reason","repacked_qty","repacking_source","engineer_output_revision"];
  extra.forEach(f => frm.toggle_display(f, active));
  if (!active) return;
  ["quantity","loose_quantity","ud_generated","lumps","start_up","floor_sweeping","online_ud_used","balance_ud",...extra].forEach(f => frm.set_df_property(f,"read_only", frozen || f === "engineer_output_revision" ? 1 : 0));
  ["total_quantity","others","cumulative_production"].forEach(f => frm.set_df_property(f,"read_only",1));
  const labels = {total_quantity:"Shift Output (Calculated)",others:"Other New Output Total (Calculated)",quantity:"Packed Good Output - This Shift",loose_quantity:"New Loose Output - This Shift",ud_generated:"New UD Output (Excluding Lumps and Start-up Purge)",lumps:"New Lumps (Not Included in UD)",start_up:"New Start-up Purge (Not Included in UD / Lumps)",floor_sweeping:"Floor Sweeping - Waste Evidence Only",online_ud_used:"Online UD Used - Input / Reuse Only",ud_section:"Other Output and Excluded Waste / Reuse"};
  Object.entries(labels).forEach(([f,label]) => frm.set_df_property(f,"label",__(label)));
  frm.set_df_property("packing_section","description",__("Record newly produced quantities once in mutually exclusive categories. Moving loose output into bags does not create new output; use Repacked Quantity and identify its source. Never include previously reported loose stock in new packed output."));
  frm.set_df_property("other_new_output","description",__("New process output not already recorded as packed, loose, lab sample, UD, lumps or start-up purge. Explain its classification. This does not authorize FG stock."));
  frm.set_df_property("floor_sweeping","description",__("Excluded from process/QC output: recovered floor material is not evidence of new production."));
}


function applyUniqueOutputFields(frm) {
  ["quantity","spy_qty","tpy_qty","lab_samples","loose_quantity","metal_separator_qty","others","total_quantity","cumulative_production","other_new_output","other_output_reason","repacked_qty","repacking_source","engineer_output_revision","ud_section","start_up","lumps","floor_sweeping","ud_generated","online_ud_used","balance_ud"].forEach(f => frm.toggle_display(f,false));
  frm.toggle_display("unique_cumulative_html",true);
}

function renderUniqueOutputRow(frm, output) {
  const wrapper = frm.fields_dict.unique_cumulative_html?.$wrapper;
  if (!wrapper) return;
  const frozen = ["Completed","Superseded","Abandoned"].includes(frm.doc.status);
  const columns = [["quantity","FG Qty"],["spy_qty","SPY"],["tpy_qty","TPY"],["loose_quantity","LB"],["lab_samples","LS"],["metal_separator_qty","Metal Separator"]];
  wrapper.html(`<div style="overflow-x:auto"><table class="table table-bordered calco-unique-output-row" style="width:100%;min-width:740px;table-layout:fixed;margin-bottom:8px"><thead><tr style="background:#d5e2f6;color:#111"><th style="width:13%">${__("FG Code")}</th><th style="width:11%">${__("FG Batch No")}</th>${columns.map(([,label]) => `<th style="white-space:normal">${__(label)}</th>`).join("")}<th>${__("Shift Output Qty")}</th><th>${__("Run Cumulative Qty")}</th></tr></thead><tbody><tr><td style="overflow-wrap:anywhere">${shiftReportEsc(frm.doc.fg_item || "")}</td><td style="overflow-wrap:anywhere">${shiftReportEsc(frm.doc.fg_batch || "")}</td>${columns.map(([field,label]) => `<td style="padding:4px"><input type="number" step="any" min="0" data-output-field="${field}" aria-label="${label}" class="form-control" style="min-width:0;width:100%;padding:4px;text-align:right" value="${Number(frm.doc[field] || 0)}" ${frozen ? "readonly" : ""}></td>`).join("")}<td style="padding:4px"><input class="form-control" aria-label="Shift Output Qty" style="min-width:0;width:100%;padding:4px;text-align:right" readonly value="${shiftReportNumber(frm.doc.total_quantity)}"></td><td style="padding:4px"><input class="form-control" aria-label="Run Cumulative Qty" style="min-width:0;width:100%;padding:4px;text-align:right" readonly value="${shiftReportNumber(output.current_cumulative)}"></td></tr></tbody></table></div>`);
  wrapper.find("[data-output-field]").off(".uniqueOutput").on("input.uniqueOutput", () => frm.dirty());
}

async function flushUniqueOutputRow(frm) {
  if (!frm.__controlled_output?.unique_mode) return;
  const wrapper = frm.fields_dict.unique_cumulative_html?.$wrapper;
  if (!wrapper) return;
  for (const input of wrapper.find("[data-output-field]").toArray()) {
    if (input.readOnly) continue;
    const value = input.value === "" ? 0 : Number(input.value);
    if (input.validity?.badInput || !Number.isFinite(value) || value < 0) {
      frappe.throw(__("Enter a valid non-negative quantity."));
    }
    await frm.set_value(input.dataset.outputField, value);
  }
}
