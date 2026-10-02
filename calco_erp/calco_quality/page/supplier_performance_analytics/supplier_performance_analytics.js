frappe.pages["supplier-performance-analytics"].on_page_load = function (wrapper) {
  new CalcoSupplierPerformanceAnalytics(wrapper);
};

class CalcoSupplierPerformanceAnalytics {
  constructor(wrapper) {
    this.wrapper = $(wrapper);
    this.make_page();
    this.make_layout();
    this.refresh();
  }

  make_page() {
    this.page = frappe.ui.make_app_page({
      parent: this.wrapper,
      title: "Supplier Performance Analytics",
      single_column: true,
    });
    this.page.set_primary_action("Refresh", () => this.refresh(), "refresh");
  }

  make_layout() {
    this.page.main.html(`
      <div class="calco-supplier-performance">
        <div class="calco-supplier-performance__cards"></div>
        <div class="calco-supplier-performance__table-wrap">
          <table class="calco-supplier-performance__table">
            <thead></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    `);

    if (!document.getElementById("calco-supplier-performance-style")) {
      const style = document.createElement("style");
      style.id = "calco-supplier-performance-style";
      style.textContent = `
        .calco-supplier-performance__cards {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 12px;
          margin-bottom: 16px;
        }
        .calco-supplier-performance__card {
          border: 1px solid var(--border-color);
          border-left: 6px solid var(--card-accent, #2563eb);
          border-radius: 14px;
          padding: 14px 16px;
          background: linear-gradient(180deg, var(--card-soft, #fff) 0%, var(--fg-color) 100%);
          box-shadow: 0 8px 18px rgba(15, 23, 42, 0.04);
        }
        .calco-supplier-performance__card-label {
          color: var(--text-muted);
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          margin-bottom: 8px;
        }
        .calco-supplier-performance__card-value {
          font-size: 28px;
          font-weight: 700;
          line-height: 1.1;
        }
        .calco-supplier-performance__table-wrap {
          overflow: auto;
          border: 1px solid var(--border-color);
          border-radius: 14px;
          background: var(--fg-color);
          max-height: calc(100vh - 260px);
          box-shadow: 0 8px 18px rgba(15, 23, 42, 0.04);
        }
        .calco-supplier-performance__table {
          width: 100%;
          border-collapse: collapse;
          min-width: 1120px;
        }
        .calco-supplier-performance__table th,
        .calco-supplier-performance__table td {
          padding: 10px 12px;
          border-bottom: 1px solid var(--border-color);
          font-size: 12px;
          text-align: left;
        }
        .calco-supplier-performance__table th {
          position: sticky;
          top: 0;
          z-index: 5;
          background: white;
          box-shadow: 0 1px 0 var(--border-color);
          white-space: nowrap;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.03em;
          color: #475467;
        }
        @media (max-width: 1000px) {
          .calco-supplier-performance__cards {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }
      `;
      document.head.appendChild(style);
    }
  }

  refresh() {
    frappe.call({
      method: "calco_erp.calco_quality.page.supplier_performance_analytics.supplier_performance_analytics.get_dashboard_data",
      freeze: true,
      freeze_message: "Loading supplier performance analytics...",
    }).then((r) => {
      this.data = r.message || {};
      this.render_cards();
      this.render_table();
    });
  }

  render_cards() {
    const cards = [
      { label: "Total Suppliers", value: this.data.cards?.total_suppliers || 0, accent: "#2563eb", soft: "#eff6ff" },
      { label: "Average On-Time %", value: this.formatNumber(this.data.cards?.avg_on_time || 0, 2), accent: "#16a34a", soft: "#effcf4" },
      { label: "Open CAPA", value: this.data.cards?.open_capa || 0, accent: "#d92d20", soft: "#fff1f1" },
      { label: "RM Rejections", value: this.data.cards?.rm_rejections || 0, accent: "#dc6803", soft: "#fffaeb" },
    ];
    this.page.main.find(".calco-supplier-performance__cards").html(
      cards.map((card) => `
        <div class="calco-supplier-performance__card" style="--card-accent:${card.accent}; --card-soft:${card.soft};">
          <div class="calco-supplier-performance__card-label">${frappe.utils.escape_html(card.label)}</div>
          <div class="calco-supplier-performance__card-value">${frappe.utils.escape_html(String(card.value))}</div>
        </div>
      `).join("")
    );
  }

  render_table() {
    const rows = this.data.rows || [];
    const columns = [
      "Supplier",
      "Supplier Type",
      "Avg Lead Time",
      "Min Lead Time",
      "Max Lead Time",
      "On-Time Delivery %",
      "Late Deliveries",
      "RM Rejections",
      "Open CAPA",
      "Supplier Rating",
    ];
    this.page.main.find(".calco-supplier-performance__table thead").html(
      `<tr>${columns.map((col) => `<th>${frappe.utils.escape_html(col)}</th>`).join("")}</tr>`
    );
    this.page.main.find(".calco-supplier-performance__table tbody").html(
      rows.map((row) => `
        <tr>
          <td>${frappe.utils.escape_html(row.supplier || "")}</td>
          <td>${frappe.utils.escape_html(row.supplier_type || "")}</td>
          <td>${frappe.format(row.avg_lead_time, { fieldtype: "Float", precision: 2 })}</td>
          <td>${frappe.format(row.min_lead_time, { fieldtype: "Float", precision: 2 })}</td>
          <td>${frappe.format(row.max_lead_time, { fieldtype: "Float", precision: 2 })}</td>
          <td>${frappe.format(row.on_time_delivery_percentage, { fieldtype: "Percent", precision: 2 })}</td>
          <td>${frappe.format(row.late_deliveries, { fieldtype: "Int" })}</td>
          <td>${frappe.format(row.rm_rejections, { fieldtype: "Int" })}</td>
          <td>${frappe.format(row.open_capa, { fieldtype: "Int" })}</td>
          <td>${frappe.format(row.supplier_rating, { fieldtype: "Float", precision: 2 })}</td>
        </tr>
      `).join("")
    );
  }

  formatNumber(value, precision = 2) {
    return Number(Number(value || 0).toFixed(precision)).toString();
  }
}
