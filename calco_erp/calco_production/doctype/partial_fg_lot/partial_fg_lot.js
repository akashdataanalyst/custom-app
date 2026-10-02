frappe.ui.form.on("Partial FG Lot",{refresh(frm){
  if(frm.doc.docstatus!==1)return;
  const api="calco_erp.calco_production.partial_fg_lots";
  if(frappe.model.can_create("Stock Entry"))frm.add_custom_button(__("Open / Create Manufacture"),()=>frappe.prompt([
    {fieldname:"operator",fieldtype:"Link",options:"Employee",label:__("Operator"),reqd:1},
    {fieldname:"shift",fieldtype:"Link",options:"Shift Type",label:__("Shift"),reqd:1}
  ],v=>frappe.call({method:`${api}.make_manufacture`,args:{name:frm.doc.name,...v},freeze:true}).then(r=>frappe.set_route("Form","Stock Entry",r.message.name)),__("Partial Manufacture")));
  if(frappe.model.can_create("Quality Inspection"))frm.add_custom_button(__("Open / Create Lot Final QI"),()=>frappe.call({method:`${api}.make_quality_inspection`,args:{name:frm.doc.name},freeze:true}).then(r=>frappe.set_route("Form","Quality Inspection",r.message.name)));
}});
