(function () {
  if (window.CalcoOperationalDashboardPage) {
    return;
  }

  class CalcoOperationalDashboardPage {
    constructor(wrapper, config) {
      this.wrapper = $(wrapper);
      this.config = config;
      this.charts = {};
      this.make_page();
      this.make_layout();
      this.refresh();
    }

    make_page() {
      this.page = frappe.ui.make_app_page({
        parent: this.wrapper,
        title: this.config.title,
        single_column: true,
      });

      this.filter_fields = {};
      const configured_filters = this.config.filters || [];

      if (configured_filters.length) {
        configured_filters.forEach((field) => {
          const original_change = field.change;
          const field_control = this.page.add_field({
            ...field,
            change: () => {
              if (original_change) {
                original_change();
              }
              this.refresh();
            },
          });
          this.filter_fields[field.fieldname] = field_control;
        });
      } else {
        this.date_field = this.page.add_field({
          label: "Date",
          fieldname: "report_date",
          fieldtype: "Date",
          default: "",
          change: () => this.refresh(),
        });
      }

      this.page.set_primary_action("Refresh", () => this.refresh(), "refresh");
    }

    make_layout() {
      this.page.main.html(`
        <div class="calco-ops-dashboard">
          <div class="calco-ops-dashboard__cards"></div>
          <div class="calco-ops-dashboard__charts"></div>
          <div class="calco-ops-dashboard__drilldowns"></div>
        </div>
      `);

      if (!document.getElementById("calco-ops-dashboard-style")) {
        const style = document.createElement("style");
        style.id = "calco-ops-dashboard-style";
        style.textContent = `
          .calco-ops-dashboard__cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
          }
          .calco-ops-dashboard__card,
          .calco-ops-dashboard__panel {
            border: 1px solid var(--border-color);
            border-radius: 12px;
            background: var(--fg-color);
          }
          .calco-ops-dashboard__card {
            display: block;
            padding: 16px;
            text-decoration: none;
            color: inherit;
          }
          .calco-ops-dashboard__card:hover {
            border-color: var(--primary);
          }
          .calco-ops-dashboard__card-label {
            color: var(--text-muted);
            font-size: 12px;
            margin-bottom: 6px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
          }
          .calco-ops-dashboard__card-value {
            font-size: 24px;
            font-weight: 700;
          }
          .calco-ops-dashboard__charts,
          .calco-ops-dashboard__drilldowns {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
            gap: 16px;
            margin-bottom: 16px;
          }
          .calco-ops-dashboard__panel {
            padding: 16px;
            min-height: 320px;
          }
          .calco-ops-dashboard__panel h4 {
            margin: 0 0 12px;
            font-size: 16px;
          }
          .calco-ops-dashboard__chart {
            min-height: 250px;
          }
          .calco-ops-dashboard__empty {
            color: var(--text-muted);
            padding: 24px 0;
          }
          .calco-ops-dashboard__list {
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .calco-ops-dashboard__row {
            display: flex;
            flex-direction: column;
            gap: 2px;
            padding: 10px 12px;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            text-decoration: none;
            color: inherit;
          }
          .calco-ops-dashboard__row:hover {
            border-color: var(--primary);
          }
          .calco-ops-dashboard__row-meta {
            color: var(--text-muted);
            font-size: 12px;
          }
        `;
        document.head.appendChild(style);
      }
    }

    refresh() {
      frappe.call({
        method: this.config.method,
        args: this.build_args(),
        freeze: true,
        freeze_message: this.config.freeze_message || "Loading dashboard...",
      }).then((r) => {
        this.data = r.message || {};
        if (this.date_field && this.data.report_date && this.date_field.get_value() !== this.data.report_date) {
          this.date_field.set_value(this.data.report_date);
        }
        this.render_cards();
        this.render_charts();
        this.render_drilldowns();
      });
    }

    build_args() {
      if (!Object.keys(this.filter_fields || {}).length) {
        return this.date_field && this.date_field.get_value()
          ? { report_date: this.date_field.get_value() }
          : {};
      }

      const args = {};
      Object.entries(this.filter_fields).forEach(([fieldname, field]) => {
        const value = field.get_value();
        if (value !== undefined && value !== null && value !== "") {
          args[fieldname] = value;
        }
      });
      return args;
    }

    render_cards() {
      const cards = this.data.cards || [];
      const html = cards
        .map((card, index) => {
          const tag = card.route ? "a" : "div";
          const href = card.route ? ` href="${card.route}"` : "";
          const value = `${format_number(card.value || 0, null, 3)}${card.suffix || ""}`;
          return `
            <${tag} class="calco-ops-dashboard__card" data-card-index="${index}"${href}>
              <div class="calco-ops-dashboard__card-label">${frappe.utils.escape_html(card.label || "")}</div>
              <div class="calco-ops-dashboard__card-value">${frappe.utils.escape_html(String(value))}</div>
            </${tag}>
          `;
        })
        .join("");
      const cards_container = this.page.main.find(".calco-ops-dashboard__cards");
      cards_container.html(html);
      cards_container.find(".calco-ops-dashboard__card").on("click", (event) => {
        const card_index = Number(event.currentTarget.dataset.cardIndex || -1);
        const card = cards[card_index];
        if (!card || !card.route_doctype || !card.route_options) {
          return;
        }

        event.preventDefault();
        frappe.route_options = card.route_options;
        frappe.set_route("List", card.route_doctype);
      });
    }

    render_charts() {
      const charts = this.data.charts || [];
      this.destroy_charts();
      const charts_html = charts
        .map(
          (chart, index) => `
            <section class="calco-ops-dashboard__panel">
              <h4>${
                chart.route
                  ? `<a href="${chart.route}" class="calco-ops-dashboard__chart-link" data-chart-index="${index}">${frappe.utils.escape_html(chart.title || "")}</a>`
                  : frappe.utils.escape_html(chart.title || "")
              }</h4>
              <div class="calco-ops-dashboard__chart" data-chart="${frappe.utils.escape_html(chart.key)}"></div>
            </section>
          `
        )
        .join("");
      this.page.main.find(".calco-ops-dashboard__charts").html(charts_html);
      this.page.main.find(".calco-ops-dashboard__chart-link").on("click", (event) => {
        const chart_index = Number(event.currentTarget.dataset.chartIndex || -1);
        const chart = charts[chart_index];
        if (!chart) {
          return;
        }

        if (chart.route_report) {
          event.preventDefault();
          frappe.route_options = chart.route_options || {};
          frappe.set_route("query-report", chart.route_report);
          return;
        }

        if (chart.route_doctype && chart.route_options) {
          event.preventDefault();
          frappe.route_options = chart.route_options;
          frappe.set_route("List", chart.route_doctype);
        }
      });

      charts.forEach((chart) => {
        const target = this.page.main.find(`[data-chart="${chart.key}"]`);
        this.render_chart(target, chart);
      });
    }

    render_chart(target, chart) {
      const labels = chart.labels || [];
      const datasets = chart.datasets || [];
      const hasData = labels.length && datasets.length && datasets.some((d) => (d.values || []).length);
      if (!hasData) {
        target.html('<div class="calco-ops-dashboard__empty">No data for the selected date.</div>');
        return;
      }

      target.empty();
      const config = {
        type: chart.type || "bar",
        height: 260,
        colors: chart.colors || ["#1f77b4"],
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
          formatTooltipY: (value) =>
            `${format_number(value || 0, null, 3)}${chart.suffix || ""}`,
        };
      }

      this.charts[chart.key] = new frappe.Chart(target[0], config);
    }

    render_drilldowns() {
      const sections = this.data.drilldowns || [];
      const html = sections
        .map((section) => {
          const rows = section.rows || [];
          const rows_html = rows.length
            ? rows
                .map(
                  (row) => `
                    <a class="calco-ops-dashboard__row" href="${row.route}">
                      <div>${frappe.utils.escape_html(row.label || "")}</div>
                      <div class="calco-ops-dashboard__row-meta">${frappe.utils.escape_html(row.meta || "")}</div>
                    </a>
                  `
                )
                .join("")
            : '<div class="calco-ops-dashboard__empty">No linked records for the selected date.</div>';

          return `
            <section class="calco-ops-dashboard__panel">
              <h4>${frappe.utils.escape_html(section.title || "")}</h4>
              <div class="calco-ops-dashboard__list">${rows_html}</div>
            </section>
          `;
        })
        .join("");
      this.page.main.find(".calco-ops-dashboard__drilldowns").html(html);
    }

    destroy_charts() {
      Object.keys(this.charts).forEach((key) => {
        this.charts[key].destroy();
        delete this.charts[key];
      });
    }
  }

  window.CalcoOperationalDashboardPage = CalcoOperationalDashboardPage;
})();
