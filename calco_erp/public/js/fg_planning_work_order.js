frappe.ui.form.on("Work Order", {
  async onload_post_render(frm) {
    const context = frm.doc.__fg_dashboard_context;
    if (context) frm.toggle_display("production_plan", false);
    if (!frm.is_new() || !context || frm.__fg_context_initialized) return;
    frm.__fg_context_initialized = true;
    const result = await frappe.call({method: "erpnext.manufacturing.doctype.work_order.work_order.get_item_details", args: {item: frm.doc.production_item, skip_bom_info: true}});
    for (const field of ["stock_uom", "description", "item_name", "allow_alternative_item"]) {
      if (result.message?.[field] !== undefined) await frm.set_value(field, result.message[field]);
    }
    if (context.production_warehouses) {
      await frm.set_value("source_warehouse", context.production_warehouses.source_warehouse);
      await frm.set_value("wip_warehouse", context.production_warehouses.wip_warehouse);
    }
    frm.set_query("bom_no", () => ({filters: {name: ["in", [...new Set(context.boms.candidates.map(r => r.bom_no))]], docstatus: 1, is_active: 1}}));
    if (context.boms.suggested) await frm.trigger("bom_no");
    else frappe.show_alert({message: __("Select a compatible BOM in the BOM field; no unambiguous default is available."), indicator: "orange"});
  },
  refresh(frm) {
    if (frm.doc.custom_fg_planning_origin !== "fg-dashboard-v1") return;
    frm.toggle_display("production_plan", false);
    if (frm.doc.docstatus === 0) frm.add_custom_button(__('Planning Change Reason'), async () => {
      const reason = await calcoPlanningReason(frm);
      if (reason) { await frm.set_value('custom_fg_planning_change_reason', reason); frm.set_intro(__('Save the revision before planning approval.'), 'orange'); }
    }, __('Planning'));
    const planningEvents = JSON.parse(frm.doc.custom_fg_planning_events || '[]');
    const lastRevision = [...planningEvents].reverse().find(e => e.event === 'Revision');
    const isApproved = lastRevision && planningEvents.some(e => e.event === 'Approved' && e.sequence > lastRevision.sequence);
    frm.set_intro(__("FG Dashboard planning: approval applies to the current quantity, BOM, production line/machine and schedule. Changes require a reason and renewed planning approval."), "blue");
    if (!isApproved) frm.set_intro(__('Planning Approval Required'), 'orange');
    frm.add_custom_button(__("Planning Details"), () => {
      const snapshot = JSON.parse(frm.doc.custom_fg_planning_snapshot || "{}");
      const events = JSON.parse(frm.doc.custom_fg_planning_events || "[]");
      const escape = value => frappe.utils.escape_html(String(value ?? ""));
      const dialog = new frappe.ui.Dialog({title: __("Planning Details"), size: "extra-large", fields: [{fieldtype: "HTML", fieldname: "details"}]});
      dialog.fields_dict.details.$wrapper.html(`<p>${escape(snapshot.company)} · ${escape(snapshot.item_code)} · ${escape(snapshot.stock_uom)}</p><p>Original planning month: ${escape(snapshot.month)}. Balance when opened: ${escape(snapshot.displayed_balance)}. Planner: ${escape(snapshot.planner)}.</p><table class="table table-bordered"><thead><tr><th>Action</th><th>By / On</th><th>Quantity / Schedule</th><th>Reason</th></tr></thead><tbody>${events.map(event => `<tr><td>${escape(event.event)}</td><td>${escape(event.user)}<br>${escape(event.on)}</td><td>${escape(event.values?.qty ?? "")}<br>${escape(event.values?.planned_start_date ?? "")}<br>${escape(event.values?.planned_end_date ?? "")}</td><td>${escape(event.reason || event.authority || "")}</td></tr>`).join("")}</tbody></table><p>Original demand and coverage sources:</p><ul>${(snapshot.sources || []).map(source => `<li><a href="/app/${frappe.router.slug(source.doctype)}/${encodeURIComponent(source.name)}">${escape(source.doctype)} ${escape(source.name)}</a> — ${escape(source.metric)}: ${escape(source.qty)}</li>`).join("")}</ul>`);
      dialog.show();
    }, __('Planning'));
    const authorities = ["System Manager", "Sales User", "Sales Manager", "Customer Service"];
    if (frm.doc.docstatus !== 2 && authorities.some(role => frappe.user.has_role(role))) {
      frm.add_custom_button(__("Approve Planning Revision"), async () => {
        if (frm.is_dirty()) { frappe.msgprint(__("Save your changes before planning approval.")); return; }
        await frappe.call({method: "calco_erp.calco_production.fg_planning_authority.approve", args: {work_order: frm.doc.name}, freeze: true});
        await frm.reload_doc();
        calcoRestorePlanningActions(frm);
        frappe.show_alert({message: __("Current planning revision approved."), indicator: "green"});
      }, __('Planning'));
    }
  }
});



function calcoScheduleInputs(frm) {
  const fields = ['name','company','production_item','qty','bom_no','custom_machine','custom_production_line','custom_fg_requested_start','planned_start_date','use_multi_level_bom','track_semi_finished_goods'];
  return Object.fromEntries(fields.map(key => [key,frm.doc[key]]));
}
async function calcoPreviewSchedule(frm, saveRevision = false) {
  if (frm.__fg_schedule_applying || frm.doc.docstatus !== 0 || !(frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1' || frm.doc.__fg_dashboard_context)) return;
  const values = calcoScheduleInputs(frm);
  if (!(values.qty > 0 && values.bom_no && (values.custom_machine || values.custom_production_line) && (values.custom_fg_requested_start || values.planned_start_date))) return;
  const identity = JSON.stringify(values), request = frm.__fg_schedule_request = (frm.__fg_schedule_request || 0) + 1;
  const response = await frappe.call({method:'calco_erp.calco_production.fg_schedule_preview.preview',args:{work_order:identity}});
  if (request !== frm.__fg_schedule_request || JSON.stringify(calcoScheduleInputs(frm)) !== identity) return;
  const result=response.message;
  if (result.reason_required && !String(frm.doc.custom_fg_planning_change_reason || '').trim()) {
    const reason = await calcoPlanningReason(frm, result);
    if (!reason) { frappe.validated = false; throw new Error(__('Planning revision was not applied. Enter a reason before saving.')); }
    if (JSON.stringify(calcoScheduleInputs(frm)) !== identity) return;
    await frm.set_value('custom_fg_planning_change_reason', reason);
  }
  frm.__fg_schedule_applying=true;
  try {
    await frm.set_value('custom_fg_requested_start',result.requested_start);
    await frm.set_value('planned_start_date',result.start);
    await frm.set_value('planned_end_date',result.end);
  } finally {frm.__fg_schedule_applying=false;}
  const escape=v=>frappe.utils.escape_html(String(v || ''));
  frm.set_intro(`Requested: ${escape(result.requested_start)}<br>ERPNext feasible: ${escape(result.start)} → ${escape(result.end)}<br>${escape(result.notice)}`, 'blue');
  if (result.reason_required) frm.set_intro(__('Planning Approval Required - save this revision before approval.'), 'orange');
  if (saveRevision === true && result.reason_required) await frm.save();
  return result;
}
async function calcoScheduleLineChanged(frm,field) {
  if (frm.__fg_schedule_applying || !(frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1' || frm.doc.__fg_dashboard_context)) return;
  const other=field==='custom_machine'?'custom_production_line':'custom_machine';
  frm.__fg_schedule_applying=true;
  try {await frm.set_value(other,frm.doc[field]);} finally {frm.__fg_schedule_applying=false;}
  return calcoPreviewSchedule(frm);
}
frappe.ui.form.on('Work Order', {
  qty: calcoPreviewSchedule,
  bom_no: calcoPreviewSchedule,
  custom_fg_requested_start: calcoPreviewSchedule,
  custom_machine: frm=>calcoScheduleLineChanged(frm,'custom_machine'),
  custom_production_line: frm=>calcoScheduleLineChanged(frm,'custom_production_line'),
  async planned_start_date(frm) {
    if (frm.__fg_schedule_applying || !(frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1' || frm.doc.__fg_dashboard_context)) return;
    frm.__fg_schedule_applying=true;
    try {await frm.set_value('custom_fg_requested_start',frm.doc.planned_start_date);} finally {frm.__fg_schedule_applying=false;}
    return calcoPreviewSchedule(frm);
  },
  before_save: calcoPreviewSchedule,
  before_submit(frm) {
    if (frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1' && !calcoCurrentPlanningApproved(frm)) {
      frappe.throw(__('Save the revision and Approve Planning Revision before Submit.'));
    }
  },
  after_save: calcoRestorePlanningActions,
  refresh(frm) {
    if (frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1') frappe.after_ajax(() => calcoRestorePlanningActions(frm));
    if (frm.doc.custom_fg_planning_origin === 'fg-dashboard-v1' && frm.doc.docstatus === 0)
      frm.add_custom_button(__('Preview Feasible Schedule'),()=>calcoPreviewSchedule(frm, true), __('Planning'));
  }
});

function calcoPlanningReason(frm, preview) {
  if (frm.__fg_reason_dialog) return frm.__fg_reason_dialog;
  frm.__fg_reason_dialog = new Promise(resolve => {
    let applied = false;
    const escape = v => frappe.utils.escape_html(String(v || ''));
    const approved = preview?.approved_schedule || {};
    const fields = [];
    if (preview) fields.push({fieldtype:'HTML', fieldname:'schedules', options:
      `<p>Current Approved Schedule: ${escape(approved.planned_start_date)} to ${escape(approved.planned_end_date)}</p><p>ERPNext Feasible Schedule: ${escape(preview.start)} to ${escape(preview.end)}</p>`});
    fields.push({fieldtype:'Small Text',fieldname:'reason',label:__('Reason for Change'),reqd:1,default:frm.doc.custom_fg_planning_change_reason || ''});
    const dialog = new frappe.ui.Dialog({title:__(preview ? 'Schedule Revision Required' : 'Planning Change Reason'),fields,
      primary_action_label:__(preview ? 'Apply Schedule Revision' : 'Use Planning Change Reason'),
      primary_action(values) {
        const reason = String(values.reason || '').trim();
        if (!reason) { frappe.msgprint(__('Reason for Change is mandatory.')); return; }
        applied = true; dialog.hide(); resolve(reason);
      }, onhide() { if (!applied) resolve(null); }
    });
    dialog.show();
  });
  return frm.__fg_reason_dialog.finally(() => { frm.__fg_reason_dialog = null; });
}

function calcoCurrentPlanningApproved(frm) {
  const events = JSON.parse(frm.doc.custom_fg_planning_events || '[]');
  const revision = [...events].reverse().find(e => e.event === 'Revision');
  return !!revision && events.some(e => e.event === 'Approved' && e.sequence > revision.sequence);
}
function calcoRestorePlanningActions(frm) {
  if (frm.doc.custom_fg_planning_origin !== 'fg-dashboard-v1' || frm.doc.docstatus !== 0) return;
  // Native toolbar owns permission, workflow, dirty-state and Save/Submit callbacks.
  // Never replace it with a custom Submit button or alter permissions/save_disabled.
  frm.toolbar.set_primary_action();
  if (frm.is_dirty()) frm.set_intro(__('Save this revision before planning approval or Submit.'), 'orange');
  else if (!calcoCurrentPlanningApproved(frm)) frm.set_intro(__('Planning Approval Required'), 'orange');
}
