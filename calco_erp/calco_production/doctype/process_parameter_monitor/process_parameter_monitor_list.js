frappe.listview_settings["Process Parameter Monitor"] = {
  add_fields: ["status"],
  get_indicator(doc) {
    const colors = {Draft: "gray", Active: "orange", Completed: "green", Superseded: "gray"};
    return [__(doc.status || "Draft"), colors[doc.status] || "gray", `status,=,${doc.status || "Draft"}`];
  },
};
