function calcoEscapeForecastValue(value) {
  return frappe.utils.escape_html((value ?? "").toString());
}

function calcoRenderForecastPreview(preview) {
  const rows = (preview.rows || [])
    .map(
      (row) => `
        <tr>
          <td>${calcoEscapeForecastValue(row.row_number)}</td>
          <td>${calcoEscapeForecastValue(row.item_code)}</td>
          <td>${calcoEscapeForecastValue(row.item_name)}</td>
          <td class="text-right">${format_number(row.demand_qty, null, 3)}</td>
          <td>${calcoEscapeForecastValue(row.delivery_date)}</td>
          <td>${calcoEscapeForecastValue(row.warehouse)}</td>
        </tr>`,
    )
    .join("");

  return `
    <div class="mb-3">
      <strong>${__("Forecast Month")}:</strong> ${calcoEscapeForecastValue(preview.forecast_month)}<br>
      <strong>${__("Delivery Date")}:</strong> ${calcoEscapeForecastValue(preview.delivery_date)}<br>
      <strong>${__("Warehouse")}:</strong> ${calcoEscapeForecastValue(preview.warehouse)}<br>
      <strong>${__("Rows")}:</strong> ${calcoEscapeForecastValue(preview.row_count)}
    </div>
    <div class="table-responsive" style="max-height: 420px; overflow: auto;">
      <table class="table table-bordered table-sm">
        <thead>
          <tr>
            <th>${__("Row")}</th>
            <th>${__("Item Code")}</th>
            <th>${__("Item Name")}</th>
            <th class="text-right">${__("Demand Qty (Kg)")}</th>
            <th>${__("Delivery Date")}</th>
            <th>${__("Warehouse")}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function calcoApplyForecastImport(frm, file, preview, dialog) {
  const applyImport = () => {
    const isNew = frm.is_new();
    frappe.call({
      method: "calco_erp.calco_production.sales_forecast_import.apply_forecast_import",
      args: {
        forecast_name: isNew ? null : frm.doc.name,
        forecast_doc: isNew ? JSON.stringify(frm.doc) : null,
        file_name: file.name,
        expected_modified: isNew ? null : frm.doc.modified,
        replace_existing: preview.has_existing_rows ? 1 : 0,
      },
      freeze: true,
      freeze_message: __("Importing forecast rows..."),
      callback(r) {
        if (!r.exc) {
          dialog.hide();
          frappe.show_alert({
            message: __("Imported {0} forecast rows.", [r.message.row_count]),
            indicator: "green",
          });
          if (isNew) {
            frappe.set_route("Form", "Sales Forecast", r.message.name);
          } else {
            frm.reload_doc();
          }
        }
      },
    });
  };

  if (preview.has_existing_rows) {
    frappe.confirm(
      __("Replace all existing forecast rows with the validated upload?"),
      applyImport,
    );
    return;
  }
  applyImport();
}

function calcoShowForecastPreview(frm, file, preview) {
  const dialog = new frappe.ui.Dialog({
    title: __("Forecast Import Preview"),
    size: "extra-large",
    fields: [
      {
        fieldname: "preview_html",
        fieldtype: "HTML",
        options: calcoRenderForecastPreview(preview),
      },
    ],
    primary_action_label: preview.has_existing_rows
      ? __("Replace Existing Rows")
      : __("Import Rows"),
    primary_action() {
      calcoApplyForecastImport(frm, file, preview, dialog);
    },
    secondary_action_label: __("Cancel"),
    secondary_action() {
      dialog.hide();
    },
  });
  dialog.show();
}

function calcoPreviewForecastFile(frm, file) {
  const isNew = frm.is_new();
  frappe.call({
    method: "calco_erp.calco_production.sales_forecast_import.preview_forecast_import",
    args: {
      forecast_name: isNew ? null : frm.doc.name,
      forecast_doc: isNew ? JSON.stringify(frm.doc) : null,
      file_name: file.name,
    },
    freeze: true,
    freeze_message: __("Validating forecast file..."),
    callback(r) {
      if (!r.exc) {
        calcoShowForecastPreview(frm, file, r.message);
      }
    },
  });
}

function calcoOpenForecastUploader(frm) {
  const uploadOptions = {
    folder: "Home/Attachments",
    allow_multiple: false,
    make_attachments_public: false,
    restrictions: {
      allowed_file_types: [".xlsx", ".csv"],
    },
    on_success(file) {
      if (!frm.is_new() && frm.attachments) {
        frm.attachments.attachment_uploaded(file);
      }
      calcoPreviewForecastFile(frm, file);
    },
  };
  if (!frm.is_new()) {
    Object.assign(uploadOptions, {
      doctype: frm.doctype,
      docname: frm.doc.name,
      frm,
    });
  }
  new frappe.ui.FileUploader(uploadOptions);
}

frappe.ui.form.on("Sales Forecast", {
  refresh(frm) {
    const canImport = frm.is_new() ? frm.has_perm("create") : frm.has_perm("write");
    if (frm.doc.docstatus === 0 && canImport) {
      frm.add_custom_button(
        __("Import Forecast Excel"),
        () => calcoOpenForecastUploader(frm),
        __("Import"),
      );
    }
  },
});

// Replace the core handler so its reload callback cannot discard returned rows.
frappe.ui.form.off("Sales Forecast", "generate_demand");
frappe.ui.form.on("Sales Forecast", {
  generate_demand(frm) {
    return frm.call({method: "generate_demand", doc: frm.doc, freeze: true, callback(r) {
      if (!r.exc) { frm.refresh_field("items"); frm.dirty(); }
    }});
  },
  refresh(frm) {
    const legacy = (frm.doc.items || []).some(row => !row.custom_forecast_period_start);
    if (legacy) frm.dashboard.set_headline_alert(
      __("Legacy forecast rows have no period identity. Submitted rows retain their Delivery Date month. Regenerate or import Draft rows before submission."), "orange");
  }
});
