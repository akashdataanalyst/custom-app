frappe.provide("calco_erp.production_execution");

(function () {
  const TRACKER_FIELDS = ["journey_tracker_html", "custom_production_journey_html", "custom_job_card_execution_journey_html"];
  const STYLE_ID = "calco-production-execution-style";
  const SUPPORTED_DOCTYPES = ["Production Requirement", "Production Job Card", "Work Order", "Job Card"];

  function esc(value) {
    return frappe.utils.escape_html(value == null ? "" : String(value));
  }

  function ensureStyle() {
    let style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }
    style.textContent = `
      .calco-production-journey {
        padding: 16px;
        border: 1px solid var(--border-color);
        border-radius: 8px;
        background: var(--fg-color);
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.05);
      }
      .calco-production-journey__header { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:14px; }
      .calco-production-journey__title { font-size:16px; font-weight:700; }
      .calco-production-journey__subtitle { font-size:12px; color:var(--text-muted); }
      .calco-production-journey__summary { display:grid; grid-template-columns:repeat(auto-fit, minmax(132px, 1fr)); gap:10px; margin-bottom:14px; }
      .calco-production-journey__metric { border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; background:#fff; }
      .calco-production-journey__metric-label { font-size:11px; text-transform:uppercase; color:var(--text-muted); margin-bottom:4px; }
      .calco-production-journey__metric-value { font-size:16px; font-weight:700; overflow-wrap:anywhere; }
      .calco-production-journey__flow { display:flex; gap:10px; overflow-x:auto; padding-bottom:4px; }
      .calco-production-journey__stage { min-width:190px; border:1px solid var(--border-color); border-radius:8px; background:var(--fg-color); padding:14px; box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8); cursor:pointer; }
      .calco-production-journey__stage-label { font-size:13px; font-weight:700; margin-bottom:8px; }
      .calco-production-journey__stage-status { display:inline-flex; border-radius:999px; padding:4px 8px; font-size:11px; font-weight:700; margin-bottom:8px; background:rgba(15, 23, 42, 0.06); }
      .calco-production-journey__stage-summary { font-size:12px; color:var(--text-muted); line-height:1.45; }
      .calco-production-journey__connector { flex:0 0 24px; align-self:center; height:2px; background:#cbd5e1; }
      .calco-production-journey__state { font-size:13px; color:var(--text-muted); padding:12px 2px; }
      .calco-production-journey__stage[data-color="grey"], .calco-wo-journey__stage[data-color="grey"], .calco-jc-cockpit__journey-stage[data-color="grey"] { --stage-color:#94a3b8; --stage-soft:#f1f5f9; --stage-text:#475569; }
      .calco-production-journey__stage[data-color="blue"], .calco-wo-journey__stage[data-color="blue"], .calco-jc-cockpit__journey-stage[data-color="blue"] { --stage-color:#1565c0; --stage-soft:#eff6ff; --stage-text:#1d4ed8; }
      .calco-production-journey__stage[data-color="green"], .calco-wo-journey__stage[data-color="green"], .calco-jc-cockpit__journey-stage[data-color="green"] { --stage-color:#2e7d32; --stage-soft:#ecfdf3; --stage-text:#15803d; }
      .calco-production-journey__stage[data-color="red"], .calco-wo-journey__stage[data-color="red"], .calco-jc-cockpit__journey-stage[data-color="red"] { --stage-color:#c62828; --stage-soft:#fff1f1; --stage-text:#b42318; }
      .calco-production-journey-dialog__summary { padding:12px 14px; border-radius:8px; background:rgba(15, 23, 42, 0.04); margin-bottom:12px; font-size:13px; }
      .calco-wo-journey { padding: 16px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--fg-color); box-shadow: 0 6px 16px rgba(15, 23, 42, 0.05); }
      .calco-wo-journey__header { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; }
      .calco-wo-journey__title { font-size:16px; font-weight:700; }
      .calco-wo-journey__subtitle { margin-top:3px; font-size:12px; color:var(--text-muted); }
      .calco-wo-journey__refresh { white-space:nowrap; }
      .calco-wo-journey__summary { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:14px; }
      .calco-wo-journey__metric { border:1px solid var(--border-color); border-radius:8px; padding:10px 12px; background:#fff; }
      .calco-wo-journey__metric-label { font-size:11px; text-transform:uppercase; color:var(--text-muted); margin-bottom:4px; }
      .calco-wo-journey__metric-value { font-size:15px; font-weight:700; overflow-wrap:anywhere; }
      .calco-wo-journey__readiness { display:grid; grid-template-columns:repeat(5, minmax(110px, 1fr)); gap:8px; margin-bottom:12px; }
      .calco-wo-journey__uat-warning { grid-column:1 / -1; margin:0; }
      .calco-wo-journey__readiness-check { padding:9px 10px; border:1px solid var(--border-color); border-radius:6px; background:#fff; }
      .calco-wo-journey__readiness-check.is-ready { border-left:3px solid #2e7d32; }
      .calco-wo-journey__readiness-check.is-blocked { border-left:3px solid #c62828; }
      .calco-wo-journey__readiness-label { font-size:11px; color:var(--text-muted); }
      .calco-wo-journey__readiness-value { margin-top:2px; font-size:12px; font-weight:700; }
      .calco-wo-journey__current { display:grid; grid-template-columns:repeat(3, minmax(140px, 1fr)) auto; gap:12px; align-items:center; margin-bottom:14px; padding:12px; border:1px solid var(--border-color); border-left:4px solid var(--stage-color, #1565c0); border-radius:6px; background:var(--stage-soft, #eff6ff); }
      .calco-wo-journey__current-heading { grid-column:1 / -1; font-size:13px; font-weight:700; color:#1f2937; }
      .calco-wo-journey__current-label { font-size:11px; text-transform:uppercase; color:var(--text-muted); margin-bottom:3px; }
      .calco-wo-journey__current-value { font-size:13px; font-weight:700; color:#1f2937; overflow-wrap:anywhere; }
      .calco-wo-journey__current-blocker { grid-column:1 / -1; color:#b42318; font-size:12px; }
      .calco-wo-journey__next-action { justify-self:end; white-space:nowrap; }
      .calco-wo-journey__strip { display:flex; gap:10px; overflow-x:auto; padding:2px 2px 120px; margin-bottom:-104px; scroll-snap-type:x proximity; }
      .calco-wo-journey__connector { flex:0 0 22px; align-self:center; height:2px; background:#cbd5e1; }
      .calco-wo-journey__stage { position:relative; flex:0 0 164px; min-height:116px; border:1px solid var(--border-color); border-radius:8px; padding:12px; background:#fff; box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8); cursor:default; text-align:left; scroll-snap-align:start; transition:box-shadow .15s ease, transform .15s ease, border-color .15s ease; }
      .calco-wo-journey__stage:hover { transform:translateY(-1px); box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8), 0 8px 18px rgba(15, 23, 42, 0.08); }
      .calco-wo-journey__stage.is-current { border-color:var(--stage-color, #1565c0); box-shadow: inset 0 4px 0 var(--stage-color, #1565c0), 0 0 0 2px var(--stage-soft, #eff6ff); }
      .calco-wo-journey__stage-title { font-size:13px; font-weight:700; line-height:1.25; min-height:32px; color:#1f2937; }
      .calco-wo-journey__status { display:inline-flex; align-items:center; justify-content:center; max-width:100%; border-radius:999px; padding:4px 8px; background:var(--stage-soft, #f1f5f9); color:var(--stage-text, #475569); font-size:11px; font-weight:700; white-space:nowrap; margin-top:8px; }
      .calco-wo-journey__doc-no { display:block; margin-top:8px; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; font-weight:700; color:#344054; }
      .calco-wo-journey__blocked-flag { display:inline-flex; align-items:center; margin-top:8px; border-radius:999px; padding:3px 7px; background:#fff1f1; color:#b42318; font-size:11px; font-weight:700; }
      .calco-wo-journey__details { display:none; position:absolute; z-index:20; left:10px; right:10px; top:calc(100% - 2px); padding:10px; border:1px solid var(--border-color); border-radius:8px; background:#fff; box-shadow:0 12px 24px rgba(15, 23, 42, 0.16); color:#344054; font-size:12px; line-height:1.4; }
      .calco-wo-journey__details-row + .calco-wo-journey__details-row { margin-top:6px; }
      .calco-wo-journey__details-label { color:var(--text-muted); font-weight:700; }
      .calco-wo-journey__stage:hover .calco-wo-journey__details, .calco-wo-journey__stage:focus .calco-wo-journey__details { display:block; }
      .calco-wo-journey__doc-chip, .calco-wo-journey__action { display:none; }
      .calco-jc-cockpit { box-shadow:none; }
      .calco-jc-cockpit__standard-controls { margin-bottom:14px; padding-bottom:12px; border-bottom:1px solid var(--border-color); }
      .calco-jc-cockpit__standard-controls .job-card-dashboard-widget { margin:0 !important; padding:0 !important; border:0 !important; background:transparent !important; }
      .calco-jc-cockpit__standard-controls .job-card-dashboard-widget > div { flex-direction:row-reverse; }
      .calco-jc-cockpit__standard-controls-empty { display:flex; justify-content:flex-end; color:var(--text-muted); font-size:12px; }
      .calco-jc-cockpit__block { margin-top:14px; }
      .calco-jc-cockpit__section-title { margin:0 0 7px; font-size:12px; font-weight:700; text-transform:uppercase; }
      .calco-jc-cockpit__section-note { margin:-3px 0 7px; color:var(--text-muted); font-size:11px; }
      .calco-jc-cockpit__context { display:grid; grid-template-columns:repeat(8, minmax(88px, 1fr)); margin-bottom:0; border-top:1px solid var(--border-color); border-bottom:1px solid var(--border-color); font-size:12px; }
      .calco-jc-cockpit__context-item { min-width:0; padding:8px 9px; border-right:1px solid var(--border-color); }
      .calco-jc-cockpit__context-item:last-child { border-right:0; }
      .calco-jc-cockpit__context-label { display:block; margin-bottom:2px; color:var(--text-muted); font-size:10px; font-weight:700; text-transform:uppercase; }
      .calco-jc-cockpit__context-value { display:block; overflow-wrap:anywhere; color:var(--text-color); font-weight:600; }
      .calco-jc-cockpit__journey { display:flex; align-items:center; gap:7px; overflow-x:auto; padding:7px 0 2px; }
      .calco-jc-cockpit__journey-stage { display:inline-flex; align-items:center; gap:6px; flex:0 0 auto; color:var(--text-muted); font-size:12px; font-weight:600; }
      button.calco-jc-cockpit__journey-stage { padding:0; border:0; background:transparent; cursor:pointer; }
      button.calco-jc-cockpit__journey-stage:hover { color:var(--text-color); text-decoration:underline; }
      .calco-jc-cockpit__journey-stage::before { width:7px; height:7px; border-radius:50%; background:var(--stage-color, #94a3b8); content:""; }
      .calco-jc-cockpit__journey-stage.is-current { color:var(--text-color); font-weight:700; }
      .calco-jc-cockpit__journey-arrow { flex:0 0 auto; color:var(--text-muted); }
      .calco-jc-cockpit__wip { margin:14px 0; overflow-x:auto; }
      .calco-jc-cockpit__table { width:100%; border-collapse:collapse; font-size:12px; }
      .calco-jc-cockpit__table th, .calco-jc-cockpit__table td { padding:7px 8px; border-bottom:1px solid var(--border-color); text-align:left; white-space:nowrap; }
      .calco-jc-cockpit__table th { color:var(--text-muted); font-size:11px; font-weight:700; text-transform:uppercase; }
      .calco-jc-cockpit__table td.is-number, .calco-jc-cockpit__table th.is-number { text-align:right; }
      .calco-jc-cockpit__groups { display:grid; gap:14px; margin-top:14px; }
      .calco-jc-cockpit__group-title { margin-bottom:7px; font-size:12px; font-weight:700; color:var(--text-muted); text-transform:uppercase; }
      .calco-jc-cockpit__tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:8px; }
      .calco-jc-cockpit__tile { min-height:96px; padding:9px 10px; border:1px solid var(--border-color); border-radius:6px; background:#fff; }
      .calco-jc-cockpit__tile-head { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; }
      .calco-jc-cockpit__tile-title { font-size:13px; font-weight:700; }
      .calco-jc-cockpit__tile-status { color:var(--text-muted); font-size:11px; font-weight:700; }
      .calco-jc-cockpit__tile-code { margin-top:5px; font-size:11px; color:#344054; }
      .calco-jc-cockpit__tile-meta { display:grid; grid-template-columns:1fr 1fr; gap:4px 8px; margin-top:9px; font-size:11px; color:var(--text-muted); }
      .calco-jc-cockpit__tile-actions { display:flex; gap:6px; margin-top:9px; }
      .calco-jc-cockpit__qi-link { white-space:nowrap; }
      @media (max-width: 1100px) { .calco-jc-cockpit__context { grid-template-columns:repeat(4, minmax(110px, 1fr)); } .calco-jc-cockpit__context-item:nth-child(4n) { border-right:0; } }
      @media (max-width: 768px) { .calco-wo-journey__header { flex-direction:column; } .calco-wo-journey__refresh { width:100%; } .calco-wo-journey__readiness { grid-template-columns:repeat(2, minmax(110px, 1fr)); } .calco-wo-journey__current { grid-template-columns:1fr; } .calco-wo-journey__current-blocker { grid-column:1; } .calco-wo-journey__next-action { justify-self:stretch; width:100%; } .calco-wo-journey__stage { flex-basis:150px; } .calco-jc-cockpit__standard-controls .job-card-dashboard-widget > div { align-items:stretch !important; flex-direction:column-reverse; } .calco-jc-cockpit__context { grid-template-columns:repeat(2, minmax(120px, 1fr)); } .calco-jc-cockpit__context-item:nth-child(2n) { border-right:0; } }
    `;
  }

  function getWrapper(frm) {
    return TRACKER_FIELDS.reduce((wrapper, fieldname) => wrapper || (frm.fields_dict[fieldname] ? frm.fields_dict[fieldname].$wrapper : null), null);
  }

  function renderState(wrapper, message) {
    wrapper.html(`
      <section class="calco-production-journey">
        <div class="calco-production-journey__header">
          <div>
            <div class="calco-production-journey__title">${__("Production Execution Journey")}</div>
            <div class="calco-production-journey__subtitle">${__("Production execution stops at FG Quarantine in Phase 3A")}</div>
          </div>
        </div>
        <div class="calco-production-journey__state">${esc(message || "")}</div>
      </section>
    `);
  }

  function buildMetrics(summary) {
    return [
      ["Requirement", summary.requirement || "-"],
      ["Work Order", summary.work_order || "-"],
      ["Job Card", summary.job_card || "-"],
      ["FG Batch", summary.fg_batch_no || "-"],
      ["Grade", summary.grade_code || "-"],
      ["Planned Qty", `${summary.planned_qty || 0} Kg`],
      ["Actual Qty", `${summary.actual_qty || 0} Kg`],
    ].map(([label, value]) => `
      <div class="calco-production-journey__metric">
        <div class="calco-production-journey__metric-label">${esc(label)}</div>
        <div class="calco-production-journey__metric-value">${esc(value)}</div>
      </div>
    `).join("");
  }

  function buildFlow(stages) {
    return (stages || []).map((stage, index) => `
      <button type="button" class="calco-production-journey__stage" data-color="${esc(stage.color)}" data-stage-key="${esc(stage.key)}">
        <div class="calco-production-journey__stage-label">${esc(stage.label)}</div>
        <div class="calco-production-journey__stage-status">${esc(stage.status)}</div>
        <div class="calco-production-journey__stage-summary">${esc(stage.summary || "")}</div>
      </button>
      ${index < stages.length - 1 ? '<div class="calco-production-journey__connector"></div>' : ""}
    `).join("");
  }

  function openStageDialog(stage) {
    const dialog = new frappe.ui.Dialog({ title: `${stage.label} - ${stage.status}`, fields: [{ fieldtype: "HTML", fieldname: "content" }], size: "large" });
    dialog.show();
    const routeHtml = stage.route ? `<div style="margin-top:10px;"><a href="#" class="calco-production-open-doc">${__("Open linked document")}</a></div>` : "";
    dialog.fields_dict.content.$wrapper.html(`<div class="calco-production-journey-dialog__summary">${esc(stage.summary || "")}</div>${routeHtml}`);
    dialog.$wrapper.on("click", ".calco-production-open-doc", (event) => {
      event.preventDefault();
      if (stage.route) frappe.set_route(...stage.route);
      dialog.hide();
    });
  }

  function renderTracker(frm, payload) {
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    wrapper.html(`
      <section class="calco-production-journey">
        <div class="calco-production-journey__header">
          <div>
            <div class="calco-production-journey__title">${__("Production Execution Journey")}</div>
            <div class="calco-production-journey__subtitle">${__("Weekly planning to FG Quarantine only")}</div>
          </div>
        </div>
        <div class="calco-production-journey__summary">${buildMetrics(payload.summary || {})}</div>
        <div class="calco-production-journey__flow">${buildFlow(payload.stages || [])}</div>
      </section>
    `);
    wrapper.find(".calco-production-journey__stage").on("click", (event) => {
      const stageKey = event.currentTarget.dataset.stageKey;
      const stage = (payload.stages || []).find((row) => row.key === stageKey);
      if (stage) openStageDialog(stage);
    });
  }

  function buildWorkOrderSummary(summary, frm) {
    return [
      ["FG Item", [summary.fg_item, summary.fg_item_name].filter(Boolean).join(" - ") || "-"],
      ["Planned Qty", `${summary.planned_qty || 0} Kg`],
      ["Produced Qty", `${frm.doc.produced_qty || 0} Kg`],
      ["Machine", frm.doc.custom_machine || frm.doc.custom_production_line || "-"],
      ["FG Batch", summary.fg_batch_no || __("Not Generated")],
    ].map(([label, value]) => `
      <div class="calco-wo-journey__metric">
        <div class="calco-wo-journey__metric-label">${esc(label)}</div>
        <div class="calco-wo-journey__metric-value">${esc(value)}</div>
      </div>
    `).join("");
  }

  function normalizeWorkOrderStages(stages) {
    return (stages || []).map((stage) => ({ ...stage }));
  }

  function getCurrentStage(stages) {
    const completed = new Set(["Completed", "Completed - Not Required"]);
    return (stages || []).find((stage) => !stage.historical && !completed.has(stage.status)) || [...(stages || [])].reverse()[0] || {};
  }
  function buildWorkOrderStageDetails(stage) {
    const rows = [
      [__("Role"), stage.responsible_role || "-"],
      [__("Action"), stage.action_label || __("Open Work Order")],
      [__("Linked"), stage.linked_docname ? `${stage.linked_doctype}: ${stage.linked_docname}` : "-"],
    ];
    if (stage.blocked_reason) rows.push([__("Blocked"), stage.blocked_reason]);
    const execution = stage.execution_details || {};
    if (stage.key === "production_execution") {
      rows.push(
        [__("Active Job Card"), execution.active_job_card || "-"],
        [__("Operation"), execution.operation || "-"],
        [__("Execution Stage"), execution.execution_stage || "-"],
        [__("Machine"), execution.machine || "-"],
        [__("Operator"), execution.operator || "-"],
        [__("Shift"), execution.shift || "-"],
        [__("FG Batch"), execution.fg_batch_no || __("Not Generated")],
        [__("Produced / Planned"), `${execution.produced_qty || 0} / ${execution.planned_qty || 0} Kg`]
      );
    }
    return rows.map(([label, value]) => `
      <div class="calco-wo-journey__details-row">
        <span class="calco-wo-journey__details-label">${esc(label)}:</span> ${esc(value)}
      </div>
    `).join("");
  }

  function buildWorkOrderStages(stages) {
    const currentKey = getCurrentStage(stages).key;
    return (stages || []).map((stage, index) => `
      <div class="calco-wo-journey__stage ${stage.key === currentKey ? "is-current" : ""}" data-color="${esc(stage.color)}" data-stage-key="${esc(stage.key)}" title="${esc(stage.blocked_reason || stage.summary || "")}">
        <div class="calco-wo-journey__stage-title">${esc(stage.label)}</div>
        <div class="calco-wo-journey__status">${esc(stage.status)}</div>
        ${stage.linked_docname ? `<span class="calco-wo-journey__doc-no">${esc(stage.linked_docname)}</span>` : ""}
        ${stage.status === "Blocked" ? `<span class="calco-wo-journey__blocked-flag">${__("Blocked")}</span>` : ""}
        <div class="calco-wo-journey__details">${buildWorkOrderStageDetails(stage)}</div>
      </div>
      ${index < stages.length - 1 ? '<div class="calco-wo-journey__connector"></div>' : ""}
    `).join("");
  }

  function buildCompactReadiness(frm, payload) {
    if (payload?.summary?.production_completed_display) return `<div class="alert alert-success">${__("Production Completed")}</div>`;
    const result = (payload || {}).readiness || frm.__production_readiness_data;
    const expectedChecks = [
      ["material", "Material"],
      ["machine", "Machine"],
      ["bom", "BOM"],
      ["planning", "Planning"],
    ];
    const checkByKey = Object.fromEntries(((result && result.checks) || []).map((check) => [check.key, check]));
    const checks = expectedChecks.map(([key, label]) => {
      const check = checkByKey[key] || {};
      const status = result ? check.status || __("Not Ready") : __("Evaluating");
      return `
        <div class="calco-wo-journey__readiness-check ${check.ready ? "is-ready" : result ? "is-blocked" : ""}">
          <div class="calco-wo-journey__readiness-label">${esc(__(label))}</div>
          <div class="calco-wo-journey__readiness-value">${esc(__(status))}</div>
        </div>
      `;
    });
    if (result && result.uat_rm_release_override_active) {
      checks.unshift(`
        <div class="alert alert-warning calco-wo-journey__uat-warning">
          <strong>${__("UAT RM RELEASE OVERRIDE ACTIVE")}</strong>
          ${
            result.uat_rm_release_override_used
              ? `<div>${__("Physical Recovery stock is being used without exact RM Release evidence.")}</div>`
              : ""
          }
        </div>
      `);
    }
    checks.push(`
      <div class="calco-wo-journey__readiness-check ${result && result.ready ? "is-ready" : result ? "is-blocked" : ""}">
        <div class="calco-wo-journey__readiness-label">${__("Overall Result")}</div>
        <div class="calco-wo-journey__readiness-value">${esc(result ? __(result.overall_result) : __("Evaluating"))}</div>
      </div>
    `);
    return `<section class="calco-wo-journey__readiness">${checks.join("")}</section>`;
  }
  function buildCurrentAction(stage) {
    if (!stage || !stage.key) return "";
    const actionLabel = stage.action_label || __("Open Work Order");
    return `
      <section class="calco-wo-journey__current" data-color="${esc(stage.color || "blue")}">
        <div class="calco-wo-journey__current-heading">${__("Current Action")}</div>
        <div>
          <div class="calco-wo-journey__current-label">${__("Current Stage")}</div>
          <div class="calco-wo-journey__current-value">${esc(stage.label || "-")}</div>
        </div>
        <div>
          <div class="calco-wo-journey__current-label">${__("Current Owner")}</div>
          <div class="calco-wo-journey__current-value">${esc(stage.responsible_role || "-")}</div>
        </div>
        <div>
          <div class="calco-wo-journey__current-label">${__("Next Action")}</div>
          <div class="calco-wo-journey__current-value">${esc(actionLabel)}</div>
        </div>
        <button type="button" class="btn btn-sm btn-primary calco-wo-journey__next-action" ${stage.action_disabled ? "disabled" : ""}>${esc(actionLabel)}</button>
        ${stage.blocked_reason ? `<div class="calco-wo-journey__current-blocker"><strong>${__("Exact Blocker")}:</strong> ${esc(stage.blocked_reason)}</div>` : ""}
      </section>
    `;
  }

  function renderWorkOrderJourney(frm, payload) {
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    const stages = normalizeWorkOrderStages((payload || {}).stages || []);
    const currentStage = getCurrentStage(stages);
    frm.__work_order_journey_data = { ...(payload || {}), stages };
    wrapper.html(`
      <section class="calco-wo-journey">
        <div class="calco-wo-journey__header">
          <div>
            <div class="calco-wo-journey__title">${__("Production Journey")}</div>
            <div class="calco-wo-journey__subtitle">${__("What has happened, what is happening, and what happens next")}</div>
          </div>
          <button type="button" class="btn btn-xs btn-default calco-wo-journey__refresh">${__("Refresh")}</button>
        </div>
        <div class="calco-wo-journey__summary">${buildWorkOrderSummary((payload || {}).summary || {}, frm)}</div>
        ${buildCompactReadiness(frm, payload)}
        ${buildCurrentAction(currentStage)}
        <div class="calco-wo-journey__strip">${buildWorkOrderStages(stages)}</div>
        ${buildInProcessQc(payload.in_process_qc)}
      </section>
    `);
    bindWorkOrderJourneyActions(frm, wrapper);
    bindInProcessQc(frm, wrapper);
  }

  async function runReadinessAction(frm, refresh = false) {
    const api = calco_erp.production_readiness || {};
    if (refresh && api.refresh) {
      await api.refresh(frm, true);
      return;
    }
    if (api.open_dialog) await api.open_dialog(frm);
  }

  async function runSelectedStageAction(frm, stage) {
    if (!stage) return;
    if (stage.key === "production_readiness") {
      await runReadinessAction(frm, !frm.__production_readiness_data);
      return;
    }
    if (stage.key === "material_reservation") {
      const api = calco_erp.material_reservation || {};
      const action = (stage.action_args || {}).reservation_action;
      if (stage.action_disabled) {
        frappe.msgprint(stage.blocked_reason || __("Complete Production Readiness first."));
        return;
      }
      if (action === "create" && api.open_creation_dialog) {
        await api.open_creation_dialog(frm);
        return;
      }
      if (action === "review" && api.review_reservation) {
        await api.review_reservation(frm);
        return;
      }
    }
    if (stage.linked_doctype && stage.linked_docname) {
      frappe.set_route("Form", stage.linked_doctype, stage.linked_docname);
      return;
    }
    if (stage.action_disabled) {
      frappe.msgprint(stage.blocked_reason || __("This action is not available yet."));
      return;
    }
    if (stage.action_label && stage.action_type) {
      await runWorkOrderStageAction(frm, stage);
      return;
    }
    frappe.set_route("Form", "Work Order", frm.doc.name);
  }

  function bindWorkOrderJourneyActions(frm, wrapper) {
    wrapper.find(".calco-wo-journey__refresh").on("click", () => loadWorkOrderJourney(frm));
    wrapper.find(".calco-wo-journey__next-action").on("click", async () => {
      await runSelectedStageAction(frm, getCurrentStage((frm.__work_order_journey_data || {}).stages || []));
    });
  }

  async function runWorkOrderStageAction(frm, stage) {
    if (stage.action_type === "route") {
      frappe.set_route(...stage.action_method_or_route);
      return;
    }
    if (stage.action_type !== "method" || !stage.action_method_or_route) return;
    if (
      stage.action_method_or_route ===
      "calco_erp.calco_production.manufacture_entry.make_controlled_manufacture_entry"
    ) {
      await openControlledManufacture(frm);
      return;
    }

    const response = await frappe.call({
      method: stage.action_method_or_route,
      args: stage.action_args || {},
      freeze: true,
    });
    const doc = response.message || {};
    if (Array.isArray(doc.route) && doc.route.length) {
      frappe.set_route(...doc.route);
      return;
    }
    if (doc.doctype) {
      frappe.model.sync(doc);
      frappe.set_route("Form", doc.doctype, doc.name || `new-${String(doc.doctype || "").toLowerCase().replace(/\\s+/g, "-")}`);
      return;
    }
    await frm.reload_doc();
    await loadWorkOrderJourney(frm);
  }

  async function openControlledManufacture(frm) {
    const previewResponse = await frappe.call({
      method: "calco_erp.calco_production.manufacture_entry.get_controlled_manufacture_preview",
      args: { work_order: frm.doc.name },
      freeze: true,
    });
    const preview = previewResponse.message || {};
    if (preview.existing_draft && preview.existing_draft.name) {
      frappe.set_route("Form", "Stock Entry", preview.existing_draft.name);
      return;
    }
    if (preview.existing && preview.existing.name) {
      frappe.set_route("Form", "Stock Entry", preview.existing.name);
      return;
    }
    if (!preview.can_create) {
      frappe.msgprint(preview.reason || __("Controlled Manufacture is not ready."));
      return;
    }
    if (!preview.requires_manual_input) {
      await createControlledManufacture(frm, {});
      return;
    }

    const dialog = new frappe.ui.Dialog({
      title: __("Create Manufacture Entry"),
      fields: [
        {
          fieldname: "actual_fg_qty",
          fieldtype: "Float",
          label: __("Actual FG Quantity"),
          reqd: 1,
        },
        {
          fieldname: "process_loss_qty",
          fieldtype: "Float",
          label: __("Actual Process Loss"),
          default: 0,
        },
        {
          fieldname: "execution_reason",
          fieldtype: "Small Text",
          label: __("Execution Reason / Evidence"),
          reqd: 1,
        },
      ],
      primary_action_label: __("Create Draft"),
      primary_action: async (values) => {
        dialog.hide();
        await createControlledManufacture(frm, values);
      },
    });
    dialog.show();
  }

  async function createControlledManufacture(frm, values) {
    const response = await frappe.call({
      method: "calco_erp.calco_production.manufacture_entry.make_controlled_manufacture_entry",
      args: { work_order: frm.doc.name, ...(values || {}) },
      freeze: true,
      freeze_message: __("Creating controlled Manufacture Entry..."),
    });
    const doc = response.message || {};
    if (!doc.doctype) return;
    frappe.model.sync(doc);
    frappe.set_route(
      "Form",
      doc.doctype,
      doc.name || "new-stock-entry"
    );
  }
  function buildJobCardSummary(summary) {
    const metrics = [
      ["Job Card", summary.job_card || "-"],
      ["Work Order", summary.work_order || "-"],
      ["Operation", summary.operation || "-"],
      ["FG Batch", summary.fg_batch_no || "-"],
      ["Machine", summary.machine || summary.production_line || "-"],
      ["Operator", summary.operator || "-"],
      ["Shift", summary.shift_type || "-"],
    ];
    if (summary.operation_profile === "compounding") {
      metrics.push(
        ["Grade Change Status", summary.grade_change_status || "-"],
        ["Cleaning Level", summary.grade_change_cleaning_level || "-"],
        ["Approval Status", summary.grade_change_approval_status || "-"]
      );
    }
    return metrics.map(([label, value]) => `
      <div class="calco-wo-journey__metric">
        <div class="calco-wo-journey__metric-label">${esc(label)}</div>
        <div class="calco-wo-journey__metric-value">${esc(value)}</div>
      </div>
    `).join("") + (summary.operation_profile === "compounding" && summary.grade_change_clearance ? `
      <div class="calco-wo-journey__metric">
        <div class="calco-wo-journey__metric-label">${__("Grade Change Clearance")}</div>
        <button type="button" class="btn btn-xs btn-default calco-grade-change-clearance-link" data-clearance="${esc(summary.grade_change_clearance)}">${esc(summary.grade_change_clearance)}</button>
      </div>
    ` : "");
  }

  function formatExecutionQty(value) {
    return `${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 })} Kg`;
  }

  function formatElapsed(seconds) {
    const total = Math.max(Number(seconds || 0), 0);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  }

  function buildCompoundingContext(context) {
    const values = [
      ["WO", context.work_order],
      ["FG", context.fg_item],
      ["BOM", context.bom],
      ["Line", context.production_line],
      ["Machine", context.machine],
      ["Shift", context.current_shift],
      ["Operator", context.operator],
      ["FG Batch", context.fg_batch || __("Not Generated")],
    ];
    return values.map(([label, value]) => `
      <div class="calco-jc-cockpit__context-item">
        <span class="calco-jc-cockpit__context-label">${esc(__(label))}</span>
        <span class="calco-jc-cockpit__context-value">${esc(value || "-")}</span>
      </div>
    `).join("");
  }

  function buildCompoundingJourney(stages) {
    const currentKey = getCurrentStage(stages).key;
    const labels = { job_card_created: __("Created"), compounding_complete: __("Complete") };
    return (stages || []).map((stage, index) => {
      const className = `calco-jc-cockpit__journey-stage ${stage.key === currentKey ? "is-current" : ""}`;
      const label = labels[stage.key] || stage.label;
      const stageHtml = stage.linked_doctype && stage.linked_docname
        ? `<button type="button" class="${className} calco-jc-cockpit__journey-link" data-color="${esc(stage.color)}" data-doctype="${esc(stage.linked_doctype)}" data-docname="${esc(stage.linked_docname)}" title="${esc(stage.status || "")}">${esc(label)}</button>`
        : `<span class="${className}" data-color="${esc(stage.color)}" title="${esc(stage.status || "")}">${esc(label)}</span>`;
      const connector = (stages[index + 1] || {}).parallel_with === stage.key ? "&#8741;" : "&rarr;";
      return `${stageHtml}${index < stages.length - 1 ? `<span class="calco-jc-cockpit__journey-arrow">${connector}</span>` : ""}`;
    }).join("");
  }

  function attachStandardJobCardControls(frm, wrapper) {
    const target = wrapper.find(".calco-jc-cockpit__standard-controls");
    const dashboardField = frm.fields_dict.job_card_dashboard;
    const dashboard = dashboardField ? $(dashboardField.wrapper).find(".job-card-dashboard-widget").first() : $();
    if (!dashboard.length) {
      const cockpit = (frm.__job_card_execution_journey_data || {}).cockpit || {};
      const context = cockpit.context || {};
      target.html(`<div class="calco-jc-cockpit__standard-controls-empty">${__("Elapsed")} ${esc(formatElapsed(context.elapsed_seconds))}</div>`);
      return false;
    }
    target.empty().append(dashboard.detach());
    return true;
  }

  function restoreStandardJobCardControls(frm, wrapper) {
    const dashboard = wrapper.find(".job-card-dashboard-widget").first();
    const dashboardField = frm.fields_dict.job_card_dashboard;
    if (dashboard.length && dashboardField) {
      $(dashboardField.wrapper).empty().append(dashboard.detach());
    }
  }

  const CONTROLLED_COMPOUNDING_LEGACY_FIELDS = [
    "custom_execution_capture_section",
    "custom_fg_batch_no",
    "custom_production_line",
    "custom_machine",
    "custom_operator",
    "custom_shift_type",
    "custom_grade_change_required",
    "custom_grade_change_status",
    "custom_rm_loading_status",
    "custom_compounding_status",
    "custom_pelletizing_status",
    "custom_initial_qc_status",
    "custom_final_qc_status",
    "custom_qc_correction_remarks",
    "custom_grade_change_section",
    "custom_grade_change_checklist",
    "custom_rm_loading_section",
    "custom_rm_loading_details",
    "custom_shift_report_section",
    "custom_shift_reports",
  ];
  const CONTROLLED_COMPOUNDING_TECHNICAL_FIELDS = [
    "work_order",
    "bom_no",
    "workstation",
    "operation",
    "posting_date",
    "company",
    "for_quantity",
    "wip_warehouse",
  ];
  const CONTROLLED_COMPOUNDING_SUPERSEDED_STANDARD_FIELDS = [
    "pending_qty",
    "total_completed_qty",
    "production_section",
    "source_warehouse",
    "workstation_type",
    "section_break_8",
    "items",
    "quality_inspection_section",
    "quality_inspection_template",
    "quality_inspection",
  ];

  function setControlledCompoundingFieldVisibility(frm, fieldname, hidden) {
    const field = frm.fields_dict[fieldname];
    if (!field) return;
    frm.__calco_compounding_field_visibility = frm.__calco_compounding_field_visibility || {};
    const original = frm.__calco_compounding_field_visibility;
    if (hidden) {
      if (!(fieldname in original)) original[fieldname] = Boolean(field.df.hidden);
      frm.toggle_display(fieldname, false);
      return;
    }
    if (!(fieldname in original)) return;
    frm.toggle_display(fieldname, !original[fieldname]);
    delete original[fieldname];
  }

  function applyControlledCompoundingLayout(frm, payload) {
    const hidden = Boolean((payload || {}).controlled_compounding);
    [
      ...CONTROLLED_COMPOUNDING_LEGACY_FIELDS,
      ...CONTROLLED_COMPOUNDING_TECHNICAL_FIELDS,
      ...CONTROLLED_COMPOUNDING_SUPERSEDED_STANDARD_FIELDS,
    ].forEach((fieldname) => setControlledCompoundingFieldVisibility(frm, fieldname, hidden));
    if (!hidden && frm.__calco_compounding_field_visibility) {
      delete frm.__calco_compounding_field_visibility;
    }
  }

  function buildWipContext(wip) {
    const rows = (wip.rows || []).map((row) => `
      <tr>
        <td>${esc(row.item_code)}</td><td>${esc(row.batch_no)}</td>
        <td class="is-number">${esc(formatExecutionQty(row.transferred_qty))}</td>
        <td class="is-number">${esc(formatExecutionQty(row.wo_attributed_available_qty))}</td>
        <td class="is-number">${esc(formatExecutionQty(row.consumed_qty))}</td>
        <td class="is-number">${esc(formatExecutionQty(row.returned_qty))}</td>
      </tr>
    `).join("");
    const totals = wip.totals || {};
    return `
      <div class="calco-jc-cockpit__wip">
        <div class="calco-jc-cockpit__section-title">${__("WO-Attributed WIP")}</div>
        <div class="calco-jc-cockpit__section-note">${__("Exact Item + Batch availability and posted WO history (including exhausted batches)")}</div>
        <table class="calco-jc-cockpit__table">
          <thead><tr><th>${__("Item")}</th><th>${__("Batch")}</th><th class="is-number">${__("Transferred")}</th><th class="is-number">${__("Available")}</th><th class="is-number">${__("Consumed")}</th><th class="is-number">${__("Returned")}</th></tr></thead>
          <tbody>${rows}<tr><th colspan="2">${__("Total")}</th><th class="is-number">${esc(formatExecutionQty(totals.transferred_qty))}</th><th class="is-number">${esc(formatExecutionQty(totals.wo_attributed_available_qty))}</th><th class="is-number">${esc(formatExecutionQty(totals.consumed_qty))}</th><th class="is-number">${esc(formatExecutionQty(totals.returned_qty))}</th></tr></tbody>
        </table>
      </div>
    `;
  }

  function buildCompoundingModules(groups) {
    return `<div class="calco-jc-cockpit__groups">${(groups || []).map((group) => `
      <section>
        <div class="calco-jc-cockpit__group-title">${esc(group.label)}</div>
        <div class="calco-jc-cockpit__tiles">${(group.modules || []).map((module) => `
          <div class="calco-jc-cockpit__tile">
            <div class="calco-jc-cockpit__tile-head">
              <div class="calco-jc-cockpit__tile-title">${esc(module.label)}</div>
              <div class="calco-jc-cockpit__tile-status">${esc(module.status)}</div>
            </div>
            <div class="calco-jc-cockpit__tile-code">${esc(module.document_code)} ${esc(module.revision)}</div>
            ${["premix", "blending"].includes(module.key) ? buildControlledRunSummary(module) : module.key === "silo_control" ? buildSiloSummary(module) : module.key === "feeder_run" ? buildFeederRunSummary(module) : module.key === "bulk_density" ? buildBulkDensitySummary(module) : module.key === "process_parameters" ? buildProcessParameterSummary(module) : module.key === "shift_reporting" ? buildShiftReportingSummary(module) : `<div class="calco-jc-cockpit__tile-meta">
              <span>${__("Records")}: ${esc(module.record_count)}</span>
              <span>${__("Last Event")}: ${esc(module.last_event || "-")}</span>
              <span>${__("Open Issue")}: ${esc(module.open_issue || "-")}</span>
              <span>${__("Shift")}: ${esc(module.current_shift || "-")}</span>
              <span>${__("Execution")}: ${module.execution_available ? __("Started") : __("Not Started")}</span>
            </div>`}
            ${module.key === "premix" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-premix-new" ${module.action_enabled ? "" : "disabled"}>${__("New Premix Run")}</button>
              <button type="button" class="btn btn-xs btn-default calco-premix-view">${__("View Runs")}</button>
            </div>` : module.key === "blending" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-blending-new" ${module.action_enabled ? "" : "disabled"}>${__("New Blending Run")}</button>
              <button type="button" class="btn btn-xs btn-default calco-blending-view">${__("View Runs")}</button>
            </div>` : module.key === "silo_control" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-silo-new" ${module.action_enabled ? "" : "disabled"}>${__("New Silo Control")}</button>
              <button type="button" class="btn btn-xs btn-default calco-silo-view">${__("View Records")}</button>
            </div>` : module.key === "feeder_run" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-feeder-run-new" ${module.action_enabled ? "" : "disabled"}>${__("New Feeder Run")}</button>
              <button type="button" class="btn btn-xs btn-default calco-feeder-run-view">${__("View Records")}</button>
            </div>` : module.key === "bulk_density" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-bulk-density-new" ${module.action_enabled ? "" : "disabled"}>${__("New Bulk Density Monitor")}</button>
              <button type="button" class="btn btn-xs btn-default calco-bulk-density-view">${__("View Records")}</button>
            </div>` : module.key === "process_parameters" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-process-parameter-new" ${module.action_enabled ? "" : "disabled"}>${__("New Process Parameter Monitor")}</button>
              <button type="button" class="btn btn-xs btn-default calco-process-parameter-view">${__("View Records")}</button>
            </div>` : module.key === "shift_reporting" ? `<div class="calco-jc-cockpit__tile-actions">
              <button type="button" class="btn btn-xs btn-primary calco-shift-report-new" ${module.action_enabled ? "" : "disabled"}>${__(module.current_report ? "Open Current Shift Report" : "New Current Shift Report")}</button>
              <button type="button" class="btn btn-xs btn-default calco-shift-report-view">${__("View Reports")}</button>
            </div>` : ""}
          </div>
        `).join("")}</div>
      </section>
    `).join("")}</div>`;
  }

  function buildControlledRunSummary(module) {
    const counts = module.counts || {};
    const output = module.controlled_output || {};
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Runs")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Completed")}: ${esc(counts.Completed || 0)}</span>
      <span>${__("In Progress")}: ${esc(counts["In Progress"] || 0)}</span>
      <span>${__("Draft")}: ${esc(counts.Draft || 0)}</span>
      <span>${__("Last Run")}: ${esc(module.latest_run_number || "-")}</span>
      <span>${__("Last Activity")}: ${esc(module.last_event || "-")}</span>
    </div>`;
  }

  function buildSiloSummary(module) {
    const counts = module.counts || {};
    const output = module.controlled_output || {};
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Optional")}</span>
      <span>${__("Records")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Completed")}: ${esc(counts.Completed || 0)}</span>
      <span>${__("Active")}: ${esc(counts.Active || 0)}</span>
      <span>${__("Draft")}: ${esc(counts.Draft || 0)}</span>
      <span>${__("Last Record")}: ${esc(module.latest_record || "-")}</span>
      <span>${__("Last Event")}: ${esc(module.last_event || "-")}</span>
      <span>${__("Silo / Feeder")}: ${esc(module.latest_silo || "-")} / ${esc(module.latest_feeder || "-")}</span>
      <span>${__("Material")}: ${esc(module.latest_material || "-")}</span>
    </div>`;
  }

  function buildFeederRunSummary(module) {
    const counts = module.counts || {};
    const output = module.controlled_output || {};
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Optional")}</span>
      <span>${__("Source")}: ${esc(module.source || "Feeder HMI")}</span>
      <span>${__("Records")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Completed")}: ${esc(counts.Completed || 0)}</span>
      <span>${__("Active")}: ${esc(counts.Active || 0)}</span>
      <span>${__("Awaiting Approval")}: ${esc(counts["Awaiting Approval"] || 0)}</span>
      <span>${__("Draft")}: ${esc(counts.Draft || 0)}</span>
      <span>${__("Active Feeders")}: ${esc(module.active_feeders || 0)}</span>
      <span>${__("Readings")}: ${esc(module.reading_count || 0)}</span>
      <span>${__("Online Changes")}: ${esc(module.online_change_count || 0)}</span>
      <span>${__("Last Reading")}: ${esc(module.last_reading || "-")}</span>
      <span>${__("Last Change")}: ${esc(module.last_change || "-")}</span>
      <span>${__("Prepared By")}: ${esc(module.prepared_by || "-")}</span>
      <span>${__("Approved By")}: ${esc(module.approved_by || "-")}</span>
    </div>`;
  }

  function buildBulkDensitySummary(module) {
    const latest = module.latest_bulk_density == null ? "-" : `${flt(module.latest_bulk_density).toFixed(3)} Kg/Ltr`;
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Optional")}</span>
      <span>${__("Records")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Readings")}: ${esc(module.reading_count || 0)}</span>
      <span>${__("Confirmed")}: ${esc(module.confirmed_count || 0)}</span>
      <span>${__("Latest Time")}: ${esc(module.latest_time || "-")}</span>
      <span>${__("Latest RM")}: ${esc(module.latest_rm || "-")}</span>
      <span>${__("Latest Batch")}: ${esc(module.latest_batch || "-")}</span>
      <span>${__("Latest Feeder")}: ${esc(module.latest_feeder || "-")}</span>
      <span>${__("Latest B.D")}: ${esc(latest)}</span>
      <span>${__("Last Activity")}: ${esc(module.last_event || "-")}</span>
    </div>`;
  }

  function buildProcessParameterSummary(module) {
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Optional")}</span>
      <span>${__("Monitors")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Observations")}: ${esc(module.observation_count || 0)}</span>
      <span>${__("MPDS")}: ${esc(module.mpds_no || module.mpds || "-")}</span>
      <span>${__("Last Activity")}: ${esc(module.last_event || "-")}</span>
      <span>${__("Shift")}: ${esc(module.current_shift || "-")}</span>
      ${module.open_issue ? `<span style="grid-column:1/-1;color:#b42318">${esc(module.open_issue)}</span>` : ""}
    </div>`;
  }

  function buildShiftReportingSummary(module) {
    const counts = module.counts || {};
    const output = module.controlled_output || {};
    return `<div class="calco-jc-cockpit__tile-meta calco-controlled-run-summary">
      <span>${__("Optional")}</span>
      <span>${__("Reports")}: ${esc(module.record_count || 0)}</span>
      <span>${__("Completed")}: ${esc(counts.Completed || 0)}</span>
      <span>${__("Active")}: ${esc(counts.Active || 0)}</span>
      <span>${__("Draft")}: ${esc(counts.Draft || 0)}</span>
      <span>${__("Current Shift")}: ${esc(module.current_occurrence?.shift || (module.current_shift_choice_required ? __("Select active shift") : "—"))}</span>
      <span>${__("Operational Date")}: ${esc(module.current_occurrence?.operational_shift_date || "—")}</span>
      ${output.enabled ? `<span>${__("Run Cumulative Output")}: ${esc(formatExecutionQty(output.current_cumulative))} ${esc(output.uom)}</span><span>${__("Planned")}: ${esc(formatExecutionQty(output.planned_qty))}</span><span>${__("Remaining")}: ${esc(formatExecutionQty(output.remaining_qty))}</span><span>${__("Last Output Reading")}: ${esc(output.last_reading?.reading_time || "—")}</span>` : `<span>${__("Latest Production Qty")}: ${esc(formatExecutionQty(module.latest_production_qty || 0))}</span>`}
      <span>${__("Last Activity")}: ${esc(module.last_event || "-")}</span>
    </div>`;
  }

  function buildInProcessQc(qc) {
    if (!qc || !qc.status) return "";
    const quality = (frappe.user_roles || []).some(r => ["Quality User", "Quality Manager"].includes(r)) || frappe.session.user === "Administrator";
    const manager = (frappe.user_roles || []).includes("Quality Manager") || frappe.session.user === "Administrator";
    const approver = (frappe.user_roles || []).some(r => ["Operations Head", "System Manager"].includes(r)) || frappe.session.user === "Administrator";
    const rows = qc.checkpoints || [];
    const periodic = rows.filter(r => r.checkpoint === "Periodic");
    const counts = {};
    periodic.forEach(r => { const state = r.state || "Available"; counts[state] = (counts[state] || 0) + 1; });
    const summary = Object.entries(counts).map(([state, count]) => `${count} ${state}`).join(" | ");
    const renderRow = row => {
      const disposition = row.disposition;
      const state = row.state || (row.satisfied ? "Satisfied" : row.available ? "Available" : "Awaiting end of batch");
      let action = "";
      if (manager && state === "Overdue" && !(row.inspections || []).length) action = "Record Disposition";
      if (approver && state === "Awaiting Escalation") action = "Approve Escalation";
      if (manager && state === "Awaiting Quality Closure") action = "Complete Disposition";
      const button = action ? `<button class="btn btn-xs btn-default calco-ipqc-missed" data-key="${esc(row.key)}" data-action="${esc(action)}">${__(action === "Record Disposition" ? "Record Missed Sample Disposition" : action)}</button>` : "";
      const evidence = disposition ? `<details><summary>${esc(disposition.reference)}</summary>${disposition.events.map(e => `<p>${esc(e.action)}: ${esc(e.reason || "")} ${esc(e.remarks)} — ${esc(e.by)} (${esc(e.on)}), ${esc(e.shift || "")} ${esc(e.quality_action || "")} ${esc(e.attachment || "")}</p>`).join("")}</details>` : (row.inspections || []).map(name => `<a href="/app/quality-inspection/${encodeURIComponent(name)}">${esc(name)}</a>`).join(", ");
      const due = row.trigger_basis ? `${row.trigger_at} ${row.trigger_basis}; window ${row.sampling_window || 0}` : "—";
      return `<tr><td>${esc(row.key)}</td><td>${row.mandatory ? __("Required") : __("Optional")}</td><td>${esc(state)}</td><td>${esc(due)}</td><td>${evidence}</td><td>${quality && row.available && row.can_create ? `<button class="btn btn-xs btn-default calco-ipqc-create" data-key="${esc(row.key)}">${__("Create QI")}</button>` : ""}${button}</td></tr>`;
    };
    const header = `<thead><tr><th>${__("Checkpoint")}</th><th>${__("Requirement")}</th><th>${__("State")}</th><th>${__("Due point / window")}</th><th>${__("Evidence")}</th><th></th></tr></thead>`;
    const checkpoints = `<table class="calco-jc-cockpit__table">${header}<tbody>${rows.filter(r => r.checkpoint !== "Periodic").map(renderRow).join("")}</tbody></table>${periodic.length ? `<details><summary>${__("Periodic")}: ${esc(summary)}</summary><div class="calco-ipqc-pages"><button class="btn btn-xs btn-default calco-ipqc-prev">${__("Previous")}</button> <span class="calco-ipqc-page-label"></span> <button class="btn btn-xs btn-default calco-ipqc-next">${__("Next")}</button><table class="calco-jc-cockpit__table">${header}<tbody class="calco-ipqc-periodic-rows">${periodic.map(renderRow).join("")}</tbody></table></div></details>` : ""}`;
    const inspections = (qc.inspections || []).map(row => `<tr><td><a href="/app/quality-inspection/${encodeURIComponent(row.name)}">${esc(row.name)}</a></td><td>${esc(row.custom_ipqc_key)}</td><td>${esc(row.status)} (${row.docstatus === 1 ? __("Submitted") : row.docstatus === 2 ? __("Cancelled") : __("Draft")})</td><td>${esc(row.custom_ipqc_shift || "-")}</td><td>${esc((row.actions || []).map(a => `${a.action}: ${a.remarks} (${a.by}, ${a.on})`).join("; "))}</td></tr>`).join("");
    return `<section class="calco-jc-cockpit__block"><h4>${__("In-Process QC")} — ${esc(qc.status)}</h4><p>${__("Runs in parallel with Compounding. All required QC and Quality holds must be resolved before Manufacture. Final FG QC remains downstream.")}</p>${checkpoints}<table class="calco-jc-cockpit__table"><thead><tr><th>${__("Quality Inspection")}</th><th>${__("Checkpoint")}</th><th>${__("Result")}</th><th>${__("Shift")}</th><th>${__("Quality / Production Actions")}</th></tr></thead><tbody>${inspections}</tbody></table><button class="btn btn-xs btn-default calco-ipqc-adjustment">${__("Record Production Adjustment")}</button></section>`;
  }

  function bindInProcessQc(frm, wrapper) {
    const tableRows = wrapper.find(".calco-ipqc-periodic-rows > tr");
    let page = 0;
    const pages = Math.max(1, Math.ceil(tableRows.length / 20));
    const showPage = () => {
      tableRows.hide().slice(page * 20, (page + 1) * 20).show();
      wrapper.find(".calco-ipqc-page-label").text(`${page + 1} / ${pages}`);
      wrapper.find(".calco-ipqc-prev").prop("disabled", page === 0);
      wrapper.find(".calco-ipqc-next").prop("disabled", page + 1 >= pages);
    };
    wrapper.find(".calco-ipqc-prev").on("click", () => { page = Math.max(0, page - 1); showPage(); });
    wrapper.find(".calco-ipqc-next").on("click", () => { page = Math.min(pages - 1, page + 1); showPage(); });
    showPage();
    wrapper.find(".calco-ipqc-missed").on("click", event => {
      const {key, action} = event.currentTarget.dataset;
      const fields = [{fieldname:"remarks", fieldtype:"Small Text", label:__("Quality / Approval Remarks"), reqd:1}];
      if (action === "Record Disposition") fields.unshift(
        {fieldname:"reason", fieldtype:"Small Text", label:__("Missed Sample Reason"), reqd:1},
        {fieldname:"shift", fieldtype:"Data", label:__("Applicable Shift"), reqd:1},
        {fieldname:"quality_action", fieldtype:"Link", options:"Quality Action", label:__("Supporting Quality Action (required for escalation)")},
        {fieldname:"attachment", fieldtype:"Link", options:"File", label:__("Supporting Work Order Attachment")});
      frappe.prompt(fields, values => frappe.call({method:"calco_erp.calco_production.missed_in_process_quality.record_missed_sample", args:{...values, work_order:frm.doc.work_order || frm.doc.name, checkpoint_key:key, action}, freeze:true}).then(() => frm.reload_doc()), `${__(action)} — ${key}`);
    });
    wrapper.find(".calco-ipqc-create").on("click", event => {
      frappe.call({method: "calco_erp.calco_production.in_process_quality.make_checkpoint_qi", args: {work_order: frm.doc.work_order || frm.doc.name, checkpoint_key: event.currentTarget.dataset.key}, freeze: true}).then(r => { if (r.message) frappe.set_route("Form", "Quality Inspection", r.message.name); });
    });
    wrapper.find(".calco-ipqc-adjustment").on("click", () => {
      frappe.prompt([{fieldname:"quality_inspection", fieldtype:"Link", options:"Quality Inspection", label:__("Failed / Review QI"), reqd:1, get_query: () => ({filters:{custom_work_order: frm.doc.work_order || frm.doc.name, custom_work_order_qc_stage:"In-Process QC"}})}, {fieldname:"process_observation", fieldtype:"Link", options:"Process Observation", label:__("Process Observation"), reqd:1, get_query: () => ({filters:{work_order: frm.doc.work_order || frm.doc.name, status:"Complete"}})}, {fieldname:"remarks", fieldtype:"Small Text", label:__("Production Adjustment"), reqd:1}], values => frappe.call({method:"calco_erp.calco_production.in_process_quality.record_production_adjustment", args:values, freeze:true}).then(() => frm.reload_doc()), __("Record Production Adjustment"));
    });
  }

  function renderCompoundingCockpit(frm, payload) {
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    const cockpit = payload.cockpit || {};
    const context = cockpit.context || {};
    frm.__job_card_execution_journey_data = payload || {};
    wrapper.html(`
      <section class="calco-wo-journey calco-jc-cockpit">
        <div class="calco-wo-journey__header">
          <div>
            <div class="calco-wo-journey__title">${__("COMPOUNDING / EXTRUSION JOB CARD")}</div>
            <div class="calco-wo-journey__subtitle">${esc(context.job_card || "-")} - ${esc(cockpit.state || "Not Started")}</div>
          </div>
          <button type="button" class="btn btn-xs btn-default calco-jc-journey__refresh">${__("Refresh")}</button>
        </div>
        <div class="calco-jc-cockpit__standard-controls"></div>
        <section class="calco-jc-cockpit__block">
          <div class="calco-jc-cockpit__section-title">${__("Execution Context")}</div>
          <div class="calco-jc-cockpit__context">${buildCompoundingContext(context)}</div>
        </section>
        <section class="calco-jc-cockpit__block">
          <div class="calco-jc-cockpit__section-title">${__("Journey")}</div>
          <div class="calco-jc-cockpit__journey">${buildCompoundingJourney(payload.stages || [])}</div>
        </section>
        ${buildInProcessQc(payload.in_process_qc)}
        ${buildWipContext(cockpit.wip_context || {})}
        ${buildCompoundingModules(cockpit.module_groups || [])}
      </section>
    `);
    if (!attachStandardJobCardControls(frm, wrapper)) {
      setTimeout(() => attachStandardJobCardControls(frm, wrapper), 0);
    }
    bindJobCardExecutionJourneyActions(frm, wrapper);
    bindInProcessQc(frm, wrapper);
    const gradeChange = calco_erp.grade_change_control || {};
    if (gradeChange.apply_job_card_ui) gradeChange.apply_job_card_ui(frm, payload || {});
  }

  function renderJobCardExecutionJourney(frm, payload) {
    applyControlledCompoundingLayout(frm, payload);
    if ((payload || {}).controlled_compounding) {
      renderCompoundingCockpit(frm, payload);
      return;
    }
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    frm.__job_card_execution_journey_data = payload || {};
    const currentStage = getCurrentStage((payload || {}).stages || []);
    wrapper.html(`
      <section class="calco-wo-journey calco-jc-journey">
        <div class="calco-wo-journey__header">
          <div>
            <div class="calco-wo-journey__title">${__("Job Card Execution Journey")}</div>
            <div class="calco-wo-journey__subtitle">${__("Shop-floor capture only; stock and QC remain standard ERPNext documents")}</div>
          </div>
          <button type="button" class="btn btn-xs btn-default calco-jc-journey__refresh">${__("Refresh")}</button>
        </div>
        <div class="calco-wo-journey__summary">${buildJobCardSummary((payload || {}).summary || {})}</div>
        ${buildCurrentAction(currentStage)}
        <div class="calco-wo-journey__strip">${buildWorkOrderStages((payload || {}).stages || [])}</div>
      </section>
    `);
    bindJobCardExecutionJourneyActions(frm, wrapper);
    bindInProcessQc(frm, wrapper);
    const gradeChange = calco_erp.grade_change_control || {};
    if (gradeChange.apply_job_card_ui) gradeChange.apply_job_card_ui(frm, payload || {});
  }

  function bindJobCardExecutionJourneyActions(frm, wrapper) {
    wrapper.find(".calco-jc-journey__refresh").on("click", () => loadJobCardExecutionJourney(frm));
    wrapper.find(".calco-wo-journey__next-action").on("click", async () => {
      const stage = getCurrentStage((frm.__job_card_execution_journey_data || {}).stages || []);
      if (!stage) return;
      if (stage.linked_doctype && stage.linked_docname) {
        frappe.set_route("Form", stage.linked_doctype, stage.linked_docname);
        return;
      }
      if (stage.action_disabled) {
        frappe.msgprint(stage.blocked_reason || __("This action is not available yet."));
        return;
      }
      if (stage.action_label && stage.action_type) {
        await runWorkOrderStageAction(frm, stage);
        return;
      }
      frappe.set_route("Form", "Job Card", frm.doc.name);
    });
    wrapper.find(".calco-grade-change-clearance-link").on("click", (event) => {
      frappe.set_route("Form", "Grade Change Clearance", event.currentTarget.dataset.clearance);
    });
    wrapper.find(".calco-initial-qc-link").on("click", (event) => {
      frappe.set_route("Form", "Quality Inspection", event.currentTarget.dataset.qualityInspection);
    });
    wrapper.find(".calco-jc-cockpit__journey-link").on("click", (event) => {
      frappe.set_route("Form", event.currentTarget.dataset.doctype, event.currentTarget.dataset.docname);
    });
    wrapper.find(".calco-premix-new").on("click", async () => {
      const response = await frappe.call({
        method: "calco_erp.calco_production.premix_execution.create_premix_run",
        args: { job_card: frm.doc.name },
        freeze: true,
        freeze_message: __("Creating controlled Premix Run..."),
      });
      frappe.set_route("Form", "Premix Run", response.message.name);
    });
    wrapper.find(".calco-premix-view").on("click", () => {
      frappe.route_options = { job_card: frm.doc.name };
      frappe.set_route("List", "Premix Run");
    });
    wrapper.find(".calco-blending-new").on("click", async () => {
      const response = await frappe.call({
        method: "calco_erp.calco_production.blending_execution.create_blending_run",
        args: { job_card: frm.doc.name },
        freeze: true,
        freeze_message: __("Creating controlled Blending Run..."),
      });
      frappe.set_route("Form", "Blending Run", response.message.name);
    });
    wrapper.find(".calco-blending-view").on("click", () => {
      frappe.route_options = { job_card: frm.doc.name };
      frappe.set_route("List", "Blending Run");
    });
    wrapper.find(".calco-silo-new").on("click", () => showNewSiloDialog(frm));
    wrapper.find(".calco-silo-view").on("click", () => {
      frappe.route_options = {job_card: frm.doc.name};
      frappe.set_route("List", "Silo Control");
    });
    wrapper.find(".calco-feeder-run-new").on("click", async () => {
      const response = await frappe.call({
        method:"calco_erp.calco_production.feeder_run.create_feeder_run",
        args:{job_card:frm.doc.name},
        freeze:true,
        freeze_message:__("Creating controlled Feeder Run..."),
      });
      frappe.set_route("Form", "Feeder Run", response.message.name);
    });
    wrapper.find(".calco-feeder-run-view").on("click", () => {
      frappe.route_options = {job_card:frm.doc.name};
      frappe.set_route("List", "Feeder Run");
    });
    wrapper.find(".calco-bulk-density-new").on("click", async () => {
      const response = await frappe.call({
        method:"calco_erp.calco_production.bulk_density_monitoring.create_bulk_density_monitor",
        args:{job_card:frm.doc.name},
        freeze:true,
        freeze_message:__("Creating controlled Bulk Density Monitor..."),
      });
      frappe.set_route("Form", "Bulk Density Monitor", response.message.name);
    });
    wrapper.find(".calco-bulk-density-view").on("click", () => {
      frappe.route_options = {job_card:frm.doc.name};
      frappe.set_route("List", "Bulk Density Monitor");
    });
    wrapper.find(".calco-process-parameter-new").on("click", async () => {
      const response = await frappe.call({
        method:"calco_erp.calco_production.process_parameter_monitoring.create_process_parameter_monitor",
        args:{job_card:frm.doc.name},
        freeze:true,
        freeze_message:__("Creating controlled Process Parameter Monitor..."),
      });
      frappe.set_route("Form", "Process Parameter Monitor", response.message.name);
    });
    wrapper.find(".calco-process-parameter-view").on("click", () => {
      frappe.route_options = {job_card:frm.doc.name};
      frappe.set_route("List", "Process Parameter Monitor");
    });
    wrapper.find(".calco-shift-report-new").on("click", () => showNewShiftReportDialog(frm));
    wrapper.find(".calco-shift-report-view").on("click", () => {
      frappe.route_options = {job_card:frm.doc.name};
      frappe.set_route("List", "Shift Report");
    });
  }

  async function showNewShiftReportDialog(frm) {
    const response = await frappe.call({method:"calco_erp.calco_production.shift_schedule.get_current_shift_report", args:{job_card:frm.doc.name}});
    const active = response.message || {};
    if (active.current_report) { frappe.set_route("Form", "Shift Report", active.current_report); return; }
    const choices = active.choices || [];
    if (!choices.length) { frappe.msgprint(active.message || __("No shift is active at the current server time.")); return; }
    const updateWindow = () => {
      const row = choices.find(r => r.shift === dialog.get_value("shift"));
      dialog.set_value("operational_shift_date", row?.operational_shift_date || "");
      dialog.set_value("shift_start", row?.shift_start || "");
      dialog.set_value("shift_end", row?.shift_end || "");
    };
    const dialog = new frappe.ui.Dialog({
      title: __("Current Shift Report"),
      fields: [
        {fieldname:"shift",fieldtype:"Select",label:__("Active Shift"),options:["",...choices.map(r=>r.shift)].join("\n"),reqd:1,change:updateWindow},
        {fieldname:"operational_shift_date",fieldtype:"Date",label:__("Operational Shift Date"),read_only:1},
        {fieldname:"shift_start",fieldtype:"Datetime",label:__("Shift Start"),read_only:1},
        {fieldname:"shift_end",fieldtype:"Datetime",label:__("Shift End"),read_only:1},
      ],
      primary_action_label: __("Open / Create"),
      async primary_action(values) {
        const resolved = await frappe.call({method:"calco_erp.calco_production.shift_schedule.get_current_shift_report",args:{job_card:frm.doc.name,shift:values.shift}});
        if (resolved.message.current_report) { dialog.hide();frappe.set_route("Form","Shift Report",resolved.message.current_report);return; }
        const result = await frappe.call({method:"calco_erp.calco_production.shift_reporting.create_shift_report",args:{job_card:frm.doc.name,shift:values.shift},freeze:true});
        dialog.hide();frappe.set_route("Form","Shift Report",result.message.name);
      },
    });
    dialog.show();
    if (active.selected_shift) { await dialog.set_value("shift",active.selected_shift); updateWindow(); }
  }

  function showNewSiloDialog(frm) {
    const payload = frm.__job_card_execution_journey_data || {};
    const context = ((payload.cockpit || {}).context) || {};
    const dialog = new frappe.ui.Dialog({
      title: __("New Silo Control"),
      fields: [
        {fieldname:"rm_item", fieldtype:"Link", options:"Item", label:__("RM Item"), reqd:1},
        {fieldname:"silo_no", fieldtype:"Select", options:"1\n2\n3\n4\n5\n6", label:__("Silo No."), reqd:1},
        {fieldname:"feeder_no", fieldtype:"Select", options:"1\n2\n3\n4\n5\n6", label:__("Feeder No."), reqd:1},
        {fieldname:"addition_rate", fieldtype:"Percent", label:__("Addition Rate %")},
        {fieldname:"blending_required", fieldtype:"Select", options:"Yes\nNo", default:"No", label:__("Blending Required"), reqd:1},
        {fieldname:"instruction", fieldtype:"HTML"},
      ],
      primary_action_label: __("Create"),
      async primary_action(values) {
        const response = await frappe.call({
          method:"calco_erp.calco_production.silo_control.create_silo_control",
          args:{job_card:frm.doc.name, ...values},
          freeze:true,
          freeze_message:__("Creating controlled Silo Control..."),
        });
        dialog.hide();
        frappe.set_route("Form", "Silo Control", response.message.name);
      },
    });
    dialog.set_query("rm_item", () => ({
      query:"calco_erp.calco_production.silo_control.silo_item_query",
      filters:{work_order:context.work_order},
    }));
    const renderInstruction = () => {
      dialog.fields_dict.instruction.$wrapper.html(
        dialog.get_value("blending_required") === "Yes"
          ? `<div class="alert alert-info">${__("F-SCS-01 instruction: Blending quantity should not exceed 500 Kg.")}</div>`
          : ""
      );
    };
    dialog.fields_dict.blending_required.df.onchange = renderInstruction;
    dialog.show();
    renderInstruction();
  }

  async function loadJobCardExecutionJourney(frm) {
    ensureStyle();
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    restoreStandardJobCardControls(frm, wrapper);
    if (frm.is_new()) {
      renderState(wrapper, __("Save this Job Card to load the Execution Journey."));
      return;
    }
    wrapper.html(`<section class="calco-wo-journey"><div class="calco-production-journey__state">${__("Loading Job Card Execution Journey...")}</div></section>`);
    try {
      const response = await frappe.call({
        method: "calco_erp.calco_production.job_card_execution.get_job_card_execution_journey",
        args: { job_card: frm.doc.name },
        freeze: false,
      });
      renderJobCardExecutionJourney(frm, response.message || {});
    } catch (error) {
      renderState(wrapper, error.message || __("Unable to load Job Card Execution Journey."));
    }
  }

  async function loadWorkOrderJourney(frm) {
    ensureStyle();
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    if (frm.is_new()) {
      renderState(wrapper, __("Save this Work Order to load the Production Journey."));
      return;
    }
    wrapper.html(`<section class="calco-wo-journey"><div class="calco-production-journey__state">${__("Loading Production Journey...")}</div></section>`);
    try {
      const response = await frappe.call({
        method: "calco_erp.calco_production.work_order_journey.get_work_order_journey",
        args: { work_order: frm.doc.name },
        freeze: false,
      });
      renderWorkOrderJourney(frm, response.message || {});
    } catch (error) {
      renderState(wrapper, error.message || __("Unable to load Production Journey."));
    }
  }

  async function loadLegacyTracker(frm) {
    ensureStyle();
    const wrapper = getWrapper(frm);
    if (!wrapper) return;
    if (frm.is_new()) {
      renderState(wrapper, __("Save this document to load the Production Execution Journey."));
      return;
    }
    renderState(wrapper, __("Loading Production Execution Journey..."));
    try {
      const response = await frappe.call({
        method: "calco_erp.calco_production.production_execution_journey.get_tracker",
        args: { doctype: frm.doctype, docname: frm.doc.name },
        freeze: false,
      });
      renderTracker(frm, response.message || {});
    } catch (error) {
      renderState(wrapper, error.message || __("Unable to load Production Execution Journey."));
    }
  }

  function loadTracker(frm) {
    if (frm.doctype === "Work Order") {
      loadWorkOrderJourney(frm);
      return;
    }
    if (frm.doctype === "Job Card") {
      loadJobCardExecutionJourney(frm);
      return;
    }
    loadLegacyTracker(frm);
  }

  const WORK_ORDER_HIDDEN_FIELDS = [
    "custom_operator",
    "custom_shift_type",
    "custom_production_requirement",
    "custom_production_job_card",
    "custom_fg_batch_no",
    "material_transferred_for_manufacturing",
    "additional_transferred_qty",
    "produced_qty",
    "process_loss_qty",
    "disassembled_qty",
    "custom_production_readiness_section",
    "custom_production_readiness_status",
    "custom_production_readiness_checked_on",
    "custom_production_readiness_checked_by",
    "custom_production_readiness_summary",
    "custom_material_readiness_section",
    "custom_material_readiness_status",
    "custom_material_readiness_check",
    "custom_material_readiness_summary",
  ];

  const WORK_ORDER_COLLAPSED_SECTIONS = [
    "custom_work_order_lifecycle_section",
    "custom_work_order_qc_section",
    "custom_partial_production_section",
    "warehouses",
    "operations_section",
    "section_break_ndpq",
    "time",
    "section_break_22",
    "production_item_info_section",
    "serial_no_and_batch_for_finished_good_section",
    "reference_section",
  ];

  function collapseSection(frm, fieldname) {
    const section = frm.layout && frm.layout.sections_dict && frm.layout.sections_dict[fieldname];
    if (!section || !section.head || !section.body) return;
    if (!section.df.collapsible) {
      section.df.collapsible = 1;
      section.head.addClass("collapsible").attr({ tabindex: 0, role: "button" });
      section.head.off(".calcoPhase4A").on("click.calcoPhase4A", () => section.collapse());
      section.head.on("keydown.calcoPhase4A", (event) => {
        if (["Enter", " "].includes(event.key)) {
          event.preventDefault();
          section.collapse();
        }
      });
    }
    section.set_icon(true);
    section.collapse(true);
  }

  function applyWorkOrderLayout(frm) {
    if (frm.doctype !== "Work Order") return;
    WORK_ORDER_HIDDEN_FIELDS.forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) frm.toggle_display(fieldname, false);
    });
    WORK_ORDER_COLLAPSED_SECTIONS.forEach((fieldname) => collapseSection(frm, fieldname));
  }

  function bindReadinessUpdates(frm) {
    if (frm.doctype !== "Work Order") return;
    $(frm.wrapper)
      .off("calco:production-readiness-updated.phase4a")
      .on("calco:production-readiness-updated.phase4a", () => {
        if (frm.__work_order_journey_data) loadWorkOrderJourney(frm);
      });
  }
  function register(doctype) {
    frappe.ui.form.on(doctype, {
      onload(frm) {
        const wrapper = getWrapper(frm);
        if (!wrapper) return;
        wrapper.html(`
          <section class="calco-production-journey">
            <div class="calco-production-journey__header">
              <div>
                <div class="calco-production-journey__title">${frm.doctype === "Work Order" ? __("Production Journey") : frm.doctype === "Job Card" ? __("Job Card Execution Journey") : __("Production Execution Journey")}</div>
                <div class="calco-production-journey__subtitle">${__("Save then open to load the journey.")}</div>
              </div>
            </div>
          </section>
        `);
      },
      refresh(frm) {
        applyWorkOrderLayout(frm);
        bindReadinessUpdates(frm);
        loadTracker(frm);
      },
    });
  }

  calco_erp.production_execution.refresh_work_order_journey = loadWorkOrderJourney;
  SUPPORTED_DOCTYPES.forEach(register);
})();
