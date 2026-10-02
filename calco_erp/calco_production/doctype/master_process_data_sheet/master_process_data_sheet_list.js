frappe.listview_settings["Master Process Data Sheet"] = {
  get_indicator(doc) {
    const colors = {
      "Imported - Unverified": "orange",
      Draft: "gray",
      "Pending Approval": "blue",
      "Approved / Current": "green",
      Superseded: "darkgrey",
    };
    return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
  },
};
