frappe.ui.form.on("Silo Control", {
  refresh(frm) {
    lockControlledFields(frm);
    collapseSystemSections(frm);
    renderBlendingInstruction(frm);
    renderEventTable(frm);
    if (frm.is_new()) {
      frm.disable_save();
      frm.dashboard.set_headline_alert(
        __("Create Silo Control from a running Compounding / Extrusion Job Card cockpit."),
        "orange"
      );
      return;
    }
    addLifecycleActions(frm);
  },
  blending_required(frm) {
    renderBlendingInstruction(frm);
  },
});

function isFrozen(frm) {
  return ["Completed", "Superseded"].includes(frm.doc.status);
}

function canOperate(frm) {
  return !frm.is_new() && !isFrozen(frm) && ["Draft", "Active"].includes(frm.doc.status);
}

function hasReviewRole() {
  return ["Production Head", "Manufacturing Manager", "System Manager"].some((role) =>
    frappe.user.has_role(role)
  );
}

function lockControlledFields(frm) {
  const editable = canOperate(frm);
  frm.toggle_enable(["addition_rate", "blending_required", "returned_kg"], editable);
  frm.toggle_enable(
    [
      "job_card", "work_order", "operation", "production_line", "machine", "fg_item",
      "fg_batch", "bom_no", "planned_job_qty", "production_date", "shift", "recorded_by",
      "recorded_on", "rm_item", "rm_item_name", "silo_no", "feeder_no", "total_loaded_bags",
      "total_loaded_kg", "completed_by", "completed_on", "supersedes", "superseded_by",
      "correction_reason",
    ],
    false
  );
}

function collapseSystemSections(frm) {
  if (frm.is_new()) return;
  ["document_control_section", "context_section", "correction_section"].forEach((fieldname) => {
    const section = frm.fields_dict[fieldname];
    if (section && typeof section.collapse === "function") section.collapse();
  });
}

function renderBlendingInstruction(frm) {
  const wrapper = frm.fields_dict.blending_instruction?.$wrapper;
  if (!wrapper) return;
  wrapper.html(
    frm.doc.blending_required === "Yes"
      ? `<div class="alert alert-info mb-3"><strong>${__("F-SCS-01 instruction:")}</strong> ${__("Blending quantity should not exceed 500 Kg.")}</div>`
      : ""
  );
}

function text(value) {
  return frappe.utils.escape_html(value == null ? "" : String(value));
}

function qty(value) {
  return flt(value).toFixed(3);
}

function renderEventTable(frm) {
  const wrapper = frm.fields_dict.loading_events_html?.$wrapper;
  if (!wrapper) return;
  const rows = (frm.doc.loading_events || []).map((row, index) => {
    const review = row.below_max_level === "No"
      ? row.supervisor_reviewed
        ? `<span class="indicator-pill green">${__("Reviewed")}</span>`
        : hasReviewRole() && !isFrozen(frm)
          ? `<button class="btn btn-xs btn-default calco-silo-review" data-event="${text(row.name)}">${__("Review")}</button>`
          : `<span class="indicator-pill orange">${__("Required")}</span>`
      : `<span class="text-muted">-</span>`;
    return `<tr>
      <td>${index + 1}</td><td>${text(frappe.datetime.str_to_user(row.event_datetime))}</td>
      <td><strong>${text(row.batch_no)}</strong></td><td class="is-number">${qty(row.bags_loaded)}</td>
      <td class="is-number">${qty(row.kg_loaded)}</td><td class="is-number">${qty(row.cumulative_bags)}</td>
      <td class="is-number">${qty(row.cumulative_kg)}</td><td>${text(row.below_max_level)}</td>
      <td>${text(row.observation || "-")}</td><td>${review}</td>
    </tr>`;
  }).join("");
  wrapper.html(`<div class="table-responsive calco-silo-event-wrap">
    <table class="table table-bordered calco-silo-event-table">
      <thead><tr><th>${__("Sr. No.")}</th><th>${__("Time")}</th><th>${__("Batch / Lot")}</th>
      <th>${__("Bags Loaded")}</th><th>${__("Kg Loaded")}</th><th>${__("Cum. Bags")}</th>
      <th>${__("Cum. Kg")}</th><th>${__("Below Max")}</th><th>${__("Observation")}</th><th>${__("Review")}</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="10" class="text-muted text-center">${__("No loading events recorded.")}</td></tr>`}</tbody>
      <tfoot><tr><th colspan="4">${__("Total Loaded")}</th><th class="is-number">${qty(frm.doc.total_loaded_kg)} Kg</th><th colspan="5"></th></tr></tfoot>
    </table>
  </div>`);
  wrapper.find(".calco-silo-review").on("click", async function () {
    await runAction(frm, "review_silo_exception", {event: $(this).data("event")});
  });
  ensureStyles();
}

function addLifecycleActions(frm) {
  if (canOperate(frm)) {
    frm.add_custom_button(__("Add Loading Event"), () => showEventDialog(frm));
    frm.add_custom_button(__("Complete Silo Control"), () => runAction(frm, "complete_silo_control"));
  }
  if (frm.doc.status === "Completed" && hasReviewRole()) {
    frm.add_custom_button(__("Create Correction"), () => createCorrection(frm));
  }
}

function showEventDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: __("Add Silo Loading Event"),
    fields: [
      {fieldname:"batch_no", fieldtype:"Link", options:"Batch", label:__("Batch / Lot"), reqd:1},
      {fieldname:"bags_loaded", fieldtype:"Float", label:__("Bags Loaded")},
      {fieldname:"kg_loaded", fieldtype:"Float", label:__("Kg Loaded")},
      {fieldname:"below_max_level", fieldtype:"Select", options:"Yes\nNo", default:"Yes", label:__("Was material poured under max. level"), reqd:1},
      {fieldname:"observation", fieldtype:"Small Text", label:__("Observation / Reason")},
    ],
    primary_action_label: __("Record Event"),
    async primary_action(values) {
      await frappe.call({
        method:"calco_erp.calco_production.silo_control.add_silo_loading_event",
        args:{name:frm.doc.name, ...values},
        freeze:true,
        freeze_message:__("Recording controlled loading evidence..."),
      });
      dialog.hide();
      await frm.reload_doc();
    },
  });
  dialog.set_query("batch_no", () => ({
    query:"calco_erp.calco_production.silo_control.silo_batch_query",
    filters:{work_order:frm.doc.work_order, item_code:frm.doc.rm_item},
  }));
  dialog.show();
}

async function runAction(frm, method, extra = {}) {
  await frappe.call({
    method:`calco_erp.calco_production.silo_control.${method}`,
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
        method:"calco_erp.calco_production.silo_control.make_silo_correction",
        args:{name:frm.doc.name, reason:values.reason},
        freeze:true,
      });
      frappe.set_route("Form", "Silo Control", response.message.name);
    },
    __("Create Controlled Correction"),
    __("Create")
  );
}

function ensureStyles() {
  if (document.getElementById("calco-silo-control-style")) return;
  $("<style>", {id:"calco-silo-control-style", text:`
    .calco-silo-event-table { min-width:1120px; table-layout:fixed; font-size:12px; line-height:1.3; }
    .calco-silo-event-table th, .calco-silo-event-table td { padding:5px !important; vertical-align:middle; white-space:normal; overflow-wrap:anywhere; }
    .calco-silo-event-table th { background:var(--subtle-fg); }
    .calco-silo-event-table th:nth-child(1), .calco-silo-event-table td:nth-child(1) { width:5%; text-align:center; }
    .calco-silo-event-table th:nth-child(2), .calco-silo-event-table td:nth-child(2) { width:13%; }
    .calco-silo-event-table th:nth-child(3), .calco-silo-event-table td:nth-child(3) { width:14%; }
    .calco-silo-event-table th:nth-child(4), .calco-silo-event-table td:nth-child(4),
    .calco-silo-event-table th:nth-child(5), .calco-silo-event-table td:nth-child(5),
    .calco-silo-event-table th:nth-child(6), .calco-silo-event-table td:nth-child(6),
    .calco-silo-event-table th:nth-child(7), .calco-silo-event-table td:nth-child(7) { width:8%; }
    .calco-silo-event-table th:nth-child(8), .calco-silo-event-table td:nth-child(8) { width:8%; text-align:center; }
    .calco-silo-event-table th:nth-child(9), .calco-silo-event-table td:nth-child(9) { width:20%; }
    .calco-silo-event-table th:nth-child(10), .calco-silo-event-table td:nth-child(10) { width:8%; text-align:center; }
    .calco-silo-event-table .is-number { text-align:right; font-variant-numeric:tabular-nums; }
  `}).appendTo(document.head);
}
