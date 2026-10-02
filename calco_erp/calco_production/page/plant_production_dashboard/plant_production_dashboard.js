frappe.pages["plant-production-dashboard"].on_page_load = function (wrapper) {
  new PlantProductionDashboard(wrapper);
};

class PlantProductionDashboard {
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
      title: "Plant Production Dashboard",
      single_column: true,
    });

    this.date_field = this.page.add_field({
      label: "Date",
      fieldname: "report_date",
      fieldtype: "Date",
      default: "",
      change: () => this.refresh(),
    });

    this.page.set_primary_action("Refresh", () => this.refresh(), "refresh");
  }

  make_layout() {
    this.page.main.html(`
      <div class="plant-dashboard">
        <div class="plant-dashboard__cards"></div>
        <div class="plant-dashboard__grid">
          <section class="plant-dashboard__panel">
            <h4>Daily Production by FG</h4>
            <div class="plant-dashboard__chart" data-chart="production"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Output per Machine</h4>
            <div class="plant-dashboard__chart" data-chart="machine-output"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Output per Operator</h4>
            <div class="plant-dashboard__chart" data-chart="operator-output"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Output per Shift</h4>
            <div class="plant-dashboard__chart" data-chart="shift-output"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Extruder Utilization</h4>
            <div class="plant-dashboard__chart" data-chart="utilization"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>RM Consumption</h4>
            <div class="plant-dashboard__chart" data-chart="rm"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>RM Cost per Kg</h4>
            <div class="plant-dashboard__chart" data-chart="rm-cost"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Manufacturing Cost per Kg</h4>
            <div class="plant-dashboard__chart" data-chart="manufacturing-cost"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Profit per Product</h4>
            <div class="plant-dashboard__chart" data-chart="profit"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>QC Failures</h4>
            <div class="plant-dashboard__chart" data-chart="qc"></div>
          </section>
          <section class="plant-dashboard__panel">
            <h4>Dispatch Quantity</h4>
            <div class="plant-dashboard__chart" data-chart="dispatch"></div>
          </section>
        </div>
      </div>
    `);

    if (!document.getElementById("plant-production-dashboard-style")) {
      const style = document.createElement("style");
      style.id = "plant-production-dashboard-style";
      style.textContent = `
        .plant-dashboard__cards {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 16px;
          margin-bottom: 20px;
        }
        .plant-dashboard__card {
          border: 1px solid var(--border-color);
          border-radius: 12px;
          background: var(--fg-color);
          padding: 16px;
        }
        .plant-dashboard__card-label {
          color: var(--text-muted);
          font-size: 12px;
          margin-bottom: 6px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .plant-dashboard__card-value {
          font-size: 24px;
          font-weight: 700;
        }
        .plant-dashboard__grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
          gap: 16px;
        }
        .plant-dashboard__panel {
          border: 1px solid var(--border-color);
          border-radius: 12px;
          background: var(--fg-color);
          padding: 16px;
          min-height: 340px;
        }
        .plant-dashboard__panel h4 {
          margin: 0 0 12px;
          font-size: 16px;
        }
        .plant-dashboard__chart {
          min-height: 260px;
        }
        .plant-dashboard__empty {
          color: var(--text-muted);
          padding: 24px 0;
        }
      `;
      document.head.appendChild(style);
    }
  }

  refresh() {
    frappe.call({
      method:
        "calco_erp.calco_production.page.plant_production_dashboard.plant_production_dashboard.get_dashboard_data",
      args: this.date_field.get_value()
        ? {
            report_date: this.date_field.get_value(),
          }
        : {},
      freeze: true,
      freeze_message: "Loading plant dashboard...",
    }).then((r) => {
      this.data = r.message || {};
      if (this.data.report_date && this.date_field.get_value() !== this.data.report_date) {
        this.date_field.set_value(this.data.report_date);
      }
      this.render_cards();
      this.render_charts();
    });
  }

  render_cards() {
    const cards = this.data.cards || {};
    const formatQty = (value) => format_number(value || 0, null, 3);
    const toInt = (value) => Number.parseInt(value || 0, 10) || 0;
    const items = [
      { label: "Production Qty", value: `${formatQty(cards.production_qty)} Kg` },
      { label: "Dispatch Qty", value: `${formatQty(cards.dispatch_qty)} Kg` },
      { label: "RM Consumed", value: `${formatQty(cards.rm_consumption_qty)} Kg` },
      { label: "QC Failures", value: toInt(cards.qc_failure_count || 0) },
      { label: "Machines Active", value: toInt(cards.machine_count || 0) },
      { label: "Operators Active", value: toInt(cards.operator_count || 0) },
      { label: "Shifts Active", value: toInt(cards.shift_count || 0) },
      { label: "Avg RM Cost / Kg", value: `Rs ${formatQty(cards.avg_rm_cost_per_kg)}` },
      { label: "Avg Mfg Cost / Kg", value: `Rs ${formatQty(cards.avg_manufacturing_cost_per_kg)}` },
      { label: "Gross Profit", value: `Rs ${formatQty(cards.gross_profit_total)}` },
      { label: "Avg Extruder Utilization", value: `${formatQty(cards.avg_extruder_utilization)}%` },
    ];

    const html = items
      .map(
        (item) => `
          <div class="plant-dashboard__card">
            <div class="plant-dashboard__card-label">${frappe.utils.escape_html(item.label)}</div>
            <div class="plant-dashboard__card-value">${frappe.utils.escape_html(String(item.value))}</div>
          </div>
        `
      )
      .join("");

    this.page.main.find(".plant-dashboard__cards").html(html);
  }

  render_charts() {
    this.renderBarChart("production", this.data.daily_production_by_fg || [], "qty", ["#c62828"]);
    this.renderBarChart("machine-output", this.data.output_by_machine || [], "produced_qty", ["#ef6c00"], "machine");
    this.renderBarChart(
      "operator-output",
      this.data.output_by_operator || [],
      "produced_qty",
      ["#00897b"],
      "operator_name"
    );
    this.renderBarChart("shift-output", this.data.output_by_shift || [], "produced_qty", ["#5e35b1"], "shift_type");
    this.renderBarChart("utilization", this.data.extruder_utilization || [], "utilization_pct", ["#1565c0"], "workstation", "%");
    this.renderBarChart("rm", this.data.rm_consumption || [], "qty", ["#2e7d32"]);
    this.renderBarChart("rm-cost", this.data.cost_by_product || [], "rm_cost_per_kg", ["#00838f"], "item_code", " Rs/Kg");
    this.renderBarChart(
      "manufacturing-cost",
      this.data.cost_by_product || [],
      "manufacturing_cost_per_kg",
      ["#ad1457"],
      "item_code",
      " Rs/Kg"
    );
    this.renderBarChart("profit", this.data.profit_by_product || [], "gross_profit_total", ["#2e7d32"], "item_code", " Rs");
    this.renderDonutChart("qc", this.data.qc_failures || [], "count", ["#ef5350", "#ffa726", "#29b6f6", "#ab47bc", "#66bb6a"], "label");
    this.renderBarChart("dispatch", this.data.dispatch_quantity || [], "qty", ["#6a1b9a"]);
  }

  renderBarChart(chartKey, rows, valueKey, colors, labelKey = "item_code", suffix = " Kg") {
    const target = this.page.main.find(`[data-chart="${chartKey}"]`);
    if (!rows.length) {
      target.html('<div class="plant-dashboard__empty">No data for the selected date.</div>');
      return;
    }

    target.empty();
    this.destroyChart(chartKey);
    this.charts[chartKey] = new frappe.Chart(target[0], {
      type: "bar",
      height: 260,
      colors,
      data: {
        labels: rows.map((row) => row[labelKey]),
        datasets: [
          {
            values: rows.map((row) => row[valueKey] || 0),
          },
        ],
      },
      axisOptions: {
        xAxisMode: "tick",
        yAxisMode: "span",
        xIsSeries: 1,
      },
      tooltipOptions: {
        formatTooltipY: (value) => `${format_number(value || 0, null, 3)}${suffix}`,
      },
    });
  }

  renderDonutChart(chartKey, rows, valueKey, colors, labelKey = "label") {
    const target = this.page.main.find(`[data-chart="${chartKey}"]`);
    const filteredRows = rows.filter((row) => (Number(row[valueKey] || 0) || 0) > 0);
    if (!filteredRows.length) {
      target.html('<div class="plant-dashboard__empty">No QC failures for the selected date.</div>');
      return;
    }

    target.empty();
    this.destroyChart(chartKey);
    this.charts[chartKey] = new frappe.Chart(target[0], {
      type: "donut",
      height: 260,
      colors,
      data: {
        labels: filteredRows.map((row) => row[labelKey]),
        datasets: [
          {
            values: filteredRows.map((row) => row[valueKey] || 0),
          },
        ],
      },
    });
  }

  destroyChart(chartKey) {
    if (this.charts[chartKey]) {
      this.charts[chartKey].destroy();
      delete this.charts[chartKey];
    }
  }
}
