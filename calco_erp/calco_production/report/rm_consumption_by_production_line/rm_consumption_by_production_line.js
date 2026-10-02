frappe.query_reports["RM Consumption by Production Line"] = {
  filters: [
    {
      fieldname: "from_date",
      label: "From Date",
      fieldtype: "Date",
      default: frappe.datetime.add_days(frappe.datetime.get_today(), -29),
    },
    {
      fieldname: "to_date",
      label: "To Date",
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
    },
    {
      fieldname: "warehouse",
      label: "Warehouse",
      fieldtype: "Link",
      options: "Warehouse",
    },
    {
      fieldname: "rm_code",
      label: "RM Code",
      fieldtype: "Link",
      options: "Item",
    },
    {
      fieldname: "fg_code",
      label: "FG Code",
      fieldtype: "Link",
      options: "Item",
    },
    {
      fieldname: "production_line",
      label: "Production Line",
      fieldtype: "Link",
      options: "Workstation",
    },
    {
      fieldname: "category",
      label: "Category",
      fieldtype: "Data",
    },
  ],};

