(function registerProductionConsumptionEntryScript() {
  if (!(window.frappe && frappe.ui && frappe.ui.form && typeof frappe.ui.form.on === "function")) {
    window.setTimeout(registerProductionConsumptionEntryScript, 500);
    return;
  }

  const scriptVersion = "20260914_pce_final_confirmation";
  if (window.__productionConsumptionEntryRecoveryRegistered === scriptVersion) {
    if (window.cur_frm && cur_frm.doctype === "Production Consumption Entry") {
      setupProductionConsumptionForm(cur_frm);
    }
    return;
  }
  window.__productionConsumptionEntryRecoveryRegistered = scriptVersion;
function toFloat(value) {
  const number = Number.parseFloat(value);
  return Number.isFinite(number) ? number : 0;
}

function getBatchStore(frm) {
  frm._pce_available_batches = frm._pce_available_batches || {};
  return frm._pce_available_batches;
}

function escapeHtml(value) {
  if (frappe.utils && frappe.utils.escape_html) {
    return frappe.utils.escape_html(String(value || ""));
  }

  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderBatchTable(dialog, batches, selectedBatchNo) {
  const rows = (batches || [])
    .map((batch) => {
      const checked = batch.batch_no === selectedBatchNo ? "checked" : "";
      return `
        <tr>
          <td style="width: 40px; text-align: center;">
            <input type="radio" name="pce_batch_choice" class="pce-batch-choice" value="${escapeHtml(batch.batch_no)}" ${checked}>
          </td>
          <td>${escapeHtml(batch.batch_no)}</td>
          <td style="text-align: right;">${toFloat(batch.available_qty).toFixed(3)}</td>
          <td>${escapeHtml(batch.batch_date || batch.fifo_key || "")}</td>
        </tr>`;
    })
    .join("");

  const html = rows
    ? `<div class="pce-batch-picker" style="max-height: 260px; overflow: auto; border: 1px solid var(--border-color); border-radius: 6px;">
        <table class="table table-bordered table-sm" style="margin: 0;">
          <thead>
            <tr>
              <th></th>
              <th>Batch No</th>
              <th style="text-align: right;">Available Qty</th>
              <th>FIFO Date</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`
    : `<div class="text-muted">No positive-stock batches found for this RM in the selected warehouse.</div>`;

  dialog.fields_dict.batch_html.$wrapper.html(html);
}

function setLoadBatchesEnabled(dialog, enabled) {
  const button = dialog.fields_dict.load_batches && dialog.fields_dict.load_batches.$input;
  if (!button) return;

  button.prop("disabled", !enabled);
  button.toggleClass("disabled", !enabled);
}

async function validateDialogRmCode(dialog, showMessage = false) {
  const itemCode = String(dialog.get_value("rm_code") || "").trim();
  dialog.rm_code_is_valid = false;
  setLoadBatchesEnabled(dialog, false);

  if (!itemCode) {
    return false;
  }

  const response = await frappe.db.get_value(
    "Item",
    {
      name: itemCode,
      item_group: "Raw Material",
      disabled: 0,
      is_stock_item: 1,
    },
    "name"
  );
  const isValid = !!(response && response.message && response.message.name);
  dialog.rm_code_is_valid = isValid;
  setLoadBatchesEnabled(dialog, isValid);

  if (!isValid) {
    renderBatchTable(dialog, [], null);
    if (showMessage) {
      frappe.msgprint("Please select a valid active stock Raw Material item.");
    }
  }

  return isValid;
}
async function loadDialogBatches(frm, dialog) {
  const itemCode = String(dialog.get_value("rm_code") || "").trim();
  dialog.selected_batch = null;
  dialog.set_value("rm_qty_consumed", null);

  if (!(await validateDialogRmCode(dialog, true))) {
    return;
  }

  if (!frm.doc.warehouse) {
    frappe.msgprint("Please select Warehouse before adding RM rows.");
    renderBatchTable(dialog, [], null);
    return;
  }

  if (!itemCode) {
    renderBatchTable(dialog, [], null);
    return;
  }

  const response = await frappe.call({
    method:
      "calco_erp.calco_production.doctype.production_consumption_entry.production_consumption_entry.get_available_rm_batches_for_consumption",
    args: {
      item_code: itemCode,
      warehouse: frm.doc.warehouse,
    },
  });

  dialog.available_batches = response.message || [];
  renderBatchTable(dialog, dialog.available_batches, null);
}

function removeEmptyRmRows(frm) {
  const items = frm.doc.items || [];
  const keep = [];

  items.forEach((row) => {
    const isEmpty =
      !row.rm_code &&
      !row.rm_batch_no &&
      !row.category &&
      !row.challan_invoice_no &&
      !row.remarks &&
      toFloat(row.available_batch_qty) === 0 &&
      toFloat(row.rm_qty_consumed) === 0;

    if (isEmpty) {
      frappe.model.clear_doc(row.doctype, row.name);
    } else {
      keep.push(row);
    }
  });

  frm.doc.items = keep;
}
function addRmConsumptionRow(frm, values, selectedBatch) {
  values = values || {};
  const selectedRmCode = String(values.rm_code || "").trim();
  const selectedBatchNo = selectedBatch && selectedBatch.batch_no;
  const selectedAvailableQty = selectedBatch ? toFloat(selectedBatch.available_qty) : 0;
  const consumedQty = toFloat(values.rm_qty_consumed);
  const category = values.category || "";
  const challanInvoiceNo = values.challan_invoice_no || "";
  const remarks = values.remarks || "";

  if (!selectedRmCode) {
    frappe.throw("RM Code is required.");
  }

  if (!selectedBatchNo) {
    frappe.throw("Please select an available batch.");
  }

  if (consumedQty <= 0) {
    frappe.throw("Consumed Qty must be greater than zero.");
  }

  if (consumedQty > selectedAvailableQty + 1e-9) {
    frappe.throw(`Consumed Qty cannot exceed available qty ${selectedAvailableQty.toFixed(3)}.`);
  }

  const duplicate = (frm.doc.items || []).some(
    (row) => row.rm_code === selectedRmCode && row.rm_batch_no === selectedBatchNo
  );
  if (duplicate) {
    frappe.throw(`Duplicate RM + Batch row is not allowed: ${selectedRmCode} / ${selectedBatchNo}`);
  }

  removeEmptyRmRows(frm);

  let child = frm.add_child("items");
  frappe.model.set_value(child.doctype, child.name, "rm_code", selectedRmCode);
  frappe.model.set_value(child.doctype, child.name, "rm_batch_no", selectedBatchNo);
  frappe.model.set_value(child.doctype, child.name, "available_batch_qty", selectedAvailableQty);
  frappe.model.set_value(child.doctype, child.name, "rm_qty_consumed", consumedQty);
  frappe.model.set_value(child.doctype, child.name, "category", category || "");
  frappe.model.set_value(child.doctype, child.name, "challan_invoice_no", challanInvoiceNo || "");
  frappe.model.set_value(child.doctype, child.name, "remarks", remarks || "");
  frm.refresh_field("items");
  frm.dirty();
}

async function submitRmConsumptionDialog(frm, dialog, values) {
  values = values || {};
  values.rm_code = String(values.rm_code || dialog.get_value("rm_code") || "").trim();
  values.rm_qty_consumed = dialog.get_value("rm_qty_consumed") || dialog.fields_dict.rm_qty_consumed.$input.val();

  if (!(await validateDialogRmCode(dialog, true))) {
    return;
  }

  if (!dialog.selected_batch) {
    const checkedBatchNo = dialog.$wrapper.find(".pce-batch-choice:checked").val();
    if (checkedBatchNo) {
      dialog.selected_batch = (dialog.available_batches || []).find((batch) => batch.batch_no === checkedBatchNo);
    }
  }

  addRmConsumptionRow(frm, values, dialog.selected_batch);
  dialog.hide();
  frappe.show_alert({ message: "RM row added.", indicator: "green" });
}

function showAddRmConsumptionDialog(frm) {
  const dialog = new frappe.ui.Dialog({
    title: "Add RM Consumption Row",
    fields: [
      {
        fieldname: "rm_code",
        label: "RM Code",
        fieldtype: "Link",
        options: "Item",
        only_select: true,
        reqd: 1,
        get_query() {
          return {
            filters: {
              item_group: "Raw Material",
              disabled: 0,
              is_stock_item: 1,
            },
          };
        },
        onchange: () => {
          dialog.selected_batch = null;
          dialog.available_batches = [];
          dialog.set_value("rm_qty_consumed", null);
          renderBatchTable(dialog, [], null);
          validateDialogRmCode(dialog);
        },
      },
      {
        fieldname: "load_batches",
        label: "Load Batches",
        fieldtype: "Button",
      },
      {
        fieldname: "batch_html",
        label: "Available Batches",
        fieldtype: "HTML",
      },
      {
        fieldname: "rm_qty_consumed",
        label: "Consumed Qty",
        fieldtype: "Float",
        precision: 3,
        reqd: 1,
      },
    ],
    primary_action_label: "Add Row",
    primary_action(values) {
      submitRmConsumptionDialog(frm, dialog, values);
    },
  });

  dialog.available_batches = [];
  dialog.selected_batch = null;
  dialog.rm_code_is_valid = false;
  renderBatchTable(dialog, [], null);
  setLoadBatchesEnabled(dialog, false);

  dialog.fields_dict.load_batches.$input.on("click", () => loadDialogBatches(frm, dialog));



  dialog.show();
}

function validateConsumedQty(frm, cdt, cdn) {
  if (frm.doc.consumption_mode === "Final Confirmation") return;
  const row = locals[cdt][cdn] || {};
  const availableQty = toFloat(row.available_batch_qty);
  const consumedQty = toFloat(row.rm_qty_consumed);

  if (availableQty > 0 && consumedQty > availableQty + 1e-9) {
    frappe.model.set_value(cdt, cdn, "rm_qty_consumed", availableQty);
    frappe.throw(
      `RM Qty Consumed cannot exceed available batch qty ${availableQty.toFixed(3)} for batch ${row.rm_batch_no || ""}.`
    );
  }
}

function setupProductionConsumptionForm(frm) {
  if (frm.doc.consumption_mode === "Final Confirmation") {
    frm.set_df_property("company","hidden",0);
    frm.set_intro(__("Enter only additional actual consumption since the previously confirmed actual quantity. Enter 0 explicitly when nothing more was consumed. Provisional relief is not measured actual."));
    ["company","work_order","job_card","production_line","fg_code","fg_batch_no","warehouse","posting_datetime"].forEach(f => frm.set_df_property(f,"read_only",1));
    const grid = frm.fields_dict.items.grid;
    grid.cannot_add_rows = true;
    grid.cannot_delete_rows = true;
    ["rm_code","rm_batch_no"].forEach(f => grid.update_docfield_property(f,"read_only",1));
    ["rm_qty_consumed","available_batch_qty","category","challan_invoice_no"].forEach(f => grid.update_docfield_property(f,"hidden",1));
    grid.update_docfield_property("rm_qty_consumed","reqd",0);
    ["rm_code","rm_batch_no","additional_actual_qty"].forEach(f => grid.update_docfield_property(f,"in_list_view",1));
    ["issued_qty","prior_actual_qty","provisional_qty","remaining_wip_qty","final_actual_qty"].forEach(f => grid.update_docfield_property(f,"in_list_view",0));
    frm.set_df_property("correction_reason","reqd",Boolean(frm.doc.supersedes));
    if (frm.doc.docstatus === 1 && frappe.user_roles.some(r=>["Production Head","Manufacturing Manager"].includes(r))) {
      frm.add_custom_button(__("Correct Final Consumption"),async()=>{
        const result=await frappe.call({method:"calco_erp.calco_production.final_consumption.open_entry",args:{final_release:frm.doc.final_release,supersedes:frm.doc.name},freeze:true});
        frappe.set_route("Form","Production Consumption Entry",result.message);
      });
    }
    return;
  }
  // Frappe reuses the form/grid across documents. Restore legacy properties
  // when navigating from a Final Confirmation back to a historical PCE.
  frm.set_intro(null);
  frm.set_df_property("company","hidden",1);
  ["warehouse","production_line","fg_code","fg_batch_no","posting_datetime"].forEach(f=>frm.set_df_property(f,"read_only",0));
  const legacyGrid=frm.fields_dict.items && frm.fields_dict.items.grid;
  if(legacyGrid){
    legacyGrid.cannot_delete_rows=false;
    legacyGrid.update_docfield_property("rm_code","read_only",0);
    ["rm_qty_consumed","available_batch_qty","category","challan_invoice_no"].forEach(f=>legacyGrid.update_docfield_property(f,"hidden",0));
    legacyGrid.update_docfield_property("rm_qty_consumed","reqd",1);
    legacyGrid.update_docfield_property("additional_actual_qty","in_list_view",0);
  }
  if (frm.doc.docstatus === 0) {
    frm.add_custom_button("Add RM Consumption Row", () => showAddRmConsumptionDialog(frm)).addClass("btn-primary");
  }

  const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
  if (grid) {
    grid.cannot_add_rows = true;
    if (grid.wrapper && typeof grid.wrapper.find === "function") {
      grid.wrapper.find(".grid-add-row, .grid-add-multiple-rows").hide();
    }
  }
}

frappe.ui.form.on("Production Consumption Entry", {
  before_submit(frm) {
    if (frm.doc.consumption_mode !== "Final Confirmation") return;
    if (!frm.doc.final_confirmation || (frm.doc.items||[]).some(r=>r.additional_actual_qty==null || String(r.additional_actual_qty).trim()==="")) {
      frappe.throw(__("Enter Consumed Qty for every RM batch, including explicit 0, and confirm the final measurements."));
    }
  },
  refresh(frm) {
    setupProductionConsumptionForm(frm);
  },
});

if (window.cur_frm && cur_frm.doctype === "Production Consumption Entry") {
  setupProductionConsumptionForm(cur_frm);
}

frappe.ui.form.on("Production Consumption RM Item", {
  rm_qty_consumed(frm, cdt, cdn) {
    validateConsumedQty(frm, cdt, cdn);
  },
});
})();
