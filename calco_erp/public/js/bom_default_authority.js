// New documents only: historical blank policy is not reclassified on open.
frappe.ui.form.on("BOM", {
    onload(frm) {
        if (frm.is_new() && !frm.doc.custom_default_authority_policy) {
            frm.set_value("custom_default_authority_policy", "Automatic Default");
        }
    },
    refresh(frm) {
        const submitted = frm.doc.docstatus !== 0;
        for (const field of ["custom_default_authority_policy", "custom_default_authority_reason"]) {
            frm.set_df_property(field, "read_only", submitted ? 1 : 0);
        }
        frm.toggle_reqd("custom_default_authority_reason", frm.doc.custom_default_authority_policy === "Keep Non-Default");
    },
    custom_default_authority_policy(frm) {
        const alternate = frm.doc.custom_default_authority_policy === "Keep Non-Default";
        frm.toggle_reqd("custom_default_authority_reason", alternate);
        if (frm.is_new() || frm.doc.docstatus === 0) {
            if (alternate) frm.set_value("is_default", 0);
        }
    }
});

// Calco percentage view: presentation-only for submitted BOMs. Native items stay intact.
(() => {
    const method = "calco_erp.planning_upgrade.formulation.presentation";
    const esc = v => frappe.utils.escape_html(String(v ?? ""));
    const pct = v => { const s = String(v ?? "0"); const [a,b=""] = s.split("."); return a+"."+b.padEnd(4,"0")+"%"; };
    async function render(frm) {
        const field = frm.fields_dict.custom_formulation_view;
        if (!field || !frm.doc.company) return;
        const generation = frm.__calco_formulation_generation = (frm.__calco_formulation_generation || 0) + 1;
        const {message:d} = await frappe.call({method, args:{document:JSON.stringify(frm.doc)}});
        if (generation !== frm.__calco_formulation_generation || !d) return;
        const edit = frm.doc.docstatus === 0 && frm.doc.custom_percentage_formulation;
        const html = `<div class="calco-formulation"><h4>${__("Formulation and Packing")}</h4>
            <p>${__("One BOM. Packing is Extra and retains its native Qty/UOM.")}</p>
            ${!d.complete ? '<div class="alert alert-warning">'+__("Some Items need RM Category review. The displayed total is incomplete; no category has been guessed.")+'</div>' : ''}
            <table class="table table-bordered"><thead><tr><th>RM Category</th><th>RM Code</th><th>Percentage</th></tr></thead><tbody>
            ${d.rows.map(r=>`<tr><td>${esc(r.category)}</td><td>${esc(r.item_code)}</td><td>${r.role==='Packing' ? '<strong>Extra</strong>' : r.role==='Unclassified' ? 'Review required' : edit ? `<input class="form-control calco-percent" aria-label="Percentage ${esc(r.item_code)}" data-row="${esc(r.name)}" type="text" inputmode="decimal" value="${esc(r.percent)}">` : esc(pct(r.percent))}</td></tr>`).join('')}
            </tbody></table><strong>Formulation Total: ${esc(pct(d.total))}</strong>
            <p class="text-muted">${__("Packing excluded. Executable basis:")} ${esc(d.basis)} Kg</p>
            <p>${__("Formulation RMC / Kg")}: ${esc(d.currency)} ${esc(d.formulation_cost_per_kg)} · ${__("Packing Cost / BOM Batch")}: ${esc(d.currency)} ${esc(d.packing_cost)}<br>
            ${__("Total Material Cost / BOM Batch")}: ${esc(d.currency)} ${esc(d.total_material_cost)}${Number(d.unclassified_cost) ? ' · Unclassified cost: '+esc(d.unclassified_cost) : ''}</p>
            <p class="text-muted">${__("Management breakdown of native base amounts. Unclassified costs are not guessed. Native BOM cost and accounting remain unchanged.")}</p>
            ${frm.doc.docstatus===0 && !frm.doc.custom_percentage_formulation?'<button type="button" class="btn btn-default calco-enable-percentage">Use Percentage Formulation</button> ':''}${edit?'<button type="button" class="btn btn-default calco-add-material">Add Material</button> ':''}<button type="button" class="btn btn-default calco-native-materials">Show Native Qty / UOM / Costs</button></div>`;
        field.$wrapper.html(html);
        // Move view before native grid without changing document values or child idx.
        field.$wrapper.insertBefore(frm.fields_dict.items.$wrapper);
        frm.toggle_display('items', Boolean(frm.__calco_native_materials));
        field.$wrapper.find('.calco-native-materials').on('click',()=>{frm.__calco_native_materials=!frm.__calco_native_materials;frm.toggle_display('items',frm.__calco_native_materials);});
        field.$wrapper.find('.calco-add-material').on('click',()=>add(frm));
        field.$wrapper.find('.calco-enable-percentage').on('click',async()=>{
            if(Number(frm.doc.quantity)!==100 || frm.doc.uom!=='Kg'){
                frappe.msgprint(__('Percentage editing requires a separately reviewed 100 Kg basis. This action never rescales native quantities.'));return;
            }
            if(!d.complete){frappe.msgprint(__('Resolve missing RM Categories first.'));return;}
            for(const r of d.rows){
                if(r.role==='Formulation') await frappe.model.set_value('BOM Item',r.name,'custom_formulation_percent',r.percent);
            }
            await frm.set_value('custom_percentage_formulation',1);
        });
        field.$wrapper.find('.calco-percent').on('change',async function(){
            const row=locals['BOM Item'][this.dataset.row];
            await frappe.model.set_value(row.doctype,row.name,'custom_formulation_percent',this.value);
            await frappe.model.set_value(row.doctype,row.name,'qty',this.value);
            await render(frm);
        });
    }
    function add(frm) {
        let cat='';
        const dialog = new frappe.ui.Dialog({title:__('Add BOM Material'), fields:[
            {fieldname:'item',label:'RM Code',fieldtype:'Link',options:'Item',reqd:1,onchange:async()=>{
                const item=dialog.get_value('item');if(!item)return;
                const {message:r}=await frappe.db.get_value('Item',item,['custom_rm_category','stock_uom']);
                cat=r.custom_rm_category||'';dialog.set_value('category',cat||'Classification required');
                dialog.set_df_property('percent','hidden',cat==='Packing');dialog.set_df_property('percent','reqd',cat!=='Packing');
                for(const f of ['qty','uom']){dialog.set_df_property(f,'hidden',cat!=='Packing');dialog.set_df_property(f,'reqd',cat==='Packing');}
                dialog.set_value('uom',r.stock_uom);
            }},
            {fieldname:'category',label:'RM Category',fieldtype:'Data',read_only:1},
            {fieldname:'percent',label:'Percentage',fieldtype:'Float',precision:9},
            {fieldname:'qty',label:'Packing Qty / BOM Batch',fieldtype:'Float',precision:9,hidden:1},
            {fieldname:'uom',label:'Packing UOM',fieldtype:'Link',options:'UOM',hidden:1}
        ],primary_action_label:__('Add Material'),primary_action:async values=>{
            if(!cat){frappe.msgprint(__('RM Category requires master review.'));return;}
            const row=frm.add_child('items');
            await frappe.model.set_value(row.doctype,row.name,'item_code',values.item);
            if(cat==='Packing'){
                await frappe.model.set_value(row.doctype,row.name,'uom',values.uom);
                await frappe.model.set_value(row.doctype,row.name,'qty',values.qty);
            }else{
                await frappe.model.set_value(row.doctype,row.name,'custom_formulation_percent',values.percent);
                await frappe.model.set_value(row.doctype,row.name,'qty',values.percent);
            }
            frm.refresh_field('items');dialog.hide();await render(frm);
        }});dialog.show();
    }
    frappe.ui.form.on('BOM',{
        onload(frm){
            if(frm.is_new() && !(frm.doc.items||[]).length && !frm.doc.custom_percentage_formulation){
                frm.set_value('custom_percentage_formulation',1);frm.set_value('quantity',100);
            }
        },
        refresh:render,
        after_save:render,
        custom_percentage_formulation:render,
        quantity:render
    });
})();
