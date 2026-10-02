frappe.listview_settings["Shift Report"] = {
  add_fields: ["status", "operational_shift_date", "shift", "job_card", "fg_item", "fg_batch", "production_line", "total_quantity", "total_downtime_hours", "senior_engineer_signed_by"],
  get_indicator(doc) {
    const colors = {Draft:"gray", Active:"orange", Completed:"green", Superseded:"gray"};
    return [__(doc.status || "Draft"), colors[doc.status] || "gray", `status,=,${doc.status || "Draft"}`];
  },
  onload(listview) {
    listview.page.clear_primary_action();
    listview.page.wrapper.find(".primary-action, .btn-primary")
      .filter((_, button) => /add shift report|create a new shift report/i.test($(button).text()))
      .remove();
  },
};
