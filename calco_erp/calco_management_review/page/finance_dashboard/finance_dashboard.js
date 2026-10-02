frappe.pages["finance-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Finance Dashboard",
      method: "calco_erp.calco_management_review.page.finance_dashboard.finance_dashboard.get_dashboard_data",
      freeze_message: "Loading finance dashboard...",
    });
  });
};
