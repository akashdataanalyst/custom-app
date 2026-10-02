frappe.pages["my-task-timeliness"].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({parent:wrapper,title:__("My Task Timeliness"),single_column:true});
    const today = moment(frappe.datetime.system_datetime());
    const from = page.add_field({fieldname:"from_date",label:__("From Date"),fieldtype:"Date",default:today.clone().startOf("quarter").format("YYYY-MM-DD")});
    const to = page.add_field({fieldname:"to_date",label:__("To Date"),fieldtype:"Date",default:today.clone().endOf("quarter").format("YYYY-MM-DD")});
    const body = $('<div class="p-4"></div>').appendTo(page.main);
    const esc = value => frappe.utils.escape_html(String(value == null ? "" : value));
    let request=0;
    async function refresh() {
        const current=++request; body.empty();
        const result=await frappe.call({method:"calco_erp.task_timeliness.access.my_timeliness",args:{filters:{from_date:from.get_value(),to_date:to.get_value()}}});
        if(current!==request) return;
        const data=result.message; body.append($("<p>").text(data.message));
        if(!data.employee) return;
        body.append($("<h4>").text(data.employee.employee_name));
        const keys=[["total_due","Total Due"],["on_time","On Time"],["late","Late"],["overdue","Overdue"],["pending","Pending"],["unknown_completion","Unknown Completion"],["on_time_percent","On-Time %"]];
        body.append('<div class="table-responsive"><table class="table table-bordered"><thead><tr>'+keys.map(x=>'<th>'+esc(__(x[1]))+'</th>').join('')+'</tr></thead><tbody><tr>'+keys.map(x=>'<td>'+esc(data.summary[x[0]])+'</td>').join('')+'</tr></tbody></table></div>');
        const cols=["assignment","reference_type","reference","due_date","completed_on","status","classification"];
        body.append('<h5>'+esc(__("My governed assignments"))+'</h5><div class="table-responsive"><table class="table table-bordered"><thead><tr>'+["Assignment","Reference Type","Reference","Due Date","Actual Completion On","Status","Classification"].map(x=>'<th>'+esc(__(x))+'</th>').join('')+'</tr></thead><tbody>'+data.assignments.map(row=>'<tr>'+cols.map(k=>'<td>'+esc(row[k])+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');
    }
    page.set_primary_action(__("Refresh"),refresh);refresh();
};
