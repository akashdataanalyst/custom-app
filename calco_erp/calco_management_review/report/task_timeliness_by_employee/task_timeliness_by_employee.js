// Use the site/business timezone, even when the User has a different timezone.
frappe.query_reports["Task Timeliness by Employee"] = {
    onload(report) {
        report.page.add_inner_button(__("Assignment Details"), () => {
            const users=(report.data || []).map(row=>row.user).filter(Boolean);
            if (!users.length) return frappe.msgprint(__("Run the report first; no reported Users are available."));
            frappe.prompt([{fieldname:"user",label:__("Reported User"),fieldtype:"Select",options:users.join("\n"),reqd:1}], values => {
                frappe.call({method:"calco_erp.task_timeliness.access.management_assignments",args:{user:values.user,filters:report.get_filter_values()},callback(result) {
                    const d=new frappe.ui.Dialog({title:__("Governed Assignment Details"),size:"extra-large",fields:[{fieldname:"rows",fieldtype:"HTML"}]});
                    const esc=v=>frappe.utils.escape_html(String(v==null?"":v));
                    const keys=["assignment","reference_type","reference","due_date","completed_on","status","classification"];
                    d.fields_dict.rows.$wrapper.html('<div class="table-responsive"><table class="table table-bordered"><thead><tr>'+keys.map(k=>"<th>"+esc(frappe.model.unscrub(k))+"</th>").join("")+"</tr></thead><tbody>"+result.message.assignments.map(row=>"<tr>"+keys.map(k=>"<td>"+esc(row[k])+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>");d.show();
                }});
            },__("Assignment Details"));
        });
    },
    filters: [
        {fieldname:"from_date",label:__("From Date"),fieldtype:"Date",reqd:1,
         default:moment(frappe.datetime.system_datetime()).startOf("quarter").format("YYYY-MM-DD")},
        {fieldname:"to_date",label:__("To Date"),fieldtype:"Date",reqd:1,
         default:moment(frappe.datetime.system_datetime()).endOf("quarter").format("YYYY-MM-DD")}
    ]
};
