frappe.ui.form.on("Bulk Density Monitor", {
  refresh(frm) {
    frm.disable_save();
    collapseContext(frm);
    renderReadings(frm);
    if (frm.is_new()) {
      frm.dashboard.set_headline_alert(
        __("Create Bulk Density Monitor from a running Compounding / Extrusion Job Card cockpit."),
        "orange"
      );
      return;
    }
    addActions(frm);
  },
});

const METHODS = "calco_erp.calco_production.bulk_density_monitoring";

function isFrozen(frm) {
  return ["Completed", "Superseded"].includes(frm.doc.status);
}

function canConfirm() {
  return ["Production Engineer", "Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function canCorrectParent() {
  return ["Production Head", "Manufacturing Manager", "System Manager"]
    .some((role) => frappe.user.has_role(role));
}

function collapseContext(frm) {
  if (frm.is_new()) return;
  ["document_control_section", "context_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function esc(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function number(value) {
  return flt(value).toFixed(3);
}

function correctionTargets(frm) {
  return new Set((frm.doc.readings || []).map((row) => row.correction_of_reading).filter(Boolean));
}

function renderReadings(frm) {
  const wrapper = frm.fields_dict.readings_html?.$wrapper;
  if (!wrapper) return;
  const corrected = correctionTargets(frm);
  const rows = (frm.doc.readings || []).map((row, index) => {
    const isCorrected = corrected.has(row.name);
    let confirmation;
    if (isCorrected) {
      confirmation = `<span class="indicator-pill gray">${__("Corrected")}</span>`;
    } else if (row.confirmed) {
      confirmation = `<span class="indicator-pill green">${__("Confirmed")}</span><div class="text-muted small">${esc(row.confirmed_by)}<br>${esc(frappe.datetime.str_to_user(row.confirmed_on))}</div>`;
    } else if (canConfirm() && !isFrozen(frm)) {
      confirmation = `<button class="btn btn-xs btn-default calco-bd-confirm" data-reading="${esc(row.name)}">${__("Confirm Reading")}</button>`;
    } else {
      confirmation = `<span class="indicator-pill orange">${__("Pending Confirmation")}</span>`;
    }
    const correct = !isCorrected && canConfirm() && frm.doc.status === "Active"
      ? `<button class="btn btn-xs btn-link calco-bd-correct" data-reading="${esc(row.name)}">${__("Correct")}</button>`
      : "";
    return `<tr class="${isCorrected ? "text-muted" : ""}">
      <td>${index + 1}</td><td>${esc(frappe.datetime.str_to_user(row.event_datetime))}</td>
      <td><strong>${esc(row.rm_item)}</strong><div class="text-muted small">${esc(row.rm_item_name)}</div></td>
      <td>${esc(row.rm_batch)}</td><td>${esc(row.feeder_no)}</td>
      <td class="is-number">${number(row.w1_kg)}</td><td class="is-number">${number(row.w2_kg)}</td>
      <td class="is-number"><strong>${number(row.bulk_density_kg_l)}</strong></td>
      <td>${confirmation}${correct}</td><td>${esc(row.observation || "-")}</td>
    </tr>`;
  }).join("");
  wrapper.html(`<div class="table-responsive calco-bd-wrap">
    <table class="table table-bordered calco-bd-table">
      <thead><tr><th>${__("Sr.")}</th><th>${__("Time")}</th><th>${__("RM")}</th><th>${__("Batch")}</th>
      <th>${__("Feeder")}</th><th>${__("W1 (Kg)")}</th><th>${__("W2 (Kg)")}</th>
      <th>${__("Bulk Density (Kg/Ltr)")}</th><th>${__("Confirmed")}</th><th>${__("Observation")}</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="10" class="text-muted text-center">${__("No Bulk Density readings recorded.")}</td></tr>`}</tbody>
    </table>
  </div>`);
  wrapper.find(".calco-bd-confirm").on("click", async function () {
    await runAction(frm, "confirm_bulk_density_reading", {reading: $(this).data("reading")});
  });
  wrapper.find(".calco-bd-correct").on("click", function () {
    showReadingDialog(frm, (frm.doc.readings || []).find((row) => row.name === $(this).data("reading")));
  });
  ensureStyles();
}

function addActions(frm) {
  if (["Draft", "Active"].includes(frm.doc.status)) {
    frm.add_custom_button(__("Add Bulk Density Reading"), () => showReadingDialog(frm));
    if (canConfirm()) {
      frm.add_custom_button(__("Complete Bulk Density Monitor"), () => runAction(frm, "complete_bulk_density_monitor"));
    }
  }
  if (frm.doc.status === "Completed" && canCorrectParent()) {
    frm.add_custom_button(__("Create Correction"), () => createCorrection(frm));
  }
}

function showReadingDialog(frm, original = null) {
  const correcting = Boolean(original);
  const dialog = new frappe.ui.Dialog({
    title: correcting ? __("Correct Bulk Density Reading") : __("Add Bulk Density Reading"),
    fields: [
      {fieldname:"rm_item", fieldtype:"Link", options:"Item", label:__("RM Item"), reqd:1, default:original?.rm_item || ""},
      {fieldname:"rm_batch", fieldtype:"Link", options:"Batch", label:__("RM Batch"), reqd:1, default:original?.rm_batch || ""},
      {fieldname:"feeder_no", fieldtype:"Select", options:"1\n2\n3\n4\n5\n6", label:__("Feeder No."), reqd:1, default:original?.feeder_no || ""},
      {fieldname:"w1_kg", fieldtype:"Float", label:__("Empty Weight of Equipment W1 (Kg)"), reqd:1, default:original?.w1_kg},
      {fieldname:"w2_kg", fieldtype:"Float", label:__("Filled Equipment + Material W2 (Kg)"), reqd:1, default:original?.w2_kg},
      {fieldname:"bulk_density_kg_l", fieldtype:"Float", label:__("Bulk Density (Kg/Ltr)"), reqd:1, default:original?.bulk_density_kg_l},
      {fieldname:"observation", fieldtype:"Small Text", label:__("Observation"), default:original?.observation || ""},
      ...(correcting ? [{fieldname:"reason", fieldtype:"Small Text", label:__("Reading Correction Reason"), reqd:1}] : []),
    ],
    primary_action_label: correcting ? __("Record Correction") : __("Record Reading"),
    async primary_action(values) {
      const method = correcting ? "correct_bulk_density_reading" : "add_bulk_density_reading";
      await frappe.call({
        method:`${METHODS}.${method}`,
        args:{name:frm.doc.name, ...(correcting ? {reading:original.name} : {}), ...values},
        freeze:true,
        freeze_message:__("Recording controlled Bulk Density evidence..."),
      });
      dialog.hide();
      await frm.reload_doc();
    },
  });
  dialog.set_query("rm_item", () => ({
    query:`${METHODS}.bulk_density_item_query`,
    filters:{work_order:frm.doc.work_order},
  }));
  dialog.set_query("rm_batch", () => ({
    query:`${METHODS}.bulk_density_batch_query`,
    filters:{work_order:frm.doc.work_order, item_code:dialog.get_value("rm_item")},
  }));
  dialog.fields_dict.rm_item.$input.on("change", () => {
    if (!correcting || dialog.get_value("rm_item") !== original.rm_item) dialog.set_value("rm_batch", "");
  });
  dialog.show();
}

async function runAction(frm, method, extra = {}) {
  await frappe.call({method:`${METHODS}.${method}`, args:{name:frm.doc.name, ...extra}, freeze:true});
  await frm.reload_doc();
}

function createCorrection(frm) {
  frappe.prompt(
    {fieldname:"reason", fieldtype:"Small Text", label:__("Correction Reason"), reqd:1},
    async (values) => {
      const response = await frappe.call({
        method:`${METHODS}.make_bulk_density_correction`,
        args:{name:frm.doc.name, reason:values.reason},
        freeze:true,
      });
      frappe.set_route("Form", "Bulk Density Monitor", response.message.name);
    },
    __("Create Controlled Correction"),
    __("Create")
  );
}

function ensureStyles() {
  if (document.getElementById("calco-bulk-density-style")) return;
  $("<style>", {id:"calco-bulk-density-style", text:`
    .calco-bd-table { min-width:1180px; table-layout:fixed; font-size:12px; line-height:1.3; }
    .calco-bd-table th, .calco-bd-table td { padding:5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
    .calco-bd-table th { background:var(--subtle-fg); }
    .calco-bd-table th:nth-child(1), .calco-bd-table td:nth-child(1) { width:4%; text-align:center; }
    .calco-bd-table th:nth-child(2), .calco-bd-table td:nth-child(2) { width:12%; }
    .calco-bd-table th:nth-child(3), .calco-bd-table td:nth-child(3) { width:14%; }
    .calco-bd-table th:nth-child(4), .calco-bd-table td:nth-child(4) { width:13%; }
    .calco-bd-table th:nth-child(5), .calco-bd-table td:nth-child(5) { width:6%; text-align:center; }
    .calco-bd-table th:nth-child(6), .calco-bd-table td:nth-child(6),
    .calco-bd-table th:nth-child(7), .calco-bd-table td:nth-child(7) { width:8%; }
    .calco-bd-table th:nth-child(8), .calco-bd-table td:nth-child(8) { width:11%; }
    .calco-bd-table th:nth-child(9), .calco-bd-table td:nth-child(9) { width:12%; }
    .calco-bd-table th:nth-child(10), .calco-bd-table td:nth-child(10) { width:12%; }
    .calco-bd-table .is-number { text-align:right; font-variant-numeric:tabular-nums; }
  `}).appendTo(document.head);
}
