frappe.pages["quality-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Quality Dashboard",
      method: "calco_erp.calco_quality.page.quality_dashboard.quality_dashboard.get_dashboard_data",
      freeze_message: "Loading quality dashboard...",
      filters: [
        {
          label: "From Date",
          fieldname: "from_date",
          fieldtype: "Date",
          default: frappe.datetime.add_days(frappe.datetime.get_today(), -29),
        },
        {
          label: "To Date",
          fieldname: "to_date",
          fieldtype: "Date",
          default: frappe.datetime.get_today(),
        },
        {
          label: "Supplier",
          fieldname: "supplier",
          fieldtype: "Link",
          options: "Supplier",
        },
        {
          label: "RM Item",
          fieldname: "item_code",
          fieldtype: "Link",
          options: "Item",
        },
      ],
    });
  });
};
