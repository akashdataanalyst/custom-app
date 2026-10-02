frappe.pages["production-planning-center"].on_page_load = function (wrapper) {
  new CalcoProductionPlanningCenter(wrapper);
};

class CalcoProductionPlanningCenter {
  constructor(wrapper) {
    this.wrapper = $(wrapper);
    this.canPlan = ["System Manager","Sales User","Sales Manager","Customer Service"].some((role)=>frappe.user.has_role(role));
    this.canProduce = ["System Manager","Manufacturing User","Manufacturing Manager","Production Manager","Production Head"].some((role)=>frappe.user.has_role(role));
    this.view = this.canPlan ? "planning" : "production";
    const weekStart = moment().startOf("isoWeek").format("YYYY-MM-DD");
    this.filters = {from_date:weekStart,to_date:frappe.datetime.add_days(weekStart,6),item_code:"",source_type:"",planning_status:"",review_status:""};
    this.makePage(); this.makeLayout(); this.bindEvents(); this.refresh();
  }

  makePage() {
    this.page=frappe.ui.make_app_page({parent:this.wrapper,title:__("Production Planning Center"),single_column:true});
    this.page.set_primary_action(__("Refresh"),()=>this.refresh(),"refresh");
  }

  makeLayout() {
    this.page.main.html(`
      <div class="calco-ppc">
        <div class="calco-ppc__tabs">
          ${this.canPlan?`<button class="btn btn-sm btn-primary ppc-tab" data-view="planning">${__("Planning Review")}</button>`:""}
          ${this.canProduce?`<button class="btn btn-sm btn-default ppc-tab" data-view="production">${__("Released to Production")}</button>`:""}
        </div>
        <div class="calco-ppc__planning">
          <div class="calco-ppc__cards"></div>
          <div class="calco-ppc__filters">
            <div class="calco-ppc__field"><label>${__("From Date")}</label><input class="form-control ppc-from" type="date"></div>
            <div class="calco-ppc__field"><label>${__("To Date")}</label><input class="form-control ppc-to" type="date"></div>
            <div class="calco-ppc__field"><label>${__("FG Item")}</label><input class="form-control ppc-item"></div>
            <div class="calco-ppc__field"><label>${__("Source")}</label><select class="form-control ppc-source"><option value="">${__("All")}</option><option>Sales Order</option><option>Forecast</option></select></div>
            <div class="calco-ppc__field"><label>${__("Status")}</label><select class="form-control ppc-status"><option value="">${__("All")}</option>${this.statusOptions()}</select></div>
            <div class="calco-ppc__field"><label>${__("Review")}</label><select class="form-control ppc-review-filter"><option value="">${__("All")}</option><option>Pending Review</option><option>Confirmed</option><option>On Hold</option><option>Rejected</option></select></div>
            <div class="calco-ppc__filter-actions"><button class="btn btn-sm btn-primary ppc-apply">${__("Apply")}</button><button class="btn btn-sm btn-default ppc-clear">${__("Clear")}</button></div>
          </div>
          <div class="calco-ppc__meta"></div>
          <div class="calco-ppc__table-wrap"><table class="calco-ppc__table"><thead><tr>
            <th>${__("FG")}</th><th>${__("Period / Required")}</th><th class="num">${__("Forecast")}</th><th class="num">${__("Firm SO")}</th>
            <th class="num">${__("Demand Basis")}</th><th class="num">${__("FG Coverage")}</th><th class="num">${__("Production Coverage")}</th>
            <th class="num">${__("Released")}</th><th class="num">${__("Remaining")}</th><th>${__("Priority")}</th><th>${__("Review")}</th><th>${__("Status")}</th><th>${__("Action")}</th>
          </tr></thead><tbody class="ppc-planning-body"></tbody></table></div>
        </div>
        <div class="calco-ppc__production" style="display:none">
          <div class="calco-ppc__meta ppc-queue-meta"></div>
          <div class="calco-ppc__table-wrap"><table class="calco-ppc__table calco-ppc__queue"><thead><tr>
            <th>${__("FG")}</th><th class="num">${__("Released Qty")}</th><th class="num">${__("Work Order Qty")}</th><th class="num">${__("Remaining Qty")}</th>
            <th>${__("Required Date")}</th><th>${__("Priority")}</th><th>${__("Planning Sources")}</th><th>${__("Status")}</th><th>${__("Action")}</th>
          </tr></thead><tbody class="ppc-production-body"></tbody></table></div>
        </div>
      </div>`);
    this.$from=this.page.main.find(".ppc-from").val(this.filters.from_date); this.$to=this.page.main.find(".ppc-to").val(this.filters.to_date);
    this.$item=this.page.main.find(".ppc-item"); this.$source=this.page.main.find(".ppc-source"); this.$status=this.page.main.find(".ppc-status"); this.$review=this.page.main.find(".ppc-review-filter");
    this.injectStyles(); this.switchView(this.view);
  }

  statusOptions() {
    return ["Waiting Planning Approval","Ready to Release","Partially Released to Production","Released to Production","Covered by Production","On Hold","Rejected","Blocked"]
      .map((value)=>`<option>${__(value)}</option>`).join("");
  }

  injectStyles() {
    if(document.getElementById("calco-ppc-style"))return;
    const style=document.createElement("style"); style.id="calco-ppc-style";
    style.textContent=`
      .calco-ppc__tabs{display:flex;gap:6px;margin-bottom:12px}.calco-ppc__cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-bottom:10px}
      .calco-ppc__card{border:1px solid var(--border-color);border-left:4px solid var(--accent,#64748b);border-radius:6px;background:var(--fg-color);padding:10px 12px;cursor:pointer}
      .calco-ppc__card.is-selected{box-shadow:0 0 0 2px rgba(37,99,235,.18)}.calco-ppc__card-label{font-size:11px;color:var(--text-muted);min-height:28px}.calco-ppc__card-value{font-size:22px;font-weight:700}
      .calco-ppc__filters{display:grid;grid-template-columns:130px 130px minmax(160px,1fr) 130px 190px 145px auto;gap:8px;align-items:end;padding:10px;border:1px solid var(--border-color);background:var(--fg-color);margin-bottom:8px}
      .calco-ppc__field label{display:block;font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:4px}.calco-ppc__field .form-control{height:34px}.calco-ppc__filter-actions,.calco-ppc__actions{display:flex;gap:5px;white-space:nowrap}
      .calco-ppc__meta{font-size:11px;color:var(--text-muted);margin:7px 2px}.calco-ppc__table-wrap{overflow:auto;max-height:calc(100vh - 295px);border:1px solid var(--border-color);background:var(--fg-color)}
      .calco-ppc__table{width:100%;min-width:1450px;border-collapse:collapse}.calco-ppc__queue{min-width:1120px}.calco-ppc__table th,.calco-ppc__table td{padding:8px;border-bottom:1px solid var(--border-color);font-size:12px;text-align:left;vertical-align:middle}
      .calco-ppc__table th{position:sticky;top:0;background:var(--fg-color);z-index:2;color:var(--text-muted);font-size:10px;text-transform:uppercase;white-space:nowrap}.calco-ppc__table .num{text-align:right;font-variant-numeric:tabular-nums}
      .calco-ppc__item{font-weight:700}.calco-ppc__sub{font-size:10px;color:var(--text-muted);margin-top:2px}.calco-ppc__status{display:inline-flex;padding:3px 7px;border-radius:4px;font-size:10px;font-weight:700;background:#eef2f6;color:#344054}
      .calco-ppc__status.ready{background:#dcfce7;color:#15803d}.calco-ppc__status.pending{background:#fef3c7;color:#92400e}.calco-ppc__status.blocked{background:#fee4e2;color:#b42318}.calco-ppc__status.released{background:#e0f2fe;color:#0369a1}
      @media(max-width:1200px){.calco-ppc__cards{grid-template-columns:repeat(3,minmax(0,1fr))}.calco-ppc__filters{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:768px){.calco-ppc__cards,.calco-ppc__filters{grid-template-columns:1fr 1fr}.calco-ppc__filter-actions{grid-column:1/-1}}
    `; document.head.appendChild(style);
  }

  bindEvents() {
    this.page.main.on("click",".ppc-tab",(event)=>this.switchView($(event.currentTarget).data("view")));
    this.page.main.on("click",".ppc-apply",()=>this.applyFilters()); this.page.main.on("click",".ppc-clear",()=>this.clearFilters());
    this.page.main.on("click",".calco-ppc__card",(event)=>this.applyCard($(event.currentTarget)));
    this.page.main.on("click",".ppc-details",(event)=>this.showDetails($(event.currentTarget).data("key")));
    this.page.main.on("click",".ppc-review",(event)=>this.openReviewDialog($(event.currentTarget).data("key")));
    this.page.main.on("click",".ppc-release",(event)=>this.openReleaseDialog($(event.currentTarget).data("key")));
    this.page.main.on("click",".ppc-create-work-order",(event)=>this.openWorkOrderDialog($(event.currentTarget).data("release")));
    this.page.main.on("click",".ppc-open",(event)=>this.openRoute($(event.currentTarget).data()));
  }

  switchView(view) {
    this.view=view; this.page.main.find(".ppc-tab").removeClass("btn-primary").addClass("btn-default");
    this.page.main.find(`.ppc-tab[data-view="${view}"]`).removeClass("btn-default").addClass("btn-primary");
    this.page.main.find(".calco-ppc__planning").toggle(view==="planning"); this.page.main.find(".calco-ppc__production").toggle(view==="production");
  }

  applyFilters() {
    this.filters={from_date:this.$from.val(),to_date:this.$to.val(),item_code:this.$item.val().trim(),source_type:this.$source.val(),planning_status:this.$status.val(),review_status:this.$review.val()}; this.refresh();
  }
  clearFilters() {
    const weekStart=moment().startOf("isoWeek").format("YYYY-MM-DD"); this.filters={from_date:weekStart,to_date:frappe.datetime.add_days(weekStart,6),item_code:"",source_type:"",planning_status:"",review_status:""};
    this.$from.val(this.filters.from_date);this.$to.val(this.filters.to_date);this.$item.val("");this.$source.val("");this.$status.val("");this.$review.val("");this.refresh();
  }
  applyCard($card) { const status=$card.data("status")||"";this.filters.planning_status=this.filters.planning_status===status?"":status;this.$status.val(this.filters.planning_status);this.refresh(); }

  refresh() {
    frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.get_planning_data",args:this.filters,freeze:true,freeze_message:__("Calculating production requirements...")})
      .then((response)=>{this.data=response.message||{};this.rows=this.data.rows||[];this.queue=this.data.production_queue||[];this.renderCards();this.renderPlanningRows();this.renderProductionRows();this.renderMeta();});
  }

  renderCards() {
    const colors=["#d97706","#16a34a","#0284c7","#2563eb","#dc2626"];
    this.page.main.find(".calco-ppc__cards").html((this.data.cards||[]).map((card,index)=>`
      <div class="calco-ppc__card ${this.filters.planning_status===card.status?"is-selected":""}" data-status="${this.esc(card.status||"")}" style="--accent:${colors[index]}">
        <div class="calco-ppc__card-label">${__(card.label)}</div><div class="calco-ppc__card-value">${card.value||0}</div>
      </div>`).join(""));
  }

  renderPlanningRows() {
    const body=this.page.main.find(".ppc-planning-body");if(!this.rows.length){body.html(`<tr><td colspan="13" class="text-muted text-center">${__("No planning demand found for this period.")}</td></tr>`);return;}
    body.html(this.rows.map((row)=>`
      <tr title="${this.esc(row.blocked_reason||row.calculation_explanation||"")}">
        <td><div class="calco-ppc__item">${this.esc(row.item_code)}</div><div class="calco-ppc__sub">${this.esc(row.item_name||"")}</div></td>
        <td>${this.esc(`${row.period_start} to ${row.period_end}`)}<div class="calco-ppc__sub">${this.esc(row.required_date||"")}</div></td>
        <td class="num">${this.qty(row.forecast_qty)}</td><td class="num">${this.qty(row.sales_order_qty)}</td><td class="num"><b>${this.qty(row.demand_basis)}</b></td>
        <td class="num">${this.qty(row.fg_allocated)}</td><td class="num">${this.qty(row.open_production_qty)}</td><td class="num">${this.qty(row.already_released_qty)}</td><td class="num"><b>${this.qty(row.remaining_qty)}</b></td>
        <td>${this.esc(row.priority||"")}</td><td>${this.badge(row.review_status||"Pending Review")}</td><td>${this.badge(row.planning_status)}</td>
        <td><div class="calco-ppc__actions">${this.reviewButton(row)}${this.releaseButton(row)}<button class="btn btn-xs btn-default ppc-details" data-key="${this.esc(row.recommendation_key)}" title="${__("Source and calculation details")}">${frappe.utils.icon("info","xs")}</button></div></td>
      </tr>`).join(""));
  }

  renderProductionRows() {
    const body=this.page.main.find(".ppc-production-body");if(!this.queue.length){body.html(`<tr><td colspan="9" class="text-muted text-center">${__("No released production requirement is waiting for Work Orders.")}</td></tr>`);return;}
    body.html(this.queue.map((row)=>`
      <tr><td><div class="calco-ppc__item">${this.esc(row.item_code)}</div><div class="calco-ppc__sub">${this.esc(row.item_name||"")}</div></td>
      <td class="num">${this.qty(row.released_qty)}</td><td class="num">${this.qty(row.work_order_qty)}</td><td class="num"><b>${this.qty(row.remaining_qty)}</b></td>
      <td>${this.esc(row.required_date||"")}</td><td>${this.esc(row.priority||"")}</td><td>${this.esc(row.source_summary||"")}</td><td>${this.badge(row.status)}</td>
      <td><div class="calco-ppc__actions">${row.draft_work_order?`<button class="btn btn-xs btn-primary ppc-open" data-doctype="Work Order" data-name="${this.esc(row.draft_work_order)}">${__("Open Draft Work Order")}</button>`:""}
      ${row.can_create_work_order&&!row.draft_work_order?`<button class="btn btn-xs btn-primary ppc-create-work-order" data-release="${this.esc(row.production_requirement)}">${__("Create Work Order")}</button>`:""}
      <button class="btn btn-xs btn-default ppc-open" data-doctype="Production Requirement" data-name="${this.esc(row.production_requirement)}" title="${__("Open released requirement")}">${frappe.utils.icon("external-link","xs")}</button>
      ${row.production_plan?`<button class="btn btn-xs btn-default ppc-open" data-doctype="Production Plan" data-name="${this.esc(row.production_plan)}" title="${__("Open internal Production Plan")}">${frappe.utils.icon("factory","xs")}</button>`:""}</div></td></tr>`).join(""));
  }

  reviewButton(row){if(!this.canPlan||["Covered by Production","Released to Production","Blocked"].includes(row.planning_status))return"";return `<button class="btn btn-xs btn-default ppc-review" data-key="${this.esc(row.recommendation_key)}" title="${__("Planning Review")}">${frappe.utils.icon("edit","xs")}</button>`;}
  releaseButton(row){return this.canPlan&&row.can_release?`<button class="btn btn-xs btn-primary ppc-release" data-key="${this.esc(row.recommendation_key)}">${__("Release to Production")}</button>`:"";}
  badge(value){let cls="pending";if(["Ready to Release","Confirmed","Covered by Production"].includes(value))cls="ready";else if(["Blocked","Rejected"].includes(value))cls="blocked";else if(["Released to Production","Partially Released to Production","Partially Converted"].includes(value))cls="released";return `<span class="calco-ppc__status ${cls}">${this.esc(value||"")}</span>`;}

  showDetails(key) {
    const row=this.rows.find((item)=>item.recommendation_key===key);if(!row)return;
    const sources=(row.source_details||[]).map((source)=>`<tr><td>${this.esc(source.source_type)}</td><td>${this.esc(source.source_name)}</td><td>${this.qty(source.qty)}</td><td>${this.esc(source.required_date||"")}</td></tr>`).join("");
    const values=[["Forecast Qty",row.forecast_qty],["Firm Sales Order Qty",row.sales_order_qty],["Demand Basis",row.demand_basis],["FG Coverage",row.fg_allocated],["External Production Coverage",row.open_production_qty],["Net Production Requirement",row.net_requirement],["Already Released",row.already_released_qty],["Remaining Requirement",row.remaining_qty],["Explanation",row.calculation_explanation]];
    frappe.msgprint({title:__("Planning Requirement Details"),message:`<dl>${values.map(([label,value])=>`<dt>${__(label)}</dt><dd>${this.esc(String(value??""))}</dd>`).join("")}</dl><table class="table table-bordered"><thead><tr><th>${__("Source")}</th><th>${__("Document")}</th><th>${__("Qty")}</th><th>${__("Required Date")}</th></tr></thead><tbody>${sources}</tbody></table>`,wide:true});
  }

  openReviewDialog(key) {
    const row=this.rows.find((item)=>item.recommendation_key===key);if(!row)return;
    const dialog=new frappe.ui.Dialog({title:__("Production Planning Review"),fields:[
      {fieldname:"item",fieldtype:"Data",label:__("FG Item"),default:row.item_code,read_only:1},{fieldname:"net_requirement",fieldtype:"Float",label:__("Net Requirement"),default:row.net_requirement,read_only:1},
      {fieldname:"decision",fieldtype:"Select",label:__("Decision"),options:"Confirmed\nOn Hold\nRejected",default:row.review_status==="Pending Review"?"Confirmed":row.review_status,reqd:1},
      {fieldname:"reviewed_qty",fieldtype:"Float",label:__("Quantity Approved for Release"),precision:3,default:row.reviewed_qty||row.net_requirement,depends_on:"eval:doc.decision==='Confirmed'"},{fieldname:"remarks",fieldtype:"Small Text",label:__("Remarks"),default:row.review_remarks||""}
    ],primary_action_label:__("Save Review"),primary_action:(values)=>{
      if(values.decision==="Confirmed"&&(!Number(values.reviewed_qty)||Number(values.reviewed_qty)>Number(row.net_requirement))){frappe.msgprint(__("Approved quantity must be greater than zero and cannot exceed Net Requirement."));return;}
      if(["On Hold","Rejected"].includes(values.decision)&&!(values.remarks||"").trim()){frappe.msgprint(__("Remarks are mandatory for On Hold or Rejected."));return;}
      dialog.disable_primary_action();frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.set_planning_review",args:{recommendation_key:key,decision:values.decision,reviewed_qty:values.reviewed_qty||0,remarks:values.remarks||"",from_date:this.filters.from_date,to_date:this.filters.to_date},freeze:true,freeze_message:__("Saving Planning Review...")}).then(()=>{dialog.hide();this.refresh();}).finally(()=>dialog.enable_primary_action());
    }});dialog.show();
  }

  async checkMaterials(){
    const response=await frappe.call({method:"calco_erp.planning_upgrade.planning.check_month",args:{from_date:this.filters.from_date,to_date:this.filters.to_date},freeze:true,freeze_message:__("Checking released RM and existing commitments...")});
    const data=response.message||{}, rows=data.rows||[];
    const html=rows.map(r=>`<h5>${this.esc(r.item_code)} — ${this.esc(r.status)}</h5><p>Buildable Now: ${this.qty(r.buildable_now)} · Waiting for RM: ${this.qty(r.waiting_for_rm)} · ${this.esc(r.month_risk||"")}</p><div style="overflow:auto"><table class="table table-bordered"><thead><tr>${["RM","Required","Released physical","Committed","Net buildable stock","Shortage","Expected PO","Receipt ETA","Eligible ETA"].map(x=>`<th>${this.esc(x)}</th>`).join("")}</tr></thead><tbody>${r.materials.map(m=>`<tr>${[m.item_code,m.required,m.released_physical,m.existing_commitments,m.net_buildable_stock,m.current_shortage,m.expected_po_supply,m.expected_receipt_date||"Unknown",m.production_eligible_eta||"Unknown"].map(v=>`<td>${this.esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`).join("");
    const blockers=(data.blockers||[]).map(x=>`<p class="text-danger">${this.esc(x.reason)}</p>`).join("");
    const token=crypto.randomUUID();
    const dialog=new frappe.ui.Dialog({title:__("Month RM Check — Released stock only"),size:"extra-large",fields:[{fieldtype:"HTML",options:html+blockers||__("No remaining approved requirements.")}],
      ...(frappe.model.can_create("Material Request") && rows.length && !(data.blockers||[]).length ? {
        primary_action_label:__("Prepare Draft Material Request"),
        primary_action:()=>{dialog.disable_primary_action();frappe.call({
          method:"calco_erp.planning_upgrade.procurement.prepare_draft",
          args:{from_date:this.filters.from_date,to_date:this.filters.to_date,request_token:token},freeze:true,
          freeze_message:__("Rechecking uncovered procurement; creating Draft only...")
        }).then(r=>{dialog.hide();frappe.set_route("Form","Material Request",r.message.name);})
          .finally(()=>dialog.enable_primary_action());}
      } : {})});dialog.show();
  }

  openReleaseDialog(key){frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.get_release_context",args:{recommendation_key:key,from_date:this.filters.from_date,to_date:this.filters.to_date},freeze:true,freeze_message:__("Refreshing approved requirement...")}).then((response)=>this.showReleaseDialog(response.message||{}));}
  showReleaseDialog(context) {
    const allocation=context.rm_allocation;
    if(!allocation){frappe.msgprint(__("RM allocation could not be established. Run RM Check and resolve the listed blockers."));return;}
    context.buildable_now=Number(allocation.buildable_now);
    const summary=[["FG",context.item_code],["Planning Period",`${context.period_start} to ${context.period_end}`],["Required Date",context.required_date],["Forecast Qty",this.qty(context.forecast_qty)],["Firm Sales Order Qty",this.qty(context.sales_order_qty)],["Demand Basis",this.qty(context.demand_basis)],["FG Coverage",this.qty(context.fg_allocated)],["Production Coverage",this.qty(context.open_production_qty)],["Net Requirement",this.qty(context.net_requirement)],["Already Released",this.qty(context.already_released_qty)],["Remaining Approved Qty",this.qty(context.remaining_to_release)],["Buildable Now — Released RM only",this.qty(context.buildable_now)],["Waiting for RM",this.qty(allocation.waiting_for_rm)],["Expected completion",allocation.expected_completion||"Unknown"],["Month risk",allocation.month_risk]];
    const dialog=new frappe.ui.Dialog({title:__("Release to Production"),fields:[{fieldname:"summary",fieldtype:"HTML",options:`<dl>${summary.map(([l,v])=>`<dt>${__(l)}</dt><dd>${this.esc(String(v||""))}</dd>`).join("")}</dl>`},{fieldname:"qty",fieldtype:"Float",label:__("Qty to Release"),precision:3,default:context.buildable_now,reqd:1},{fieldname:"priority",fieldtype:"Select",label:__("Priority"),options:"High\nNormal\nLow",default:context.priority||"Normal",reqd:1}],primary_action_label:__("Release to Production"),primary_action:(values)=>{
      if(Number(values.qty)<=0||Number(values.qty)>Number(context.buildable_now)){frappe.msgprint(__("Release quantity must be greater than zero and cannot exceed current Buildable Now."));return;}
      dialog.disable_primary_action();frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.release_to_production",args:{recommendation_key:context.recommendation_key,qty:values.qty,priority:values.priority,release_token:context.release_token,from_date:this.filters.from_date,to_date:this.filters.to_date},freeze:true,freeze_message:__("Releasing requirement to Production...")}).then(()=>{dialog.hide();this.switchView("production");this.refresh();}).finally(()=>dialog.enable_primary_action());
    }});dialog.show();
  }

  openWorkOrderDialog(release){frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.get_work_order_creation_context",args:{production_requirement:release},freeze:true,freeze_message:__("Resolving Production configuration...")}).then((response)=>{const context=response.message||{};if(context.existing_work_order){frappe.set_route("Form","Work Order",context.existing_work_order);return;}this.showWorkOrderDialog(context);});}
  showWorkOrderDialog(context) {
    const lines=context.line_options||[];const dialog=new frappe.ui.Dialog({title:__("Create Work Order"),fields:[
      {fieldname:"summary",fieldtype:"HTML",options:`<dl><dt>${__("FG")}</dt><dd>${this.esc(context.fg)}</dd><dt>${__("Released Qty")}</dt><dd>${this.qty(context.released_qty)}</dd><dt>${__("Already Converted")}</dt><dd>${this.qty(context.already_converted_qty)}</dd><dt>${__("Remaining Qty")}</dt><dd>${this.qty(context.remaining_qty)}</dd></dl>`},
      {fieldname:"qty",fieldtype:"Float",label:__("Qty to Manufacture Now"),precision:3,default:context.remaining_qty,reqd:1},{fieldname:"production_line",fieldtype:"Select",label:__("Production Line"),options:["",...lines].join("\n"),default:lines.length===1?lines[0]:"",reqd:1},
      {fieldname:"bom_no",fieldtype:"Select",label:__("Resolved Compatible BOM"),options:"",reqd:1},{fieldname:"planned_start_date",fieldtype:"Datetime",label:__("Planned Production Start"),default:frappe.datetime.now_datetime(),reqd:1}
    ],primary_action_label:__("Create Draft Work Order"),primary_action:(values)=>{
      if(Number(values.qty)<=0||Number(values.qty)>Number(context.remaining_qty)){frappe.msgprint(__("Quantity must be greater than zero and cannot exceed Remaining Qty."));return;}
      dialog.disable_primary_action();frappe.call({method:"calco_erp.calco_production.page.production_planning_center.production_planning_center.create_work_order_from_planning_center",args:{production_requirement:context.production_requirement,qty:values.qty,production_line:values.production_line,bom_no:values.bom_no,planned_start_date:values.planned_start_date},freeze:true,freeze_message:__("Submitting internal Production Plan and creating Draft Work Order...")}).then((result)=>{dialog.hide();const message=result.message||{};if(message.route)frappe.set_route(...message.route);}).finally(()=>dialog.enable_primary_action());
    }});
    const refreshBom=()=>{const line=dialog.get_value("production_line");const options=(context.bom_options||[]).filter((row)=>row.production_line===line).map((row)=>row.bom_no);const field=dialog.get_field("bom_no");field.df.options=["",...options].join("\n");field.refresh();dialog.set_value("bom_no",options.length===1?options[0]:"");};
    dialog.get_field("production_line").df.onchange=refreshBom;dialog.show();refreshBom();
  }

  renderMeta(){this.page.main.find(".calco-ppc__meta").first().text(`${this.data.from_date||""} to ${this.data.to_date||""} | ${this.rows.length} consolidated FG requirement(s) | ${this.data.backend_time_ms||0} ms`);this.page.main.find(".ppc-queue-meta").text(`${this.queue.length} released requirement(s)`);}
  openRoute(data){if(data.doctype&&data.name)frappe.set_route("Form",data.doctype,data.name);} qty(value){return format_number(Number(value||0),null,3);} esc(value){return frappe.utils.escape_html(String(value??""));}
}
