frappe.listview_settings["Bulk Density Monitor"] = {
  onload(listview) {
    listview.page.clear_primary_action();
    listview.page.wrapper
      .find(".primary-action, .btn-primary")
      .filter((_, button) => /add bulk density monitor|create a new bulk density monitor/i.test($(button).text()))
      .remove();
  },
};
