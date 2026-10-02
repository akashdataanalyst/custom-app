frappe.pages["inventory-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Inventory Dashboard",
      method: "calco_erp.calco_purchase.page.inventory_dashboard.inventory_dashboard.get_dashboard_data",
      freeze_message: "Loading inventory dashboard...",
    });
  });
};
