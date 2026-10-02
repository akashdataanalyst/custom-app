frappe.pages["sales-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Sales Dashboard",
      method: "calco_erp.calco_customer_approval.page.sales_dashboard.sales_dashboard.get_dashboard_data",
      freeze_message: "Loading sales dashboard...",
    });
  });
};
