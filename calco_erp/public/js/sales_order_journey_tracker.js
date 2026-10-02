frappe.provide("calco_erp.sales_order_journey");

(function () {
  const HTML_FIELD = "custom_order_journey_tracker";
  const STYLE_ID = "calco-sales-order-journey-style";

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .calco-order-journey {
        padding: 16px;
        border: 1px solid var(--border-color);
        border-radius: 16px;
        background:
          radial-gradient(circle at top right, rgba(21, 101, 192, 0.08), transparent 34%),
          linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98));
      }
      .calco-order-journey__header {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        margin-bottom: 14px;
      }
      .calco-order-journey__title {
        font-size: 16px;
        font-weight: 700;
      }
      .calco-order-journey__subtitle {
        font-size: 12px;
        color: var(--text-muted);
      }
      .calco-order-journey__flow {
        display: flex;
        align-items: stretch;
        gap: 12px;
        overflow-x: auto;
        padding-bottom: 4px;
      }
      .calco-order-journey__stage {
        position: relative;
        min-width: 180px;
        border: 1px solid var(--border-color);
        border-radius: 14px;
        background: var(--fg-color);
        padding: 14px 16px;
        text-align: left;
        transition: border-color 0.2s ease, transform 0.2s ease;
        box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8);
      }
      .calco-order-journey__stage:hover {
        border-color: var(--stage-color, var(--primary));
        transform: translateY(-1px);
      }
      .calco-order-journey__stage-label {
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 8px;
      }
      .calco-order-journey__stage-status {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        margin-bottom: 8px;
        background: var(--stage-bg, rgba(148, 163, 184, 0.18));
        color: var(--stage-text, #334155);
      }
      .calco-order-journey__stage-summary {
        font-size: 12px;
        line-height: 1.45;
        color: var(--text-muted);
        min-height: 34px;
      }
      .calco-order-journey__stage-hint {
        font-size: 11px;
        color: var(--text-muted);
        margin-top: 10px;
      }
      .calco-order-journey__connector {
        flex: 0 0 28px;
        align-self: center;
        height: 2px;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(148, 163, 184, 0.6), rgba(148, 163, 184, 0.18));
      }
      .calco-order-journey__placeholder,
      .calco-order-journey__empty {
        padding: 18px 4px;
        color: var(--text-muted);
        font-size: 13px;
      }
      .calco-order-journey__stage[data-color="grey"] {
        --stage-color: #94a3b8;
        --stage-bg: rgba(148, 163, 184, 0.16);
        --stage-text: #475569;
      }
      .calco-order-journey__stage[data-color="blue"] {
        --stage-color: #1565c0;
        --stage-bg: rgba(21, 101, 192, 0.12);
        --stage-text: #0f4c91;
      }
      .calco-order-journey__stage[data-color="green"] {
        --stage-color: #2e7d32;
        --stage-bg: rgba(46, 125, 50, 0.12);
        --stage-text: #1f5b24;
      }
      .calco-order-journey__stage[data-color="orange"] {
        --stage-color: #ef6c00;
        --stage-bg: rgba(239, 108, 0, 0.12);
        --stage-text: #b45309;
      }
      .calco-order-journey__stage[data-color="red"] {
        --stage-color: #c62828;
        --stage-bg: rgba(198, 40, 40, 0.12);
        --stage-text: #8f1d1d;
      }
      .calco-order-journey-dialog__summary {
        padding: 12px 14px;
        border-radius: 12px;
        background: rgba(15, 23, 42, 0.04);
        margin-bottom: 12px;
        font-size: 13px;
        line-height: 1.55;
      }
      .calco-order-journey-dialog__details {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .calco-order-journey-dialog__detail {
        padding: 10px 12px;
        border: 1px solid var(--border-color);
        border-radius: 12px;
        background: var(--fg-color);
      }
      .calco-order-journey-dialog__detail-label {
        font-size: 11px;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 4px;
      }
      .calco-order-journey-dialog__detail-value {
        font-size: 13px;
        font-weight: 600;
      }
      .calco-order-journey-dialog__docs {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .calco-order-journey-dialog__section {
        margin-top: 16px;
      }
      .calco-order-journey-dialog__section:first-child {
        margin-top: 0;
      }
      .calco-order-journey-dialog__section-title {
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-muted);
        margin-bottom: 8px;
      }
      .calco-order-journey-dialog__doc {
        padding: 12px 14px;
        border: 1px solid var(--border-color);
        border-radius: 12px;
        background: var(--fg-color);
      }
      .calco-order-journey-dialog__doc-top {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        margin-bottom: 6px;
      }
      .calco-order-journey-dialog__doc-link {
        font-weight: 700;
      }
      .calco-order-journey-dialog__doc-type {
        font-size: 11px;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .calco-order-journey-dialog__doc-status {
        font-size: 11px;
        font-weight: 700;
        padding: 4px 8px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.16);
      }
      .calco-order-journey-dialog__doc-detail {
        color: var(--text-muted);
        font-size: 12px;
      }
      .calco-order-journey-dialog__doc-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 10px;
      }
      .calco-order-journey-dialog__action {
        border: 1px solid var(--border-color);
        border-radius: 999px;
        padding: 6px 10px;
        background: rgba(15, 23, 42, 0.03);
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
      }
      .calco-order-journey-dialog__action:hover {
        border-color: var(--primary);
        color: var(--primary);
      }
      @media (max-width: 767px) {
        .calco-order-journey {
          padding: 14px;
        }
        .calco-order-journey__stage {
          min-width: 160px;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function getWrapper(frm) {
    return frm.fields_dict[HTML_FIELD] && frm.fields_dict[HTML_FIELD].$wrapper;
  }

  function escapeHtml(value) {
    return frappe.utils.escape_html(String(value || ""));
  }

  function renderState(wrapper, message, subtitle) {
    wrapper.html(`
      <section class="calco-order-journey">
        <div class="calco-order-journey__header">
          <div>
            <div class="calco-order-journey__title">${__("Order Journey Tracker")}</div>
            <div class="calco-order-journey__subtitle">${escapeHtml(subtitle || __("Live stage flow from linked ERP documents"))}</div>
          </div>
        </div>
        <div class="calco-order-journey__placeholder">${escapeHtml(message)}</div>
      </section>
    `);
  }

  function buildFlowHtml(stages) {
    if (!stages.length) {
      return `<div class="calco-order-journey__empty">${__("No stages available.")}</div>`;
    }

    return stages.map((stage, index) => {
      const connector = index < stages.length - 1 ? `<div class="calco-order-journey__connector"></div>` : "";
      return `
        <button
          type="button"
          class="calco-order-journey__stage"
          data-stage-key="${escapeHtml(stage.key)}"
          data-color="${escapeHtml(stage.color)}"
        >
          <div class="calco-order-journey__stage-label">${escapeHtml(stage.label)}</div>
          <div class="calco-order-journey__stage-status">${escapeHtml(stage.status)}</div>
          <div class="calco-order-journey__stage-summary">${escapeHtml(stage.summary)}</div>
          <div class="calco-order-journey__stage-hint">${__("Click for linked documents")}</div>
        </button>
        ${connector}
      `;
    }).join("");
  }

  function renderTracker(frm, data) {
    const wrapper = getWrapper(frm);
    if (!wrapper) {
      return;
    }

    const stages = data.stages || [];
    wrapper.html(`
      <section class="calco-order-journey">
        <div class="calco-order-journey__header">
          <div>
            <div class="calco-order-journey__title">${__("Order Journey Tracker")}</div>
            <div class="calco-order-journey__subtitle">${__("Live stage flow from linked ERP documents")}</div>
          </div>
          <div class="calco-order-journey__subtitle">${__("Read only")}</div>
        </div>
        <div class="calco-order-journey__flow">${buildFlowHtml(stages)}</div>
      </section>
    `);

    wrapper.find(".calco-order-journey__stage").on("click", (event) => {
      openStageDialog(frm, event.currentTarget.dataset.stageKey);
    });
  }

  function buildDialogHtml(stage) {
    const detailsHtml = (stage.details || []).map((detail) => `
      <div class="calco-order-journey-dialog__detail">
        <div class="calco-order-journey-dialog__detail-label">${escapeHtml(detail.label)}</div>
        <div class="calco-order-journey-dialog__detail-value">${escapeHtml(detail.value)}</div>
      </div>
    `).join("");

    const documentsHtml = (stage.sections || []).length
      ? stage.sections.map((section) => `
          <div class="calco-order-journey-dialog__section">
            <div class="calco-order-journey-dialog__section-title">${escapeHtml(section.label)}</div>
            <div class="calco-order-journey-dialog__docs">${buildDocumentsHtml(section.documents || [], section.empty_message)}</div>
          </div>
        `).join("")
      : `<div class="calco-order-journey-dialog__docs">${buildDocumentsHtml(stage.documents || [], stage.empty_message)}</div>`;

    return `
      <div class="calco-order-journey-dialog__summary">${escapeHtml(stage.summary)}</div>
      <div class="calco-order-journey-dialog__details">${detailsHtml}</div>
      ${documentsHtml}
    `;
  }

  function buildDocumentsHtml(documents, emptyMessage) {
    if (!documents.length) {
      return `<div class="calco-order-journey__empty">${escapeHtml(emptyMessage || __("No linked documents found."))}</div>`;
    }

    return documents.map((doc) => `
      <div class="calco-order-journey-dialog__doc">
        <div class="calco-order-journey-dialog__doc-top">
          <div>
            <div class="calco-order-journey-dialog__doc-type">${escapeHtml(doc.label)}</div>
            <a
              href="#"
              class="calco-order-journey-dialog__doc-link"
              data-doctype="${escapeHtml(doc.doctype)}"
              data-name="${escapeHtml(doc.name)}"
            >
              ${escapeHtml(doc.name)}
            </a>
          </div>
          <div class="calco-order-journey-dialog__doc-status">${escapeHtml(doc.status)}</div>
        </div>
        <div class="calco-order-journey-dialog__doc-detail">${escapeHtml(doc.detail || __("No extra details"))}</div>
        ${buildDocumentActionsHtml(doc.actions || [])}
      </div>
    `).join("");
  }

  function buildDocumentActionsHtml(actions) {
    if (!actions.length) {
      return "";
    }

    return `
      <div class="calco-order-journey-dialog__doc-actions">
        ${actions.map((action) => `
          <button
            type="button"
            class="calco-order-journey-dialog__action"
            data-action-type="${escapeHtml(action.type)}"
            data-doctype="${escapeHtml(action.doctype || "")}"
            data-name="${escapeHtml(action.name || "")}"
            data-url="${escapeHtml(action.url || "")}"
            data-target="${escapeHtml(action.target || "_blank")}"
          >
            ${escapeHtml(action.label)}
          </button>
        `).join("")}
      </div>
    `;
  }

  function openStageDialog(frm, stageKey) {
    const stage = ((frm.__order_journey_data || {}).stages || []).find((row) => row.key === stageKey);
    if (!stage) {
      return;
    }

    const dialog = new frappe.ui.Dialog({
      title: `${stage.label} - ${stage.status}`,
      fields: [{ fieldtype: "HTML", fieldname: "content" }],
      size: "large",
    });
    dialog.show();
    dialog.fields_dict.content.$wrapper.html(buildDialogHtml(stage));
    dialog.$wrapper.on("click", ".calco-order-journey-dialog__doc-link", (event) => {
      event.preventDefault();
      const target = event.currentTarget;
      frappe.set_route("Form", target.dataset.doctype, target.dataset.name);
      dialog.hide();
    });
    dialog.$wrapper.on("click", ".calco-order-journey-dialog__action", (event) => {
      const target = event.currentTarget.dataset;
      if (target.actionType === "form" && target.doctype && target.name) {
        frappe.set_route("Form", target.doctype, target.name);
        dialog.hide();
        return;
      }

      if (target.actionType === "url" && target.url) {
        window.open(target.url, target.target || "_blank");
      }
    });
  }

  async function loadTracker(frm) {
    ensureStyle();

    const wrapper = getWrapper(frm);
    if (!wrapper) {
      return;
    }

    if (frm.is_new()) {
      renderState(
        wrapper,
        __("Save the Sales Order to load the journey tracker."),
        __("Tracker appears directly below the Items table")
      );
      return;
    }

    renderState(wrapper, __("Loading journey from linked ERP documents..."));

    try {
      const response = await frappe.call({
        method: "calco_erp.calco_customer_approval.sales_order_journey.get_sales_order_journey",
        args: { sales_order: frm.doc.name },
        freeze: false,
      });
      frm.__order_journey_data = response.message || {};
      renderTracker(frm, frm.__order_journey_data);
    } catch (error) {
      renderState(wrapper, error.message || __("Unable to load Order Journey Tracker right now."));
    }
  }

  frappe.ui.form.on("Sales Order", {
    refresh(frm) {
      loadTracker(frm);
    },
  });
})();
