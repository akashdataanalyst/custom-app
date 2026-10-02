frappe.listview_settings["Blending Run"] = {
  onload(listview) {
    listview.page.clear_primary_action();
    listview.page.wrapper
      .find(".primary-action, .btn-primary")
      .filter((_, button) => /add blending run|create a new blending run/i.test($(button).text()))
      .hide();
  },
};
