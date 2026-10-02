frappe.pages["production-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Production Dashboard",
      method: "calco_erp.calco_production.page.production_dashboard.production_dashboard.get_dashboard_data",
      freeze_message: "Loading production dashboard...",
    });
  });
};
