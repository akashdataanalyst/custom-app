frappe.provide("calco_erp.purchase_journey");

(function () {
  const HTML_FIELD = "custom_purchase_journey_tracker";
  const STYLE_ID = "calco-purchase-journey-style";

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .calco-purchase-journey {
        padding: 16px;
        border: 1px solid var(--border-color);
        border-radius: 18px;
        background:
          radial-gradient(circle at top right, rgba(46, 125, 50, 0.09), transparent 30%),
          linear-gradient(180deg, rgba(248, 250, 252, 0.98), rgba(255, 255, 255, 1));
      }
      .calco-purchase-journey__header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 14px;
      }
      .calco-purchase-journey__title {
        font-size: 16px;
        font-weight: 700;
      }
      .calco-purchase-journey__subtitle {
        font-size: 12px;
        color: var(--text-muted);
      }
      .calco-purchase-journey__tag {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.05);
        padding: 5px 10px;
        font-size: 11px;
        font-weight: 700;
      }
      .calco-purchase-journey__overview {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .calco-purchase-journey__metric {
        border: 1px solid var(--border-color);
        border-radius: 14px;
        padding: 10px 12px;
        background: rgba(255, 255, 255, 0.75);
      }
      .calco-purchase-journey__metric-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        margin-bottom: 4px;
      }
      .calco-purchase-journey__metric-value {
        font-size: 18px;
        font-weight: 700;
      }
      .calco-purchase-journey__actions {
        display: flex;
        justify-content: flex-end;
        margin-bottom: 10px;
      }
      .calco-purchase-journey__traceability {
        border: 1px solid rgba(21, 101, 192, 0.2);
        background: rgba(21, 101, 192, 0.07);
        color: #0f4c91;
        border-radius: 999px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 700;
        cursor: pointer;
      }
      .calco-purchase-journey__traceability:hover {
        border-color: #1565c0;
      }
      .calco-purchase-journey__flow {
        display: flex;
        align-items: stretch;
        gap: 12px;
        overflow-x: auto;
        padding-bottom: 4px;
      }
      .calco-purchase-journey__stage {
        position: relative;
        min-width: 188px;
        border: 1px solid var(--border-color);
        border-radius: 14px;
        background: var(--fg-color);
        padding: 14px 16px;
        text-align: left;
        transition: border-color 0.2s ease, transform 0.2s ease;
        box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8);
      }
      .calco-purchase-journey__stage:hover {
        border-color: var(--stage-color, var(--primary));
        transform: translateY(-1px);
      }
      .calco-purchase-journey__stage-label {
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 8px;
      }
      .calco-purchase-journey__stage-status {
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
      .calco-purchase-journey__stage-summary {
        font-size: 12px;
        line-height: 1.45;
        color: var(--text-muted);
        min-height: 34px;
      }
      .calco-purchase-journey__stage-hint {
        font-size: 11px;
        color: var(--text-muted);
        margin-top: 10px;
      }
      .calco-purchase-journey__connector {
        flex: 0 0 28px;
        align-self: center;
        height: 2px;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(148, 163, 184, 0.6), rgba(148, 163, 184, 0.18));
      }
      .calco-purchase-journey__placeholder,
      .calco-purchase-journey__empty {
        padding: 18px 4px;
        color: var(--text-muted);
        font-size: 13px;
      }
      .calco-purchase-journey__stage[data-color="grey"] {
        --stage-color: #94a3b8;
        --stage-bg: rgba(148, 163, 184, 0.16);
        --stage-text: #475569;
      }
      .calco-purchase-journey__stage[data-color="blue"] {
        --stage-color: #1565c0;
        --stage-bg: rgba(21, 101, 192, 0.12);
        --stage-text: #0f4c91;
      }
      .calco-purchase-journey__stage[data-color="green"] {
        --stage-color: #2e7d32;
        --stage-bg: rgba(46, 125, 50, 0.12);
        --stage-text: #1f5b24;
      }
      .calco-purchase-journey__stage[data-color="orange"] {
        --stage-color: #ef6c00;
        --stage-bg: rgba(239, 108, 0, 0.12);
        --stage-text: #b45309;
      }
      .calco-purchase-journey__stage[data-color="red"] {
        --stage-color: #c62828;
        --stage-bg: rgba(198, 40, 40, 0.12);
        --stage-text: #8f1d1d;
      }
      .calco-purchase-journey-dialog__summary {
        padding: 12px 14px;
        border-radius: 12px;
        background: rgba(15, 23, 42, 0.04);
        margin-bottom: 12px;
        font-size: 13px;
        line-height: 1.55;
      }
      .calco-purchase-journey-dialog__details {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .calco-purchase-journey-dialog__detail {
        padding: 10px 12px;
        border: 1px solid var(--border-color);
        border-radius: 12px;
        background: var(--fg-color);
      }
      .calco-purchase-journey-dialog__detail-label {
        font-size: 11px;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 4px;
      }
      .calco-purchase-journey-dialog__detail-value {
        font-size: 13px;
        font-weight: 600;
      }
      .calco-purchase-journey-dialog__docs {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .calco-purchase-journey-dialog__section {
        margin-top: 16px;
      }
      .calco-purchase-journey-dialog__section-title {
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--text-muted);
        margin-bottom: 8px;
      }
      .calco-purchase-journey-dialog__doc {
        padding: 12px 14px;
        border: 1px solid var(--border-color);
        border-radius: 12px;
        background: var(--fg-color);
      }
      .calco-purchase-journey-dialog__doc-top {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        margin-bottom: 6px;
      }
      .calco-purchase-journey-dialog__doc-link {
        font-weight: 700;
      }
      .calco-purchase-journey-dialog__doc-type {
        font-size: 11px;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .calco-purchase-journey-dialog__doc-status {
        font-size: 11px;
        font-weight: 700;
        padding: 4px 8px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.16);
      }
      .calco-purchase-journey-dialog__doc-detail {
        color: var(--text-muted);
        font-size: 12px;
      }
      @media (max-width: 767px) {
        .calco-purchase-journey {
          padding: 14px;
        }
        .calco-purchase-journey__stage {
          min-width: 164px;
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
      <section class="calco-purchase-journey">
        <div class="calco-purchase-journey__header">
          <div>
            <div class="calco-purchase-journey__title">${__("Purchase Journey Tracker")}</div>
            <div class="calco-purchase-journey__subtitle">${escapeHtml(subtitle || __("Live stage flow from linked ERP documents"))}</div>
          </div>
          <div class="calco-purchase-journey__tag">${__("Read only")}</div>
        </div>
        <div class="calco-purchase-journey__placeholder">${escapeHtml(message)}</div>
      </section>
    `);
  }

  function buildOverviewHtml(overview) {
    const items = [
      ["Requested", overview.requested_qty],
      ["Ordered", overview.ordered_qty],
      ["Received", overview.received_qty],
      ["Accepted", overview.accepted_qty],
      ["Cancelled", overview.quantity_uom_conflict ? __("Multiple UOMs") : overview.cancelled_qty],
      ["Returned", overview.returned_qty],
      ["Rejected", overview.rejected_qty],
      ["Balance", overview.quantity_uom_conflict ? __("Multiple UOMs") : overview.balance_qty],
    ];

    return items.map(([label, value]) => `
      <div class="calco-purchase-journey__metric">
        <div class="calco-purchase-journey__metric-label">${escapeHtml(label)}</div>
        <div class="calco-purchase-journey__metric-value">${escapeHtml(value)}</div>
      </div>
    `).join("");
  }

  function buildFlowHtml(stages) {
    if (!stages.length) {
      return `<div class="calco-purchase-journey__empty">${__("No stages available.")}</div>`;
    }

    return stages.map((stage, index) => {
      const connector = index < stages.length - 1 ? `<div class="calco-purchase-journey__connector"></div>` : "";
      return `
        <button
          type="button"
          class="calco-purchase-journey__stage"
          data-stage-key="${escapeHtml(stage.key)}"
          data-color="${escapeHtml(stage.color)}"
        >
          <div class="calco-purchase-journey__stage-label">${escapeHtml(stage.label)}</div>
          <div class="calco-purchase-journey__stage-status">${escapeHtml(stage.status)}</div>
          <div class="calco-purchase-journey__stage-summary">${escapeHtml(stage.summary)}</div>
          <div class="calco-purchase-journey__stage-hint">${__("Click for linked documents")}</div>
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

    wrapper.html(`
      <section class="calco-purchase-journey">
        <div class="calco-purchase-journey__header">
          <div>
            <div class="calco-purchase-journey__title">${__("Purchase Journey Tracker")}</div>
            <div class="calco-purchase-journey__subtitle">${__("Live stage flow from linked ERP documents")}</div>
          </div>
          <div class="calco-purchase-journey__tag">${__("Read only")}</div>
        </div>
        <div class="calco-purchase-journey__overview">${buildOverviewHtml(data.overview || {})}</div>
        <div class="calco-purchase-journey__actions">
          <button type="button" class="calco-purchase-journey__traceability">
            ${__("Open Material Traceability")}
          </button>
        </div>
        <div class="calco-purchase-journey__flow">${buildFlowHtml(data.stages || [])}</div>
      </section>
    `);

    wrapper.find(".calco-purchase-journey__stage").on("click", (event) => {
      openStageDialog(frm, event.currentTarget.dataset.stageKey);
    });
    wrapper.find(".calco-purchase-journey__traceability").on("click", () => {
      frappe.set_route("material-traceability", {
        query: frm.doc.name,
        search_by: "MR",
      });
    });
  }

  function buildDialogHtml(stage) {
    const detailsHtml = (stage.details || []).map((detail) => `
      <div class="calco-purchase-journey-dialog__detail">
        <div class="calco-purchase-journey-dialog__detail-label">${escapeHtml(detail.label)}</div>
        <div class="calco-purchase-journey-dialog__detail-value">${escapeHtml(detail.value)}</div>
      </div>
    `).join("");

    const sections = stage.sections || [];
    const documentsHtml = sections.length
      ? sections.map((section) => `
          <div class="calco-purchase-journey-dialog__section">
            <div class="calco-purchase-journey-dialog__section-title">${escapeHtml(section.label)}</div>
            <div class="calco-purchase-journey-dialog__docs">${buildDocumentsHtml(section.documents || [], section.empty_message)}</div>
          </div>
        `).join("")
      : `<div class="calco-purchase-journey-dialog__docs">${buildDocumentsHtml(stage.documents || [], stage.empty_message)}</div>`;

    return `
      <div class="calco-purchase-journey-dialog__summary">${escapeHtml(stage.summary)}</div>
      <div class="calco-purchase-journey-dialog__details">${detailsHtml}</div>
      ${documentsHtml}
    `;
  }

  function buildDocumentsHtml(documents, emptyMessage) {
    if (!documents.length) {
      return `<div class="calco-purchase-journey__empty">${escapeHtml(emptyMessage || __("No linked documents found."))}</div>`;
    }

    return documents.map((doc) => `
      <div class="calco-purchase-journey-dialog__doc">
        <div class="calco-purchase-journey-dialog__doc-top">
          <div>
            <div class="calco-purchase-journey-dialog__doc-type">${escapeHtml(doc.label)}</div>
            <a
              href="#"
              class="calco-purchase-journey-dialog__doc-link"
              data-doctype="${escapeHtml(doc.doctype)}"
              data-name="${escapeHtml(doc.name)}"
            >
              ${escapeHtml(doc.name)}
            </a>
          </div>
          <div class="calco-purchase-journey-dialog__doc-status">${escapeHtml(doc.status)}</div>
        </div>
        <div class="calco-purchase-journey-dialog__doc-detail">${escapeHtml(doc.detail || __("No extra details"))}</div>
      </div>
    `).join("");
  }

  function openStageDialog(frm, stageKey) {
    const stage = ((frm.__purchase_journey_data || {}).stages || []).find((row) => row.key === stageKey);
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
    dialog.$wrapper.on("click", ".calco-purchase-journey-dialog__doc-link", (event) => {
      event.preventDefault();
      const target = event.currentTarget;
      frappe.set_route("Form", target.dataset.doctype, target.dataset.name);
      dialog.hide();
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
        __("Save the Material Request to load the purchase journey tracker."),
        __("Tracker appears directly below the Items table")
      );
      return;
    }

    renderState(wrapper, __("Loading purchase journey from linked ERP documents..."));

    try {
      const response = await frappe.call({
        method: "calco_erp.calco_purchase.purchase_journey.get_purchase_journey",
        args: { material_request: frm.doc.name },
        freeze: false,
      });
      frm.__purchase_journey_data = response.message || {};
      renderTracker(frm, frm.__purchase_journey_data);
    } catch (error) {
      renderState(wrapper, error.message || __("Unable to load Purchase Journey Tracker right now."));
    }
  }

  frappe.ui.form.on("Material Request", {
    refresh(frm) {
      loadTracker(frm);
    },
  });
})();
