(() => {
  const api="calco_erp.calco_production.fg_true_up_draft";
  const call=(method,args)=>frappe.call({method:`${api}.${method}`,args,freeze:true}).then(r=>r.message);
  const esc=v=>frappe.utils.escape_html(String(v??""));
  async function reconcile(frm){
    if(frm.is_dirty()){frappe.msgprint(__("Save/reload the Job Card first."));return;}
    const ctx=await call("context",{job_card:frm.doc.name});
    if(!ctx.lots.length){frappe.msgprint(__("No submitted partial Manufacture receipt to reconcile."));return;}
    const link=(type,name)=>name?`<a href="/app/${type}/${encodeURIComponent(name)}" target="_blank" rel="noopener">${esc(name)}</a>`:esc(__("None"));
    const source=lot=>link("stock-entry",ctx.lots.find(x=>x.lot===lot)?.stock_entry);
    const d=new frappe.ui.Dialog({title:__("Final Reconciliation — Draft Valuation Adjustment"),size:"extra-large",fields:[
      {fieldtype:"HTML",options:__("Measured quantity and valuation are separate. This action creates a Draft LCV only. Submission and native reposting require authorized review.")},
      {fieldname:"lot_name",fieldtype:"Select",label:__("Production Lot"),options:ctx.lots.map(x=>x.lot),default:ctx.lots[0].lot,reqd:1,onchange:()=>d.fields_dict.manufacture_source.$wrapper.html(`<strong>${__("Manufacture Stock Entry")}</strong>: ${source(d.get_value("lot_name"))}`)},
      {fieldname:"manufacture_source",fieldtype:"HTML",options:`<strong>${__("Manufacture Stock Entry")}</strong>: ${source(ctx.lots[0].lot)}`},
      {fieldname:"measurements",fieldtype:"Table",label:__("Final Measured Consumption"),cannot_add_rows:true,cannot_delete_rows:true,in_place_edit:true,data:ctx.quantities.map(row=>({...row,qty:"",measurement_reference:""})),description:__("Enter a measured quantity and reference for every RM row. Enter 0 explicitly for a measured zero."),fields:[
        {fieldname:"item_code",fieldtype:"Data",label:__("RM"),read_only:1,in_list_view:1},
        {fieldname:"batch_no",fieldtype:"Data",label:__("Batch"),read_only:1,in_list_view:1},
        {fieldname:"already_accounted_qty",fieldtype:"Float",label:__("Accounted Qty"),read_only:1,in_list_view:1},
        {fieldname:"qty",fieldtype:"Data",label:__("Final Measured Qty"),reqd:1,in_list_view:1},
        {fieldname:"measurement_reference",fieldtype:"Data",label:__("Measurement Reference"),reqd:1,in_list_view:1}]},
      {fieldname:"currency",fieldtype:"Data",default:ctx.currency,hidden:1,read_only:1},
      {fieldname:"target",fieldtype:"Currency",label:__("Final Required FG Lot Value (Including Conversion Cost)")+` (${ctx.currency})`,options:"currency",reqd:1},
      {fieldname:"account",fieldtype:"Link",options:"Account",label:__("Valuation Expense / Clearing Account"),default:ctx.expense_account,read_only:!ctx.can_change_account,reqd:1},
      {fieldname:"reason",fieldtype:"Small Text",label:__("Reconciliation Evidence / Reason"),reqd:1}
    ],primary_action_label:__("Calculate Reconciliation"),primary_action:async v=>{
      const entries=v.measurements||[];
      const keys=rows=>rows.map(r=>`${r.item_code}\u001f${r.batch_no}`).sort().join("\u001e");
      if(!entries.length||keys(entries)!==keys(ctx.quantities)){
        frappe.msgprint(__("Final measurements must cover every required RM row."));return;
      }
      for(const row of entries){
        const raw=row.qty==null?"":String(row.qty).trim();
        if(raw===""||!Number.isFinite(Number(raw))||Number(raw)<0){
          frappe.msgprint(__("Enter Final Measured Qty for every RM row. Enter 0 explicitly for a measured zero."));return;
        }
        if(!String(row.measurement_reference||"").trim()){
          frappe.msgprint(__("Measurement Reference is required for every measured RM row."));return;
        }
      }
      if(!String(v.reason||"").trim()){frappe.msgprint(__("Reconciliation Evidence / Reason is required."));return;}
      const result=await call("preview",{lot_name:v.lot_name,measurements:entries,target:v.target,account:v.account,reason:v.reason}),s=result.data;
      const money=value=>`${esc(value)} ${esc(result.currency||ctx.currency)}`;
      const pending=result.existing_draft;
      const rows=[["Work Order",esc(s.work_order)],["Production Lot",esc(s.lot)],
        ["Manufacture Stock Entry",link("stock-entry",s.manufacture)],["Reconciliation Version",esc(s.version)],
        ["Current FG Lot Value",money(s.valuation.current_accounted_value)],
        ["Prior Submitted LCV Adjustment",money(s.valuation.previous_value_adjustments)],
        ["Final Required FG Lot Value (Including Conversion Cost)",money(s.valuation.final_target_value)],
        ["Residual Valuation Adjustment",money(s.valuation.residual_value_delta)],
        ["Existing Draft LCV",link("landed-cost-voucher",pending?.name)]];
      const review=new frappe.ui.Dialog({title:__("Review Reconciliation"),size:"extra-large",fields:[{fieldtype:"HTML",options:
        `<table class="table table-bordered">${rows.map(([k,val])=>`<tr><th>${__(k)}</th><td style="overflow-wrap:anywhere">${val}</td></tr>`).join("")}</table>`+
        `<table class="table table-bordered"><thead><tr>${["RM / Batch","Accounted Consumption","Final Measured Consumption","Quantity Delta"].map(x=>`<th>${__(x)}</th>`).join("")}</tr></thead><tbody>${s.quantity.map(x=>`<tr><td>${esc(x.item_code)} / ${esc(x.batch_no)}</td><td>${esc(x.already_accounted_qty)}</td><td>${esc(x.final_measured_qty)}</td><td>${esc(x.quantity_delta)}</td></tr>`).join("")}</tbody></table>`+
        `<strong>${__("Blockers")}</strong>`+(result.blockers.length?result.blockers.map(x=>`<p class="text-danger">${esc(x)}</p>`).join(""):`<p>${__("None")}</p>`)+
        (pending&&!pending.same_version?`<p class="text-warning">${__("An existing Draft uses a different reconciliation version. Open it for controlled review; another Draft cannot be created.")}</p>`:"")
      }],primary_action_label:__(pending?"Open Existing Draft LCV":"Create Draft LCV"),primary_action:async()=>{
        if(pending){frappe.set_route("Form","Landed Cost Voucher",pending.name);return;}
        if(!result.token)return;review.get_primary_btn().prop("disabled",true);
        try{const created=await call("create_draft",{token:result.token});review.hide();d.hide();frappe.set_route("Form","Landed Cost Voucher",created.name);}catch(e){review.get_primary_btn().prop("disabled",false);throw e;}
      }});review.show();review.get_primary_btn().prop("disabled",!pending&&!result.token);
    }});d.show();
  }
  frappe.ui.form.on("Job Card",{refresh(frm){
    if(frm.is_new()||frm.doc.operation!=="Compounding / Extrusion"||!frm.doc.custom_shift_output_activation)return;
    if(!frappe.user_roles.some(r=>["Manufacturing Manager","Accounts Manager"].includes(r)))return;
    if(!frappe.model.can_create("Landed Cost Voucher"))return;
    frm.add_custom_button(__("Final Reconciliation"),()=>reconcile(frm),__("Advanced / Exceptions"));
  }});
})();
