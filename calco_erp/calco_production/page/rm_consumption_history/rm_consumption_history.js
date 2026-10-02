frappe.pages['rm-consumption-history'].on_page_load = wrapper => {
  const page=frappe.ui.make_app_page({parent:wrapper,title:__('RM Consumption History'),single_column:true});
  let index=0,request=0;
  const company=page.add_field({fieldname:'company',fieldtype:'Link',options:'Company',label:__('Company'),default:frappe.defaults.get_user_default('Company')});
  const source=page.add_field({fieldname:'source',fieldtype:'Select',label:__('Records'),options:['All','Final','Manual','Historical'],default:'All'});
  const search=page.add_field({fieldname:'search',fieldtype:'Data',label:__('Find PCE / FG / Batch / Line')});
  page.main.append('<p class="text-muted">Manual parallel production, final batch confirmations and historical consumption entries. Open the original record for quantities, posting details or authorized corrections.</p><div class="rm-history-table" style="overflow:auto"></div><div class="text-right"><button class="btn btn-default btn-sm rm-history-prev">Previous</button> <span class="rm-history-page"></span> <button class="btn btn-default btn-sm rm-history-next">Next</button></div>');
  const esc=v=>frappe.utils.escape_html(String(v??''));
  async function load(reset=false){if(reset)index=0;const current=++request;
    const result=await frappe.call({method:'calco_erp.calco_production.page.rm_consumption_history.rm_consumption_history.get_history',args:{company:company.get_value(),source:source.get_value()||'All',search:search.get_value()||'',page:index}});
    if(current!==request)return;const data=result.message;
    page.main.find('.rm-history-table').html(`<table class="table table-bordered"><thead><tr><th>Record</th><th>Evidence type</th><th>Date</th><th>FG / Work Order</th><th>Batch</th><th>Status</th></tr></thead><tbody>${data.rows.map(r=>`<tr><td><a href="/desk/${frappe.router.slug(r.doctype)}/${encodeURIComponent(r.name)}">${esc(r.name)}</a></td><td>${r.source==='Final'?'Final actual confirmation':r.source==='Manual'?'Manual / Parallel Production':'Historical consumption entry'}</td><td>${esc(r.date)}</td><td>${esc(r.fg_code||r.work_order)}</td><td>${esc(r.batch)}</td><td>${esc(r.status)}</td></tr>`).join('')||'<tr><td colspan="6">No accessible records match these filters.</td></tr>'}</tbody></table>`);
    page.main.find('.rm-history-page').text(`Page ${index+1}`);page.main.find('.rm-history-prev').prop('disabled',index===0);page.main.find('.rm-history-next').prop('disabled',!data.has_more);
  }
  if (frappe.model.can_create('Production Consumption Entry')) {
    page.set_primary_action(__('+ Add Consumption'),()=>frappe.new_doc('Production Consumption Entry', {consumption_mode:'Manual / Parallel Production'}),'add');
  }
  page.set_secondary_action(__('Apply Filters'),()=>load(true));
  page.main.find('.rm-history-prev').on('click',()=>{index--;load();});page.main.find('.rm-history-next').on('click',()=>{index++;load();});load();
};
