frappe.pages["purchase-performance-dashboard"].on_page_load = function (wrapper) {
  new CalcoPurchasePerformanceDashboard(wrapper);
};

class CalcoPurchasePerformanceDashboard {
  constructor(wrapper) {
    this.wrapper = $(wrapper);
    this.charts = {};
    this.make_page();
    this.make_layout();
    this.refresh();
  }

  make_page() {
    this.page = frappe.ui.make_app_page({
      parent: this.wrapper,
      title: "Purchase Performance Dashboard",
      single_column: true,
    });

    this.filter_fields = {};
    this.filter_fields.supplier = this.page.add_field({
      label: "Supplier",
      fieldname: "supplier",
      fieldtype: "Link",
      options: "Supplier",
      change: () => this.refresh(),
    });
    this.filter_fields.item = this.page.add_field({
      label: "Item",
      fieldname: "item",
      fieldtype: "Link",
      options: "Item",
      change: () => this.refresh(),
    });
    this.filter_fields.month = this.page.add_field({
      label: "Month",
      fieldname: "month",
      fieldtype: "Select",
      options: buildMonthOptions(),
      change: () => {
        if (this.filter_fields.month.get_value()) {
          this.filter_fields.quarter.set_value("");
        }
        this.refresh();
      },
    });
    this.filter_fields.quarter = this.page.add_field({
      label: "Quarter",
      fieldname: "quarter",
      fieldtype: "Select",
      options: buildQuarterOptions(),
      change: () => {
        if (this.filter_fields.quarter.get_value()) {
          this.filter_fields.month.set_value("");
        }
        this.refresh();
      },
    });
    this.filter_fields.supplier_type = this.page.add_field({
      label: "Local / Overseas",
      fieldname: "supplier_type",
      fieldtype: "Select",
      options: "\nLocal\nOverseas",
      change: () => this.refresh(),
    });

    this.page.set_primary_action("Refresh", () => this.refresh(), "refresh");
  }

  make_layout() {
    this.page.main.html(`
      <div class="calco-purchase-performance">
        <div class="calco-purchase-performance__hero">
          <div>
            <div class="calco-purchase-performance__eyebrow">Purchase Analytics</div>
            <h1 class="calco-purchase-performance__title">Separate Purchase Performance Dashboard</h1>
            <p class="calco-purchase-performance__subtitle">
              Procurement health, supplier reliability, delivery execution, quality outcomes, commercial variance, and supply risk in one focused workspace.
            </p>
          </div>
          <div class="calco-purchase-performance__context"></div>
        </div>
        <div class="calco-purchase-performance__sections"></div>
      </div>
    `);

    if (!document.getElementById("calco-purchase-performance-style")) {
      const style = document.createElement("style");
      style.id = "calco-purchase-performance-style";
      style.textContent = `
        .calco-purchase-performance {
          display: flex;
          flex-direction: column;
          gap: 24px;
          padding-bottom: 24px;
        }
        .calco-purchase-performance__hero {
          display: grid;
          grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.8fr);
          gap: 20px;
          padding: 24px;
          border-radius: 22px;
          background:
            radial-gradient(circle at top left, rgba(14, 116, 144, 0.12), transparent 40%),
            linear-gradient(135deg, rgba(255,255,255,1), rgba(241,245,249,0.96));
          border: 1px solid rgba(14, 116, 144, 0.12);
        }
        .calco-purchase-performance__eyebrow {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.16em;
          color: #0f766e;
          font-weight: 700;
          margin-bottom: 10px;
        }
        .calco-purchase-performance__title {
          margin: 0 0 10px;
          font-size: 30px;
          line-height: 1.05;
        }
        .calco-purchase-performance__subtitle {
          margin: 0;
          color: var(--text-muted);
          max-width: 760px;
          line-height: 1.55;
        }
        .calco-purchase-performance__context {
          border-radius: 18px;
          border: 1px solid rgba(15, 23, 42, 0.08);
          background: rgba(255,255,255,0.88);
          padding: 18px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .calco-purchase-performance__context-row {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          font-size: 13px;
        }
        .calco-purchase-performance__context-label {
          color: var(--text-muted);
        }
        .calco-purchase-performance__section {
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .calco-purchase-performance__section-header {
          display: flex;
          align-items: end;
          justify-content: space-between;
          gap: 12px;
        }
        .calco-purchase-performance__section-title {
          margin: 0;
          font-size: 22px;
        }
        .calco-purchase-performance__card-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
          gap: 14px;
        }
        .calco-purchase-performance__card,
        .calco-purchase-performance__panel {
          border-radius: 18px;
          border: 1px solid rgba(15, 23, 42, 0.08);
          background: rgba(255,255,255,0.96);
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
        }
        .calco-purchase-performance__card {
          padding: 18px;
          cursor: pointer;
          transition: transform 0.15s ease, border-color 0.15s ease;
        }
        .calco-purchase-performance__card:hover {
          transform: translateY(-2px);
          border-color: rgba(14, 116, 144, 0.35);
        }
        .calco-purchase-performance__card-label {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: var(--text-muted);
          margin-bottom: 8px;
        }
        .calco-purchase-performance__card-value {
          font-size: 28px;
          font-weight: 700;
          line-height: 1.05;
        }
        .calco-purchase-performance__panel-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
          gap: 14px;
        }
        .calco-purchase-performance__panel {
          padding: 18px;
          min-height: 300px;
        }
        .calco-purchase-performance__panel h4 {
          margin: 0 0 12px;
          font-size: 16px;
        }
        .calco-purchase-performance__chart {
          min-height: 240px;
        }
        .calco-purchase-performance__rows {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .calco-purchase-performance__row {
          display: block;
          padding: 12px 14px;
          border-radius: 14px;
          border: 1px solid rgba(15, 23, 42, 0.08);
          text-decoration: none;
          color: inherit;
        }
        .calco-purchase-performance__row:hover {
          border-color: rgba(14, 116, 144, 0.35);
        }
        .calco-purchase-performance__row-meta {
          color: var(--text-muted);
          font-size: 12px;
          margin-top: 4px;
        }
        .calco-purchase-performance__empty {
          color: var(--text-muted);
          padding: 16px 0;
        }
        @media (max-width: 900px) {
          .calco-purchase-performance__hero {
            grid-template-columns: 1fr;
          }
        }
      `;
      document.head.appendChild(style);
    }
  }

  refresh() {
    const requestId = this.requestId = (this.requestId || 0) + 1;
    frappe.call({
      method: "calco_erp.calco_purchase.page.purchase_performance_dashboard.purchase_performance_dashboard.get_dashboard_data",
      args: this.build_args(),
      freeze: true,
      freeze_message: "Loading purchase performance dashboard...",
    }).then((r) => {
      if (requestId !== this.requestId) return;
      this.data = r.message || {};
      this.render_context();
      this.render_sections();
    });
  }

  build_args() {
    const args = {};
    Object.entries(this.filter_fields).forEach(([fieldname, field]) => {
      const value = field.get_value();
      if (value !== undefined && value !== null && value !== "") {
        args[fieldname] = value;
      }
    });
    return args;
  }

  render_context() {
    const context = this.page.main.find(".calco-purchase-performance__context");
    const rows = [
      ["Period", this.data.period_label || "All Time"],
      ["As Of", this.data.as_of || ""],
      ["Supplier", this.filter_fields.supplier.get_value() || "All Suppliers"],
      ["Item", this.filter_fields.item.get_value() || "All Items"],
      ["Scope", this.filter_fields.supplier_type.get_value() || "Local + Overseas"],
      ["Basis", this.data.scope_note || ""],
    ];
    context.html(
      rows
        .map(
          ([label, value]) => `
            <div class="calco-purchase-performance__context-row">
              <span class="calco-purchase-performance__context-label">${frappe.utils.escape_html(label)}</span>
              <strong>${frappe.utils.escape_html(String(value || "-"))}</strong>
            </div>
          `
        )
        .join("")
    );
  }

  render_sections() {
    this.destroy_charts();
    const container = this.page.main.find(".calco-purchase-performance__sections");
    const sections = this.data.sections || [];
    container.html(
      sections
        .map(
          (section, index) => `
            <section class="calco-purchase-performance__section" data-section-index="${index}">
              <div class="calco-purchase-performance__section-header">
                <div>
                  <h2 class="calco-purchase-performance__section-title">${frappe.utils.escape_html(section.title || "")}</h2>
                </div>
              </div>
              <div class="calco-purchase-performance__card-grid">
                ${(section.cards || [])
                  .map(
                    (card, cardIndex) => `
                      <div class="calco-purchase-performance__card" data-card-index="${cardIndex}" data-section-index="${index}">
                        <div class="calco-purchase-performance__card-label">${frappe.utils.escape_html(card.label || "")}</div>
                        <div class="calco-purchase-performance__card-value">${frappe.utils.escape_html(formatCardValue(card))}</div>
                      </div>
                    `
                  )
                  .join("")}
              </div>
              <div class="calco-purchase-performance__panel-grid">
                ${(section.charts || [])
                  .map(
                    (chart, chartIndex) => `
                      <section class="calco-purchase-performance__panel">
                        <h4>${frappe.utils.escape_html(chart.title || "")}</h4>
                        <div class="calco-purchase-performance__chart" data-section-chart="${index}-${chartIndex}"></div>
                      </section>
                    `
                  )
                  .join("")}
                ${(section.drilldowns || [])
                  .map(
                    (drilldown) => `
                      <section class="calco-purchase-performance__panel">
                        <h4>${frappe.utils.escape_html(drilldown.title || "")}</h4>
                        <div class="calco-purchase-performance__rows">
                          ${renderDrilldownRows(drilldown.rows || [])}
                        </div>
                      </section>
                    `
                  )
                  .join("")}
              </div>
            </section>
          `
        )
        .join("")
    );

    container.find(".calco-purchase-performance__card").on("click", (event) => {
      const section = sections[Number(event.currentTarget.dataset.sectionIndex || -1)];
      const card = section?.cards?.[Number(event.currentTarget.dataset.cardIndex || -1)];
      openRoute(card);
    });

    sections.forEach((section, sectionIndex) => {
      (section.charts || []).forEach((chart, chartIndex) => {
        this.render_chart(
          this.page.main.find(`[data-section-chart="${sectionIndex}-${chartIndex}"]`),
          chart
        );
      });
    });
  }

  render_chart(target, chart) {
    const labels = chart.labels || [];
    const datasets = chart.datasets || [];
    const hasData = labels.length && datasets.length && datasets.some((d) => (d.values || []).length);
    if (!hasData) {
      target.html('<div class="calco-purchase-performance__empty">No data for the selected filters.</div>');
      return;
    }

    target.empty();
    const config = {
      type: chart.type || "bar",
      height: 250,
      colors: chart.colors || ["#0f766e"],
      data: {
        labels,
        datasets: datasets.map((dataset) => ({
          name: dataset.name || "Value",
          values: dataset.values || [],
        })),
      },
    };

    if (config.type !== "donut") {
      config.axisOptions = {
        xAxisMode: "tick",
        yAxisMode: "span",
        xIsSeries: 1,
      };
      config.tooltipOptions = {
        formatTooltipY: (value) => `${format_number(value || 0, null, chart.precision ?? 3)}${chart.suffix || ""}`,
      };
    }

    this.charts[chart.key || frappe.utils.get_random(8)] = new frappe.Chart(target[0], config);
  }

  destroy_charts() {
    Object.keys(this.charts).forEach((key) => {
      this.charts[key].destroy();
      delete this.charts[key];
    });
  }
}

function formatCardValue(card) {
  return `${format_number(card.value || 0, null, card.precision ?? 3)}${card.suffix || ""}`;
}

function renderDrilldownRows(rows) {
  if (!rows.length) {
    return '<div class="calco-purchase-performance__empty">No supporting records for the selected filters.</div>';
  }
  return rows
    .map(
      (row) => `
        <a class="calco-purchase-performance__row" href="${row.route}">
          <div>${frappe.utils.escape_html(row.label || "")}</div>
          <div class="calco-purchase-performance__row-meta">${frappe.utils.escape_html(row.meta || "")}</div>
        </a>
      `
    )
    .join("");
}

function openRoute(payload) {
  if (!payload) {
    return;
  }
  if (payload.route_doctype && payload.route_options) {
    frappe.route_options = payload.route_options;
    frappe.set_route("List", payload.route_doctype);
    return;
  }
  if (payload.route_doctype) {
    frappe.set_route("List", payload.route_doctype);
    return;
  }
  if (payload.route) {
    window.location.href = payload.route;
  }
}

function buildMonthOptions() {
  const options = [""];
  const now = frappe.datetime.str_to_obj(frappe.datetime.get_today());
  for (let offset = 0; offset < 12; offset += 1) {
    const date = new Date(now.getFullYear(), now.getMonth() - offset, 1);
    options.push(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`);
  }
  return options.join("\n");
}

function buildQuarterOptions() {
  const options = [""];
  const now = frappe.datetime.str_to_obj(frappe.datetime.get_today());
  const currentYear = now.getFullYear();
  [currentYear, currentYear - 1].forEach((year) => {
    [1, 2, 3, 4].forEach((quarter) => {
      options.push(`${year}-Q${quarter}`);
    });
  });
  return options.join("\n");
}
