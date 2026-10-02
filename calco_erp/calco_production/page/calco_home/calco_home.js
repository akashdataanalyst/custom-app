frappe.pages["calco-home"].on_page_load = function(wrapper) {
  const page=frappe.ui.make_app_page({parent:wrapper,title:__("My Home"),single_column:true});
  wrapper.calco_home=new CalcoEnterpriseHome(page);
};
frappe.pages["calco-home"].on_page_show=wrapper=>wrapper.calco_home?.refresh();
class CalcoEnterpriseHome {
  constructor(page) {this.page=page;this.root=$("<div class='calco-home'></div>").appendTo(page.main);this.request=0;page.set_secondary_action(__("Refresh"),()=>this.refresh());}
  open(tile) {frappe.route_options=Object.assign({},tile.filters);frappe.set_route("List",tile.doctype);}
  tile(tile) {
    const button=$("<button type='button' class='ch-tile'></button>");
    $("<span class='ch-tile-title'></span>").text(tile.label).appendTo(button);
    $("<span class='ch-num'></span>").text(String(tile.count)).appendTo(button);
    $("<span class='ch-unit'></span>").text(__("pending")).appendTo(button);
    return button.on("click",()=>this.open(tile));
  }
  refresh() {
    const token=++this.request;this.root.attr('aria-busy','true');
    frappe.call({method:"calco_erp.enterprise_ui.home.get_home_data",callback:r=>{if(token===this.request)this.render(r.message);},error:()=>{if(token===this.request)this.root.empty().append($("<div role='alert' class='ch-panel ch-empty'></div>").text(__("Unable to load permitted work. Use Refresh to retry.")));},always:()=>this.root.attr('aria-busy','false')});
  }
  render(data) {
    this.root.empty();
    for(const stage of data.stages || []) {
      const section=$("<section><h2></h2><div class='ch-grid ch-metrics'></div></section>").appendTo(this.root);section.find('h2').text(stage.title);
      for(const tile of stage.tiles)section.find('.ch-metrics').append(this.tile(tile));
    }
    const split=$("<div class='ch-grid ch-split'></div>").appendTo(this.root);
    if(data.queue) {
      const q=data.queue,panel=$("<section class='ch-panel'><div class='ch-panel-head'><h2></h2></div></section>").appendTo(split);
      panel.find('h2').text(__("Incoming QC queue")+" ("+q.total+")");
      $("<button type='button' class='ch-link'></button>").text(__("Open full list")).on('click',()=>this.open(q)).appendTo(panel.find('.ch-panel-head'));
      if(!q.rows.length)$("<p class='ch-empty'></p>").text(__("No incoming inspections pending.")).appendTo(panel);
      else {
        const table=$("<table><thead><tr></tr></thead><tbody></tbody></table>");
        for(const label of ['Inspection','Reference','Item','Batch','Created'])$('<th scope="col"></th>').text(__(label)).appendTo(table.find('thead tr'));
        for(const row of q.rows) {const tr=$('<tr></tr>').appendTo(table.find('tbody'));const cell=$('<td></td>').appendTo(tr);$('<a class="ch-link"></a>').text(row.name).attr('href',frappe.utils.get_form_link(q.doctype,row.name)).appendTo(cell);for(const value of [row.reference_name,row.item_code,row.batch_no,frappe.datetime.str_to_user(row.creation)])$('<td></td>').text(value || '').appendTo(tr);}
        $("<div class='ch-table-wrap'></div>").append(table).appendTo(panel);
      }
    }
    const side=$("<div class='ch-side'></div>").appendTo(split);
    if(data.inbox)side.append(this.tile(data.inbox));
    if(data.create?.length) {const panel=$("<section class='ch-panel ch-list'><div class='ch-panel-head'><h2></h2></div></section>").appendTo(side);panel.find('h2').text(__("Create"));for(const dt of data.create)$("<button type='button'></button>").text(__(dt)).on('click',()=>frappe.new_doc(dt)).appendTo(panel);}
    if(data.dashboards?.length) {const section=$("<section><h2></h2><div class='ch-grid ch-dash'></div></section>").appendTo(this.root);section.find('h2').text(__("Dashboards and reviews"));for(const d of data.dashboards)$("<button type='button' class='ch-tile is-small'></button>").text(d.label).on('click',()=>frappe.set_route(d.route)).appendTo(section.find('.ch-dash'));}
    if(!data.stages?.length && !data.inbox && !data.queue)$('<p class="ch-empty"></p>').text(__("No pending-work views are available with your current permissions.")).appendTo(this.root);
  }
}
