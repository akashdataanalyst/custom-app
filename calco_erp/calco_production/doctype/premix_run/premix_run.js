frappe.ui.form.on("Premix Run", {
  refresh(frm) {
    const frozen = isFrozen(frm);
    frm.toggle_enable(
      ["premix_size", "planned_minutes", "scale_calibration_due", "shift", "engineer", "materials", "confirmations", "online_changes", "remarks"],
      !frozen
    );
    frm.toggle_enable(
      ["job_card", "work_order", "operation", "production_line", "machine", "fg_item", "fg_batch", "bom_no", "planned_job_qty", "production_date", "recorded_by", "recorded_on"],
      false
    );
    setMaterialQueries(frm);
    setupOnlineChangeGrid(frm);
    renderMaterialTable(frm);
    renderConfirmationTable(frm);
    renderPlannedTimer(frm);
    collapseSystemContext(frm);
    if (frm.is_new()) {
      frm.disable_save();
      frm.dashboard.set_headline_alert(
        __("Create Premix Run from a started Compounding / Extrusion Job Card."),
        "orange"
      );
      return;
    }
    if (frm.doc.status === "Draft") {
      frm.add_custom_button(__("Start Premix"), () => runAction(frm, "start_premix_run"));
    }
    if (frm.doc.status === "In Progress") {
      frm.add_custom_button(__("Stop Premix"), () => runAction(frm, "stop_premix_run"));
    }
    if (frm.doc.status === "Completed") {
      frm.add_custom_button(__("Create Correction"), () => createCorrection(frm));
    }
  },
  premix_size(frm) {
    (frm.doc.materials || []).forEach((row) => updatePlannedQty(frm, row));
    renderMaterialTable(frm);
  },
  planned_minutes(frm) {
    if (!isFrozen(frm) && flt(frm.doc.planned_minutes) === 0) {
      frm.doc.planned_minutes = null;
      frm.refresh_field("planned_minutes");
    }
    renderPlannedTimer(frm);
  },
});

frappe.ui.form.on("Premix Run Material", {
  item_code(frm, cdt, cdn) {
    frappe.model.set_value(cdt, cdn, "batch_no", "");
  },
  specification_percent(frm, cdt, cdn) {
    updatePlannedQty(frm, locals[cdt][cdn]);
  },
});

function isFrozen(frm) {
  return ["Completed", "Superseded"].includes(frm.doc.status);
}

function canExecute(frm) {
  return !frm.is_new() && !isFrozen(frm) && ["Draft", "In Progress"].includes(frm.doc.status);
}

function text(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function quantity(value) {
  return flt(value).toFixed(3);
}

function updatePlannedQty(frm, row) {
  row.planned_qty = (flt(frm.doc.premix_size) * flt(row.specification_percent)) / 100;
  frm.dirty();
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
      <td class="calco-premix-sequence">${index + 1}</td>
      <td class="calco-premix-material"><strong>${text(row.item_code)}</strong><br><span class="text-muted">${text(row.item_name)}</span></td>
      <td>${text(row.batch_no)}</td>
      <td class="is-number">${quantity(row.wip_available_qty)} ${text(row.uom)}</td>
      <td><input class="form-control calco-premix-specification" type="number" min="0" step="any" value="${text(row.specification_percent)}" ${editable ? "" : "disabled"}></td>
      <td class="is-number calco-premix-planned">${quantity(row.planned_qty)}</td>
      <td><input class="form-control calco-premix-actual" type="number" min="0" step="any" value="${text(row.actual_qty)}" ${editable ? "" : "disabled"}></td>
      <td class="is-number calco-premix-variance ${needsReason ? "has-variance" : ""}">${rowVariance > 0 ? "+" : ""}${quantity(rowVariance)}</td>
      <td><textarea class="form-control calco-premix-reason" rows="1" placeholder="${needsReason ? __("Required") : __("Not required")}" ${editable ? "" : "disabled"}>${text(row.observation)}</textarea></td>
      <td class="calco-premix-remove-cell">${editable ? `<button type="button" class="btn btn-xs btn-default calco-premix-remove" title="${__("Remove Material")}">${frappe.utils.icon("delete", "xs")}</button>` : ""}</td>
    </tr>`;
  }).join("");
  const table = $(`<div class="calco-premix-controlled-wrap table-responsive">
    <table class="table table-bordered calco-premix-material-table">
      <thead><tr>
        <th>${__("Sr. No.")}</th><th>${__("Material")}</th><th>${__("Batch")}</th>
        <th>${__("Available WIP")}</th><th>${__("Specification %")}</th>
        <th>${__("Planned Qty")}</th><th>${__("Actual Qty")}</th>
        <th>${__("Variance")}</th><th>${__("Variance Reason")}</th><th></th>
      </tr></thead>
      <tbody>${rows || `<tr><td colspan="10" class="text-muted text-center">${__("No Premix materials added.")}</td></tr>`}</tbody>
    </table>
    ${editable ? `<button type="button" class="btn btn-xs btn-default calco-premix-add">${__("Add Material")}</button>` : ""}
  </div>`);
  lockStandardGrid(grid, "calco-premix-material-grid");
  grid.wrapper.find(".calco-premix-controlled-wrap").remove();
  grid.wrapper.append(table);
  bindMaterialTable(frm, table);
  ensurePremixStyles();
}

function bindMaterialTable(frm, table) {
  const findRow = (element) => {
    const name = $(element).closest("tr").data("row-name");
    return (frm.doc.materials || []).find((row) => row.name === name);
  };
  table.on("change", ".calco-premix-specification", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.specification_percent = flt($(this).val());
    updatePlannedQty(frm, row);
    const tr = $(this).closest("tr");
    tr.find(".calco-premix-planned").text(quantity(row.planned_qty));
    refreshVarianceCells(row, tr);
  });
  table.on("change", ".calco-premix-actual", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.actual_qty = flt($(this).val());
    frm.dirty();
    refreshVarianceCells(row, $(this).closest("tr"));
  });
  table.on("input", ".calco-premix-reason", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    row.observation = $(this).val();
    resizeTextarea(this);
    frm.dirty();
  });
  table.on("click", ".calco-premix-remove", function () {
    const row = findRow(this);
    if (!row || !canExecute(frm)) return;
    const index = (frm.doc.materials || []).findIndex((entry) => entry.name === row.name);
    if (index >= 0) frm.doc.materials.splice(index, 1);
    (frm.doc.materials || []).forEach((entry, rowIndex) => { entry.idx = rowIndex + 1; entry.sequence = rowIndex + 1; });
    frm.dirty();
    frm.refresh_field("materials");
    renderMaterialTable(frm);
  });
  table.on("click", ".calco-premix-add", () => showAddMaterialDialog(frm));
  table.find("textarea").each((_index, element) => resizeTextarea(element));
}

function refreshVarianceCells(row, tr) {
  const value = variance(row);
  const changed = hasVariance(row);
  tr.find(".calco-premix-variance")
    .toggleClass("has-variance", changed)
    .text(`${value > 0 ? "+" : ""}${quantity(value)}`);
  tr.find(".calco-premix-reason").attr("placeholder", changed ? __("Required") : __("Not required"));
}

function showAddMaterialDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Premix Material"),
    fields: [
      {fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Material"), reqd: 1},
      {fieldname: "batch_no", fieldtype: "Link", options: "Batch", label: __("Batch"), reqd: 1},
      {fieldname: "specification_percent", fieldtype: "Percent", label: __("Specification %"), reqd: 1},
      {fieldname: "actual_qty", fieldtype: "Float", label: __("Actual Qty")},
      {fieldname: "observation", fieldtype: "Small Text", label: __("Variance Reason")},
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
        specification_percent: values.specification_percent,
        actual_qty: values.actual_qty || 0,
        observation: values.observation || "",
        wip_available_qty: evidence.available_qty,
        uom: evidence.stock_uom,
      });
      row.sequence = (frm.doc.materials || []).length;
      updatePlannedQty(frm, row);
      dialog.hide();
      frm.refresh_field("materials");
      renderMaterialTable(frm);
    },
  });
  dialog.set_query("item_code", () => ({
    query: "calco_erp.calco_production.premix_execution.premix_item_query",
    filters: {work_order: frm.doc.work_order},
  }));
  dialog.set_query("batch_no", () => ({
    query: "calco_erp.calco_production.premix_execution.premix_batch_query",
    filters: {work_order: frm.doc.work_order, item_code: dialog.get_value("item_code")},
  }));
  dialog.show();
}

function renderConfirmationTable(frm) {
  const field = frm.fields_dict.confirmations;
  if (!field || !field.grid) return;
  const grid = field.grid;
  const editable = canExecute(frm);
  const rows = (frm.doc.confirmations || []).map((row, index) => `<tr data-row-name="${text(row.name)}">
    <td>${index + 1}</td>
    <td class="calco-premix-confirmation-text">${text(row.confirmation)}</td>
    <td><textarea class="form-control calco-premix-confirmation-observation" rows="1" ${editable ? "" : "disabled"}>${text(row.observation)}</textarea></td>
    <td class="calco-premix-confirmed-cell"><input type="checkbox" class="calco-premix-confirmed" ${cint(row.confirmed) ? "checked" : ""} ${editable ? "" : "disabled"}>
      ${(row.checked_by || row.checked_on) ? `<div class="small text-muted">${text(row.checked_by || "-")}<br>${text(row.checked_on || "-")}</div>` : ""}
    </td>
  </tr>`).join("");
  const table = $(`<div class="calco-premix-confirmation-wrap table-responsive">
    <table class="table table-bordered calco-premix-confirmation-table">
      <thead><tr><th>${__("Sr. No.")}</th><th>${__("Confirmation")}</th><th>${__("Observation")}</th><th>${__("Confirmed")}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`);
  lockStandardGrid(grid, "calco-premix-confirmation-grid");
  grid.cannot_add_rows = true;
  grid.cannot_delete_rows = true;
  grid.wrapper.find(".calco-premix-confirmation-wrap").remove();
  grid.wrapper.append(table);
  table.on("input", ".calco-premix-confirmation-observation", function () {
    const row = findConfirmation(frm, this);
    if (!row || !canExecute(frm)) return;
    row.observation = $(this).val();
    resizeTextarea(this);
    frm.dirty();
  });
  table.on("change", ".calco-premix-confirmed", function () {
    const row = findConfirmation(frm, this);
    if (!row || !canExecute(frm)) return;
    row.confirmed = this.checked ? 1 : 0;
    frm.dirty();
  });
  table.find("textarea").each((_index, element) => resizeTextarea(element));
  ensurePremixStyles();
}

function findConfirmation(frm, element) {
  const name = $(element).closest("tr").data("row-name");
  return (frm.doc.confirmations || []).find((row) => row.name === name);
}

function lockStandardGrid(grid, className) {
  grid.wrapper.addClass(className);
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
    unspecified ? __("Not Specified") : __("Planned duration in minutes; actual time is server-derived.")
  );
  if (isFrozen(frm) && unspecified) {
    field.$wrapper.find(".control-value").text(__("Not Specified"));
  }
}

function collapseSystemContext(frm) {
  const section = frm.fields_dict.context_section;
  if (!frm.is_new() && section && typeof section.collapse === "function") section.collapse();
}

function resizeTextarea(element) {
  element.style.height = "auto";
  element.style.height = `${Math.max(element.scrollHeight, 28)}px`;
}

function setMaterialQueries(frm) {
  frm.set_query("item_code", "materials", () => ({query: "calco_erp.calco_production.premix_execution.premix_item_query", filters: {work_order: frm.doc.work_order}}));
  frm.set_query("batch_no", "materials", (doc, cdt, cdn) => ({query: "calco_erp.calco_production.premix_execution.premix_batch_query", filters: {work_order: doc.work_order, item_code: locals[cdt][cdn].item_code}}));
  frm.set_query("item_code", "online_changes", () => ({filters: {name: ["in", [...new Set((frm.doc.materials || []).map(row => row.item_code).filter(Boolean))]]}}));
  frm.set_query("batch_no", "online_changes", (doc, cdt, cdn) => ({filters: {item: locals[cdt][cdn].item_code, name: ["in", onlineChangeBatches(doc, locals[cdt][cdn].item_code)]}}));
}

async function runAction(frm, method) {
  await frappe.call({method: `calco_erp.calco_production.premix_execution.${method}`, args: {name: frm.doc.name}, freeze: true});
  await frm.reload_doc();
}

function createCorrection(frm) {
  frappe.prompt({fieldname: "reason", fieldtype: "Small Text", label: __("Correction Reason"), reqd: 1}, async (values) => {
    const response = await frappe.call({method: "calco_erp.calco_production.premix_execution.make_premix_correction", args: {name: frm.doc.name, reason: values.reason}, freeze: true});
    frappe.set_route("Form", "Premix Run", response.message.name);
  }, __("Create Controlled Correction"), __("Create"));
}

function ensurePremixStyles() {
  if (document.getElementById("calco-premix-controlled-style")) return;
  $("<style>", {
    id: "calco-premix-controlled-style",
    text: `
      .calco-premix-material-table, .calco-premix-confirmation-table { width:100%; min-width:1080px; table-layout:fixed; margin-bottom:8px; font-size:12px; line-height:1.3; }
      .calco-premix-material-table th, .calco-premix-material-table td, .calco-premix-confirmation-table th, .calco-premix-confirmation-table td { padding:4px 5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
      .calco-premix-material-table th { background:var(--subtle-fg); }
      .calco-premix-material-table th:nth-child(1), .calco-premix-material-table td:nth-child(1) { width:4%; text-align:center; }
      .calco-premix-material-table th:nth-child(2), .calco-premix-material-table td:nth-child(2) { width:18%; }
      .calco-premix-material-table th:nth-child(3), .calco-premix-material-table td:nth-child(3) { width:12%; }
      .calco-premix-material-table th:nth-child(4), .calco-premix-material-table td:nth-child(4) { width:9%; }
      .calco-premix-material-table th:nth-child(5), .calco-premix-material-table td:nth-child(5) { width:9%; }
      .calco-premix-material-table th:nth-child(6), .calco-premix-material-table td:nth-child(6) { width:8%; }
      .calco-premix-material-table th:nth-child(7), .calco-premix-material-table td:nth-child(7) { width:8%; }
      .calco-premix-material-table th:nth-child(8), .calco-premix-material-table td:nth-child(8) { width:8%; }
      .calco-premix-material-table th:nth-child(9), .calco-premix-material-table td:nth-child(9) { width:20%; }
      .calco-premix-material-table th:nth-child(10), .calco-premix-material-table td:nth-child(10) { width:4%; text-align:center; }
      .calco-premix-material-table input, .calco-premix-material-table textarea, .calco-premix-confirmation-table textarea { min-height:28px; height:28px; padding:3px 5px; font-size:12px; }
      .calco-premix-material-table textarea, .calco-premix-confirmation-table textarea { resize:none; overflow-y:hidden; }
      .calco-premix-material-table .is-number { text-align:right; font-variant-numeric:tabular-nums; }
      .calco-premix-variance.has-variance { color:var(--red-600); font-weight:600; }
      .calco-premix-confirmation-table { min-width:760px; }
      .calco-premix-confirmation-table th:nth-child(1), .calco-premix-confirmation-table td:nth-child(1) { width:6%; text-align:center; }
      .calco-premix-confirmation-table th:nth-child(2), .calco-premix-confirmation-table td:nth-child(2) { width:46%; }
      .calco-premix-confirmation-table th:nth-child(3), .calco-premix-confirmation-table td:nth-child(3) { width:36%; }
      .calco-premix-confirmation-table th:nth-child(4), .calco-premix-confirmation-table td:nth-child(4) { width:12%; text-align:center; }
      .calco-premix-confirmed { width:16px; height:16px; }
      .calco-premix-material-grid .form-grid, .calco-premix-confirmation-grid .form-grid { display:none !important; }
    `,
  }).appendTo(document.head);
}

function onlineChangeBatches(doc, item) {
  return [...new Set((doc.materials || []).filter(m => m.item_code === item).map(m => m.batch_no).filter(Boolean))];
}
async function resolveOnlineChangeBatch(frm, cdt, cdn) {
  if (isFrozen(frm)) return;
  const row = locals[cdt][cdn];
  // Never recalculate recorded nonblank batch evidence when materials change.
  if (row.changed_on && row.batch_no) return;
  const matches = (frm.doc.materials || []).filter(m => m.item_code === row.item_code);
  const batch = matches.length === 1 ? (matches[0].batch_no || '') : '';
  await frappe.model.set_value(cdt, cdn, 'batch_no', batch);
}
frappe.ui.form.on('Premix Run Online Change', {
  item_code: resolveOnlineChangeBatch,
  form_render(frm, cdt, cdn) {
    if (!locals[cdt][cdn].batch_no) return resolveOnlineChangeBatch(frm, cdt, cdn);
  }
});
function setupOnlineChangeGrid(frm) {
  const grid = frm.fields_dict.online_changes?.grid;
  if (!grid || grid.__calco_batch_columns) return;
  grid.__calco_batch_columns = true;
  // Controlled five-column layout, including for users with old saved grid columns.
  grid.editable_fields = ['item_code','batch_no','revised_percent','reason','changed_on'].map(fieldname => ({fieldname}));
  grid.setup_user_defined_columns = function () { this.user_defined_columns = []; };
  grid.visible_columns = [];
  grid.update_docfield_property('batch_no', 'hidden', 0);
  grid.refresh();
}
