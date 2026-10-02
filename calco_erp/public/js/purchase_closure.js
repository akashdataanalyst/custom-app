(function () {
  "use strict";

  const METHOD_BY_DOCTYPE = {
    "Material Request": "erpnext.stock.doctype.material_request.material_request.update_status",
    "Purchase Order": "erpnext.buying.doctype.purchase_order.purchase_order.update_status",
  };

  function getOpenRows(frm) {
    return (frm.doc.items || [])
      .map((row) => {
        const requested = flt(row.stock_qty || row.qty);
        const fulfilled = frm.doctype === "Material Request" ? flt(row.ordered_qty) : flt(row.received_qty);
        return {
          item_code: row.item_code,
          requested,
          fulfilled,
          remaining: Math.max(requested - fulfilled, 0),
          uom: row.stock_uom || row.uom || "",
        };
      })
      .filter((row) => row.remaining > 0.000000001);
  }

  function rowsHtml(rows) {
    return `
      <div class="alert alert-warning" style="margin-bottom: 12px;">
        ${__("This action permanently cancels the remaining quantity for ALL open items in this document.")}
      </div>
      <div style="max-height: 240px; overflow:auto;">
        <table class="table table-bordered table-condensed">
          <thead><tr>
            <th>${__("Item")}</th>
            <th class="text-right">${__("Requested / Ordered")}</th>
            <th class="text-right">${__("Fulfilled")}</th>
            <th class="text-right">${__("Remaining")}</th>
          </tr></thead>
          <tbody>${rows.map((row) => `
            <tr>
              <td>${frappe.utils.escape_html(row.item_code || "")}</td>
              <td class="text-right">${format_number(row.requested)} ${frappe.utils.escape_html(row.uom)}</td>
              <td class="text-right">${format_number(row.fulfilled)} ${frappe.utils.escape_html(row.uom)}</td>
              <td class="text-right"><strong>${format_number(row.remaining)} ${frappe.utils.escape_html(row.uom)}</strong></td>
            </tr>`).join("")}</tbody>
        </table>
      </div>`;
  }

  function noOpenBalanceHtml(frm) {
    const message = frm.doctype === "Material Request"
      ? __("This Material Request is fully ordered. Stopping it permanently closes the procurement demand but does not close any remaining Purchase Order receipt balance.")
      : __("No unreceived Purchase Order balance remains. Closing will still record a permanent business closure with Cancelled Qty 0.");
    return `
      <div class="alert alert-warning" style="margin-bottom: 12px;">
        ${message}
      </div>`;
  }

  function requestClosure(frm, targetStatus) {
    const rows = getOpenRows(frm);

    const dialog = new frappe.ui.Dialog({
      title: frm.doctype === "Material Request" ? __("Permanently Stop Material Request") : __("Permanently Close Purchase Order"),
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "warning",
          options: rows.length ? rowsHtml(rows) : noOpenBalanceHtml(frm),
        },
        { fieldtype: "Small Text", fieldname: "closure_reason", label: __("Closure Reason"), reqd: 1 },
      ],
      primary_action_label: frm.doctype === "Material Request" ? __("Stop") : __("Close"),
      primary_action(values) {
        dialog.hide();
        callStandardStatus(frm, targetStatus, values.closure_reason);
      },
    });
    dialog.show();
  }

  function interceptMaterialRequestStatus(frm) {
    if (frm.__calco_closure_status_intercepted || !frm.events.update_status) {
      return;
    }

    const standardUpdateStatus = frm.events.update_status;
    frm.events.update_status = function (currentFrm, status) {
      const targetFrm = currentFrm || frm;
      if (status === "Stopped") {
        return requestClosure(targetFrm, status);
      }
      return standardUpdateStatus.call(this, targetFrm, status);
    };
    frm.__calco_closure_status_intercepted = true;
  }

  function callStandardStatus(frm, status, closureReason) {
    return frappe.call({
      method: METHOD_BY_DOCTYPE[frm.doctype],
      args: { name: frm.doc.name, status, closure_reason: closureReason },
      freeze: true,
      freeze_message: status === "Stopped" ? __("Stopping Material Request...") : __("Closing Purchase Order..."),
      callback(response) {
        if (!response.exc) {
          frm.reload_doc();
        }
      },
    });
  }

  function replaceMaterialRequestStop(frm) {
    if (frm.doc.docstatus !== 1 || frm.doc.status === "Stopped") {
      return;
    }
    frm.remove_custom_button(__("Stop"));
    frm.add_custom_button(__("Stop"), () => requestClosure(frm, "Stopped"));
  }

  function hidePermanentReopen(frm) {
    if (!frm.doc.custom_calco_permanent_closure) {
      return;
    }
    if (frm.doctype === "Material Request") {
      frm.remove_custom_button(__("Re-open"));
    } else {
      frm.remove_custom_button(__("Re-open"), __("Status"));
    }
  }

  function replacePurchaseOrderClose(frm) {
    if (frm.doc.docstatus !== 1 || ["Closed", "Delivered"].includes(frm.doc.status)) {
      return;
    }
    frm.remove_custom_button(__("Close"), __("Status"));
    frm.add_custom_button(__("Close"), () => requestClosure(frm, "Closed"), __("Status"));
  }

  frappe.ui.form.on("Material Request", {
    refresh(frm) {
      interceptMaterialRequestStatus(frm);
      setTimeout(() => {
        replaceMaterialRequestStop(frm);
        hidePermanentReopen(frm);
      }, 0);
    },
  });

  frappe.ui.form.on("Purchase Order", {
    refresh(frm) {
      setTimeout(() => {
        replacePurchaseOrderClose(frm);
        hidePermanentReopen(frm);
      }, 0);
    },
  });
})();
