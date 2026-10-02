frappe.provide("calco_erp.wip_return");

(() => {
  const PREVIEW_METHOD =
    "calco_erp.calco_production.wip_return.get_wip_return_preview";
  const CREATE_METHOD =
    "calco_erp.calco_production.wip_return.make_unused_wip_return_stock_entry";

  async function addReturnAction(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 1 || !frappe.model.can_create("Stock Entry")) return;
    const response = await frappe.call({
      method: PREVIEW_METHOD,
      args: { work_order: frm.doc.name },
      silent: true,
    });
    const preview = response.message || {};
    frm.__calco_wip_return_preview = preview;
    if (!preview.controlled) return;

    frm.add_custom_button(
      __("RM Reconciliation"),
      () => openReconciliationDialog(preview),
      __("Production")
    );
    if (!preview.can_create) return;

    frm.add_custom_button(
      __("Return Unused Material"),
      () => openReturnDialog(frm, preview),
      __("Production")
    );
  }

  function openReconciliationDialog(preview) {
    const dialog = new frappe.ui.Dialog({
      title: __("RM Reconciliation"),
      fields: [
        {
          fieldname: "items",
          fieldtype: "Table",
          cannot_add_rows: true,
          cannot_delete_rows: true,
          in_place_edit: false,
          fields: [
            { fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item", in_list_view: 1, read_only: 1 },
            { fieldname: "batch_no", label: __("Batch"), fieldtype: "Link", options: "Batch", in_list_view: 1, read_only: 1 },
            { fieldname: "transferred_qty", label: __("Transferred"), fieldtype: "Float", in_list_view: 1, read_only: 1 },
            { fieldname: "actual_consumed_qty", label: __("Actual Consumed"), fieldtype: "Float", in_list_view: 1, read_only: 1 },
            { fieldname: "returned_qty", label: __("Returned"), fieldtype: "Float", in_list_view: 1, read_only: 1 },
            { fieldname: "remaining_wip_qty", label: __("Remaining WIP"), fieldtype: "Float", in_list_view: 1, read_only: 1 },
          ],
        },
      ],
      size: "extra-large",
    });
    dialog.fields_dict.items.df.data = preview.reconciliation || [];
    dialog.fields_dict.items.grid.refresh();
    dialog.show();
  }

  function openReturnDialog(frm, preview) {
    const dialog = new frappe.ui.Dialog({
      title: __("Return Unused Material"),
      fields: [
        {
          fieldname: "from_warehouse",
          label: __("From Warehouse"),
          fieldtype: "Link",
          options: "Warehouse",
          default: preview.source_warehouse,
          read_only: 1,
        },
        {
          fieldname: "to_warehouse",
          label: __("To Warehouse"),
          fieldtype: "Link",
          options: "Warehouse",
          default: preview.target_warehouse,
          read_only: 1,
        },
        {
          fieldname: "items",
          label: __("Unused WIP Batches"),
          fieldtype: "Table",
          cannot_add_rows: true,
          cannot_delete_rows: true,
          in_place_edit: true,
          fields: [
            {
              fieldname: "item_code",
              label: __("Item"),
              fieldtype: "Link",
              options: "Item",
              in_list_view: 1,
              read_only: 1,
            },
            {
              fieldname: "batch_no",
              label: __("Batch"),
              fieldtype: "Link",
              options: "Batch",
              in_list_view: 1,
              read_only: 1,
            },
            {
              fieldname: "actual_consumed_qty",
              label: __("Consumed"),
              fieldtype: "Float",
              in_list_view: 1,
              read_only: 1,
            },
            {
              fieldname: "returnable_qty",
              label: __("Returnable"),
              fieldtype: "Float",
              in_list_view: 1,
              read_only: 1,
            },
            {
              fieldname: "return_qty",
              label: __("Return Qty"),
              fieldtype: "Float",
              in_list_view: 1,
            },
            {
              fieldname: "stock_uom",
              label: __("UOM"),
              fieldtype: "Link",
              options: "UOM",
              in_list_view: 1,
              read_only: 1,
            },
          ],
        },
      ],
      primary_action_label: __("Create Draft Stock Entry"),
      async primary_action(values) {
        const rows = (values.items || [])
          .filter((row) => flt(row.return_qty) > 0)
          .map((row) => ({
            item_code: row.item_code,
            batch_no: row.batch_no || "",
            qty: flt(row.return_qty),
          }));
        if (!rows.length) {
          frappe.msgprint(__("Enter a positive return quantity for at least one row."));
          return;
        }
        const result = await frappe.call({
          method: CREATE_METHOD,
          args: { work_order: frm.doc.name, rows },
          freeze: true,
          freeze_message: __("Creating unused WIP return"),
        });
        dialog.hide();
        const docs = frappe.model.sync(result.message);
        const stockEntry = docs[0];
        frappe.set_route("Form", stockEntry.doctype, stockEntry.name);
      },
      size: "extra-large",
    });
    dialog.fields_dict.items.df.data = (preview.rows || []).map((row) => ({
      item_code: row.item_code,
      batch_no: row.batch_no,
      actual_consumed_qty: row.actual_consumed_qty,
      returnable_qty: row.returnable_qty,
      return_qty: 0,
      stock_uom: row.stock_uom,
    }));
    dialog.fields_dict.items.grid.refresh();
    dialog.show();
  }

  frappe.ui.form.on("Work Order", {
    refresh(frm) {
      setTimeout(() => {
        addReturnAction(frm).catch((error) => {
          console.error("Unable to load unused WIP return", error);
        });
      }, 0);
    },
  });
})();
