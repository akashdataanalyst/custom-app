frappe.pages["maintenance-dashboard"].on_page_load = function (wrapper) {
  frappe.require("/assets/calco_erp/js/operational_dashboard.js", () => {
    const today = frappe.datetime.get_today();
    new window.CalcoOperationalDashboardPage(wrapper, {
      title: "Maintenance Dashboard",
      method: "calco_erp.calco_maintenance.page.maintenance_dashboard.maintenance_dashboard.get_dashboard_data",
      freeze_message: "Loading maintenance dashboard...",
      filters: [
        {
          label: "From Date",
          fieldname: "from_date",
          fieldtype: "Date",
          default: frappe.datetime.add_days(today, -179),
        },
        {
          label: "To Date",
          fieldname: "to_date",
          fieldtype: "Date",
          default: today,
        },
        {
          label: "Machine",
          fieldname: "machine",
          fieldtype: "Link",
          options: "Maintenance Equipment",
          default: "",
        },
      ],
    });
  });
};
