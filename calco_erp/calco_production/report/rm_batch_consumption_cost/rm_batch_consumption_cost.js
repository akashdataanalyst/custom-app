frappe.query_reports["RM Batch Consumption Cost"] = {
  filters: [
    {
      fieldname: "from_date",
      label: __("From Date"),
      fieldtype: "Date",
      default: frappe.datetime.month_start(),
      reqd: 1,
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1,
    },
    {
      fieldname: "production_line",
      label: __("Production Line"),
      fieldtype: "Link",
      options: "Workstation",
    },
    {
      fieldname: "fg_item",
      label: __("FG Item"),
      fieldtype: "Link",
      options: "Item",
    },
    {
      fieldname: "production_batch",
      label: __("Production Batch"),
      fieldtype: "Data",
    },
    {
      fieldname: "production_consumption_entry",
      label: __("Production Consumption Entry"),
      fieldtype: "Link",
      options: "Production Consumption Entry",
    },
    {
      fieldname: "rm_item",
      label: __("RM Item"),
      fieldtype: "Link",
      options: "Item",
    },
    {
      fieldname: "rm_batch",
      label: __("RM Batch"),
      fieldtype: "Data",
    },
  ],
  formatter(value, row, column, data, default_formatter) {
    const formatted = default_formatter(value, row, column, data);
    if (column.fieldname !== "rm_batch" || !data || !data.batch_exists || !value) {
      return formatted;
    }

    const escaped = frappe.utils.escape_html(String(value));
    return `<a href="/app/batch/${encodeURIComponent(value)}">${escaped}</a>`;
  },
};
