frappe.ui.form.on("Blending Run", {
  refresh(frm) {
    const frozen = isFrozen(frm);
    frm.toggle_enable(["planned_minutes", "materials", "remarks"], !frozen);
    frm.toggle_enable(
      [
        "job_card", "work_order", "operation", "production_line", "machine", "fg_item",
        "fg_batch", "bom_no", "planned_job_qty", "production_date", "shift", "recorded_by",
        "recorded_on", "actual_started_on", "started_by", "completed_on", "completed_by",
        "actual_duration_minutes", "total_planned_qty", "total_actual_qty",
      ],
      false
    );
    setMaterialQueries(frm);
    renderMaterialTable(frm);
    renderPlannedTimer(frm);
    collapseSystemSections(frm);
    if (frm.is_new()) {
      frm.disable_save();
      frm.dashboard.set_headline_alert(
        __("Create Blending Run from a running Compounding / Extrusion Job Card."),
        "orange"
      );
      return;
    }
    if (frm.doc.status === "Draft") {
      frm.add_custom_button(__("Start Blending"), () => runAction(frm, "start_blending_run"));
    }
    if (frm.doc.status === "In Progress") {
      frm.add_custom_button(__("Complete Blending"), () => runAction(frm, "complete_blending_run"));
    }
    if (frm.doc.status === "Completed" && canCorrect()) {
      frm.add_custom_button(__("Create Correction"), () => createCorrection(frm));
    }
  },
  planned_minutes(frm) {
    if (!isFrozen(frm) && flt(frm.doc.planned_minutes) === 0) {
      frm.doc.planned_minutes = null;
      frm.refresh_field("planned_minutes");
    }
    renderPlannedTimer(frm);
  },
});

function isFrozen(frm) {
  return ["Completed", "Superseded"].includes(frm.doc.status);
}

function canExecute(frm) {
  return !frm.is_new() && !isFrozen(frm) && ["Draft", "In Progress"].includes(frm.doc.status);
}

function canCorrect() {
  return ["Production Head", "Manufacturing Manager", "System Manager"].some((role) =>
    frappe.user.has_role(role)
  );
}

function text(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function quantity(value) {
  return flt(value).toFixed(3);
}

function variance(row) {
  return flt(row.actual_qty) - flt(row.planned_qty);
}

function hasVariance(row) {
  return Number(flt(row.actual_qty).toFixed(9)) !== Number(flt(row.planned_qty).toFixed(9));
}

function renderMaterialTable(frm) {
  const field = frm.fields_dict.materials;
  if (!field || !field.grid) return;
  const grid = field.grid;
  const editable = canExecute(frm);
  const rows = (frm.doc.materials || []).map((row, index) => {
    const rowVariance = variance(row);
    const needsReason = hasVariance(row);
    return `<tr data-row-name="${text(row.name)}">
      <td class="calco-blending-sequence">${index + 1}</td>
      <td class="calco-blending-material"><strong>${text(row.item_code)}</strong><br><span class="text-muted">${text(row.item_name)}</span></td>
      <td>${text(row.batch_no)}</td>
      <td class="is-number">${quantity(row.wip_available_qty)} ${text(row.uom)}</td>
      <td><input class="form-control calco-blending-planned" type="number" min="0" step="any" value="${text(row.planned_qty)}" ${editable ? "" : "disabled"}></td>
      <td><input class="form-control calco-blending-actual" type="number" min="0" step="any" value="${text(row.actual_qty)}" ${editable ? "" : "disabled"}></td>
      <td class="is-number calco-blending-variance ${needsReason ? "has-variance" : ""}">${rowVariance > 0 ? "+" : ""}${quantity(rowVariance)}</td>
      <td><textarea class="form-control calco-blending-reason" rows="1" placeholder="${needsReason ? __("Required") : __("Not required")}" ${editable ? "" : "disabled"}>${text(row.observation)}</textarea></td>
      <td class="calco-blending-remove-cell">${editable ? `<button type="button" class="btn btn-xs btn-default calco-blending-remove" title="${__("Remove Material")}">${frappe.utils.icon("delete", "xs")}</button>` : ""}</td>
    </tr>`;
  }).join("");
  const table = $(`<div class="calco-blending-controlled-wrap table-responsive">
    <table class="table table-bordered calco-blending-material-table">
      <thead><tr>
        <th>${__("Sr. No.")}</th><th>${__("Material / Description")}</th><th>${__("Batch")}</th>
        <th>${__("Available WIP")}</th><th>${__("Planned Qty")}</th><th>${__("Actual Qty")}</th>
        <th>${__("Variance Qty")}</th><th>${__("Variance Reason / Observation")}</th><th></th>
      </tr></thead>
      <tbody>${rows || `<tr><td colspan="9" class="text-muted text-center">${__("No Blending materials added.")}</td></tr>`}</tbody>
    </table>
    ${editable ? `<button type="button" class="btn btn-xs btn-default calco-blending-add">${__("Add Material")}</button>` : ""}
  </div>`);
  lockStandardGrid(grid);
  grid.wrapper.find(".calco-blending-controlled-wrap").remove();
  grid.wrapper.append(table);
  bindMaterialTable(frm, table);
  ensureStyles();
}

function bindMaterialTable(frm, table) {
  const findRow = (element) => {
    const name = $(element).closest("tr").data("row-name");
    return (frm.doc.materials || []).find((row) => row.name === name);
  };
  table.on("change", ".calco-blending-planned", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.planned_qty = flt($(this).val());
    updateVariance(row, $(this).closest("tr"));
    frm.dirty();
  });
  table.on("change", ".calco-blending-actual", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.actual_qty = flt($(this).val());
    updateVariance(row, $(this).closest("tr"));
    frm.dirty();
  });
  table.on("input", ".calco-blending-reason", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.observation = $(this).val();
    resizeTextarea(this);
    frm.dirty();
  });
  table.on("click", ".calco-blending-remove", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    const index = (frm.doc.materials || []).findIndex((entry) => entry.name === row.name);
    if (index >= 0) frm.doc.materials.splice(index, 1);
    resequence(frm);
    frm.dirty();
    frm.refresh_field("materials");
    renderMaterialTable(frm);
  });
  table.on("click", ".calco-blending-add", () => showAddMaterialDialog(frm));
  table.find("textarea").each((_index, element) => resizeTextarea(element));
}

function updateVariance(row, tr) {
  row.variance_qty = variance(row);
  const changed = hasVariance(row);
  tr.find(".calco-blending-variance")
    .toggleClass("has-variance", changed)
    .text(`${row.variance_qty > 0 ? "+" : ""}${quantity(row.variance_qty)}`);
  tr.find(".calco-blending-reason").attr("placeholder", changed ? __("Required") : __("Not required"));
}

function resequence(frm) {
  (frm.doc.materials || []).forEach((row, index) => {
    row.idx = index + 1;
    row.sequence = index + 1;
  });
}

function showAddMaterialDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Blending Material"),
    fields: [
      {fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Material"), reqd: 1},
      {fieldname: "batch_no", fieldtype: "Link", options: "Batch", label: __("Batch"), reqd: 1},
      {fieldname: "planned_qty", fieldtype: "Float", label: __("Planned Qty"), reqd: 1},
      {fieldname: "actual_qty", fieldtype: "Float", label: __("Actual Qty")},
      {fieldname: "observation", fieldtype: "Small Text", label: __("Variance Reason / Observation")},
    ],
    primary_action_label: __("Add Material"),
    async primary_action(values) {
      const response = await frappe.call({
        method: "calco_erp.calco_production.wip_consumption.get_wip_consumption_preview",
        args: {work_order: frm.doc.work_order},
      });
      const evidence = ((response.message || {}).rows || []).find(
        (row) => row.item_code === values.item_code && row.batch_no === values.batch_no
      );
      if (!evidence) {
        frappe.throw(__("The selected Material and Batch is not available in WO-attributed WIP."));
      }
      const itemResponse = await frappe.db.get_value("Item", values.item_code, "item_name");
      const row = frm.add_child("materials", {
        item_code: values.item_code,
        item_name: (itemResponse.message || {}).item_name || values.item_code,
        batch_no: values.batch_no,
        uom: evidence.stock_uom,
        wip_available_qty: evidence.available_qty,
        planned_qty: values.planned_qty,
        actual_qty: values.actual_qty || 0,
        variance_qty: flt(values.actual_qty) - flt(values.planned_qty),
        observation: values.observation || "",
      });
      row.sequence = (frm.doc.materials || []).length;
      dialog.hide();
      frm.refresh_field("materials");
      renderMaterialTable(frm);
    },
  });
  dialog.set_query("item_code", () => ({
    query: "calco_erp.calco_production.blending_execution.blending_item_query",
    filters: {work_order: frm.doc.work_order},
  }));
  dialog.set_query("batch_no", () => ({
    query: "calco_erp.calco_production.blending_execution.blending_batch_query",
    filters: {work_order: frm.doc.work_order, item_code: dialog.get_value("item_code")},
  }));
  dialog.show();
}

function setMaterialQueries(frm) {
  frm.set_query("item_code", "materials", () => ({
    query: "calco_erp.calco_production.blending_execution.blending_item_query",
    filters: {work_order: frm.doc.work_order},
  }));
  frm.set_query("batch_no", "materials", (doc, cdt, cdn) => ({
    query: "calco_erp.calco_production.blending_execution.blending_batch_query",
    filters: {work_order: doc.work_order, item_code: locals[cdt][cdn].item_code},
  }));
}

function lockStandardGrid(grid) {
  grid.wrapper.addClass("calco-blending-material-grid");
  grid.wrapper.find(".form-grid").hide();
  grid.wrapper.find(
    ".grid-add-row, .grid-add-multiple-rows, .grid-remove-rows, .grid-footer, .grid-buttons, .grid-row-open, .btn-open-row, .row-check, .row-index, .grid-duplicate-row, .grid-move-row"
  ).hide();
}

function renderPlannedTimer(frm) {
  const field = frm.fields_dict.planned_minutes;
  if (!field) return;
  const unspecified = flt(frm.doc.planned_minutes) <= 0;
  frm.set_df_property(
    "planned_minutes",
    "description",
    unspecified ? __("Not Specified") : __("Planned minutes; actual timer is server-derived.")
  );
  if (isFrozen(frm) && unspecified) field.$wrapper.find(".control-value").text(__("Not Specified"));
}

function collapseSystemSections(frm) {
  if (frm.is_new()) return;
  ["document_control_section", "context_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function resizeTextarea(element) {
  element.style.height = "auto";
  element.style.height = `${Math.max(element.scrollHeight, 28)}px`;
}

async function runAction(frm, method) {
  await frappe.call({
    method: `calco_erp.calco_production.blending_execution.${method}`,
    args: {name: frm.doc.name},
    freeze: true,
  });
  await frm.reload_doc();
}

function createCorrection(frm) {
  frappe.prompt(
    {fieldname: "reason", fieldtype: "Small Text", label: __("Correction Reason"), reqd: 1},
    async (values) => {
      const response = await frappe.call({
        method: "calco_erp.calco_production.blending_execution.make_blending_correction",
        args: {name: frm.doc.name, reason: values.reason},
        freeze: true,
      });
      frappe.set_route("Form", "Blending Run", response.message.name);
    },
    __("Create Controlled Correction"),
    __("Create")
  );
}

function ensureStyles() {
  if (document.getElementById("calco-blending-controlled-style")) return;
  $("<style>", {
    id: "calco-blending-controlled-style",
    text: `
      .calco-blending-material-table { width:100%; min-width:1040px; table-layout:fixed; margin-bottom:8px; font-size:12px; line-height:1.3; }
      .calco-blending-material-table th, .calco-blending-material-table td { padding:4px 5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
      .calco-blending-material-table th { background:var(--subtle-fg); }
      .calco-blending-material-table th:nth-child(1), .calco-blending-material-table td:nth-child(1) { width:4%; text-align:center; }
      .calco-blending-material-table th:nth-child(2), .calco-blending-material-table td:nth-child(2) { width:20%; }
      .calco-blending-material-table th:nth-child(3), .calco-blending-material-table td:nth-child(3) { width:12%; }
      .calco-blending-material-table th:nth-child(4), .calco-blending-material-table td:nth-child(4) { width:10%; }
      .calco-blending-material-table th:nth-child(5), .calco-blending-material-table td:nth-child(5),
      .calco-blending-material-table th:nth-child(6), .calco-blending-material-table td:nth-child(6),
      .calco-blending-material-table th:nth-child(7), .calco-blending-material-table td:nth-child(7) { width:9%; }
      .calco-blending-material-table th:nth-child(8), .calco-blending-material-table td:nth-child(8) { width:23%; }
      .calco-blending-material-table th:nth-child(9), .calco-blending-material-table td:nth-child(9) { width:4%; text-align:center; }
      .calco-blending-material-table input, .calco-blending-material-table textarea { min-height:28px; height:28px; padding:3px 5px; font-size:12px; }
      .calco-blending-material-table textarea { resize:none; overflow-y:hidden; }
      .calco-blending-material-table .is-number { text-align:right; font-variant-numeric:tabular-nums; }
      .calco-blending-variance.has-variance { color:var(--red-600); font-weight:600; }
      .calco-blending-material-grid .form-grid { display:none !important; }
    `,
  }).appendTo(document.head);
}
