frappe.ui.form.on("Master Process Data Sheet", {
  refresh(frm) {
    renderSpecifications(frm);
    addLifecycleActions(frm);
    collapseSupportSections(frm);
  },
});

const MPDS_EDITABLE = ["Imported - Unverified", "Draft"];
const MPDS_REVIEW_ROLES = ["Technical User", "Production Engineer", "Production Head", "Manufacturing Manager", "System Manager"];
const MPDS_APPROVAL_ROLES = ["Production Head", "Manufacturing Manager", "System Manager"];

function hasRole(roles) {
  return roles.some((role) => frappe.user.has_role(role));
}

function escape(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function display(value) {
  return value == null || value === "" ? '<span class="text-muted">Blank</span>' : escape(value);
}

function badge(value) {
  const color = value === "Mapped" ? "green" : value === "Ambiguous" || value === "Missing Source" ? "orange" : "gray";
  return `<span class="indicator-pill ${color}">${escape(value || "Unmapped")}</span>`;
}

function renderSpecifications(frm) {
  const wrapper = frm.fields_dict.specification_review_html?.$wrapper;
  if (!wrapper) return;
  const grouped = {};
  (frm.doc.specifications || []).forEach((row) => {
    const domain = row.domain || __("Other Operational Instructions");
    (grouped[domain] ||= []).push(row);
  });
  const canEdit = MPDS_EDITABLE.includes(frm.doc.status) && hasRole(MPDS_REVIEW_ROLES);
  const sections = Object.entries(grouped).map(([domain, rows]) => {
    const body = rows.sort((a, b) => (a.sequence || 0) - (b.sequence || 0)).map((row) => `<tr>
      <td class="mpds-sequence">${escape(row.sequence)}</td>
      <td class="mpds-position">${display(row.feeder_or_position)}</td>
      <td class="mpds-parameter"><strong>${display(row.display_label || row.exact_source_header)}</strong><div class="text-muted">${display(row.parameter_group)}</div></td>
      <td class="mpds-specification">${display(row.specification_text)}</td>
      <td class="mpds-unit">${display(row.unit)}</td>
      <td>${display(row.applicability)}</td>
      <td class="mpds-source"><strong>${display(row.source_cell)}</strong><div class="text-muted">${display(row.exact_source_header)}</div></td>
      <td>${badge(row.mapping_status)}${row.mapping_note ? `<div class="text-muted mpds-note">${escape(row.mapping_note)}</div>` : ""}</td>
      <td>${canEdit ? `<button type="button" class="btn btn-xs btn-default mpds-edit-spec" data-row="${escape(row.name)}">${__("Edit")}</button>` : ""}</td>
    </tr>`).join("");
    return `<section class="mpds-domain"><h5>${escape(domain)}</h5><div class="table-responsive"><table class="table table-bordered mpds-table">
      <thead><tr><th>${__("No.")}</th><th>${__("Feeder / Position")}</th><th>${__("Parameter")}</th><th>${__("Specification")}</th><th>${__("Unit")}</th><th>${__("Applicability")}</th><th>${__("Source")}</th><th>${__("Mapping")}</th><th></th></tr></thead>
      <tbody>${body}</tbody></table></div></section>`;
  }).join("");
  wrapper.html(sections || `<div class="text-muted text-center">${__("No controlled MPDS specifications recorded.")}</div>`);
  wrapper.find(".mpds-edit-spec").on("click", function () {
    editSpecification(frm, $(this).data("row"));
  });
  ensureStyles();
}

function editSpecification(frm, rowName) {
  const row = (frm.doc.specifications || []).find((item) => item.name === rowName);
  if (!row) return;
  const dialog = new frappe.ui.Dialog({
    title: __("Review MPDS Specification"),
    fields: [
      {fieldname:"parameter", fieldtype:"Data", label:__("Parameter"), default:row.display_label, read_only:1},
      {fieldname:"source", fieldtype:"Data", label:__("Original Source Evidence"), default:`${row.source_cell || "No Line2 Authority"}: ${row.source_raw_value || "Blank"}`, read_only:1},
      {fieldname:"specification_text", fieldtype:"Small Text", label:__("Specification Text"), default:row.specification_text || ""},
      {fieldname:"unit", fieldtype:"Data", label:__("Unit"), default:row.unit || ""},
      {fieldname:"applicability", fieldtype:"Select", label:__("Applicability"), options:["Blank", "Specified", "Explicit Dash", "Not Applicable"], default:row.applicability || "Blank"},
      {fieldname:"mapping_status", fieldtype:"Select", label:__("Mapping Status"), options:["Mapped", "Ambiguous", "Unmapped", "Missing Source", "Not Applicable"], default:row.mapping_status || "Unmapped"},
      {fieldname:"mapping_note", fieldtype:"Small Text", label:__("Mapping Note"), default:row.mapping_note || ""},
    ],
    primary_action_label: __("Save Reviewed Value"),
    async primary_action(values) {
      await callAction(frm, "update_specification", {specification:row.name, ...values});
      dialog.hide();
    },
  });
  dialog.show();
}

function addLifecycleActions(frm) {
  if (frm.is_new()) return;
  if (MPDS_EDITABLE.includes(frm.doc.status) && hasRole(MPDS_REVIEW_ROLES)) {
    frm.add_custom_button(__("Mark Reviewed"), () => {
      frappe.prompt({fieldname:"review_notes", fieldtype:"Small Text", label:__("Technical Review Notes"), default:frm.doc.review_notes || ""},
        (values) => callAction(frm, "mark_reviewed", values), __("Mark MPDS Reviewed"), __("Confirm"));
    }, __("MPDS"));
    if (frm.doc.reviewed_on) {
      frm.add_custom_button(__("Submit for Approval"), () => callAction(frm, "submit_for_approval"), __("MPDS"));
    }
  }
  if (frm.doc.status === "Pending Approval" && hasRole(MPDS_APPROVAL_ROLES)) {
    frm.add_custom_button(__("Approve / Make Current"), () => {
      frappe.prompt({fieldname:"approval_notes", fieldtype:"Small Text", label:__("Approval Notes"), default:frm.doc.approval_notes || ""},
        (values) => callAction(frm, "approve_and_make_current", values), __("Approve MPDS"), __("Approve"));
    }, __("MPDS"));
  }
  if (frm.doc.status === "Approved / Current" && hasRole(MPDS_REVIEW_ROLES)) {
    frm.add_custom_button(__("Create Controlled Revision"), () => {
      frappe.prompt([
        {fieldname:"revision", fieldtype:"Data", label:__("New Revision"), reqd:1},
        {fieldname:"revision_reason", fieldtype:"Small Text", label:__("Revision Reason"), reqd:1},
      ], async (values) => {
        const response = await frappe.call({method:"calco_erp.calco_production.mpds_master.create_revision", args:{name:frm.doc.name, ...values}, freeze:true});
        frappe.set_route("Form", "Master Process Data Sheet", response.message.name);
      }, __("Create Controlled Revision"), __("Create Draft"));
    }, __("MPDS"));
  }
}

async function callAction(frm, action, args = {}) {
  await frappe.call({
    method:`calco_erp.calco_production.mpds_master.${action}`,
    args:{name:frm.doc.name, ...args},
    freeze:true,
    freeze_message:__("Updating controlled MPDS..."),
  });
  await frm.reload_doc();
}

function collapseSupportSections(frm) {
  if (frm.is_new()) return;
  ["source_provenance_section", "data_quality_section", "revision_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function ensureStyles() {
  if (document.getElementById("calco-mpds-styles")) return;
  $("<style id='calco-mpds-styles'>").text(`
    .mpds-domain { margin: 0 0 18px; }
    .mpds-domain h5 { margin: 0 0 8px; font-weight: 600; }
    .mpds-table { font-size: 12px; margin-bottom: 0; }
    .mpds-table th, .mpds-table td { padding: 6px 7px !important; vertical-align: top !important; white-space: normal !important; line-height: 1.3; }
    .mpds-sequence { width: 42px; text-align: center; }
    .mpds-position { width: 76px; }
    .mpds-parameter { min-width: 180px; width: 20%; }
    .mpds-specification { min-width: 150px; width: 18%; overflow-wrap: anywhere; }
    .mpds-unit { width: 74px; }
    .mpds-source { min-width: 160px; width: 18%; overflow-wrap: anywhere; }
    .mpds-note { margin-top: 4px; max-width: 240px; }
  `).appendTo("head");
}
