frappe.provide("calco_erp.wip_consumption");

(() => {
  const PREVIEW_METHOD =
    "calco_erp.calco_production.wip_consumption.get_wip_consumption_preview";
  const CREATE_METHOD =
    "calco_erp.calco_production.wip_consumption.make_wip_consumption_stock_entry";

  function removeStandardAction(frm) {
    frm.remove_custom_button(__("Material Consumption"), __("Make"));
  }

  async function addControlledAction(frm) {
    if (frm.is_new() || frm.doc.docstatus !== 1 || !frappe.model.can_create("Stock Entry")) return;
    const response = await frappe.call({
      method: PREVIEW_METHOD,
      args: { work_order: frm.doc.name },
      silent: true,
    });
    const preview = response.message || {};
    frm.__calco_wip_consumption_preview = preview;
    if (!preview.controlled) return;
    removeStandardAction(frm);
    if (!preview.can_create) return;

    frm.add_custom_button(
      __("Record WIP Consumption"),
      () => openConsumptionDialog(frm, preview),
      __("Production")
    );
  }

  function openConsumptionDialog(frm, preview) {
    const dialog = new frappe.ui.Dialog({
      title: __("Record WIP Consumption"),
      fields: [
        {
          fieldname: "warehouse",
          label: __("Source Warehouse"),
          fieldtype: "Link",
          options: "Warehouse",
          default: preview.warehouse,
          read_only: 1,
        },
        {
          fieldname: "items",
          label: __("Transferred WIP Batches"),
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
              fieldname: "available_qty",
              label: __("Available"),
              fieldtype: "Float",
              in_list_view: 1,
              read_only: 1,
            },
            {
              fieldname: "consume_qty",
              label: __("Consume Qty"),
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
          .filter((row) => flt(row.consume_qty) > 0)
          .map((row) => ({
            item_code: row.item_code,
            batch_no: row.batch_no || "",
            qty: flt(row.consume_qty),
          }));
        if (!rows.length) {
          frappe.msgprint(__("Enter a positive consumption quantity for at least one row."));
          return;
        }
        const result = await frappe.call({
          method: CREATE_METHOD,
          args: { work_order: frm.doc.name, rows },
          freeze: true,
          freeze_message: __("Creating controlled WIP consumption"),
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
      available_qty: row.available_qty,
      consume_qty: 0,
      stock_uom: row.stock_uom,
    }));
    dialog.fields_dict.items.grid.refresh();
    dialog.show();
  }

  frappe.ui.form.on("Work Order", {
    refresh(frm) {
      setTimeout(() => {
        addControlledAction(frm).catch((error) => {
          console.error("Unable to load controlled WIP consumption", error);
        });
      }, 0);
    },
  });
})();
