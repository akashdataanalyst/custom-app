frappe.listview_settings["Feeder Run"] = {
  onload(listview) {
    listview.page.clear_primary_action();
    listview.page.wrapper
      .find(".primary-action, .btn-primary")
      .filter((_, button) => /add feeder run|create a new feeder run/i.test($(button).text()))
      .hide();
  },
};
