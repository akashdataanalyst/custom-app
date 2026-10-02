frappe.pages["master-data-governance"].on_page_load = function (wrapper) {
  new CalcoMasterDataGovernanceDashboard(wrapper);
};

class CalcoMasterDataGovernanceDashboard {
  constructor(wrapper) {
    this.wrapper = $(wrapper);
    this.rows = [];
    this.data = {};
    this.filters = {
      search: "",
      governance_type: "",
      supplier_selection: "",
      current_stage: "",
      status: "",
      owner: "",
      priority: "",
      only_my_pending_actions: 0,
      card_key: "",
    };
    this.make_page();
    this.make_layout();
    this.refresh();
  }

  make_page() {
    this.page = frappe.ui.make_app_page({
      parent: this.wrapper,
      title: "Master Data Governance Center",
      single_column: true,
    });
    this.page.set_primary_action("Proposed RM", () => frappe.new_doc("New RM Request"), "add");
    this.page.add_action_item(
      "Existing RM + Proposed Supplier",
      () => this.open_existing_rm_proposed_supplier_dialog(),
    );
    this.page.add_action_item("Refresh", () => this.refresh());
  }

  open_existing_rm_proposed_supplier_dialog() {
    frappe.model.with_doctype("New Supplier Request", () => {
      const supplierTypeField = frappe.meta.get_docfield(
        "New Supplier Request",
        "supplier_type",
      );
      const dialog = new frappe.ui.Dialog({
        title: __("Existing RM + Proposed Supplier"),
        fields: [
          {
            fieldname: "existing_rm",
            fieldtype: "Link",
            label: __("Existing RM"),
            options: "Item",
            reqd: 1,
            get_query: () => ({
              filters: {
                item_group: "Raw Material",
                disabled: 0,
              },
            }),
          },
          {
            fieldname: "proposed_supplier_name",
            fieldtype: "Data",
            label: __("Proposed Supplier Name"),
            reqd: 1,
          },
          {
            fieldname: "supplier_type",
            fieldtype: supplierTypeField?.fieldtype || "Select",
            label: __("Supplier Type"),
            options: supplierTypeField?.options || "Local\nOverseas\nTrader\nManufacturer",
            reqd: 1,
          },
        ],
        primary_action_label: __("Open Supplier Approval Request"),
        primary_action: (values) => {
          dialog.hide();
          frappe.new_doc("New Supplier Request", {
            supplier_source: "Proposed Supplier",
            proposed_supplier_name: values.proposed_supplier_name,
            supplier_type: values.supplier_type,
            supplier_name: "",
            source_rm_code: values.existing_rm,
          });
        },
      });
      dialog.show();
    });
  }

  make_layout() {
    this.page.main.html(`
      <div class="calco-mdg-dashboard">
        <div class="calco-mdg-dashboard__cards"></div>
        <div class="calco-mdg-dashboard__filters">
          <div class="calco-mdg-dashboard__filter-row">
            ${this.renderFilterInput("search", "Search RM / Supplier / Request ID", "Search")}
            ${this.renderFilterSelect("governance_type", "Governance Type")}
            ${this.renderFilterSelect("supplier_selection", "Supplier Selection")}
            ${this.renderFilterSelect("current_stage", "Current Stage")}
            ${this.renderFilterSelect("status", "Status")}
            ${this.renderFilterSelect("owner", "Current Owner")}
            ${this.renderFilterSelect("priority", "Priority")}
            <label class="calco-mdg-dashboard__checkline">
              <input type="checkbox" class="calco-mdg-dashboard__filter" data-filter="only_my_pending_actions" />
              <span>${__("Only My Pending Actions")}</span>
            </label>
            <div class="calco-mdg-dashboard__filter-actions">
              <button class="btn btn-sm btn-primary calco-mdg-dashboard__apply">${__("Apply")}</button>
              <button class="btn btn-sm btn-default calco-mdg-dashboard__clear">${__("Clear")}</button>
            </div>
          </div>
        </div>
        <div class="calco-mdg-dashboard__summary text-muted"></div>
        <div class="calco-mdg-dashboard__table-wrap">
          <table class="calco-mdg-dashboard__table">
            <thead></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    `);
    this.inject_style();
    this.bind_filters();
  }

  renderFilterInput(fieldname, label, placeholder) {
    return `
      <div class="calco-mdg-dashboard__filter-field calco-mdg-dashboard__filter-field--wide">
        <label>${frappe.utils.escape_html(label)}</label>
        <input type="text" class="form-control calco-mdg-dashboard__filter" data-filter="${fieldname}" placeholder="${frappe.utils.escape_html(placeholder)}" />
      </div>
    `;
  }

  renderFilterSelect(fieldname, label) {
    return `
      <div class="calco-mdg-dashboard__filter-field">
        <label>${frappe.utils.escape_html(label)}</label>
        <select class="form-control calco-mdg-dashboard__filter" data-filter="${fieldname}">
          <option value="">${__("All")}</option>
        </select>
      </div>
    `;
  }

  inject_style() {
    if (document.getElementById("calco-mdg-dashboard-style")) return;
    const style = document.createElement("style");
    style.id = "calco-mdg-dashboard-style";
    style.textContent = `
      .calco-mdg-dashboard__cards {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .calco-mdg-dashboard__card {
        border: 1px solid var(--border-color);
        border-left: 5px solid var(--card-color, #64748b);
        border-radius: 8px;
        background: var(--fg-color);
        padding: 12px 14px;
        min-height: 82px;
      }
      .calco-mdg-dashboard__card-label {
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        line-height: 1.3;
      }
      .calco-mdg-dashboard__card--active {
        border-color: var(--card-color, #64748b);
        box-shadow: 0 0 0 2px color-mix(in srgb, var(--card-color, #64748b) 18%, transparent);
      }
      .calco-mdg-dashboard__card-value {
        font-size: 26px;
        font-weight: 700;
        line-height: 1.2;
        margin-top: 8px;
      }
      .calco-mdg-dashboard__filters {
        border: 1px solid var(--border-color);
        border-radius: 8px;
        background: var(--fg-color);
        padding: 12px;
        margin-bottom: 10px;
      }
      .calco-mdg-dashboard__filter-row {
        display: grid;
        grid-template-columns: minmax(260px, 1.8fr) repeat(6, minmax(150px, 1fr)) minmax(170px, auto) auto;
        gap: 10px;
        align-items: end;
      }
      .calco-mdg-dashboard__filter-field label {
        display: block;
        font-size: 11px;
        font-weight: 700;
        color: var(--text-muted);
        margin-bottom: 5px;
      }
      .calco-mdg-dashboard__checkline {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 38px;
        margin: 0;
        font-weight: 600;
      }
      .calco-mdg-dashboard__filter-actions {
        display: flex;
        gap: 8px;
        justify-content: flex-end;
      }
      .calco-mdg-dashboard__summary {
        margin: 6px 0 10px;
        font-size: 12px;
      }
      .calco-mdg-dashboard__table-wrap {
        border: 1px solid var(--border-color);
        border-radius: 8px;
        overflow: auto;
        max-height: calc(100vh - 330px);
        background: var(--fg-color);
      }
      .calco-mdg-dashboard__table {
        width: 100%;
        min-width: 1900px;
        border-collapse: collapse;
      }
      .calco-mdg-dashboard__table th,
      .calco-mdg-dashboard__table td {
        padding: 9px 10px;
        border-bottom: 1px solid var(--border-color);
        vertical-align: top;
        font-size: 12px;
      }
      .calco-mdg-dashboard__table th {
        position: sticky;
        top: 0;
        z-index: 2;
        background: var(--fg-color);
        font-size: 11px;
        text-transform: uppercase;
        color: var(--text-muted);
        white-space: nowrap;
      }
      .calco-mdg-dashboard__request-link {
        font-weight: 700;
      }
      .calco-mdg-dashboard__badge {
        display: inline-flex;
        align-items: center;
        padding: 3px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        background: #eef2ff;
        color: #3730a3;
        white-space: nowrap;
      }
      .calco-mdg-dashboard__badge--good { background: #dcfce7; color: #166534; }
      .calco-mdg-dashboard__badge--warn { background: #fef3c7; color: #92400e; }
      .calco-mdg-dashboard__badge--bad { background: #fee2e2; color: #991b1b; }
      .calco-mdg-dashboard__badge--muted { background: #f1f5f9; color: #475569; }
      .calco-mdg-dashboard__actions {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        max-width: 260px;
      }
      .calco-mdg-dashboard__readiness {
        min-width: 180px;
      }
      .calco-mdg-dashboard__readiness-bar {
        height: 7px;
        background: #e2e8f0;
        border-radius: 999px;
        overflow: hidden;
        margin: 6px 0;
      }
      .calco-mdg-dashboard__readiness-fill {
        height: 100%;
        background: #0f766e;
      }
      .calco-mdg-dashboard__readiness-components {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
      }
      .calco-mdg-dashboard__journey {
        border: 1px solid var(--border-color);
        border-radius: 8px;
        background: var(--fg-color);
        margin-top: 12px;
        padding: 12px;
      }
      .calco-mdg-dashboard__journey-title {
        font-weight: 700;
        margin-bottom: 10px;
      }
      .calco-mdg-dashboard__journey-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(160px, 1fr));
        gap: 8px;
      }
      .calco-mdg-dashboard__journey-stage {
        border: 1px solid var(--border-color);
        border-radius: 8px;
        padding: 8px;
        min-height: 82px;
      }
      .calco-mdg-dashboard__journey-label {
        font-weight: 700;
        font-size: 12px;
      }
      .calco-mdg-dashboard__journey-owner {
        color: var(--text-muted);
        font-size: 11px;
        margin-top: 6px;
      }
      .calco-mdg-dashboard__empty {
        padding: 22px;
        color: var(--text-muted);
        text-align: center;
      }
      @media (max-width: 1500px) {
        .calco-mdg-dashboard__cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .calco-mdg-dashboard__filter-row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .calco-mdg-dashboard__filter-field--wide { grid-column: span 3; }
      }
      @media (max-width: 800px) {
        .calco-mdg-dashboard__cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .calco-mdg-dashboard__filter-row { grid-template-columns: 1fr; }
        .calco-mdg-dashboard__filter-field--wide { grid-column: span 1; }
      }
    `;
    document.head.appendChild(style);
  }

  bind_filters() {
    const $main = this.page.main;
    $main.find(".calco-mdg-dashboard__apply").on("click", () => {
      this.capture_filters();
      this.refresh();
    });
    $main.find(".calco-mdg-dashboard__clear").on("click", () => this.clear_filters());
    $main.find(".calco-mdg-dashboard__filter").on("keydown", (event) => {
      if (event.key === "Enter") {
        this.capture_filters();
        this.refresh();
      }
    });
    $main.find(".calco-mdg-dashboard__filter").on("change", (event) => {
      if ($(event.currentTarget).attr("data-filter") !== "search") {
        this.capture_filters();
        this.refresh();
      }
    });
  }

  refresh() {
    frappe.call({
      method: "calco_erp.calco_purchase.page.master_data_governance.master_data_governance.get_dashboard_data",
      args: this.filters,
      freeze: true,
      freeze_message: "Loading Master Data Governance Center...",
    }).then((r) => {
      this.data = r.message || {};
      this.rows = this.data.rows || [];
      this.render_cards();
      this.render_filter_options();
      this.sync_filters();
      this.render_table();
      this.render_summary();
    });
  }

  capture_filters() {
    this.page.main.find(".calco-mdg-dashboard__filter").each((_, node) => {
      const $node = $(node);
      const field = $node.attr("data-filter");
      if (!field) return;
      if ($node.attr("type") === "checkbox") {
        this.filters[field] = $node.is(":checked") ? 1 : 0;
      } else {
        this.filters[field] = ($node.val() || "").toString().trim();
      }
    });
  }

  sync_filters() {
    this.page.main.find(".calco-mdg-dashboard__filter").each((_, node) => {
      const $node = $(node);
      const field = $node.attr("data-filter");
      if (!field) return;
      if ($node.attr("type") === "checkbox") {
        $node.prop("checked", !!this.filters[field]);
      } else {
        $node.val(this.filters[field] || "");
      }
    });
  }

  clear_filters() {
    Object.keys(this.filters).forEach((key) => {
      this.filters[key] = key === "only_my_pending_actions" ? 0 : "";
    });
    this.sync_filters();
    this.refresh();
  }

  render_filter_options() {
    const options = this.data.filters || {};
    Object.keys(options).forEach((field) => {
      const current = this.filters[field] || "";
      const choices = options[field] || [];
      const html = [`<option value="">${__("All")}</option>`].concat(
        choices.map((value) => `<option value="${frappe.utils.escape_html(value)}">${frappe.utils.escape_html(value)}</option>`)
      ).join("");
      this.page.main.find(`select[data-filter="${field}"]`).html(html).val(current);
    });
  }

  render_cards() {
    const colors = ["#2563eb", "#475569", "#0f766e", "#b45309", "#7c3aed", "#dc2626", "#0891b2", "#16a34a", "#15803d", "#991b1b", "#334155"];
    this.page.main.find(".calco-mdg-dashboard__cards").html((this.data.cards || []).map((card, index) => {
      const key = card.key || "";
      const activeClass = this.filters.card_key === key ? " calco-mdg-dashboard__card--active" : "";
      return `
        <button type="button" class="calco-mdg-dashboard__card${activeClass}" data-card-key="${frappe.utils.escape_html(key)}" style="--card-color:${colors[index % colors.length]}">
          <div class="calco-mdg-dashboard__card-label">${frappe.utils.escape_html(card.label || "")}</div>
          <div class="calco-mdg-dashboard__card-value">${frappe.utils.escape_html(String(card.value ?? 0))}</div>
        </button>
      `;
    }).join(""));
    this.page.main.find(".calco-mdg-dashboard__card").on("click", (event) => {
      const key = $(event.currentTarget).attr("data-card-key") || "";
      this.filters.card_key = this.filters.card_key === key ? "" : key;
      this.refresh();
    });
  }

  render_summary() {
    const timestamp = this.data.generated_on ? ` | ${__("Generated")}: ${frappe.utils.escape_html(this.data.generated_on)}` : "";
    this.page.main.find(".calco-mdg-dashboard__summary").text(`${__("Rows")}: ${this.rows.length}${timestamp}`);
  }

  render_table() {
    const columns = [
      ["Governance Record", "request_id"],
      ["RM Code / Proposed RM", "rm_code"],
      ["RM Name", "rm_name"],
      ["Supplier", "supplier"],
      ["Supplier Selection", "supplier_selection"],
      ["Resolution Status", "resolution_status"],
      ["Current Stage", "current_stage"],
      ["Technical Status", "technical_status"],
      ["Quality Readiness Status", "quality_readiness_status"],
      ["Commercial Status", "commercial_status"],
      ["Management Status", "management_status"],
      ["Supplier Approval Matrix Status", "supplier_approval_matrix_status"],
      ["RM Quality Template Status", "rm_quality_template_status"],
      ["RM Planning Status", "rm_planning_status"],
      ["Current Owner", "current_owner"],
      ["Operational Readiness", "operational_readiness"],
      ["Age", "age"],
      ["Priority", "priority"],
      ["Next Action", "_actions"],
    ];
    this.page.main.find(".calco-mdg-dashboard__table thead").html(
      `<tr>${columns.map(([label]) => `<th>${frappe.utils.escape_html(label)}</th>`).join("")}</tr>`
    );
    if (!this.rows.length) {
      this.page.main.find(".calco-mdg-dashboard__table tbody").html(`<tr><td colspan="${columns.length}" class="calco-mdg-dashboard__empty">${__("No governance records found.")}</td></tr>`);
      return;
    }
    this.page.main.find(".calco-mdg-dashboard__table tbody").html(this.rows.map((row, index) => `
      <tr data-row-index="${index}">
        ${columns.map(([label, field]) => `<td>${this.render_cell(row, field)}</td>`).join("")}
      </tr>
    `).join(""));
    this.bind_table_actions();
  }

  render_cell(row, field) {
    if (field === "request_id") {
      return `<a href="#" class="calco-mdg-dashboard__request-link" data-open-action="request" data-doctype="${frappe.utils.escape_html(row.doctype || "")}" data-name="${frappe.utils.escape_html(row.request_id || "")}">${frappe.utils.escape_html(row.request_id || "")}</a>`;
    }
    if (field === "_actions") {
      return this.render_actions(row);
    }
    if (field === "operational_readiness") {
      return this.render_operational_readiness(row);
    }
    if (field.endsWith("status") || ["supplier_selection", "current_stage", "priority"].includes(field)) {
      return this.render_badge(row[field]);
    }
    return frappe.utils.escape_html(row[field] || "");
  }

  render_badge(value) {
    const text = value || "";
    const lowered = text.toLowerCase();
    let klass = "calco-mdg-dashboard__badge--muted";
    if (["approved", "ready", "completed", "existing approved supplier", "normal"].some((item) => lowered.includes(item))) klass = "calco-mdg-dashboard__badge--good";
    if (["pending", "waiting", "medium", "new rm approval", "new supplier approval"].some((item) => lowered.includes(item))) klass = "calco-mdg-dashboard__badge--warn";
    if (["rejected", "blocked", "high"].some((item) => lowered.includes(item))) klass = "calco-mdg-dashboard__badge--bad";
    return `<span class="calco-mdg-dashboard__badge ${klass}">${frappe.utils.escape_html(text || "-")}</span>`;
  }

  render_actions(row) {
    const actions = row.actions || [];
    const nextAction = row.next_action
      ? `<div>${this.render_badge(row.next_action)}</div>`
      : "";
    if (!actions.length) return nextAction || "-";
    return `<div class="calco-mdg-dashboard__actions">${nextAction}${actions.map((action) => `
      <button type="button" class="btn btn-xs btn-default calco-mdg-dashboard__open-doc" data-doctype="${frappe.utils.escape_html(action.doctype || "")}" data-name="${frappe.utils.escape_html(action.name || "")}">${frappe.utils.escape_html(action.label || "Open")}</button>
    `).join("")}</div>`;
  }


  render_operational_readiness(row) {
    const percent = Number(row.operational_readiness_percent || 0);
    const overall = row.operational_readiness_overall || "In Progress";
    const components = row.operational_readiness_components || [];
    return `
      <div class="calco-mdg-dashboard__readiness">
        <strong>${frappe.utils.escape_html(percent + "%")}</strong> ${this.render_badge(overall)}
        <div class="calco-mdg-dashboard__readiness-bar"><div class="calco-mdg-dashboard__readiness-fill" style="width:${Math.max(0, Math.min(percent, 100))}%"></div></div>
        <div class="calco-mdg-dashboard__readiness-components">
          ${components.map((component) => this.render_badge(`${component.label}: ${component.ready ? "Ready" : "Pending"}`)).join("")}
        </div>
      </div>
    `;
  }

  render_journey_preview(row) {
    const $journey = this.page.main.find(".calco-mdg-dashboard__journey");
    if (!row) {
      $journey.html(`<div class="text-muted">${__("Select a governance item to view its journey.")}</div>`);
      return;
    }
    const stages = row.journey || [];
    $journey.html(`
      <div class="calco-mdg-dashboard__journey-title">${__("Journey Preview")}: ${frappe.utils.escape_html(row.request_id || "")}</div>
      <div class="calco-mdg-dashboard__journey-grid">
        ${stages.map((stage) => `
          <div class="calco-mdg-dashboard__journey-stage">
            <div class="calco-mdg-dashboard__journey-label">${frappe.utils.escape_html(stage.label || "")}</div>
            <div>${this.render_badge(stage.status || "Pending")}</div>
            <div class="calco-mdg-dashboard__journey-owner">${frappe.utils.escape_html(stage.owner || "")}</div>
          </div>
        `).join("")}
      </div>
    `);
  }
  bind_table_actions() {
    this.page.main.find(".calco-mdg-dashboard__table tbody tr").on("click", (event) => {
      const index = Number($(event.currentTarget).attr("data-row-index"));
      this.render_journey_preview(this.rows[index]);
    });
    this.page.main.find(".calco-mdg-dashboard__open-doc, .calco-mdg-dashboard__request-link").on("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const $target = $(event.currentTarget);
      const doctype = $target.attr("data-doctype");
      const name = $target.attr("data-name");
      if (doctype && name) {
        frappe.set_route("Form", doctype, name);
      }
    });
  }
}
