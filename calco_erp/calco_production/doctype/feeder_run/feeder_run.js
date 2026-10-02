frappe.ui.form.on("Feeder Run", {
  refresh(frm) {
    frm.disable_save();
    lockControlledFields(frm);
    collapseSystemSections(frm);
    renderSetup(frm);
    renderOnlineChanges(frm);
    renderReadings(frm);
    if (frm.is_new()) {
      frm.dashboard.set_headline_alert(
        __("Create Feeder Run from a running Compounding / Extrusion Job Card cockpit."),
        "orange"
      );
      return;
    }
    addLifecycleActions(frm);
  },
});

const EDITABLE_STATUSES = ["Draft", "Active"];
const PREPARE_ROLES = ["Production Engineer", "Manufacturing Manager", "System Manager"];
const APPROVE_ROLES = ["Production Head", "Manufacturing Manager", "System Manager"];

function hasAnyRole(roles) {
  return roles.some((role) => frappe.user.has_role(role));
}

function canCapture(frm) {
  return !frm.is_new() && EDITABLE_STATUSES.includes(frm.doc.status);
}

function escape(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function display(value) {
  return value == null || value === "" ? "-" : escape(value);
}

function number(value) {
  return value == null || value === "" ? "-" : escape(flt(value));
}

function materialReference(row) {
  if (row.material_source === "Premix") return row.item_name || row.premix_run || "";
  if (row.material_source === "Blended Material") return row.item_name || row.blending_run || "";
  return row.item_code || "";
}

function materialDescription(row) {
  if (row.material_source === "Premix") return row.premix_run || "";
  if (row.material_source === "Blended Material") return row.blending_run || "";
  return row.item_name || "";
}

function lockControlledFields(frm) {
  (frm.meta.fields || []).forEach((field) => {
    if (field.fieldname && !["setup_html", "online_changes_html", "readings_html"].includes(field.fieldname)) {
      frm.toggle_enable(field.fieldname, false);
    }
  });
}

function collapseSystemSections(frm) {
  if (frm.is_new()) return;
  ["document_control_section", "context_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function renderSetup(frm) {
  const wrapper = frm.fields_dict.setup_html?.$wrapper;
  if (!wrapper) return;
  const editable = canCapture(frm);
  const rows = (frm.doc.setup_rows || []).map((row) => `<tr>
    <td>${escape(row.feeder_no)}</td>
    <td>${row.used ? `<span class="indicator-pill green">${__("Used")}</span>` : `<span class="text-muted">${__("Unused")}</span>`}</td>
    <td>${display(row.material_source)}</td>
    <td><strong>${display(materialReference(row))}</strong><div class="text-muted">${display(materialDescription(row))}</div></td>
    <td>${display(row.bulk_density)}</td><td>${display(row.max_value_feed_rate)}</td>
    <td>${display(row.screw_size)}</td><td>${display(row.barrel_size)}</td>
    <td>${display(row.min_level)}</td><td>${display(row.max_level)}</td>
    <td>${editable ? `<button type="button" class="btn btn-xs btn-default calco-feeder-setup-edit" data-feeder="${escape(row.feeder_no)}">${__("Edit")}</button>` : ""}</td>
  </tr>`).join("");
  wrapper.html(`<div class="table-responsive"><table class="table table-bordered calco-feeder-setup-table">
    <thead><tr><th>${__("Feeder")}</th><th>${__("Used")}</th><th>${__("Source")}</th><th>${__("Material")}</th><th>${__("B.D")}</th>
    <th>${__("Max / Feed Rate")}</th><th>${__("Screw Size")}</th><th>${__("Barrel Size")}</th>
    <th>${__("Min Level")}</th><th>${__("Max Level")}</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>`);
  wrapper.find(".calco-feeder-setup-edit").on("click", function () {
    showSetupDialog(frm, $(this).data("feeder"));
  });
  ensureStyles();
}

function showSetupDialog(frm, feederNo) {
  const row = (frm.doc.setup_rows || []).find((item) => item.feeder_no === String(feederNo));
  if (!row) return;
  let dialog;
  const clearIncompatibleSources = () => {
    if (!dialog) return;
    const used = dialog.get_value("used");
    const source = used ? dialog.get_value("material_source") : "";
    if (!used && dialog.get_value("material_source")) dialog.set_value("material_source", "");
    if (source !== "Raw Material" && dialog.get_value("item_code")) dialog.set_value("item_code", "");
    if (source !== "Premix" && dialog.get_value("premix_run")) dialog.set_value("premix_run", "");
    if (source !== "Blended Material" && dialog.get_value("blending_run")) dialog.set_value("blending_run", "");
  };
  dialog = new frappe.ui.Dialog({
    title: __("Feeder {0} Setup", [feederNo]),
    fields: [
      {fieldname:"used", fieldtype:"Check", label:__("Used / Active"), default:row.used ? 1 : 0, change:clearIncompatibleSources},
      {fieldname:"material_source", fieldtype:"Select", label:__("Material Source"), options:["", "Raw Material", "Premix", "Blended Material"], default:row.material_source || "", depends_on:"eval:doc.used", change:clearIncompatibleSources},
      {fieldname:"item_code", fieldtype:"Link", options:"Item", label:__("Raw Material Item"), default:row.item_code || "", depends_on:"eval:doc.used && doc.material_source=='Raw Material'"},
      {fieldname:"premix_run", fieldtype:"Link", options:"Premix Run", label:__("Premix Run"), default:row.premix_run || "", depends_on:"eval:doc.used && doc.material_source=='Premix'"},
      {fieldname:"blending_run", fieldtype:"Link", options:"Blending Run", label:__("Blending Run"), default:row.blending_run || "", depends_on:"eval:doc.used && doc.material_source=='Blended Material'"},
      {fieldname:"bulk_density", fieldtype:"Data", label:__("B.D"), default:row.bulk_density || ""},
      {fieldname:"max_value_feed_rate", fieldtype:"Data", label:__("Check Max Value / Feed Rate"), default:row.max_value_feed_rate || ""},
      {fieldname:"screw_size", fieldtype:"Data", label:__("Screw Size"), default:row.screw_size || ""},
      {fieldname:"barrel_size", fieldtype:"Data", label:__("Barrel Size"), default:row.barrel_size || ""},
      {fieldname:"min_level", fieldtype:"Data", label:__("Feeder Min Level"), default:row.min_level || ""},
      {fieldname:"max_level", fieldtype:"Data", label:__("Feeder Max Level"), default:row.max_level || ""},
    ],
    primary_action_label: __("Save Setup"),
    async primary_action(values) {
      if (values.used && !values.material_source) {
        frappe.msgprint(__("Material Source is required when this Feeder is Used."));
        return;
      }
      const requiredField = {"Raw Material":"item_code", Premix:"premix_run", "Blended Material":"blending_run"}[values.material_source];
      if (values.used && requiredField && !values[requiredField]) {
        frappe.msgprint(__("Select the controlled material reference for this source."));
        return;
      }
      await callAction(frm, "update_feeder_setup", {feeder_no:feederNo, ...values});
      dialog.hide();
    },
  });
  dialog.set_query("item_code", () => ({
    query:"calco_erp.calco_production.feeder_run.feeder_item_query",
    filters:{work_order:frm.doc.work_order},
  }));
  dialog.set_query("premix_run", () => ({
    query:"calco_erp.calco_production.feeder_run.feeder_premix_query",
    filters:{feeder_run:frm.doc.name},
  }));
  dialog.set_query("blending_run", () => ({
    query:"calco_erp.calco_production.feeder_run.feeder_blending_query",
    filters:{feeder_run:frm.doc.name},
  }));
  dialog.show();
}

function renderOnlineChanges(frm) {
  const wrapper = frm.fields_dict.online_changes_html?.$wrapper;
  if (!wrapper) return;
  const rows = (frm.doc.online_changes || []).map((row) => `<tr>
    <td>${escape(row.sequence)}</td><td>${display(frappe.datetime.str_to_user(row.event_datetime))}</td>
    ${[1,2,3,4,5,6].map((index) => `<td class="is-number">${number(row[`feeder_${index}_pct`])}</td>`).join("")}
    <td class="is-number"><strong>${number(row.total_pct)}</strong></td><td>${display(row.observation)}</td>
    <td>${display(row.shift)}</td><td>${display(row.recorded_by)}</td>
  </tr>`).join("");
  wrapper.html(`<div class="table-responsive"><table class="table table-bordered calco-feeder-change-table">
    <thead><tr><th>${__("No.")}</th><th>${__("Time")}</th>${[1,2,3,4,5,6].map((index) => `<th>F${index} %</th>`).join("")}
    <th>${__("Total %")}</th><th>${__("Reason / Observation")}</th><th>${__("Shift")}</th><th>${__("Recorded By")}</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="12" class="text-muted text-center">${__("No Online Changes recorded.")}</td></tr>`}</tbody>
  </table></div>`);
  ensureStyles();
}

function showOnlineChangeDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Feeder Online Change"),
    fields: [
      {fieldname:"pct_section", fieldtype:"Section Break", label:__("Feeder HMI Percentages")},
      ...[1,2,3,4,5,6].flatMap((index) => [
        ...(index % 2 === 0 ? [{fieldname:`column_${index}`, fieldtype:"Column Break"}] : index > 1 ? [{fieldname:`section_${index}`, fieldtype:"Section Break"}] : []),
        {fieldname:`feeder_${index}_pct`, fieldtype:"Percent", label:__("Feeder {0} %", [index])},
      ]),
      {fieldname:"reason_section", fieldtype:"Section Break"},
      {fieldname:"observation", fieldtype:"Small Text", label:__("Reason / Observation"), reqd:1},
    ],
    primary_action_label: __("Record Online Change"),
    async primary_action(values) {
      await callAction(frm, "add_online_change", values);
      dialog.hide();
    },
  });
  dialog.show();
}

function renderReadings(frm) {
  const wrapper = frm.fields_dict.readings_html?.$wrapper;
  if (!wrapper) return;
  const cards = (frm.doc.readings || []).map((row) => `<article class="calco-feeder-reading-card">
    <header><strong>${__("Reading {0}", [row.sequence])}</strong><span>${display(frappe.datetime.str_to_user(row.event_datetime))}</span>
      <span>${__("Shift")}: ${display(row.shift)}</span><span>${__("Recorded By")}: ${display(row.recorded_by)}</span></header>
    <table class="table table-bordered"><thead><tr><th>${__("Feeder")}</th><th>%</th><th>${__("Kg")}</th></tr></thead>
      <tbody>${[1,2,3,4,5,6].map((index) => `<tr><td>${index}</td><td class="is-number">${number(row[`feeder_${index}_pct`])}</td><td class="is-number">${number(row[`feeder_${index}_kg`])}</td></tr>`).join("")}</tbody>
    </table></article>`).join("");
  wrapper.html(cards ? `<div class="calco-feeder-reading-list">${cards}</div>` : `<div class="text-muted text-center calco-feeder-empty">${__("No Feeder HMI Readings recorded.")}</div>`);
  ensureStyles();
}

function showReadingDialog(frm) {
  const fields = [];
  [1,2,3,4,5,6].forEach((index) => {
    fields.push({fieldname:`feeder_${index}_section`, fieldtype:"Section Break", label:__("Feeder {0}", [index])});
    fields.push({fieldname:`feeder_${index}_pct`, fieldtype:"Percent", label:"%"});
    fields.push({fieldname:`feeder_${index}_column`, fieldtype:"Column Break"});
    fields.push({fieldname:`feeder_${index}_kg`, fieldtype:"Float", label:__("Kg")});
  });
  const dialog = new frappe.ui.Dialog({
    title: __("Add Feeder HMI Reading"),
    fields,
    primary_action_label: __("Record Reading"),
    async primary_action(values) {
      const hasValue = Object.values(values).some((value) => value !== null && value !== "");
      if (!hasValue) {
        frappe.msgprint(__("Enter at least one Percentage or Kg reading."));
        return;
      }
      await callAction(frm, "add_feeder_reading", values);
      dialog.hide();
    },
  });
  dialog.show();
}

function addLifecycleActions(frm) {
  if (canCapture(frm)) {
    frm.add_custom_button(__("Add Online Change"), () => showOnlineChangeDialog(frm));
    frm.add_custom_button(__("Add Feeder Reading"), () => showReadingDialog(frm));
    if (hasAnyRole(PREPARE_ROLES)) {
      frm.add_custom_button(__("Prepare / Submit for Approval"), () => callAction(frm, "prepare_feeder_run"));
    }
  }
  if (frm.doc.status === "Awaiting Approval" && hasAnyRole(APPROVE_ROLES)) {
    frm.add_custom_button(__("Approve"), () => callAction(frm, "approve_feeder_run"));
  }
  if (frm.doc.status === "Completed" && hasAnyRole(APPROVE_ROLES)) {
    frm.add_custom_button(__("Create Correction"), () => createCorrection(frm));
  }
}

async function callAction(frm, method, extra = {}) {
  await frappe.call({
    method:`calco_erp.calco_production.feeder_run.${method}`,
    args:{name:frm.doc.name, ...extra},
    freeze:true,
  });
  await frm.reload_doc();
}

function createCorrection(frm) {
  frappe.prompt(
    {fieldname:"reason", fieldtype:"Small Text", label:__("Correction Reason"), reqd:1},
    async (values) => {
      const response = await frappe.call({
        method:"calco_erp.calco_production.feeder_run.make_feeder_run_correction",
        args:{name:frm.doc.name, reason:values.reason},
        freeze:true,
      });
      frappe.set_route("Form", "Feeder Run", response.message.name);
    },
    __("Create Controlled Correction"),
    __("Create")
  );
}

function ensureStyles() {
  if (document.getElementById("calco-feeder-run-style")) return;
  $("<style>", {id:"calco-feeder-run-style", text:`
    .calco-feeder-setup-table, .calco-feeder-change-table { min-width:1080px; table-layout:fixed; font-size:12px; line-height:1.3; }
    .calco-feeder-setup-table th, .calco-feeder-setup-table td,
    .calco-feeder-change-table th, .calco-feeder-change-table td { padding:5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
    .calco-feeder-setup-table th, .calco-feeder-change-table th,
    .calco-feeder-reading-card th { background:var(--subtle-fg); }
    .calco-feeder-setup-table th:nth-child(1), .calco-feeder-setup-table td:nth-child(1) { width:5%; text-align:center; }
    .calco-feeder-setup-table th:nth-child(2), .calco-feeder-setup-table td:nth-child(2) { width:7%; text-align:center; }
    .calco-feeder-setup-table th:nth-child(3), .calco-feeder-setup-table td:nth-child(3) { width:12%; }
    .calco-feeder-setup-table th:nth-child(4), .calco-feeder-setup-table td:nth-child(4) { width:18%; }
    .calco-feeder-setup-table th:nth-child(n+5):nth-child(-n+10), .calco-feeder-setup-table td:nth-child(n+5):nth-child(-n+10) { width:9%; }
    .calco-feeder-setup-table th:nth-child(11), .calco-feeder-setup-table td:nth-child(11) { width:7%; text-align:center; }
    .calco-feeder-change-table th:nth-child(1), .calco-feeder-change-table td:nth-child(1) { width:4%; text-align:center; }
    .calco-feeder-change-table th:nth-child(2), .calco-feeder-change-table td:nth-child(2) { width:14%; }
    .calco-feeder-change-table th:nth-child(n+3):nth-child(-n+9), .calco-feeder-change-table td:nth-child(n+3):nth-child(-n+9) { width:6%; }
    .calco-feeder-change-table th:nth-child(10), .calco-feeder-change-table td:nth-child(10) { width:24%; }
    .calco-feeder-change-table th:nth-child(11), .calco-feeder-change-table td:nth-child(11) { width:8%; }
    .calco-feeder-change-table th:nth-child(12), .calco-feeder-change-table td:nth-child(12) { width:14%; }
    .calco-feeder-reading-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:10px; }
    .calco-feeder-reading-card { border:1px solid var(--border-color); border-radius:6px; padding:8px; }
    .calco-feeder-reading-card header { display:flex; flex-wrap:wrap; gap:6px 14px; margin-bottom:6px; font-size:12px; }
    .calco-feeder-reading-card table { margin:0; font-size:12px; }
    .calco-feeder-reading-card th, .calco-feeder-reading-card td { padding:3px 6px !important; }
    .calco-feeder-reading-card th:first-child, .calco-feeder-reading-card td:first-child { width:34%; text-align:center; }
    .calco-feeder-empty { padding:18px; border:1px dashed var(--border-color); }
    .is-number { text-align:right; font-variant-numeric:tabular-nums; }
  `}).appendTo(document.head);
}
