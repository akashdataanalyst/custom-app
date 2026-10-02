frappe.provide("calco_erp.material_reservation");

(() => {
  const STATE_METHOD =
    "calco_erp.calco_production.material_reservation_draft.get_draft_material_reservation_state";
  const CREATE_METHOD =
    "calco_erp.calco_production.material_reservation_draft.create_draft_material_reservation";
  const SET_STATE_METHOD =
    "calco_erp.calco_production.material_reservation_submission.get_material_reservation_set_state";
  const REVIEW_METHOD =
    "calco_erp.calco_production.material_reservation_submission.get_material_reservation_review";
  const SUBMIT_METHOD =
    "calco_erp.calco_production.material_reservation_submission.submit_material_reservation";
  const CANCEL_METHOD =
    "calco_erp.calco_production.material_reservation_submission.cancel_material_reservation";
  const PREVIEW_METHOD =
    "calco_erp.calco_production.material_reservation.get_material_reservation_preview";
  const SUBMITTER_ROLES = ["Stock Manager"];
  const GENERATOR_ROLES = ["Production Manager", "Production Head"];

  function authorized() {
    return (
      frappe.session.user === "Administrator" ||
      GENERATOR_ROLES.some((role) => frappe.user_roles.includes(role))
    );
  }

  function canSubmit() {
    return (
      frappe.session.user === "Administrator" ||
      SUBMITTER_ROLES.some((role) => frappe.user_roles.includes(role))
    );
  }

  function reservationLinks(names) {
    return (names || [])
      .map(
        (name) =>
          `<a href="/app/stock-reservation-entry/${encodeURIComponent(name)}">${frappe.utils.escape_html(
            name
          )}</a>`
      )
      .join("<br>");
  }

  function showResult(result) {
    const links = reservationLinks(result.sre_names);
    const message = [
      result.reservation_set_id
        ? `<b>${__("Reservation Set")}:</b> ${frappe.utils.escape_html(result.reservation_set_id)}`
        : "",
      `<b>${__("Status")}:</b> ${frappe.utils.escape_html(result.status || "")}`,
      result.rm_count != null ? `<b>${__("Raw Materials")}:</b> ${result.rm_count}` : "",
      result.effective_production_qty != null
        ? `<b>${__("Effective Production Quantity")}:</b> ${format_number(
            result.effective_production_qty
          )}`
        : "",
      links,
      result.reason ? frappe.utils.escape_html(result.reason) : "",
    ]
      .filter(Boolean)
      .join("<br>");
    frappe.msgprint({ title: __("Draft Material Reservation"), message });
  }

  async function createDraftSet(frm) {
    const response = await frappe.call({
      method: CREATE_METHOD,
      args: { work_order: frm.doc.name },
      freeze: true,
      freeze_message: __("Revalidating and creating Draft reservations..."),
    });
    showResult(response.message || {});
    await refreshAction(frm);
  }

  async function openCreationDialog(frm) {
    const response = await frappe.call({
      method: PREVIEW_METHOD,
      args: { work_order: frm.doc.name },
      freeze: true,
      freeze_message: __("Calculating exact material allocation..."),
    });
    const preview = response.message || {};
    if (preview.overall_status !== "READY_TO_RESERVE") {
      frappe.msgprint({
        title: __("Material Reservation Blocked"),
        indicator: "red",
        message: frappe.utils.escape_html(
          JSON.stringify(preview.validation_errors || [], null, 2)
        ),
      });
      return;
    }
    const rows = [];
    (preview.raw_materials || []).forEach((item) => {
      (item.allocations || []).forEach((allocation) => {
        rows.push({
          item_code: item.item_code,
          batch_no: allocation.batch_no,
          allocated_qty: allocation.allocated_qty,
          warehouse: item.source_warehouse,
        });
      });
    });
    const dialog = new frappe.ui.Dialog({
      title: __("Create Material Reservation"),
      size: "large",
      fields: [
        {
          fieldname: "allocations",
          fieldtype: "Table",
          label: __("Exact Batch Allocation"),
          cannot_add_rows: true,
          cannot_delete_rows: true,
          in_place_edit: false,
          data: rows,
          fields: [
            { fieldname: "item_code", label: __("RM"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
            { fieldname: "batch_no", label: __("Batch"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
            { fieldname: "allocated_qty", label: __("Qty"), fieldtype: "Float", in_list_view: 1, read_only: 1 },
            { fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
          ],
        },
      ],
      primary_action_label: __("Create Draft SRE"),
      async primary_action() {
        dialog.hide();
        await createDraftSet(frm);
        const journey = calco_erp.production_execution || {};
        if (journey.refresh_work_order_journey) {
          await journey.refresh_work_order_journey(frm);
        }
      },
    });
    dialog.show();
  }

  async function runSetAction(frm, method, actionLabel) {
    const response = await frappe.call({
      method,
      args: { work_order: frm.doc.name },
      freeze: true,
      freeze_message: __(`${actionLabel} complete reservation set...`),
    });
    const result = response.message || {};
    if (result.status === "STALE_RESERVATION") {
      frappe.msgprint({
        title: __("Stale Material Reservation"),
        indicator: "orange",
        message: `<pre>${frappe.utils.escape_html(
          JSON.stringify(result.differences || [], null, 2)
        )}</pre>`,
      });
    } else {
      showResult({ ...result, status: result.state });
    }
    await refreshAction(frm);
    return result;
  }

  function reviewSummary(review) {
    return [
      '<div class="mb-3">',
      '<div><b>' + __("Reservation Set") + ":</b> " +
        frappe.utils.escape_html(review.reservation_set_id || "") + "</div>",
      '<div><b>' + __("Work Order") + ":</b> " +
        frappe.utils.escape_html(review.work_order || "") + "</div>",
      '<div><b>' + __("Total RM Items") + ":</b> " +
        (review.total_rm_items || 0) + "</div>",
      '<div><b>' + __("Total Reserved Qty") + ":</b> " +
        format_number(review.total_reserved_qty || 0, null, 3) + " Kg</div>",
      '<div><b>' + __("Status") + ":</b> " +
        frappe.utils.escape_html(review.status_label || review.state || "") + "</div>",
      "</div>",
    ].join("");
  }

  function reservationDocuments(names) {
    const links = reservationLinks(names);
    if (!links) return "";
    return "<details><summary>" + __("Reservation Documents") +
      '</summary><div class="mt-2">' + links + "</div></details>";
  }

  async function refreshJourney(frm) {
    const journey = calco_erp.production_execution || {};
    if (journey.refresh_work_order_journey) {
      await journey.refresh_work_order_journey(frm);
    }
  }

  async function openReviewDialog(frm) {
    const response = await frappe.call({
      method: REVIEW_METHOD,
      args: { work_order: frm.doc.name },
      freeze: true,
      freeze_message: __("Loading persisted Draft reservations..."),
    });
    const review = response.message || {};
    if (review.state !== "Draft") {
      frappe.msgprint({
        title: __("Material Reservation"),
        indicator: review.state === "Reserved" ? "green" : "orange",
        message:
          review.state === "Reserved"
            ? __("Reserved - stock remains in Stores until Material Transfer.")
            : __("Reservation set is {0}.", [review.state || __("Unavailable")]),
      });
      await refreshAction(frm);
      await refreshJourney(frm);
      return;
    }

    const dialog = new frappe.ui.Dialog({
      title: __("Review Material Reservation"),
      size: "large",
      fields: [
        { fieldname: "summary", fieldtype: "HTML", options: reviewSummary(review) },
        {
          fieldname: "allocations",
          fieldtype: "Table",
          label: __("Persisted Batch Allocation"),
          cannot_add_rows: true,
          cannot_delete_rows: true,
          in_place_edit: false,
          data: review.allocations || [],
          fields: [
            { fieldname: "item_code", label: __("RM"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
            { fieldname: "batch_no", label: __("Batch"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
            { fieldname: "reserved_qty", label: __("Reserved Qty"), fieldtype: "Float", precision: 3, in_list_view: 1, read_only: 1 },
            { fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Data", in_list_view: 1, read_only: 1 },
          ],
        },
        {
          fieldname: "documents",
          fieldtype: "HTML",
          options: reservationDocuments(review.sre_names),
        },
      ],
      primary_action_label: __("Submit Material Reservation"),
      async primary_action() {
        const button = dialog.get_primary_btn();
        button.prop("disabled", true);
        try {
          const result = await runSetAction(frm, SUBMIT_METHOD, __("Submitting"));
          if (result.status === "STALE_RESERVATION") return;
          dialog.hide();
          await refreshJourney(frm);
          frappe.show_alert({
            message: __("Reserved - stock remains in Stores until Material Transfer."),
            indicator: "green",
          });
        } finally {
          button.prop("disabled", false);
        }
      },
    });
    if (!review.can_submit) dialog.get_primary_btn().hide();
    dialog.show();
  }

  async function refreshAction(frm) {
    frm.remove_custom_button(__("Create Draft Material Reservation"), __("Production"));
    frm.remove_custom_button(__("Submit Material Reservation"), __("Production"));
    frm.remove_custom_button(__("Cancel Material Reservation"), __("Production"));
    if (frm.doc.docstatus !== 1) return;

    const response = await frappe.call({
      method: STATE_METHOD,
      args: { work_order: frm.doc.name },
      silent: true,
    });
    const state = response.message || {};
    frm.__calco_material_reservation_state = state;
    if (state.can_create && authorized()) {
      frm.add_custom_button(
        __("Create Draft Material Reservation"),
        () => openCreationDialog(frm),
        __("Production")
      );
    }

    if (!canSubmit() || !(state.sre_names || []).length) return;
    const setResponse = await frappe.call({
      method: SET_STATE_METHOD,
      args: { work_order: frm.doc.name },
      silent: true,
    });
    const setState = setResponse.message || {};
    frm.__calco_material_reservation_set_state = setState;
    if (setState.can_submit) {
      frm.add_custom_button(
        __("Review Material Reservation"),
        () => openReviewDialog(frm),
        __("Production")
      );
    }
    if (setState.can_cancel) {
      frm.add_custom_button(
        __("Cancel Material Reservation"),
        () => runSetAction(frm, CANCEL_METHOD, __("Cancelling")),
        __("Production")
      );
    }
  }

  calco_erp.material_reservation.refresh_action = refreshAction;
  calco_erp.material_reservation.open_creation_dialog = openCreationDialog;
  calco_erp.material_reservation.review_reservation = openReviewDialog;

  frappe.ui.form.on("Work Order", {
    refresh(frm) {
      refreshAction(frm);
    },
  });
})();
