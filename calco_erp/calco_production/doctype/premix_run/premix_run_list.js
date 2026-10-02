frappe.listview_settings["Premix Run"] = {
  onload(listview) {
    listview.page.clear_primary_action();
    listview.page.wrapper
      .find(".primary-action, .btn-primary")
      .filter((_, button) => /add premix run|create a new premix run/i.test($(button).text()))
      .hide();
  },
};
