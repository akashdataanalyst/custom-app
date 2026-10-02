frappe.listview_settings["Preventive Maintenance Plan"] = {
    get_indicator(doc) {
        if (doc.schedule_status === "Overdue") {
            return [__("Overdue"), "red", "schedule_status,=,Overdue"];
        }

        if (doc.schedule_status === "Due Today") {
            return [__("Due Today"), "orange", "schedule_status,=,Due Today"];
        }

        if (doc.schedule_status === "Upcoming") {
            return [__("Upcoming"), "green", "schedule_status,=,Upcoming"];
        }

        if (doc.schedule_status === "Inactive") {
            return [__("Inactive"), "gray", "schedule_status,=,Inactive"];
        }

        return [__("Manual"), "blue", "schedule_status,=,Manual"];
    },
};
