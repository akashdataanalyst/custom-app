frappe.ui.form.on("Process Parameter Monitor", {
  async refresh(frm) {
    frm.disable_save();
    collapseContext(frm);
    renderSnapshot(frm);
    if (frm.is_new()) {
      frm.dashboard.set_headline_alert(
        __("Create Process Parameter Monitor from a running Compounding / Extrusion Job Card cockpit."),
        "orange"
      );
      return;
    }
    await loadObservationView(frm);
    addActions(frm);
  },
});

const PROCESS_METHODS = "calco_erp.calco_production.process_parameter_monitoring";

function esc(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function canReview() {
  return ["Production Engineer", "Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function canCorrectParent() {
  return ["Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function collapseContext(frm) {
  ["document_control_section", "context_section", "snapshot_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function groupedSnapshot(frm) {
  const groups = new Map();
  (frm.doc.parameter_snapshot || []).forEach((row) => {
    if (!groups.has(row.parameter_group)) groups.set(row.parameter_group, []);
    groups.get(row.parameter_group).push(row);
  });
  return groups;
}

function renderSnapshot(frm) {
  const wrapper = frm.fields_dict.snapshot_html?.$wrapper;
  if (!wrapper || frm.is_new()) return;
  const sections = [...groupedSnapshot(frm)].map(([group, rows]) => `
    <section class="calco-ppm-spec-group">
      <div class="calco-ppm-group-title">${esc(group)}</div>
      <div class="table-responsive"><table class="table table-bordered calco-ppm-table">
        <thead><tr><th>${__("Parameter")}</th><th>${__("Specification")}</th><th>${__("Unit")}</th><th>${__("Applicability")}</th><th>${__("Source")}</th></tr></thead>
        <tbody>${rows.map((row) => `<tr>
          <td><strong>${esc(row.display_label)}</strong></td>
          <td>${esc(row.specification_text || "Blank")}</td><td>${esc(row.unit || "-")}</td>
          <td>${esc(row.applicability)}</td>
          <td>${esc(row.exact_source_header || "Missing Source")}<div class="text-muted small">${esc(row.mapping_status)}</div></td>
        </tr>`).join("")}</tbody>
      </table></div>
    </section>`).join("");
  wrapper.html(sections);
  ensureProcessStyles();
}

async function loadObservationView(frm) {
  const response = await frappe.call({
    method: `${PROCESS_METHODS}.get_process_parameter_monitor_view`,
    args: {name: frm.doc.name},
  });
  frm.__process_parameter_view = response.message || {observations: []};
  renderObservations(frm);
}

function renderObservations(frm) {
  const wrapper = frm.fields_dict.observations_html?.$wrapper;
  if (!wrapper) return;
  const observations = (frm.__process_parameter_view || {}).observations || [];
  const cards = observations.map((observation) => {
    const superseded = observation.status === "Superseded";
    const rows = (observation.values || []).map((row) => {
      const indicator = row.comparison_result === "Outside" ? "red" : row.comparison_result === "Within" ? "green" : "gray";
      const acknowledgement = row.comparison_result === "Outside"
        ? row.exception_acknowledged
          ? `<div class="text-muted small">${__("Acknowledged by")} ${esc(row.acknowledged_by)}<br>${esc(row.acknowledged_on ? frappe.datetime.str_to_user(row.acknowledged_on) : "")}</div>`
          : canReview() && !superseded && !["Completed", "Superseded"].includes(frm.doc.status)
            ? `<button class="btn btn-xs btn-default calco-ppm-ack" data-observation="${esc(observation.name)}" data-value="${esc(row.name)}">${__("Acknowledge")}</button>`
            : `<span class="indicator-pill orange">${__("Pending Acknowledgement")}</span>`
        : "";
      return `<tr>
        <td>${esc(row.display_label)}</td><td>${esc(row.specification_text || "Blank")}</td>
        <td><strong>${esc(row.actual_value || "-")}</strong> ${esc(row.unit || "")}</td>
        <td><span class="indicator-pill ${indicator}">${esc(row.comparison_result)}</span></td>
        <td>${esc(row.exception_reason || "-")}${acknowledgement}</td>
      </tr>`;
    }).join("");
    const correct = !superseded && frm.doc.status === "Active" && canReview()
      ? `<button class="btn btn-xs btn-link calco-ppm-correct-observation" data-observation="${esc(observation.name)}">${__("Correct Observation")}</button>`
      : "";
    return `<section class="calco-ppm-observation ${superseded ? "text-muted" : ""}">
      <div class="calco-ppm-observation-head">
        <div><strong>${esc(observation.observation_number)}</strong> <span class="indicator-pill ${superseded ? "gray" : "green"}">${esc(observation.status)}</span>
          <div class="text-muted small">${esc(frappe.datetime.str_to_user(observation.observation_time))} · ${esc(observation.shift || "No shift")} · ${esc(observation.recorded_by)}</div>
        </div>${correct}
      </div>
      <div class="table-responsive"><table class="table table-bordered calco-ppm-table">
        <thead><tr><th>${__("Parameter")}</th><th>${__("Specification")}</th><th>${__("Actual")}</th><th>${__("Result")}</th><th>${__("Exception / Review")}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </section>`;
  }).join("");
  wrapper.html(cards || `<div class="text-muted text-center calco-ppm-empty">${__("No Process Observations recorded.")}</div>`);
  wrapper.find(".calco-ppm-ack").on("click", async function () {
    await frappe.call({
      method: `${PROCESS_METHODS}.acknowledge_process_exception`,
      args: {observation: $(this).data("observation"), value: $(this).data("value")},
      freeze: true,
    });
    await loadObservationView(frm);
  });
  wrapper.find(".calco-ppm-correct-observation").on("click", function () {
    const observation = observations.find((row) => row.name === $(this).data("observation"));
    showObservationDialog(frm, observation);
  });
  ensureProcessStyles();
}

function addActions(frm) {
  if (["Draft", "Active"].includes(frm.doc.status)) {
    frm.add_custom_button(__("Add Process Observation"), () => showObservationDialog(frm));
    if (canReview()) {
      frm.add_custom_button(__("Complete Process Parameter Monitor"), async () => {
        await frappe.call({method: `${PROCESS_METHODS}.complete_process_parameter_monitor`, args: {name: frm.doc.name}, freeze: true});
        await frm.reload_doc();
      });
    }
  }
  if (frm.doc.status === "Completed" && canCorrectParent()) {
    frm.add_custom_button(__("Create Correction"), () => {
      frappe.prompt(
        {fieldname: "reason", fieldtype: "Small Text", label: __("Correction Reason"), reqd: 1},
        async (values) => {
          const response = await frappe.call({method: `${PROCESS_METHODS}.make_process_parameter_correction`, args: {name: frm.doc.name, reason: values.reason}, freeze: true});
          frappe.set_route("Form", "Process Parameter Monitor", response.message.name);
        },
        __("Create Controlled Correction"),
        __("Create")
      );
    });
  }
}

function inputField(snapshot, defaultValue) {
  const base = {
    fieldname: `parameter_${snapshot.sequence}`,
    label: `${snapshot.display_label}${snapshot.unit ? ` (${snapshot.unit})` : ""}`,
    description: `${__("MPDS Specification")}: ${snapshot.specification_text || __("Blank - actual observation permitted")}`,
    reqd: snapshot.applicability !== "Not Applicable",
    default: defaultValue || "",
  };
  if (snapshot.applicability === "Not Applicable") return {...base, fieldtype: "Data", read_only: 1, default: __("Not Applicable"), reqd: 0};
  if (snapshot.input_type === "Numeric") return {...base, fieldtype: "Float"};
  if (snapshot.input_type === "Status") return {...base, fieldtype: "Select", options: snapshot.status_options || "OK\nNot OK"};
  return {...base, fieldtype: "Data"};
}

function observationDefaults(observation) {
  const result = {};
  (observation?.values || []).forEach((row) => { result[row.parameter_key] = row.actual_value; });
  return result;
}

function showObservationDialog(frm, original = null) {
  const defaults = observationDefaults(original);
  const fields = [];
  [...groupedSnapshot(frm)].forEach(([group, rows]) => {
    fields.push({fieldname: `section_${rows[0].group_sequence}`, fieldtype: "Section Break", label: group});
    rows.forEach((row) => fields.push(inputField(row, defaults[row.parameter_key])));
  });
  if (original) fields.push({fieldname: "correction_reason", fieldtype: "Small Text", label: __("Correction Reason"), reqd: 1});
  const dialog = new frappe.ui.Dialog({
    title: original ? `${__("Correct")} ${original.observation_number}` : __("Add Process Observation"),
    size: "extra-large",
    fields,
    primary_action_label: original ? __("Record Correction") : __("Review Observation"),
    async primary_action(values) {
      const payload = (frm.doc.parameter_snapshot || [])
        .filter((row) => row.applicability !== "Not Applicable")
        .map((row) => ({parameter_key: row.parameter_key, actual_value: values[`parameter_${row.sequence}`]}));
      const preview = await frappe.call({method: `${PROCESS_METHODS}.preview_process_observation`, args: {name: frm.doc.name, values: payload}});
      const outside = (preview.message || {}).outside || [];
      const save = async (reasons = {}) => {
        payload.forEach((row) => { row.exception_reason = reasons[row.parameter_key] || ""; });
        const method = original ? "correct_process_observation" : "add_process_observation";
        await frappe.call({
          method: `${PROCESS_METHODS}.${method}`,
          args: original
            ? {observation: original.name, reason: values.correction_reason, values: payload}
            : {name: frm.doc.name, values: payload},
          freeze: true,
          freeze_message: __("Recording controlled Process Parameter evidence..."),
        });
        dialog.hide();
        await frm.reload_doc();
      };
      if (!outside.length) return save();
      const reasonDialog = new frappe.ui.Dialog({
        title: __("Outside-Specification Reasons"),
        fields: outside.map((row, index) => ({
          fieldname: `reason_${index}`,
          fieldtype: "Small Text",
          label: `${row.display_label}: ${row.actual_value} (${__("Specification")}: ${row.specification_text})`,
          reqd: 1,
        })),
        primary_action_label: __("Record Complete Observation"),
        async primary_action(reasonValues) {
          const reasons = {};
          outside.forEach((row, index) => { reasons[row.parameter_key] = reasonValues[`reason_${index}`]; });
          reasonDialog.hide();
          await save(reasons);
        },
      });
      reasonDialog.show();
    },
  });
  dialog.show();
}

function ensureProcessStyles() {
  if (document.getElementById("calco-process-parameter-style")) return;
  $("<style>", {id: "calco-process-parameter-style", text: `
    .calco-ppm-spec-group, .calco-ppm-observation { margin-bottom:14px; }
    .calco-ppm-group-title { font-size:12px; font-weight:700; text-transform:uppercase; margin:0 0 6px; }
    .calco-ppm-table { min-width:900px; table-layout:fixed; font-size:12px; line-height:1.3; }
    .calco-ppm-table th, .calco-ppm-table td { padding:5px 7px !important; vertical-align:top; white-space:normal; overflow-wrap:anywhere; }
    .calco-ppm-table th { background:var(--subtle-fg); }
    .calco-ppm-observation-head { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px; }
    .calco-ppm-empty { padding:20px; border:1px dashed var(--border-color); }
  `}).appendTo(document.head);
}
