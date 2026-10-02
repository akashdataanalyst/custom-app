frappe.provide("calco_erp.production_readiness");

(() => {
  const READINESS_METHOD =
    "calco_erp.calco_production.production_readiness.get_production_readiness";
  const REFRESH_METHOD =
    "calco_erp.calco_production.production_readiness.refresh_production_readiness";
  const PARTIAL_METHOD =
    "calco_erp.calco_production.production_readiness.approve_partial_production";
  const APPROVER_ROLES = ["Production Head"];
  let refreshTimer;

  function escape(value) {
    return frappe.utils.escape_html(String(value ?? ""));
  }

  function getWrapper(frm) {
    return frm.fields_dict.custom_production_readiness_summary?.$wrapper;
  }

  function ensureStyle() {
    if (document.getElementById("calco-production-readiness-style")) return;
    const style = document.createElement("style");
    style.id = "calco-production-readiness-style";
    style.textContent = `
      .calco-readiness { border: 1px solid var(--border-color); border-radius: 6px; overflow: hidden; }
      .calco-readiness__checks { display: grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); }
      .calco-readiness__check { padding: 10px 12px; border-right: 1px solid var(--border-color); }
      .calco-readiness__check:last-child { border-right: 0; }
      .calco-readiness__label { color: var(--text-muted); font-size: 12px; }
      .calco-readiness__value { font-weight: 600; margin-top: 2px; }
      .calco-readiness__footer { padding: 10px 12px; border-top: 1px solid var(--border-color); }
      .calco-readiness__overall { font-weight: 600; }
      .calco-readiness__blockers { margin: 8px 0 0; padding-left: 18px; }
      .calco-readiness__blockers li { margin: 3px 0; }
      .calco-readiness--ready .calco-readiness__overall,
      .calco-readiness__check--ready .calco-readiness__value { color: var(--green-600); }
      .calco-readiness--blocked .calco-readiness__overall,
      .calco-readiness__check--blocked .calco-readiness__value { color: var(--red-600); }
      @media (max-width: 767px) {
        .calco-readiness__checks { grid-template-columns: repeat(2, minmax(110px, 1fr)); }
        .calco-readiness__check { border-bottom: 1px solid var(--border-color); }
      }
    `;
    document.head.appendChild(style);
  }

  function buildReadinessHtml(result) {
    if (!result) return `<div class="text-muted">${__("Production readiness is unavailable.")}</div>`;

    const checks = (result.checks || [])
      .map(
        (check) => `
          <div class="calco-readiness__check ${
            check.ready ? "calco-readiness__check--ready" : "calco-readiness__check--blocked"
          }">
            <div class="calco-readiness__label">${escape(__(check.label))}</div>
            <div class="calco-readiness__value">${escape(__(check.status))}</div>
          </div>
        `
      )
      .join("");
    const uatWarning = result.uat_rm_release_override_active
      ? `<div class="alert alert-warning mb-0">
          <strong>${__("UAT RM RELEASE OVERRIDE ACTIVE")}</strong>
          ${
            result.uat_rm_release_override_used
              ? `<div>${__("One or more batches are eligible from physical Recovery stock without exact RM Release evidence.")}</div>`
              : ""
          }
        </div>`
      : "";
    const blockers = (result.blockers || [])
      .map(
        (row) => `
          <li>
            <strong>${escape(__(row.check))}:</strong>
            ${escape(row.reason)}
            <span class="text-muted">(${__("Responsible Owner")}: ${escape(row.owner)})</span>
          </li>
        `
      )
      .join("");

    return `
      <section class="calco-readiness ${
        result.ready ? "calco-readiness--ready" : "calco-readiness--blocked"
      }">
        ${uatWarning}
        <div class="calco-readiness__checks">${checks}</div>
        <div class="calco-readiness__footer">
          <div class="calco-readiness__overall">
            ${__("Overall Result")}: ${escape(__(result.overall_result))}
          </div>
          ${
            result.partial_production_approved
              ? `<div class="text-muted">${__("Evaluated for approved partial quantity")}:
                  ${format_number(result.evaluation_qty)}</div>`
              : ""
          }
          ${blockers ? `<ul class="calco-readiness__blockers">${blockers}</ul>` : ""}
        </div>
      </section>
    `;
  }

  function render(frm, result) {
    ensureStyle();
    frm.__production_readiness_data = result || null;
    const wrapper = getWrapper(frm);
    if (wrapper) wrapper.empty();
    $(frm.wrapper).trigger("calco:production-readiness-updated", [result]);
  }
  async function evaluate(frm, persist = false) {
    if (frm.is_new()) return null;
    const method = persist ? REFRESH_METHOD : READINESS_METHOD;
    const args = persist
      ? { work_order: frm.doc.name }
      : {
          work_order: frm.doc.name,
          bom_no: frm.doc.bom_no || "",
          qty: frm.doc.qty,
          machine: frm.doc.custom_machine || "",
        };
    try {
      const response = await frappe.call({ method, args, freeze: persist });
      const result = response.message || null;
      render(frm, result);
      if (persist) await frm.reload_doc();
      return result;
    } catch (error) {
      frappe.show_alert(
        {
          message: error.message || __("Unable to evaluate Production Readiness."),
          indicator: "red",
        },
        7
      );
      return null;
    }
  }

  async function refreshReadiness(frm, persist = true) {
    if (frm.is_dirty()) await frm.save();
    return evaluate(frm, persist);
  }

  async function openReadinessDialog(frm) {
    let result = frm.__production_readiness_data || (await evaluate(frm));
    const dialog = new frappe.ui.Dialog({
      title: __("Production Readiness"),
      fields: [{ fieldtype: "HTML", fieldname: "readiness_details" }],
      primary_action_label: __("Refresh Readiness"),
      async primary_action() {
        result = await refreshReadiness(frm, true);
        if (result) dialog.fields_dict.readiness_details.$wrapper.html(buildReadinessHtml(result));
      },
      size: "large",
    });
    dialog.show();
    dialog.fields_dict.readiness_details.$wrapper.html(buildReadinessHtml(result));
  }

  calco_erp.production_readiness.refresh = refreshReadiness;
  calco_erp.production_readiness.open_dialog = openReadinessDialog;
  function scheduleEvaluation(frm) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => evaluate(frm), 250);
  }

  function canApprovePartial() {
    return APPROVER_ROLES.some((role) => frappe.user_roles.includes(role));
  }

  function addActions(frm) {
    if (frm.is_new() || frm.doc.docstatus === 2) return;
    frm.add_custom_button(
      __("Refresh Readiness"),
      async () => {
        if (frm.is_dirty()) await frm.save();
        await evaluate(frm, true);
      },
      __("Production")
    );

    if (!canApprovePartial()) return;
    frm.add_custom_button(
      __("Approve Partial Production"),
      () => {
        const dialog = new frappe.ui.Dialog({
          title: __("Approve Partial Production"),
          fields: [
            {
              fieldname: "approved_qty",
              label: __("Approved Production Quantity"),
              fieldtype: "Float",
              reqd: 1,
              default: frm.doc.custom_partial_production_qty || null,
            },
            {
              fieldname: "reason",
              label: __("Reason"),
              fieldtype: "Small Text",
              reqd: 1,
              default: frm.doc.custom_partial_production_reason || "",
            },
          ],
          primary_action_label: __("Approve"),
          async primary_action(values) {
            await frappe.call({
              method: PARTIAL_METHOD,
              args: {
                work_order: frm.doc.name,
                approved_qty: values.approved_qty,
                reason: values.reason,
              },
              freeze: true,
            });
            dialog.hide();
            await frm.reload_doc();
          },
        });
        dialog.show();
      },
      __("Production")
    );
  }

  function hideLegacyMaterialReadiness(frm) {
    [
      "custom_material_readiness_section",
      "custom_material_readiness_status",
      "custom_material_readiness_check",
      "custom_material_readiness_summary",
    ].forEach((fieldname) => frm.toggle_display(fieldname, false));
    frm.remove_custom_button(__("Create Material Readiness Check"), __("Material Readiness"));
    frm.remove_custom_button(__("Open Material Readiness Check"), __("Material Readiness"));
  }

  frappe.ui.form.on("Work Order", {
    refresh(frm) {
      hideLegacyMaterialReadiness(frm);
      addActions(frm);
      scheduleEvaluation(frm);
    },
    custom_machine: scheduleEvaluation,
    custom_production_line: scheduleEvaluation,
    bom_no: scheduleEvaluation,
    qty: scheduleEvaluation,
  });
})();
