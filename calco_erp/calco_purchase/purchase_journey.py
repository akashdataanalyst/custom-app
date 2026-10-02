from __future__ import annotations

from collections import defaultdict
from time import perf_counter

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint, date_diff, flt, formatdate, getdate, now_datetime, today

from calco_erp.calco_purchase.import_shipment import (
    LC_IMPORT_SHIPMENT_DOCTYPE,
    get_import_shipment_gate_for_purchase_order,
)
from calco_erp.calco_purchase.commercial_approval import (
    COMMERCIAL_APPROVAL_DOCTYPE,
    APPROVAL_APPROVED_STATUS,
    APPROVAL_PENDING_STATUS,
    APPROVAL_REJECTED_STATUS,
    get_commercial_approval_snapshot,
)


TRACKER_SECTION_FIELD = "custom_purchase_journey_section"
TRACKER_HTML_FIELD = "custom_purchase_journey_tracker"
TRACEABILITY_PAGE_ROUTE = "material-traceability"
CLIENT_SCRIPT_NAME = "Material Request Purchase Journey Tracker"

STAGE_COLORS = {
    "Not Started": "grey",
    "Not Due": "blue",
    "In Progress": "blue",
    "Pending Finance": "orange",
    "Completed": "green",
    "Skipped": "green",
    "Rejected": "red",
    "On Hold": "orange",
    "Partially Received - Balance Closed": "green",
    "Partially Received - Balance Cancelled": "green",
}

DOC_DATE_FIELDS = {
    "Material Request": "transaction_date",
    "Request for Quotation": "transaction_date",
    "Supplier Quotation": "transaction_date",
    COMMERCIAL_APPROVAL_DOCTYPE: "approval_date",
    "Purchase Order": "transaction_date",
    LC_IMPORT_SHIPMENT_DOCTYPE: "modified",
    "Purchase Receipt": "posting_date",
    "Quality Inspection": "report_date",
    "RM QC Decision": "modified",
    "RM Deviation Approval": "modified",
    "Supplier CAPA Request": "modified",
    "RM Release Note": "modified",
    "Purchase Return": "posting_date",
    "Purchase Invoice": "posting_date",
    "Payment Entry": "posting_date",
}

PR_QC_STATUS_FIELD = "custom_qc_status"
PR_ACCEPTED_QTY_FIELD = "custom_accepted_qty"
PR_REJECTED_QTY_FIELD = "custom_rejected_qty"
PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD = "custom_supplier_purchase_invoice_attachment"
PR_SUPPLIER_TEST_CERTIFICATE_FIELD = "custom_supplier_test_certificate_attachment"
PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD = "custom_raw_material_storage_photo"
PR_RM_EXPIRY_DATE_FIELD = "custom_rm_expiry_date"
FINANCE_ROLES = {"Accounts User", "Accounts Manager", "Finance Manager", "System Manager"}


def ensure_purchase_journey_setup():
    create_custom_fields(
        {
            "Material Request": [
                {
                    "fieldname": TRACKER_SECTION_FIELD,
                    "fieldtype": "Section Break",
                    "label": "Purchase Journey Tracker",
                    "insert_after": "items",
                },
                {
                    "fieldname": TRACKER_HTML_FIELD,
                    "fieldtype": "HTML",
                    "label": "Purchase Journey Tracker",
                    "insert_after": TRACKER_SECTION_FIELD,
                },
            ]
        },
        update=True,
    )
    align_purchase_journey_fields()
    ensure_purchase_journey_client_script()
    frappe.clear_cache()


def align_purchase_journey_fields():
    update_custom_field_position(
        "Material Request",
        TRACKER_SECTION_FIELD,
        insert_after="items",
        label="Purchase Journey Tracker",
        hidden=0,
        read_only=0,
        print_hide=0,
    )
    update_custom_field_position(
        "Material Request",
        TRACKER_HTML_FIELD,
        insert_after=TRACKER_SECTION_FIELD,
        label="Purchase Journey Tracker",
        hidden=0,
        read_only=0,
        print_hide=0,
    )
    ensure_property_setter(
        doctype="Material Request",
        fieldname="terms_tab",
        property_name="insert_after",
        value=TRACKER_HTML_FIELD,
        property_type="Data",
    )


def update_custom_field_position(doctype: str, fieldname: str, **values):
    custom_field_name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname}, "name")
    if not custom_field_name:
        return
    for key, value in values.items():
        frappe.db.set_value("Custom Field", custom_field_name, key, value, update_modified=False)


def ensure_property_setter(doctype, fieldname, property_name, value, property_type):
    existing_name = frappe.db.get_value(
        "Property Setter",
        {
            "doc_type": doctype,
            "field_name": fieldname,
            "property": property_name,
        },
        "name",
    )

    if existing_name:
        if frappe.db.get_value("Property Setter", existing_name, "value") != value:
            frappe.db.set_value("Property Setter", existing_name, "value", value, update_modified=False)
        return existing_name

    property_setter = make_property_setter(
        doctype=doctype,
        fieldname=fieldname,
        property=property_name,
        value=value,
        property_type=property_type,
        validate_fields_for_doctype=False,
    )
    return property_setter.name


def purchase_journey_client_script():
    return """
(function () {
  const SCRIPT_VERSION = "2026-06-06-lc-new-doc-fix";
  const HTML_FIELD = "custom_purchase_journey_tracker";
  const FALLBACK_CLASS = "calco-material-request-purchase-journey";

  function escapeHtml(value) {
    return frappe.utils.escape_html(String(value || ""));
  }

  function getWrapper(frm) {
    if (frm.fields_dict[HTML_FIELD] && frm.fields_dict[HTML_FIELD].$wrapper && frm.fields_dict[HTML_FIELD].$wrapper.is(':visible')) {
      return frm.fields_dict[HTML_FIELD].$wrapper;
    }

    const gridWrapper = frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.wrapper
      ? $(frm.fields_dict.items.grid.wrapper)
      : null;
    if (!gridWrapper || !gridWrapper.length) {
      return null;
    }

    let wrapper = gridWrapper.siblings(`.${FALLBACK_CLASS}`);
    if (!wrapper.length) {
      wrapper = $(`<div class="${FALLBACK_CLASS}"></div>`);
      wrapper.insertAfter(gridWrapper);
    }
    return wrapper;
  }

  function renderPlaceholder(frm, message) {
    const wrapper = getWrapper(frm);
    if (!wrapper) {
      return;
    }

    wrapper.html(`
      <section style="margin-top: 16px; padding: 16px; border: 1px solid var(--border-color); border-radius: 14px; background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,1));">
        <div style="font-size: 16px; font-weight: 700; margin-bottom: 6px;">${__("Purchase Journey Tracker")}</div>
        <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 10px;">${__("Read only visualization from linked ERP documents")}</div>
        <div style="font-size: 13px; color: var(--text-muted);">${escapeHtml(message)}</div>
      </section>
    `);
  }

  function renderInline(frm, data) {
    const wrapper = getWrapper(frm);
    if (!wrapper) {
      return;
    }

    const overview = data.overview || {};
    const stages = (data.stages || []).map((stage) => `
      <div class="calco-purchase-journey-stage" data-stage-key="${escapeHtml(stage.key)}" style="min-width: 160px; border: 1px solid var(--border-color); border-radius: 12px; padding: 12px; background: var(--fg-color); box-shadow: inset 0 4px 0 ${stage.color === 'green' ? '#2e7d32' : stage.color === 'red' ? '#c62828' : stage.color === 'orange' ? '#ef6c00' : stage.color === 'blue' ? '#1565c0' : '#94a3b8'}; cursor:pointer;">
        <div style="font-size: 12px; font-weight: 700; margin-bottom: 6px;">${escapeHtml(stage.label)}</div>
        <div style="display: inline-flex; border-radius: 999px; padding: 3px 8px; font-size: 11px; font-weight: 700; background: rgba(15,23,42,0.06); margin-bottom: 6px;">${escapeHtml(stage.status)}</div>
        <div style="font-size: 12px; color: var(--text-muted); line-height: 1.45;">${escapeHtml(stage.summary)}</div>
      </div>
    `).join("");

    wrapper.html(`
      <section style="margin-top: 16px; padding: 16px; border: 1px solid var(--border-color); border-radius: 14px; background: linear-gradient(180deg, rgba(248,250,252,0.98), rgba(255,255,255,1));">
        <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom: 12px;">
          <div>
            <div style="font-size: 16px; font-weight: 700;">${__("Purchase Journey Tracker")}</div>
            <div style="font-size: 12px; color: var(--text-muted);">${__("Live purchase lifecycle from Material Request onward")}</div>
          </div>
          <button type="button" class="btn btn-default btn-sm calco-open-purchase-journey">${__("Show Purchase Journey")}</button>
        </div>
        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; margin-bottom: 12px;">
          ${(data.stock_quantity_groups || []).flatMap((group) => [
            ["Requested", group.requested], ["Ordered (Submitted)", group.ordered],
            ["Received", group.received], ["QC Accepted", group.qc_accepted],
            ["QC Pending", group.qc_pending], ["Rejected", group.rejected],
            ["Returned", group.returned], ["RM Released", group.released],
            ["Outstanding PO", group.outstanding_po],
          ].map(([label, value]) => [label + " (" + group.stock_uom + ")",
            value == null && group.qc_quantity_review_required && ["QC Accepted", "QC Pending", "Rejected"].includes(label)
              ? __("QC UOM review required") : value ?? 0]))
          .concat([
            ["Cancelled", data.overview.quantity_uom_conflict ? __("UOM review required") : data.overview.cancelled_qty],
            ["Balance", data.overview.quantity_uom_conflict ? __("UOM review required") : data.overview.balance_qty],
          ])
          .map(([label, value]) => `
            <div style="border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; background: rgba(255,255,255,0.75);">
              <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin-bottom: 4px;">${escapeHtml(label)}</div>
              <div style="font-size: 17px; font-weight: 700;">${escapeHtml(value)}</div>
            </div>
          `).join("")}
        </div>
        <div style="display:flex; gap: 10px; overflow-x:auto; padding-bottom: 4px;">${stages}</div>
        <div style="margin-top: 12px; font-size: 11px; color: var(--text-muted); border-top: 1px dashed var(--border-color); padding-top: 10px;">
          <strong>${__("Stage order source")}:</strong>
          ${escapeHtml((data.stage_order_source || []).join(" > "))}
        </div>
      </section>
    `);

    wrapper.find(".calco-open-purchase-journey").on("click", () => openJourneyDialog(frm, data));
    wrapper.find(".calco-purchase-journey-stage").on("click", (event) => {
      const key = event.currentTarget.dataset.stageKey;
      handleStageClick(frm, data, key);
    });
  }

  function buildDocsHtml(documents) {
    if (!documents || !documents.length) {
      return `<div style="font-size: 12px; color: var(--text-muted);">${__("No linked documents found.")}</div>`;
    }

    return documents.map((doc) => `
      <div style="border:1px solid var(--border-color); border-radius: 12px; padding: 12px; margin-bottom: 8px; background: var(--fg-color);">
        <div style="display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom: 5px;">
          <a href="#" class="calco-purchase-journey-doc" data-doctype="${escapeHtml(doc.doctype)}" data-name="${escapeHtml(doc.name)}" style="font-weight:700;">${escapeHtml(doc.name)}</a>
          <div style="font-size: 11px; font-weight: 700;">${escapeHtml(doc.status)}</div>
        </div>
        <div style="font-size: 12px; color: var(--text-muted);">${escapeHtml(doc.detail || "")}</div>
      </div>
    `).join("");
  }

  function buildQuantitySourcesHtml(quantitySources) {
    const labels = {
      requested: __("Requested"),
      ordered: __("Ordered"),
      received: __("Received"),
      accepted: __("Accepted"),
      rejected: __("Rejected"),
      returned: __("Returned"),
    };
    const entries = Object.entries(quantitySources || {});
    if (!entries.length) {
      return "";
    }

    return entries.map(([key, rows]) => `
      <div style="margin-bottom: 14px;">
        <div style="font-size: 13px; font-weight: 700; margin-bottom: 6px;">${escapeHtml(labels[key] || key)}</div>
        ${rows && rows.length ? rows.map((row) => `
          <div style="border:1px solid var(--border-color); border-radius: 10px; padding: 10px 12px; margin-bottom: 6px; background: rgba(255,255,255,0.85);">
            <div style="display:flex; justify-content:space-between; gap:10px; margin-bottom: 4px;">
              <div style="font-weight:600;">${escapeHtml(row.name || "")}</div>
              <div style="font-weight:700;">${escapeHtml(row.qty)}</div>
            </div>
            <div style="font-size: 12px; color: var(--text-muted);">
              ${escapeHtml([row.doctype, row.item_code, row.batch_no, row.row_name].filter(Boolean).join(" | "))}
            </div>
          </div>
        `).join("") : `<div style="font-size: 12px; color: var(--text-muted);">${__("No linked documents.")}</div>`}
      </div>
    `).join("");
  }

  function openJourneyDialog(frm, data) {
    const dialog = new frappe.ui.Dialog({
      title: `${__("Purchase Journey")} - ${frm.doc.name}`,
      fields: [{ fieldtype: "HTML", fieldname: "content" }],
      size: "large",
    });

    const stagesHtml = (data.stages || []).map((stage) => `
      <div style="margin-bottom: 16px;">
        <div style="font-size: 14px; font-weight: 700; margin-bottom: 4px;">${escapeHtml(stage.label)} - ${escapeHtml(stage.status)}</div>
        <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;">${escapeHtml(stage.summary)}</div>
        ${buildDocsHtml(stage.documents || [])}
      </div>
    `).join("");

    dialog.show();
    dialog.fields_dict.content.$wrapper.html(`
      <div style="padding: 4px 0;">
        <div style="margin-bottom: 18px;">
          <div style="font-size: 14px; font-weight: 700; margin-bottom: 8px;">${__("Quantity Sources")}</div>
          ${buildQuantitySourcesHtml(data.quantity_sources)}
        </div>
        ${stagesHtml}
      </div>
    `);

    dialog.$wrapper.on("click", ".calco-purchase-journey-doc", (event) => {
      event.preventDefault();
      const target = event.currentTarget.dataset;
      frappe.set_route("Form", target.doctype, target.name);
      dialog.hide();
    });
  }

  function getStage(data, key) {
    return (data.stages || []).find((stage) => stage.key === key) || null;
  }

  function handleStageClick(frm, data, stageKey) {
    const stage = getStage(data, stageKey);
    if (!stage) {
      return;
    }

    if (stage.documents && stage.documents.length) {
      openJourneyDialog(frm, {
        ...data,
        stages: [stage],
      });
      return;
    }

    const action = stage.action || null;
    if (!action) {
      frappe.msgprint(stage.reason || stage.summary || __("No linked documents found for this stage."));
      return;
    }

    runStageAction(frm, action);
  }

  function runStageAction(frm, action) {
    if (!action) {
      return;
    }

    if ((action.doctype || "") === "LC Import Shipment") {
      console.log("LC card clicked payload:", action);
    }

    if (action.action_type === "blocked" || action.action_type === "info") {
      frappe.msgprint(action.message || __("This action is not available yet."));
      return;
    }

    if (action.action_type === "mapped_doc") {
      frappe.model.open_mapped_doc({
        method: action.method,
        frm,
        source_name: action.source_name || null,
        args: action.args || {},
      });
      return;
    }

    if (action.action_type === "new_doc") {
      frappe.new_doc(action.doctype, action.route_options || {});
      return;
    }

    if (action.action_type === "create_from") {
      if (!action.method && action.doctype) {
        frappe.new_doc(action.doctype, action.route_options || {});
        return;
      }
      if (action.route) {
        if (action.route_options) {
          frappe.route_options = action.route_options;
        }
        frappe.set_route(...action.route);
        return;
      }
      frappe.call({
        method: action.method,
        args: action.args || {},
        freeze: true,
        callback: (response) => {
          const message = response.message || {};
          if (message.doctype) {
            frappe.model.sync(message);
            frappe.set_route("Form", message.doctype, message.name);
          } else if (message.name && action.doctype) {
            frappe.set_route("Form", action.doctype, message.name);
          } else if (message.route) {
            frappe.set_route(...message.route);
          } else {
            frm.reload_doc();
          }
        },
      });
      return;
    }

    if (action.action_type === "route" && action.route) {
      if (action.route_options) {
        frappe.route_options = action.route_options;
      }
      frappe.set_route(...action.route);
    }
  }

  async function loadJourney(frm, openDialog = false) {
    if (frm.is_new()) {
      renderPlaceholder(frm, __("Save the Material Request to view the Purchase Journey."));
      return;
    }

    renderPlaceholder(frm, __("Loading Purchase Journey..."));

    const response = await frappe.call({
      method: "calco_erp.calco_purchase.purchase_journey.get_purchase_journey",
      args: { material_request: frm.doc.name },
      freeze: false,
    });

    const data = response.message || {};
    renderInline(frm, data);
    if (openDialog) {
      openJourneyDialog(frm, data);
    }
  }

  frappe.ui.form.on("Material Request", {
    refresh(frm) {
      frm.add_custom_button(__("Show Purchase Journey"), () => {
        loadJourney(frm, true);
      });
      loadJourney(frm).catch((error) => {
        renderPlaceholder(frm, error.message || __("Unable to load Purchase Journey."));
      });
    },
  });
})();
""".strip()

def ensure_purchase_journey_client_script():
    if not frappe.db.exists("DocType", "Client Script"):
        return
    script = purchase_journey_client_script()
    existing_name = frappe.db.get_value("Client Script", {"dt": "Material Request", "name": CLIENT_SCRIPT_NAME}, "name")
    if not existing_name:
        existing_name = frappe.db.get_value("Client Script", {"dt": "Material Request", "view": "Form", "name": CLIENT_SCRIPT_NAME}, "name")

    if existing_name:
        doc = frappe.get_doc("Client Script", existing_name)
        changed = False
        if doc.script != script:
            doc.script = script
            changed = True
        if getattr(doc, "enabled", 1) != 1:
            doc.enabled = 1
            changed = True
        if doc.view != "Form":
            doc.view = "Form"
            changed = True
        if changed:
            doc.save(ignore_permissions=True)
        return doc.name

    doc = frappe.get_doc(
        {
            "doctype": "Client Script",
            "name": CLIENT_SCRIPT_NAME,
            "dt": "Material Request",
            "view": "Form",
            "enabled": 1,
            "script": script,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def get_purchase_journey(material_request: str) -> dict[str, object]:
    if not material_request:
        frappe.throw(_("Material Request is required."))

    frappe.get_doc("Material Request", material_request).check_permission("read")
    start = perf_counter()
    from calco_erp.planning_upgrade.traceability import quantities
    stock_quantity_groups = quantities('`tabMaterial Request`.name=%(mr)s', {'mr':material_request})
    context = build_purchase_context(material_request)
    stages = [
        build_material_request_stage(context),
        build_rfq_stage(context),
        build_supplier_ack_stage(context),
        build_commercial_approval_stage(context),
        build_purchase_order_stage(context),
        build_import_shipment_stage(context),
        build_purchase_receipt_stage(context),
        build_quarantine_stage(context),
        build_incoming_qc_stage(context),
        build_rm_qc_decision_stage(context),
        build_deviation_stage(context),
        build_purchase_return_stage(context),
        build_rm_release_stage(context),
        build_stores_stage(context),
        build_mr_closure_stage(context),
        build_payment_stage(context),
    ]
    elapsed_ms = round((perf_counter() - start) * 1000, 2)
    return {
        "material_request": material_request,
        "generated_on": str(now_datetime()),
        "performance_ms": elapsed_ms,
        "overview": context["overview"],
        "stock_quantity_groups": stock_quantity_groups,
        "quantity_sources": context["quantity_sources"],
        "stage_order_source": [stage.get("label") for stage in stages],
        "search_options": ["Any", "Batch No", "PO", "PR", "MR", "Supplier", "Item Code"],
        "stages": stages,
        "traceability_route": TRACEABILITY_PAGE_ROUTE,
    }


@frappe.whitelist()
def create_stage_document(source_doctype: str, source_name: str, target_doctype: str) -> dict[str, object]:
    if not source_doctype or not source_name or not target_doctype:
        frappe.throw(_("Source document and target document are required."))

    allowed = {
        ("Material Request", "Purchase Order"): "erpnext.stock.doctype.material_request.material_request.make_purchase_order",
        ("Supplier Quotation", "Purchase Order"): "erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order",
        ("Purchase Order", "Purchase Receipt"): "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
        ("Purchase Order", "Purchase Invoice"): "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
        ("Purchase Receipt", "Purchase Invoice"): "erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
    }
    method_path = allowed.get((source_doctype, target_doctype))
    if not method_path:
        frappe.throw(_("Unsupported journey action: {0} -> {1}").format(source_doctype, target_doctype))

    doc = frappe.get_attr(method_path)(source_name)
    return doc.as_dict() if hasattr(doc, "as_dict") else doc


@frappe.whitelist()
def search_material_traceability(query: str | None = None, search_by: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, object]:
    query = (query or "").strip()
    search_by = (search_by or "Any").strip()
    limit = max(1, min(cint(limit) or 20, 50))

    start = perf_counter()
    from calco_erp.planning_upgrade.traceability import page
    paging = page(query, search_by, limit, offset)
    mr_names = paging["names"]
    results = []
    for name in mr_names[:limit]:
        context = build_purchase_context(name)
        from calco_erp.planning_upgrade.traceability import quantities
        quantity_groups = quantities('`tabMaterial Request`.name=%(mr)s', {'mr':name})
        unit = quantity_groups[0] if len(quantity_groups)==1 else {}
        summary = {
            "material_request": name,
            "status": build_mr_closure_stage(context)["status"],
            "requested_qty": context["overview"]["requested_qty"],
            "ordered_qty": context["overview"]["ordered_qty"],
            "received_qty": context["overview"]["received_qty"],
            "accepted_qty": context["overview"]["accepted_qty"],
            "rejected_qty": context["overview"]["rejected_qty"],
            "returned_qty": context["overview"]["returned_qty"],
            "supplier": first_non_empty([row.get("supplier") for row in context["purchase_orders"] + context["purchase_receipts"] + context["purchase_invoices"]]),
            "item_codes": unique_non_empty(row.get("item_code") for row in context["mr_items"]),
            "purchase_orders": get_names(context["purchase_orders"]),
            "purchase_receipts": get_names(context["purchase_receipts"]),
            "batches": unique_non_empty(row.get("batch_no") for row in context["purchase_receipt_rows"] if row.get("batch_no")),
            "deviations": get_names(context["deviations"]),
            "supplier_capa_requests": get_names(context["capa_requests"]),
            "route": ["Form", "Material Request", name],
        }
        summary['quantity_groups'] = quantity_groups
        summary['quantity_uom'] = unit.get('stock_uom') or 'Multiple UOMs — see breakdown'
        summary['qc_quantity_review_required'] = any(g.get('qc_quantity_review_required') for g in quantity_groups)
        for output,source in [('requested_qty','requested'),('ordered_qty','ordered'),('received_qty','received'),
                ('accepted_qty','qc_accepted'),('rejected_qty','rejected'),('returned_qty','returned'),
                ('released_qty','released'),('qc_pending_qty','qc_pending'),('po_balance_qty','outstanding_po')]:
            summary[output] = unit.get(source,0) if unit else None
        summary['release_notes'] = get_names(context['release_notes'])
        results.append(summary)

    return {
        "query": query,
        "search_by": search_by,
        "performance_ms": round((perf_counter() - start) * 1000, 2),
        "results": results,
        **{k:v for k,v in paging.items() if k != "names"},
    }


def build_purchase_context(material_request: str) -> dict[str, object]:
    mr_doc = frappe.get_doc("Material Request", material_request)
    mr_items = [row.as_dict() for row in mr_doc.get("items", []) if row.get("item_code")]
    mr_item_names = get_names(mr_items)
    mr_item_name_set = set(mr_item_names)
    item_codes = unique_non_empty(row.get("item_code") for row in mr_items)

    rfq_filter_sets = [{"material_request": material_request}]
    if mr_item_names:
        rfq_filter_sets.append({"material_request_item": ("in", mr_item_names)})
    rfq_rows = get_child_rows(
        "Request for Quotation Item",
        rfq_filter_sets,
        ["item_code", "qty", "material_request", "material_request_item"],
    )
    rfq_rows = [
        row
        for row in rfq_rows
        if row.get("material_request") == material_request
        or row.get("material_request_item") in mr_item_name_set
    ]
    rfqs = get_parent_docs("Request for Quotation", rfq_rows, ["status", "transaction_date"])

    rfq_name_set = set(get_names(rfqs))
    supplier_quotation_filter_sets = [{"material_request": material_request}]
    if mr_item_names:
        supplier_quotation_filter_sets.append({"material_request_item": ("in", mr_item_names)})
    if rfq_name_set:
        supplier_quotation_filter_sets.append({"request_for_quotation": ("in", list(rfq_name_set))})
    supplier_quotation_rows = get_child_rows(
        "Supplier Quotation Item",
        supplier_quotation_filter_sets,
        ["item_code", "item_name", "qty", "uom", "stock_uom", "rate", "base_rate", "material_request", "material_request_item", "request_for_quotation"],
    )
    supplier_quotation_rows = [
        row
        for row in supplier_quotation_rows
        if row.get("material_request") == material_request
        or row.get("material_request_item") in mr_item_name_set
        or row.get("request_for_quotation") in rfq_name_set
    ]
    supplier_quotations = get_parent_docs(
        "Supplier Quotation",
        supplier_quotation_rows,
        ["status", "transaction_date", "supplier", "currency", "conversion_rate"],
    )
    supplier_quotation_names = get_names(supplier_quotations)
    commercial_approval_snapshot = get_commercial_approval_snapshot(supplier_quotation_names)

    po_filter_sets = [{"material_request": material_request}]
    if mr_item_names:
        po_filter_sets.append({"material_request_item": ("in", mr_item_names)})
    po_rows = get_child_rows(
        "Purchase Order Item",
        po_filter_sets,
        [
            "item_code",
            "qty",
            "received_qty",
            "conversion_factor",
            "docstatus",
            "uom",
            "stock_uom",
            "material_request",
            "material_request_item",
            "supplier_quotation",
            "supplier_quotation_item",
            "schedule_date",
        ],
    )
    po_rows = [
        row
        for row in po_rows
        if row.get("material_request") == material_request
        or row.get("material_request_item") in mr_item_name_set
    ]
    purchase_orders = get_parent_docs(
        "Purchase Order",
        po_rows,
        [
            "status",
            "transaction_date",
            "schedule_date",
            "supplier",
            "payment_terms_template",
            "custom_calco_permanent_closure",
            "custom_cancelled_qty",
            "custom_closure_reason",
            "custom_closed_by",
            "custom_closed_on",
        ],
    )
    purchase_order_history = purchase_orders
    purchase_orders = active_documents(purchase_order_history)
    purchase_order_names = get_names(purchase_orders)
    purchase_order_name_set = set(purchase_order_names)
    po_rows = rows_for_parents(po_rows, purchase_order_name_set)
    import_shipment_requirements = [
        get_import_shipment_gate_for_purchase_order(name)
        for name in purchase_order_names
    ] if purchase_orders else []
    import_shipment_docs = merge_named_records(
        [
            doc
            for row in import_shipment_requirements
            for doc in row.get("active_docs", [])
        ]
    )

    po_item_names = get_names(po_rows)
    po_item_name_set = set(po_item_names)

    pr_filter_sets = [{"material_request": material_request}]
    if purchase_order_names:
        pr_filter_sets.append({"purchase_order": ("in", purchase_order_names)})
    if po_item_names:
        pr_filter_sets.append({"purchase_order_item": ("in", po_item_names)})
    source_purchase_receipt_rows = get_child_rows(
        "Purchase Receipt Item",
        pr_filter_sets,
        [
            "item_code",
            "qty",
            "received_qty",
            "conversion_factor",
            "docstatus",
            "uom",
            "stock_uom",
            "purchase_order",
            "purchase_order_item",
            "material_request",
            "serial_and_batch_bundle",
            "batch_no",
            "warehouse",
            PR_QC_STATUS_FIELD,
            PR_ACCEPTED_QTY_FIELD,
            PR_REJECTED_QTY_FIELD,
            "quality_inspection",
            "custom_quality_inspection",
            "purchase_receipt_item",
        ],
    )
    source_purchase_receipt_rows = [
        row
        for row in source_purchase_receipt_rows
        if row.get("material_request") == material_request
        or row.get("purchase_order_item") in po_item_name_set
        or row.get("purchase_order") in purchase_order_name_set
    ]
    purchase_receipt_history = get_parent_docs(
        "Purchase Receipt",
        source_purchase_receipt_rows,
        [
            "status",
            "posting_date",
            "supplier",
            "is_return",
            "return_against",
            PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD,
            PR_SUPPLIER_TEST_CERTIFICATE_FIELD,
            PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD,
            PR_RM_EXPIRY_DATE_FIELD,
        ],
    )
    non_return_receipt_history = [row for row in purchase_receipt_history if not cint(row.get("is_return"))]
    return_receipt_history = [row for row in purchase_receipt_history if cint(row.get("is_return"))]
    non_return_receipts = active_documents(non_return_receipt_history)
    return_receipts = active_documents(return_receipt_history)
    non_return_pr_names = get_names(non_return_receipts)
    non_return_pr_name_set = set(non_return_pr_names)
    purchase_receipt_history_rows = list(source_purchase_receipt_rows)
    source_purchase_receipt_rows = [
        row for row in source_purchase_receipt_rows if row.parent in non_return_pr_name_set
    ]
    return_receipts = [
        row for row in return_receipts if row.get("return_against") in non_return_pr_name_set
    ]
    return_receipt_names = get_names(return_receipts)
    return_receipt_rows = []
    if return_receipt_names:
        return_receipt_rows = frappe.get_all(
            "Purchase Receipt Item",
            filters={"parent": ("in", return_receipt_names)},
            fields=[
                "name",
                "parent",
                *filter_existing_fields(
                    "Purchase Receipt Item",
                    [
                        "item_code",
                        "qty",
                        "received_qty",
                        "conversion_factor",
                        "docstatus",
                        "uom",
                        "stock_uom",
                        "purchase_order",
                        "purchase_order_item",
                        "material_request",
                        "serial_and_batch_bundle",
                        "batch_no",
                        "warehouse",
                        PR_QC_STATUS_FIELD,
                        PR_ACCEPTED_QTY_FIELD,
                        PR_REJECTED_QTY_FIELD,
                        "quality_inspection",
                        "custom_quality_inspection",
                        "purchase_receipt_item",
                    ],
                ),
            ],
            limit_page_length=0,
        )
    purchase_receipt_rows = source_purchase_receipt_rows + return_receipt_rows
    purchase_receipts = non_return_receipts + return_receipts

    quality_inspections = get_docs(
        "Quality Inspection",
        [
            {"reference_type": "Purchase Receipt", "reference_name": ("in", non_return_pr_names)},
        ],
        [
            "status",
            "docstatus",
            "reference_type",
            "reference_name",
            "item_code",
            "batch_no",
            "report_date",
            "inspection_type",
            "custom_overall_result",
            "custom_accepted_qty",
            "child_row_reference",
        ],
    )
    rm_qc_decisions = get_docs(
        "RM QC Decision",
        [
            {"purchase_receipt": ("in", non_return_pr_names)},
        ],
        ["status", "decision", "purchase_receipt", "item_code", "batch_no", "quality_inspection", "sample_qty"],
    )
    deviations = get_docs(
        "RM Deviation Approval",
        [
            {"purchase_receipt": ("in", non_return_pr_names)},
        ],
        [
            "approval_status",
            "purchase_receipt",
            "purchase_receipt_item",
            "item_code",
            "batch_no",
            "quality_inspection",
            "approved_qty",
            "supplier_capa_request",
            "rm_qc_decision",
        ],
    )
    release_notes = get_docs(
        "RM Release Note",
        [
            {"rm_qc_decision": ("in", get_names(rm_qc_decisions))},
            {"custom_quality_inspection": ("in", get_names(quality_inspections))},
            {"custom_purchase_receipt": ("in", non_return_pr_names)},
            {"custom_rm_deviation_approval": ("in", get_names(deviations))},
        ],
        [
            "status",
            "rm_qc_decision",
            "item_code",
            "batch_no",
            "release_qty",
            "release_warehouse",
            "custom_rm_deviation_approval",
            "custom_purchase_receipt",
            "custom_quality_inspection",
            "custom_purchase_receipt_item",
            "custom_release_authority_key",
            "custom_release_basis",
            "custom_generated_stock_entry",
        ],
    )
    release_notes, release_note_debug = filter_release_notes_for_chain(
        release_notes,
        linked_purchase_receipts=non_return_pr_names,
        linked_quality_inspections=get_names(quality_inspections),
        linked_rm_qc_decisions=get_names(rm_qc_decisions),
        linked_deviations=get_names(deviations),
    )
    release_stock_entries = get_docs(
        "Stock Entry",
        [
            {
                "name": (
                    "in",
                    unique_non_empty(row.get("custom_generated_stock_entry") for row in release_notes),
                )
            }
        ],
        ["purpose", "stock_entry_type", "posting_date", "custom_rm_release_note"],
    )
    capa_requests = get_docs(
        "Supplier CAPA Request",
        [
            {"purchase_receipt": ("in", non_return_pr_names)},
        ],
        ["supplier", "purchase_receipt", "quality_inspection", "item_code", "batch_no", "status", "required_response_date"],
    )

    purchase_invoices = get_docs(
        "Purchase Invoice",
        [
            {"docstatus": ("<", 2), "update_stock": ("in", [0, 1])},
        ],
        ["status", "posting_date", "supplier", "outstanding_amount", "grand_total"],
        linked_receipt_names=non_return_pr_names,
    )

    payment_entries = get_payment_entries_for_invoices(get_names(purchase_invoices))
    payment_schedule_rows = get_payment_schedule_rows_for_orders(purchase_order_names)

    requested_sources = [
        make_quantity_source(
            "Material Request",
            material_request,
            row.get("name"),
            row.get("item_code"),
            row.get("qty"),
        )
        for row in mr_items
    ]
    ordered_sources = [
        make_quantity_source(
            "Purchase Order",
            row.get("parent"),
            row.get("name"),
            row.get("item_code"),
            row.get("qty"),
        )
        for row in po_rows
    ]
    received_sources = [
        make_quantity_source(
            "Purchase Receipt",
            row.get("parent"),
            row.get("name"),
            row.get("item_code"),
            row.get("received_qty") or row.get("qty"),
        )
        for row in source_purchase_receipt_rows
    ]
    accepted_sources = [
        make_quantity_source(
            "Purchase Receipt",
            row.get("parent"),
            row.get("name"),
            row.get("item_code"),
            row.get(PR_ACCEPTED_QTY_FIELD),
            batch_no=row.get("batch_no"),
            extra={"qc_status": row.get(PR_QC_STATUS_FIELD)},
        )
        for row in source_purchase_receipt_rows
        if flt(row.get(PR_ACCEPTED_QTY_FIELD) or 0)
    ]
    rejected_sources = [
        make_quantity_source(
            "Purchase Receipt",
            row.get("parent"),
            row.get("name"),
            row.get("item_code"),
            row.get(PR_REJECTED_QTY_FIELD),
            batch_no=row.get("batch_no"),
            extra={"qc_status": row.get(PR_QC_STATUS_FIELD)},
        )
        for row in source_purchase_receipt_rows
        if flt(row.get(PR_REJECTED_QTY_FIELD) or 0)
    ]
    returned_sources = [
        make_quantity_source(
            "Purchase Receipt",
            row.get("parent"),
            row.get("name"),
            row.get("item_code"),
            abs(flt(row.get("qty") or 0)),
            batch_no=row.get("batch_no"),
            extra={"return_against": frappe.db.get_value("Purchase Receipt", row.get("parent"), "return_against")},
        )
        for row in return_receipt_rows
    ]

    overview = {
        "requested_qty": round(sum(flt(row.get("qty") or 0) for row in requested_sources), 3),
        "ordered_qty": round(sum(flt(row.get("qty") or 0) for row in ordered_sources), 3),
        "received_qty": round(sum(flt(row.get("qty") or 0) for row in received_sources), 3),
        "accepted_qty": round(sum(flt(row.get("qty") or 0) for row in accepted_sources), 3),
        "rejected_qty": round(sum(flt(row.get("qty") or 0) for row in rejected_sources), 3),
        "returned_qty": round(sum(flt(row.get("qty") or 0) for row in returned_sources), 3),
    }
    quantity_uoms = unique_non_empty(
        row.get("stock_uom") or row.get("uom")
        for row in [*mr_items, *po_rows, *source_purchase_receipt_rows]
    )
    overview["quantity_uom"] = quantity_uoms[0] if len(quantity_uoms) == 1 else ""
    overview["quantity_uom_conflict"] = len(quantity_uoms) > 1
    mr_raw_outstanding = round(max(overview["requested_qty"] - overview["ordered_qty"], 0), 3)
    mr_permanent_closure = bool(cint(mr_doc.get("custom_calco_permanent_closure")))
    overview["mr_permanent_closure"] = mr_permanent_closure
    overview["mr_cancelled_qty"] = (
        round(flt(mr_doc.get("custom_cancelled_qty")), 3) if mr_permanent_closure else 0
    )
    overview["mr_balance_qty"] = 0 if mr_permanent_closure else mr_raw_outstanding
    overview["mr_closed_balance"] = (
        mr_raw_outstanding
        if normalize_status(mr_doc.status) == "stopped" and not mr_permanent_closure
        else 0
    )
    permanently_closed_pos = [
        doc
        for doc in purchase_orders
        if cint(doc.get("custom_calco_permanent_closure"))
        and normalize_status(doc.get("status")) == "closed"
    ]
    overview["po_permanent_closure_count"] = len(permanently_closed_pos)
    overview["po_cancelled_qty"] = round(
        sum(flt(doc.get("custom_cancelled_qty")) for doc in permanently_closed_pos),
        3,
    )
    overview["po_closed_balance"] = round(
        sum(
            max(flt(row.get("qty")) - get_po_row_received_qty(row, source_purchase_receipt_rows), 0)
            for row in po_rows
            if normalize_status(parent_status(purchase_orders, row.get("parent"))) == "closed"
            and not cint(
                next(
                    (
                        doc.get("custom_calco_permanent_closure")
                        for doc in purchase_orders
                        if doc.get("name") == row.get("parent")
                    ),
                    0,
                )
            )
        ),
        3,
    )
    overview["po_open_balance"] = round(
        sum(
            max(flt(row.get("qty")) - get_po_row_received_qty(row, source_purchase_receipt_rows), 0)
            for row in po_rows
            if normalize_status(parent_status(purchase_orders, row.get("parent"))) != "closed"
        ),
        3,
    )
    apply_chain_quantity_summary(overview)
    quantity_sources = {
        "requested": requested_sources,
        "ordered": ordered_sources,
        "received": received_sources,
        "accepted": accepted_sources,
        "rejected": rejected_sources,
        "returned": returned_sources,
    }
    supplier_matrix_rows = get_supplier_matrix_rows(item_codes)
    batches = unique_non_empty(row.get("batch_no") for row in purchase_receipt_rows if row.get("batch_no"))
    bundles = unique_non_empty(row.get("serial_and_batch_bundle") for row in purchase_receipt_rows)
    if bundles:
        batches = unique_non_empty(batches + frappe.get_all("Serial and Batch Entry",
            filters={"parent": ("in", bundles)}, pluck="batch_no", limit_page_length=0))
    batch_balances = {batch_no: get_batch_balances(batch_no, item_codes) for batch_no in batches}

    return {
        "material_request": mr_doc,
        "mr_items": mr_items,
        "rfq_rows": rfq_rows,
        "rfqs": rfqs,
        "supplier_quotation_rows": supplier_quotation_rows,
        "supplier_quotations": supplier_quotations,
        "commercial_approval_snapshot": commercial_approval_snapshot,
        "po_rows": po_rows,
        "purchase_orders": purchase_orders,
        "purchase_order_history": purchase_order_history,
        "import_shipment_requirements": import_shipment_requirements,
        "import_shipments": import_shipment_docs,
        "purchase_receipt_rows": purchase_receipt_rows,
        "purchase_receipt_history_rows": purchase_receipt_history_rows,
        "purchase_receipts": purchase_receipts,
        "non_return_receipts": non_return_receipts,
        "non_return_receipt_history": non_return_receipt_history,
        "return_receipts": return_receipts,
        "return_receipt_history": return_receipt_history,
        "return_receipt_rows": return_receipt_rows,
        "quality_inspections": quality_inspections,
        "rm_qc_decisions": rm_qc_decisions,
        "deviations": deviations,
        "release_notes": release_notes,
        "release_stock_entries": release_stock_entries,
        "release_note_debug": release_note_debug,
        "capa_requests": capa_requests,
        "purchase_invoices": purchase_invoices,
        "payment_entries": payment_entries,
        "payment_schedule_rows": payment_schedule_rows,
        "supplier_matrix_rows": supplier_matrix_rows,
        "overview": overview,
        "quantity_sources": quantity_sources,
        "batch_balances": batch_balances,
    }


def build_material_request_stage(context):
    mr = context["material_request"]
    status = "Completed" if cint(mr.docstatus) == 1 else "In Progress"
    if cint(mr.docstatus) == 2:
        status = "Rejected"
    return make_stage(
        "material_request",
        "Material Request",
        status,
        "Anchor demand document for the purchase journey.",
        build_document_entries("Material Request", [mr.as_dict()]),
        details=[
            detail("Status", mr.status or format_doc_status(mr.as_dict())),
            detail("Requested Qty", context["overview"]["requested_qty"]),
        ],
        reason="Material Request is the anchor document for this purchase chain.",
        source_rows=get_names(context["mr_items"]),
    )


def has_successful_rfq_route(context) -> bool:
    rfq_names = set(get_names(context.get("rfqs") or []))
    successful_supplier_quotations = {
        doc.get("name")
        for doc in context.get("supplier_quotations") or []
        if is_successful_stage_doc(doc)
    }
    return any(
        row.get("parent") in successful_supplier_quotations
        and row.get("request_for_quotation") in rfq_names
        for row in context.get("supplier_quotation_rows") or []
    )


def has_successful_supplier_ack_route(context) -> bool:
    supplier_quotation_names = set(get_names(context.get("supplier_quotations") or []))
    successful_purchase_orders = {
        doc.get("name")
        for doc in context.get("purchase_orders") or []
        if is_successful_stage_doc(doc)
    }
    return any(
        row.get("parent") in successful_purchase_orders
        and row.get("supplier_quotation") in supplier_quotation_names
        for row in context.get("po_rows") or []
    )


def failed_attempt_count(docs) -> int:
    return sum(
        cint(doc.get("docstatus")) == 2
        or normalize_status(format_doc_status(doc)) in {"rejected", "cancelled", "stopped"}
        for doc in docs
    )


def build_rfq_stage(context):
    docs = context["rfqs"]
    if not docs:
        return make_stage(
            "rfq",
            "RFQ",
            "Not Started",
            "RFQ is optional and has not been used here.",
            [],
            reason="No Request for Quotation rows are linked to this Material Request.",
            source_rows=get_names(context["rfq_rows"]),
            action=make_action(
                "mapped_doc",
                doctype="Request for Quotation",
                docnames=[],
                source_doctype="Material Request",
                source_name=context["material_request"].name,
                method="calco_erp.calco_purchase.supplier_approval_matrix.make_request_for_quotation_with_supplier_matrix",
                message="Create Request for Quotation from linked Material Request.",
            ),
        )
    status = derive_docs_stage_status(
        docs,
        downstream_success=has_successful_rfq_route(context),
    )
    failed_count = failed_attempt_count(docs)
    summary = "RFQ stage for supplier solicitation."
    if status == "Completed" and failed_count:
        summary = f"RFQ completed through a valid route. {failed_count} earlier attempt(s) failed or were cancelled."
    return make_stage(
        "rfq",
        "RFQ",
        status,
        summary,
        build_document_entries("Request for Quotation", docs),
        reason=f"RFQ status is based on RFQ documents linked through MR rows: {', '.join(get_names(docs))}.",
        source_rows=get_names(context["rfq_rows"]),
        action=make_action(
            "open_existing",
            doctype="Request for Quotation",
            docnames=get_names(docs),
            source_doctype="Material Request",
            source_name=context["material_request"].name,
        ),
    )


def build_purchase_order_stage(context):
    docs = context["purchase_orders"]
    historical_docs = context.get("purchase_order_history") or docs
    commercial_gate = get_commercial_approval_gate(context)
    status = derive_docs_stage_status(historical_docs)
    summary = "Purchase order placement against Material Request."
    closed_balance = flt(context["overview"].get("po_closed_balance"))
    open_balance = flt(context["overview"].get("po_open_balance"))
    has_open_balance = bool(context["overview"].get("po_has_open_balance"))
    cancelled_qty = flt(context["overview"].get("po_cancelled_qty"))
    permanent_closure_count = cint(context["overview"].get("po_permanent_closure_count"))
    permanently_closed_docs = [
        doc
        for doc in docs
        if cint(doc.get("custom_calco_permanent_closure"))
        and normalize_status(doc.get("status")) == "closed"
    ]
    quantity_uom = context["overview"].get("quantity_uom")
    quantity_suffix = f" {quantity_uom}" if quantity_uom else ""
    if has_open_balance and context["overview"].get("quantity_uom_conflict"):
        status = "In Progress"
        summary = (
            "Purchase Order has active outstanding quantities in multiple UOMs. "
            "Review the linked Purchase Order rows; no mixed-UOM aggregate is shown."
        )
    elif open_balance > 0:
        status = "In Progress"
        summary = (
            f"Partially received. Ordered {context['overview']['ordered_qty']}, "
            f"received {context['overview']['received_qty']}, "
            f"outstanding {round(open_balance, 3)}{quantity_suffix}."
        )
    elif permanent_closure_count:
        if cancelled_qty > 0:
            status = "Partially Received - Balance Cancelled"
            summary = (
                f"Partially Received - Balance Cancelled. Ordered {context['overview']['ordered_qty']}, "
                f"received {context['overview']['received_qty']}, cancelled {round(cancelled_qty, 3)}, "
                "balance 0."
            )
        else:
            status = "Completed"
            summary = "Purchase Order permanently closed. No open balance remained at closure."
    elif closed_balance > 0:
        status = "Partially Received - Balance Closed"
        summary = (
            f"Partially Received - Balance Closed. Ordered {context['overview']['ordered_qty']}, "
            f"received {context['overview']['received_qty']}, closed balance {round(closed_balance, 3)}."
        )
    if historical_docs and commercial_gate["blocked"]:
        if commercial_gate["status"] == "Rejected":
            status = "Rejected"
            summary = (
                "Commercial Approval is Rejected. Existing Purchase Order records remain historical "
                "evidence and are not treated as a legitimate approved progression."
            )
        else:
            status = "On Hold"
            summary = (
                "Commercial Approval is unresolved. Existing Purchase Order records remain historical "
                "evidence; further controlled Purchase progression is blocked."
            )
    if not docs and not historical_docs:
        status = "Not Started"
        if commercial_gate["status"] == "Rejected":
            status = "Rejected"
        elif commercial_gate["blocked"]:
            status = "On Hold"
    first_supplier = next(
        (doc.get("supplier") for doc in [*docs, *historical_docs] if doc.get("supplier")),
        "",
    )
    supplier_summary = get_supplier_matrix_summary(
        context.get("supplier_matrix_rows") or [],
        first_supplier,
        [row.get("item_code") for row in context.get("mr_items", [])],
    )
    return make_stage(
        "purchase_order",
        "Purchase Order",
        status,
        summary,
        build_document_entries("Purchase Order", historical_docs, group_rows_by_parent(context["po_rows"])),
        details=[
            detail("Ordered Qty", context["overview"]["ordered_qty"]),
            detail("Received Qty", context["overview"]["received_qty"]),
            detail("Outstanding Qty", open_balance or None),
            detail("Cancelled Qty", cancelled_qty or None),
            detail("Balance Qty", open_balance),
            detail("Closed Balance", closed_balance or None),
            detail(
                "Closure Reason",
                ", ".join(unique_non_empty(doc.get("custom_closure_reason") for doc in permanently_closed_docs)) or None,
            ),
            detail(
                "Closed By",
                ", ".join(unique_non_empty(doc.get("custom_closed_by") for doc in permanently_closed_docs)) or None,
            ),
            detail(
                "Closed On",
                ", ".join(
                    unique_non_empty(
                        str(doc.get("custom_closed_on")) if doc.get("custom_closed_on") else ""
                        for doc in permanently_closed_docs
                    )
                ) or None,
            ),
            detail("Quantity UOM", quantity_uom or None),
            detail("Selected Supplier", supplier_summary.get("supplier_name") or get_supplier_display_name(first_supplier) or None),
            detail("Supplier Category", supplier_summary.get("approval_status") or None),
            detail("Overseas/Local", supplier_summary.get("supplier_type") or None),
            detail("Commercial Approval", commercial_gate["summary"]),
        ],
        reason=(
            f"Purchase Order is {status} because linked Purchase Order Items found for this MR: {', '.join(get_names(context['po_rows']))}."
            if docs
            else commercial_gate["purchase_order_reason"]
        ),
        source_rows=get_names(context["po_rows"]),
        action=get_purchase_order_action(context),
    )


def build_supplier_ack_stage(context):
    docs = context["supplier_quotations"]
    first_supplier = next((doc.get("supplier") for doc in docs if doc.get("supplier")), "")
    supplier_summary = get_supplier_matrix_summary(
        context.get("supplier_matrix_rows") or [],
        first_supplier,
        [row.get("item_code") for row in context.get("mr_items", [])],
    )
    if docs:
        status = derive_docs_stage_status(
            docs,
            downstream_success=has_successful_supplier_ack_route(context),
        )
        summary = "Supplier response captured through Supplier Quotation."
        failed_count = failed_attempt_count(docs)
        if status == "Completed" and failed_count:
            summary = (
                f"Supplier response completed through a valid route. {failed_count} earlier attempt(s) failed or were cancelled."
            )
        entries = build_document_entries("Supplier Quotation", docs, group_rows_by_parent(context["supplier_quotation_rows"]))
    elif context["rfqs"]:
        status = "Not Started"
        summary = "Supplier Quotation is the next step after RFQ."
        entries = []
    else:
        status = "Not Started"
        summary = "No supplier acknowledgement evidence is linked yet."
        entries = []
    return make_stage(
        "supplier_ack",
        "Supplier Acknowledgement",
        status,
        summary,
        entries,
        details=[
            detail("Supplier", supplier_summary.get("supplier_name") or get_supplier_display_name(first_supplier) or None),
            detail("Supplier Category", supplier_summary.get("approval_status") or None),
            detail("Overseas/Local", supplier_summary.get("supplier_type") or None),
        ],
        reason=summary,
        source_rows=get_names(context["supplier_quotation_rows"]),
        action=get_supplier_ack_action(context),
    )


def build_commercial_approval_stage(context):
    gate = get_commercial_approval_gate(context)
    if not context["supplier_quotations"]:
        return make_stage(
            "commercial_approval",
            "Commercial Approval",
            "Not Started",
            "Commercial Approval becomes relevant after Supplier Acknowledgement.",
            [],
            reason="Submitted Supplier Quotation is required before Commercial Approval can be evaluated.",
            source_rows=get_names(context["supplier_quotation_rows"]),
            action=get_commercial_approval_action(context, gate),
        )

    details = [
        detail("Approval Required Rows", gate["required_count"] or 0),
        detail("Approval Approved Rows", gate["approved_count"] or 0),
        detail("Skipped Rows", gate["skipped_count"] or 0),
    ]
    if gate["pending_count"]:
        details.append(detail("Pending Rows", gate["pending_count"]))
    if gate["rejected_count"]:
        details.append(detail("Rejected Rows", gate["rejected_count"]))

    return make_stage(
        "commercial_approval",
        "Commercial Approval",
        gate["status"],
        gate["summary"],
        build_document_entries(COMMERCIAL_APPROVAL_DOCTYPE, gate["approval_docs"]),
        details=details,
        reason=gate["reason"],
        source_rows=get_names(context["supplier_quotation_rows"]),
        action=get_commercial_approval_action(context, gate),
    )


def build_import_shipment_stage(context):
    requirements = context.get("import_shipment_requirements") or []
    docs = context.get("import_shipments") or []
    overseas_requirements = [row for row in requirements if row.get("required")]
    if not context["purchase_orders"]:
        return make_stage(
            "lc_import_shipment",
            "LC / Import Shipment",
            "Not Started",
            "LC / Import Shipment becomes relevant only after Purchase Order for overseas suppliers.",
            [],
            reason="Purchase Order is required before LC / Import Shipment can be evaluated.",
            source_rows=get_names(context["po_rows"]),
            action=get_import_shipment_action(context),
        )

    if not overseas_requirements:
        return make_stage(
            "lc_import_shipment",
            "LC / Import Shipment",
            "Completed",
            "Skipped. Local supplier does not require LC / Import Shipment.",
            [],
            details=[detail("Overseas Supplier", "No")],
            reason="Selected supplier is Local, so LC / Import Shipment is not applicable.",
            source_rows=get_names(context["purchase_orders"]),
            action=get_import_shipment_action(context),
        )

    if not docs:
        return make_stage(
            "lc_import_shipment",
            "LC / Import Shipment",
            "Not Started",
            "Overseas supplier requires LC / Import Shipment before Purchase Receipt.",
            [],
            details=[
                detail("Overseas Supplier", "Yes"),
                detail("Purchase Orders", ", ".join(row.get("purchase_order") for row in overseas_requirements)),
            ],
            reason="No submitted LC / Import Shipment is linked to the overseas Purchase Order chain yet.",
            source_rows=get_names(context["purchase_orders"]),
            action=get_import_shipment_action(context),
        )

    status = derive_docs_stage_status(docs)
    return make_stage(
        "lc_import_shipment",
        "LC / Import Shipment",
        status,
        "Overseas import control before inward receipt.",
        build_document_entries(LC_IMPORT_SHIPMENT_DOCTYPE, docs),
        details=[
            detail("Overseas Supplier", "Yes"),
            detail("Open Overseas POs", len(overseas_requirements)),
        ],
        reason=f"LC / Import Shipment status is based on linked import shipment records: {', '.join(get_names(docs))}.",
        source_rows=get_names(context["purchase_orders"]),
        action=get_import_shipment_action(context),
    )


def build_purchase_receipt_stage(context):
    from calco_erp.calco_purchase.import_landed_cost import (
        calculate_estimated_landed_cost,
        get_import_receipt_context,
    )

    docs = context["non_return_receipts"]
    historical_docs = context.get("non_return_receipt_history") or docs
    status = derive_docs_stage_status(historical_docs)
    if not docs and not historical_docs:
        status = "Not Started"
    document_capture_complete = has_complete_purchase_receipt_document_capture(docs)
    document_capture_summary = (
        "Completed"
        if document_capture_complete
        else ("Pending" if docs else "Not Started")
    )
    purchase_receipt_action = (
        make_action(
            "open_existing",
            doctype="Purchase Receipt",
            docnames=get_names(docs),
            source_doctype="Purchase Order",
            source_name=docs[0]["name"] if docs else None,
        )
        if docs
        else get_purchase_receipt_action(context)
    )
    blocked_for_import = not docs and is_import_shipment_blocking_purchase_receipt(context)
    summary = "Material inward and supplier document capture against purchase order."
    if blocked_for_import:
        summary = "Purchase Receipt is blocked until LC / Import Shipment is submitted for the overseas supplier."
    stage_details = [
        detail("Received Qty", context["overview"]["received_qty"]),
        detail("Returned Qty", context["overview"]["returned_qty"]),
        detail("Document Capture", document_capture_summary),
    ]
    for row in docs:
        pr = frappe.get_doc("Purchase Receipt", row["name"])
        if not get_import_receipt_context(pr).get("is_import"):
            continue
        cost = calculate_estimated_landed_cost(pr)
        accepted_qty = flt(cost["accepted_stock_qty"])
        material_value = flt(cost["base_material_value"])
        material_rate = material_value / accepted_qty if accepted_qty else 0
        stage_details.extend(
            [
                detail(f"Import PR {pr.name}", f"{pr.currency} @ {flt(pr.conversion_rate)}"),
                detail(f"Material Rate INR {pr.name}", material_rate),
                detail(f"Base Material Value INR {pr.name}", material_value),
                detail(f"Import Charges {pr.name}", cost["eligible_charges"]),
                detail(
                    f"RM Inward Cost INR {pr.name}",
                    cost["rm_inward_cost_per_stock_uom"]
                    if cost["calculation_status"] == "Calculated"
                    else cost["calculation_error"],
                ),
            ]
        )
    return make_stage(
        "purchase_receipt",
        "Purchase Receipt",
        status,
        summary,
        build_document_entries(
            "Purchase Receipt",
            historical_docs,
            group_rows_by_parent(
                (context.get("purchase_receipt_history_rows") or context["purchase_receipt_rows"]),
                allowed_parents=get_names(historical_docs),
            ),
        ),
        details=stage_details,
        reason=(
            f"Purchase Receipt is {status} because linked PRs found from Purchase Order chain: {', '.join(get_names(docs))}. "
            f"Supplier document capture is {document_capture_summary.lower()}."
            if docs
            else (
                "LC / Import Shipment must be submitted before Purchase Receipt for overseas suppliers."
                if blocked_for_import
                else "Purchase Order is required before Purchase Receipt."
            )
        ),
        source_rows=get_names(context["purchase_receipt_rows"]),
        action=purchase_receipt_action,
    )


def build_quarantine_stage(context):
    docs = []
    any_quarantine = False
    released_balanced = False
    for batch_no, warehouse_map in context["batch_balances"].items():
        for warehouse, qty in warehouse_map.items():
            docs.append(
                {
                    "label": "Batch Balance",
                    "doctype": "Batch",
                    "name": batch_no,
                    "status": "Completed" if qty > 0 else "In Progress",
                    "date": "",
                    "aging_days": "",
                    "detail": f"{warehouse}: {round(flt(qty), 3)}",
                }
            )
            if "Quarantine" in warehouse and flt(qty) > 0:
                any_quarantine = True
            if "Stores" in warehouse and flt(qty) > 0:
                released_balanced = True

    if any_quarantine:
        status = "Completed"
        summary = "Stock is present in RM Quarantine."
    elif released_balanced:
        status = "Completed"
        summary = "Quarantine stock has been cleared after release."
    else:
        status = "Not Started"
        summary = "No quarantine stock found."
    return make_stage(
        "rm_quarantine",
        "RM Quarantine",
        status,
        summary,
        docs,
        reason=summary,
        source_rows=unique_non_empty(doc.get("name") for doc in docs),
    )


def build_incoming_qc_stage(context):
    docs = context["quality_inspections"]
    if not docs:
        return make_stage(
            "incoming_qc",
            "Incoming QC",
            "Not Started",
            "Incoming QC has not started yet.",
            [],
            reason=(
                "Purchase Receipt is required before Incoming QC."
                if not context["non_return_receipts"]
                else "No Quality Inspection is linked to the Purchase Receipt chain yet."
            ),
            source_rows=get_names(context["purchase_receipt_rows"]),
            action=get_quality_inspection_action(context),
        )
    status = derive_qi_stage_status(docs)
    return make_stage(
        "incoming_qc",
        "Incoming QC",
        status,
        "Incoming Quality Inspection linked to Purchase Receipt.",
        build_document_entries("Quality Inspection", docs),
        details=[
            detail("Accepted Qty", context["overview"]["accepted_qty"]),
            detail("Rejected Qty", context["overview"]["rejected_qty"]),
        ],
        reason=f"Incoming QC status is based on linked Quality Inspections: {', '.join(get_names(docs))}.",
        source_rows=get_names(context["purchase_receipt_rows"]),
        action=make_action(
            "open_existing",
            doctype="Quality Inspection",
            docnames=get_names(docs),
            source_doctype="Purchase Receipt",
            source_name=context["non_return_receipts"][0]["name"] if context["non_return_receipts"] else None,
        ),
    )


def build_rm_qc_decision_stage(context):
    docs = get_active_rm_qc_decisions(context)
    if not docs:
        if has_accepted_incoming_qc(context):
            return make_stage(
                "rm_qc_decision",
                "RM QC Decision",
                "Completed",
                "Skipped. Accepted Incoming QC goes directly to RM Release Note.",
                [],
                reason="Accepted submitted Incoming QC does not require RM QC Decision.",
                source_rows=get_names(context["quality_inspections"]),
                action=get_rm_release_action(context),
            )
        return make_stage(
            "rm_qc_decision",
            "RM QC Decision",
            "Not Started",
            "No RM QC Decision submitted yet.",
            [],
            reason=(
                "Incoming QC is required before RM QC Decision."
                if not context["quality_inspections"]
                else "RM QC Decision is required only for non-accepted Incoming QC."
            ),
            source_rows=get_names(context["quality_inspections"]),
            action=get_rm_qc_decision_action(context),
        )
    status = derive_rm_qc_decision_stage_status(docs)
    return make_stage(
        "rm_qc_decision",
        "RM QC Decision",
        status,
        "QC release / hold / rejection decision.",
        build_document_entries("RM QC Decision", docs),
        reason=f"RM QC Decision status is based on linked decisions: {', '.join(get_names(docs))}.",
        source_rows=get_names(docs),
    )


def build_deviation_stage(context):
    docs = [row for row in context["deviations"] if cint(row.get("docstatus")) < 2]
    deviation_docs = build_document_entries("RM Deviation Approval", docs)
    extra_docs = build_document_entries("Supplier CAPA Request", context["capa_requests"])
    if is_return_to_supplier_route(context):
        return make_stage(
            "rm_deviation",
            "RM Deviation Approval",
            "Completed",
            "Skipped. Return to Supplier does not use deviation approval.",
            extra_docs,
            sections=[
                {
                    "label": "Supplier CAPA",
                    "documents": extra_docs,
                    "empty_message": "No CAPA request is linked.",
                },
            ],
            reason="Submitted RM QC Decision is Return to Supplier, so deviation approval is not required.",
            source_rows=get_names(context["rm_qc_decisions"]) + get_names(context["capa_requests"]),
            action=get_deviation_action(context),
        )
    if not docs and not extra_docs:
        status = "In Progress" if has_submitted_deviation_required_decision(context) else "Not Started"
        return make_stage(
            "rm_deviation",
            "RM Deviation Approval",
            status,
            "Deviation approval is pending." if has_submitted_deviation_required_decision(context) else "No deviation path used.",
            [],
            reason=(
                "Deviation becomes available only after a submitted RM QC Decision is marked Deviation Required."
                if not has_deviation_required_decision(context)
                else "Deviation Required RM QC Decision is submitted and RM Deviation Approval is the next step."
            ),
            source_rows=get_names(context["rm_qc_decisions"]),
            action=get_deviation_action(context),
        )
    status = "In Progress"
    if any((doc.get("approval_status") or "") == "Approved" for doc in docs):
        status = "Completed"
    elif any((doc.get("approval_status") or "") == "Rejected" for doc in docs):
        status = "Rejected"
    elif any((doc.get("approval_status") or "") in ("Pending", "Pending Operations Approval") for doc in docs):
        status = "On Hold"
    summary = "Deviation and CAPA path when RM is rejected."
    return make_stage(
        "rm_deviation",
        "RM Deviation Approval",
        status,
        (
            "Deviation rejected. Purchase Return and Supplier CAPA are now required."
            if has_rejected_deviation(context)
            else summary
        ),
        deviation_docs + extra_docs,
        sections=[
            {
                "label": "Deviation Approvals",
                "documents": deviation_docs,
                "empty_message": "No deviation approvals are linked.",
            },
            {
                "label": "Supplier CAPA",
                "documents": extra_docs,
                "empty_message": (
                    "Supplier CAPA is required after rejected deviation approval."
                    if has_rejected_deviation(context)
                    else "No CAPA request is linked."
                ),
            },
        ],
        reason=(
            "Rejected RM Deviation Approval routes the material to Purchase Return and requires Supplier CAPA."
            if has_rejected_deviation(context)
            else summary
        ),
        source_rows=get_names(docs) + get_names(context["capa_requests"]),
        action=make_action(
            "open_existing",
            doctype="RM Deviation Approval" if docs else "Supplier CAPA Request",
            docnames=get_names(docs) or get_names(context["capa_requests"]),
            source_doctype="Purchase Receipt",
            source_name=(docs[0].get("purchase_receipt") if docs else None),
        ),
    )


def build_rm_release_stage(context):
    docs = context["release_notes"]
    if not docs:
        if is_return_to_supplier_route(context):
            return make_stage(
                "rm_release",
                "RM Release Note",
                "Completed",
                "Skipped. Return to Supplier route does not use RM Release Note.",
                [],
                reason="Submitted RM QC Decision is Return to Supplier, so RM Release Note is not required.",
                source_rows=get_names(context["rm_qc_decisions"]) + get_names(context["return_receipts"]),
                action=get_rm_release_action(context),
            )
        if is_deviation_rejected_return_route(context):
            return make_stage(
                "rm_release",
                "RM Release Note",
                "Completed",
                "Skipped. Rejected deviation approval routes material to Purchase Return instead of RM Release Note.",
                [],
                reason="Rejected RM Deviation Approval blocks release and requires Purchase Return.",
                source_rows=get_names(context["deviations"]) + get_names(context["return_receipts"]),
                action=get_rm_release_action(context),
            )
        if is_hold_route(context):
            return make_stage(
                "rm_release",
                "RM Release Note",
                "On Hold",
                "RM Release Note is blocked while RM QC Decision is Hold for Review.",
                [],
                reason="Hold for Review keeps material blocked and cannot move to release.",
                source_rows=get_names(context["rm_qc_decisions"]),
                action=get_rm_release_action(context),
            )
        if has_accepted_incoming_qc(context):
            return make_stage(
                "rm_release",
                "RM Release Note",
                "Not Started",
                "Accepted Incoming QC can move directly to RM Release Note.",
                [],
                reason="Accepted submitted Incoming QC is eligible for direct RM Release Note.",
                source_rows=get_names(context["quality_inspections"]),
                action=get_rm_release_action(context),
            )
        if has_approved_deviation(context):
            return make_stage(
                "rm_release",
                "RM Release Note",
                "Not Started",
                "Approved deviation can move to RM Release Note.",
                [],
                reason="Approved RM Deviation Approval is eligible for release.",
                source_rows=get_names(context["deviations"]),
                action=get_rm_release_action(context),
            )
        return make_stage(
            "rm_release",
            "RM Release Note",
            "Not Started",
            "No RM Release Note submitted yet.",
            [],
            reason=(
                "Accepted Incoming QC or approved RM Deviation Approval is required before release."
                if not context["quality_inspections"]
                else "No RM Release Note is linked to this chain yet."
            ),
            source_rows=get_names(context["quality_inspections"]) + get_names(context["deviations"]),
            action=get_rm_release_action(context),
        )
    status = derive_release_stage_status(docs)
    return make_stage(
        "rm_release",
        "RM Release Note",
        status,
        "Released material movement approval.",
        build_document_entries("RM Release Note", docs),
        details=[detail("Accepted Qty", context["overview"]["accepted_qty"])],
        reason=f"RM Release status is based on linked release notes: {', '.join(get_names(docs))}.",
        source_rows=get_names(docs),
        action=get_rm_release_action(context),
        debug={
            "linked_purchase_receipts": get_names(context["non_return_receipts"]),
            "linked_quality_inspections": get_names(context["quality_inspections"]),
            "linked_rm_qc_decisions": get_names(context["rm_qc_decisions"]),
            "linked_deviations": get_names(context["deviations"]),
            "included_release_notes": get_names(docs),
            "inclusion_reasons": context.get("release_note_debug", {}),
        },
    )


def build_stores_stage(context):
    if is_return_to_supplier_route(context):
        return make_stage(
            "stores_cppl",
            "Stores - CPPL",
            "Completed",
            "Skipped. Returned rejected material does not move to Stores.",
            [],
            details=[detail("Returned Qty", context["overview"]["returned_qty"])],
            reason="Return to Supplier route bypasses Stores because material leaves the chain through Purchase Return.",
            source_rows=get_names(context["return_receipt_rows"]),
        )
    if is_deviation_rejected_return_route(context):
        return make_stage(
            "stores_cppl",
            "Stores - CPPL",
            "Completed",
            "Skipped. Rejected deviation approval routes material out through Purchase Return instead of Stores.",
            [],
            details=[detail("Rejected Qty", context["overview"]["rejected_qty"])],
            reason="Rejected RM Deviation Approval prevents stock release to Stores and requires return to supplier.",
            source_rows=get_names(context["deviations"]) + get_names(context["return_receipt_rows"]),
        )
    if is_hold_route(context):
        return make_stage(
            "stores_cppl",
            "Stores - CPPL",
            "On Hold",
            "Material remains blocked and cannot move to Stores while on hold.",
            [],
            details=[detail("Rejected/Hold Qty", context["overview"]["rejected_qty"])],
            reason="Hold for Review keeps material out of released Stores stock.",
            source_rows=get_names(context["rm_qc_decisions"]),
        )
    automatic_releases = [
        row
        for row in context["release_notes"]
        if row.get("custom_release_authority_key") and cint(row.get("docstatus")) == 1
    ]
    if automatic_releases:
        stock_entries = {
            row.get("custom_rm_release_note"): row
            for row in context.get("release_stock_entries", [])
            if cint(row.get("docstatus")) == 1 and row.get("custom_rm_release_note")
        }
        completed_releases = [row for row in automatic_releases if row.get("name") in stock_entries]
        released_qty = round(sum(flt(row.get("release_qty") or 0) for row in completed_releases), 3)
        received_qty = flt(context["overview"]["received_qty"])
        accepted_qty = flt(context["overview"]["accepted_qty"])
        remaining_quarantine_qty = max(received_qty - released_qty, 0)
        complete_received_qty = received_qty > 0 and released_qty + 1e-9 >= received_qty
        status = "Completed" if complete_received_qty else ("In Progress" if released_qty > 0 else "Not Started")
        summary = (
            "Exact linked release movement completed."
            if complete_received_qty
            else "Accepted quantity moved; remaining quantity stays in RM Quarantine."
            if released_qty > 0
            else "No exact linked submitted release Stock Entry is available."
        )
        return make_stage(
            "stores",
            "Stores - CPPL",
            status,
            summary,
            build_document_entries("Stock Entry", list(stock_entries.values())),
            details=[
                detail("Received Qty", received_qty),
                detail("Accepted Qty", accepted_qty),
                detail("Released Qty", released_qty),
                detail("Remaining Quarantine Qty", remaining_quarantine_qty),
            ],
            reason=summary,
            source_rows=get_names(completed_releases) + get_names(list(stock_entries.values())),
        )

    rows = []
    has_released_stock = False
    for batch_no, warehouse_map in context["batch_balances"].items():
        released_qty = 0.0
        for warehouse, qty in warehouse_map.items():
            if "Stores" in warehouse and flt(qty) > 0:
                released_qty += flt(qty)
        if released_qty > 0:
            has_released_stock = True
            rows.append(
                {
                    "label": "Released Stock",
                    "doctype": "Batch",
                    "name": batch_no,
                    "status": "Completed",
                    "date": "",
                    "aging_days": "",
                    "detail": f"{warehouses_label('released')}: {round(released_qty, 3)}",
                }
            )
    status = "Completed" if has_released_stock else "Not Started"
    summary = "Released stock available in stores." if has_released_stock else "No released stock is available in stores yet."
    return make_stage(
        "stores",
        "Stores - CPPL",
        status,
        summary,
        rows,
        details=[detail("Released Qty", context["overview"]["accepted_qty"])],
        reason=summary,
        source_rows=unique_non_empty(doc.get("name") for doc in rows),
    )


def build_purchase_invoice_stage(context):
    docs = context["purchase_invoices"]
    if not docs:
        return make_stage(
            "purchase_invoice",
            "Purchase Invoice",
            "Not Started",
            "No Purchase Invoice submitted yet.",
            [],
            reason=(
                "Purchase Receipt or Purchase Order is required before Purchase Invoice."
                if not context["purchase_orders"] and not context["non_return_receipts"]
                else "No Purchase Invoice is linked to this MR chain yet."
            ),
            source_rows=get_names(context["purchase_orders"]) + get_names(context["non_return_receipts"]),
            action=get_purchase_invoice_action(context),
        )
    status = derive_invoice_stage_status(docs)
    return make_stage(
        "purchase_invoice",
        "Purchase Invoice",
        status,
        "Supplier invoice stage.",
        build_document_entries("Purchase Invoice", docs),
        reason=f"Purchase Invoice status is based on linked invoices: {', '.join(get_names(docs))}.",
        source_rows=get_names(docs),
        action=get_purchase_invoice_action(context),
    )


def build_purchase_return_stage(context):
    docs = context["return_receipts"]
    if docs:
        return make_stage(
            "purchase_return",
            "Purchase Return",
            derive_docs_stage_status(docs),
            "Rejected material return to supplier.",
            build_document_entries("Purchase Return", docs, group_rows_by_parent(context["return_receipt_rows"])),
            details=[detail("Returned Qty", context["overview"]["returned_qty"])],
            reason=f"Purchase Return status is based on linked return receipts: {', '.join(get_names(docs))}.",
            source_rows=get_names(context["return_receipt_rows"]),
            action=make_action(
                "open_existing",
                doctype="Purchase Receipt",
                docnames=get_names(docs),
                source_doctype="Purchase Receipt",
                source_name=docs[0].get("return_against") if docs else None,
            ),
        )

    if requires_purchase_return_route(context):
        return make_stage(
            "purchase_return",
            "Purchase Return",
            "Not Started",
            (
                "Purchase Return is required for rejected material after deviation approval was rejected."
                if is_deviation_rejected_return_route(context)
                else "Purchase Return is required for rejected material marked Return to Supplier."
            ),
            [],
            details=[detail("Rejected Qty", context["overview"]["rejected_qty"])],
            reason=(
                "Rejected RM Deviation Approval makes Purchase Return the required next step."
                if is_deviation_rejected_return_route(context)
                else "Submitted RM QC Decision is Return to Supplier, so Purchase Return is the required next step."
            ),
            source_rows=get_names(context["rm_qc_decisions"]) + get_names(context["deviations"]),
            action=get_purchase_return_action(context),
        )

    return make_stage(
        "purchase_return",
        "Purchase Return",
        "Completed",
        "Skipped. Purchase Return is not required for this route.",
        [],
        reason="Purchase Return is only required when RM QC Decision is Return to Supplier.",
        source_rows=get_names(context["rm_qc_decisions"]),
        action=get_purchase_return_action(context),
    )


def build_payment_stage(context):
    docs = context["payment_entries"]
    due_context = get_payment_due_context(context)
    finance_user = current_user_has_finance_role()

    if docs:
        status = due_context["status"] or "Completed"
        summary = due_context["message"] or "Supplier payment has been recorded."
        action = make_action(
            "open_existing",
            doctype="Payment Entry",
            docnames=get_names(docs),
            source_doctype=due_context.get("source_doctype"),
            source_name=due_context.get("source_name"),
            message=summary,
        )
        if not finance_user:
            action = make_action(
                "info",
                doctype="Payment Entry",
                docnames=get_names(docs),
                source_doctype=due_context.get("source_doctype"),
                source_name=due_context.get("source_name"),
                message="Payment Entry is managed by Finance. Existing payment records are shown read-only.",
            )
        return make_stage(
            "payment_entry",
            "Payment Entry",
            status,
            summary,
            build_document_entries("Payment Entry", docs),
            details=due_context["details"],
            reason=f"Payment Entry status is based on linked payments: {', '.join(get_names(docs))}.",
            source_rows=get_names(docs),
            action=action,
        )

    status = due_context["status"]
    summary = due_context["message"]
    if status == "Pending Finance" and finance_user and due_context.get("source_name"):
        action = make_action(
            "create_from",
            doctype="Payment Entry",
            docnames=[],
            source_doctype=due_context.get("source_doctype"),
            source_name=due_context.get("source_name"),
            route=["Form", "Payment Entry", "new-payment-entry"],
            route_options={
                "party_type": "Supplier",
                "party": due_context.get("supplier"),
                "reference_no": due_context.get("source_name"),
                "remarks": f"Payment due for {due_context.get('source_doctype')} {due_context.get('source_name')}",
            },
            message="Payment due. Pending Finance action.",
        )
    elif status == "Pending Finance":
        action = make_action(
            "info",
            doctype="Payment Entry",
            docnames=[],
            source_doctype=due_context.get("source_doctype"),
            source_name=due_context.get("source_name"),
            message="Payment due. Pending Finance action.",
        )
    elif status == "Not Due":
        action = make_action(
            "info",
            doctype="Payment Entry",
            docnames=[],
            source_doctype=due_context.get("source_doctype"),
            source_name=due_context.get("source_name"),
            message="Payment not due as per supplier payment terms.",
        )
    else:
        action = make_action(
            "info",
            doctype="Payment Entry",
            docnames=[],
            source_doctype=due_context.get("source_doctype"),
            source_name=due_context.get("source_name"),
            message=summary,
        )

    return make_stage(
        "payment_entry",
        "Payment Entry",
        status,
        summary,
        [],
        details=due_context["details"],
        reason="Payment Entry is finance-owned and becomes actionable only when the supplier payment due date is reached.",
        source_rows=[],
        action=action,
    )


def build_mr_closure_stage(context):
    mr = context["material_request"]
    overview = context["overview"]
    resolved_qty = flt(overview["accepted_qty"]) + flt(overview["returned_qty"])
    requested_qty = flt(overview["requested_qty"])
    payment_entries = context["payment_entries"]
    return_route = requires_purchase_return_route(context)
    hold_route = is_hold_route(context)
    accepted_route_complete = bool(context["release_notes"]) or has_accepted_incoming_qc(context) or has_approved_deviation(context)
    return_route_complete = return_route and bool(context["return_receipts"]) and flt(overview["returned_qty"]) + 1e-9 >= flt(overview["rejected_qty"])
    completed = requested_qty > 0 and resolved_qty + 1e-9 >= requested_qty and (return_route_complete or accepted_route_complete or resolved_qty + 1e-9 >= requested_qty)
    mr_closed_balance = flt(overview.get("mr_closed_balance"))
    mr_permanent_closure = bool(overview.get("mr_permanent_closure"))
    mr_cancelled_qty = flt(overview.get("mr_cancelled_qty"))
    if cint(mr.docstatus) == 2:
        status = "Rejected"
        summary = "Material Request is cancelled."
        action = make_action("info", doctype="Material Request", docnames=[mr.name], source_doctype="Material Request", source_name=mr.name, message=summary)
    elif normalize_status(mr.status) == "stopped" and mr_permanent_closure:
        status = "Completed"
        if mr_cancelled_qty > 0:
            summary = (
                f"Partially Fulfilled - Balance Cancelled. Requested {round(requested_qty, 3)}, "
                f"ordered {round(flt(overview['ordered_qty']), 3)}, cancelled "
                f"{round(mr_cancelled_qty, 3)}, balance 0."
            )
        else:
            po_open_balance = flt(overview.get("po_open_balance"))
            summary = "Material Request permanently stopped. No open MR balance remained at closure."
            if po_open_balance > 0:
                summary += f" Linked Purchase Order receipt balance {round(po_open_balance, 3)} remains open."
        action = make_action(
            "open_existing",
            doctype="Material Request",
            docnames=[mr.name],
            source_doctype="Material Request",
            source_name=mr.name,
            message=summary,
        )
    elif normalize_status(mr.status) == "stopped":
        status = "Completed"
        if mr_closed_balance > 0:
            summary = (
                f"Partially Fulfilled - Balance Closed. Requested {round(requested_qty, 3)}, "
                f"ordered {round(flt(overview['ordered_qty']), 3)}, remaining "
                f"{round(mr_closed_balance, 3)} intentionally closed."
            )
        else:
            po_open_balance = flt(overview.get("po_open_balance"))
            summary = "Material Request stopped. No unordered MR balance remains."
            if po_open_balance > 0:
                summary += f" Linked Purchase Order receipt balance {round(po_open_balance, 3)} remains open."
        action = make_action(
            "open_existing",
            doctype="Material Request",
            docnames=[mr.name],
            source_doctype="Material Request",
            source_name=mr.name,
            message=summary,
        )
    elif hold_route:
        status = "On Hold"
        summary = "Material Request cannot close while RM is on Hold for Review."
        action = make_action(
            "blocked",
            doctype="Material Request",
            docnames=[mr.name],
            source_doctype="RM QC Decision",
            source_name=get_latest_submitted_rm_qc_decision(context).get("name") if get_latest_submitted_rm_qc_decision(context) else None,
            message="Material Request Closure is blocked until Hold for Review is resolved.",
        )
    elif completed:
        status = "Completed"
        summary = "Material Request lifecycle is fully resolved."
        if return_route_complete:
            summary = (
                "Material Request is resolved through rejected deviation material returned to supplier."
                if is_deviation_rejected_return_route(context)
                else "Material Request is resolved through rejected material return to supplier."
            )
        action = make_action(
            "open_existing",
            doctype="Material Request",
            docnames=[mr.name],
            source_doctype="Material Request",
            source_name=mr.name,
            message=summary,
        )
    elif overview["ordered_qty"] or overview["received_qty"]:
        status = "In Progress"
        if return_route:
            summary = "Submit the Purchase Return to close this rejected-return route."
            action = make_action(
                "blocked",
                doctype="Material Request",
                docnames=[mr.name],
                source_doctype="Purchase Receipt",
                source_name=context["return_receipts"][0]["name"] if context["return_receipts"] else (context["non_return_receipts"][0]["name"] if context["non_return_receipts"] else None),
                message="Material Request Closure becomes available after Purchase Return is submitted.",
            )
        else:
            summary = "Material Request is still moving through procurement."
            action = make_action(
                "info",
                doctype="Material Request",
                docnames=[mr.name],
                source_doctype="Material Request",
                source_name=mr.name,
                message=summary,
            )
    else:
        status = "Not Started"
        summary = "Material Request has not started procurement yet."
        action = make_action("info", doctype="Material Request", docnames=[mr.name], source_doctype="Material Request", source_name=mr.name, message=summary)
    return make_stage(
        "mr_closure",
        "Material Request Closure",
        status,
        summary,
        build_document_entries("Material Request", [mr.as_dict()]),
        details=[
            detail("Requested", overview["requested_qty"]),
            detail("Ordered", overview["ordered_qty"]),
            detail("Resolved", round(resolved_qty, 3)),
            detail("Cancelled Qty", mr_cancelled_qty if mr_permanent_closure else None),
            detail("Balance Qty", 0 if mr_permanent_closure else overview.get("mr_balance_qty")),
            detail(
                "Closed MR Balance",
                mr_closed_balance
                if normalize_status(mr.status) == "stopped" and not mr_permanent_closure
                else None,
            ),
            detail("Closure Reason", mr.get("custom_closure_reason") if mr_permanent_closure else None),
            detail("Closed By", mr.get("custom_closed_by") if mr_permanent_closure else None),
            detail(
                "Closed On",
                str(mr.get("custom_closed_on")) if mr_permanent_closure and mr.get("custom_closed_on") else None,
            ),
            detail("Payments", len(payment_entries)),
        ],
        reason=summary,
        source_rows=get_names(context["mr_items"]),
        action=action,
    )


def has_rejected_pr_rows(context) -> bool:
    return any((row.get(PR_QC_STATUS_FIELD) or "") == "Rejected" for row in context["purchase_receipt_rows"])


def has_complete_purchase_receipt_document_capture(docs) -> bool:
    if not docs:
        return False

    required_fields = [
        PR_SUPPLIER_INVOICE_ATTACHMENT_FIELD,
        PR_SUPPLIER_TEST_CERTIFICATE_FIELD,
        PR_RAW_MATERIAL_STORAGE_PHOTO_FIELD,
        PR_RM_EXPIRY_DATE_FIELD,
    ]
    for doc in docs:
        for fieldname in required_fields:
            value = doc.get(fieldname)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                return False
    return True


def get_submitted_quality_inspections(context):
    return [row for row in context["quality_inspections"] if cint(row.get("docstatus")) == 1]


def get_latest_submitted_quality_inspection(context):
    submitted = get_submitted_quality_inspections(context)
    return submitted[0] if submitted else None


def quality_inspection_result(inspection):
    inspection = inspection or {}
    if cint(inspection.get("docstatus")) == 1:
        submitted_status = normalize_status(inspection.get("status"))
        if submitted_status in {"accepted", "rejected", "review required", "hold"}:
            return submitted_status
    return normalize_status(inspection.get("custom_overall_result") or inspection.get("status"))


def has_accepted_incoming_qc(context) -> bool:
    return any(quality_inspection_result(row) == "accepted" for row in get_submitted_quality_inspections(context))


def get_nonaccepted_submitted_quality_inspections(context):
    return [row for row in get_submitted_quality_inspections(context) if quality_inspection_result(row) != "accepted"]


def get_active_rm_qc_decisions(context):
    return [row for row in context["rm_qc_decisions"] if cint(row.get("docstatus")) < 2]


def get_submitted_rm_qc_decisions(context):
    return [row for row in get_active_rm_qc_decisions(context) if cint(row.get("docstatus")) == 1]


def get_latest_active_rm_qc_decision(context):
    decisions = get_active_rm_qc_decisions(context)
    return decisions[0] if decisions else None


def get_latest_submitted_rm_qc_decision(context):
    decisions = get_submitted_rm_qc_decisions(context)
    return decisions[0] if decisions else None


def get_latest_rm_qc_decision_value(context) -> str:
    decision = get_latest_submitted_rm_qc_decision(context)
    if not decision:
        return ""
    return (decision.get("decision") or decision.get("status") or "").strip()


def is_return_to_supplier_route(context) -> bool:
    return normalize_status(get_latest_rm_qc_decision_value(context)) == "return to supplier"


def is_hold_route(context) -> bool:
    return normalize_status(get_latest_rm_qc_decision_value(context)) == "hold for review"


def is_deviation_required_stage_value(value) -> bool:
    return normalize_status(value) in {"deviation required", "deviation", "qc deviation", "deviation approval"}


def get_latest_submitted_deviation_required_decision(context):
    for row in get_submitted_rm_qc_decisions(context):
        if is_deviation_required_stage_value(row.get("decision") or row.get("status")):
            return row
    return None


def get_latest_draft_deviation_required_decision(context):
    for row in get_active_rm_qc_decisions(context):
        if cint(row.get("docstatus")) != 0:
            continue
        if is_deviation_required_stage_value(row.get("decision") or row.get("status")):
            return row
    return None


def has_deviation_required_decision(context) -> bool:
    return any(is_deviation_required_stage_value(row.get("decision") or row.get("status")) for row in get_active_rm_qc_decisions(context))


def has_submitted_deviation_required_decision(context) -> bool:
    return any(is_deviation_required_stage_value(row.get("decision") or row.get("status")) for row in get_submitted_rm_qc_decisions(context))


def has_approved_deviation(context) -> bool:
    return any(normalize_status(row.get("approval_status")) == "approved" for row in context["deviations"])


def has_rejected_deviation(context) -> bool:
    return any(
        cint(row.get("docstatus")) == 1 and normalize_status(row.get("approval_status")) == "rejected"
        for row in context["deviations"]
    )


def is_deviation_rejected_return_route(context) -> bool:
    return has_submitted_deviation_required_decision(context) and has_rejected_deviation(context)


def requires_purchase_return_route(context) -> bool:
    return is_return_to_supplier_route(context) or is_deviation_rejected_return_route(context)


def get_overseas_import_requirements(context) -> list[dict]:
    return [row for row in (context.get("import_shipment_requirements") or []) if row.get("required")]


def has_submitted_import_shipment(context) -> bool:
    return all(row.get("has_submitted") for row in get_overseas_import_requirements(context))


def is_import_shipment_blocking_purchase_receipt(context) -> bool:
    requirements = get_overseas_import_requirements(context)
    return bool(requirements) and not has_submitted_import_shipment(context)


def get_latest_pending_import_shipment_doc(context):
    docs = sorted(
        [row for row in (context.get("import_shipments") or []) if cint(row.get("docstatus")) == 0],
        key=lambda row: (row.get("modified") or "", row.get("name") or ""),
        reverse=True,
    )
    return docs[0] if docs else None


def get_commercial_approval_gate(context):
    snapshot = context.get("commercial_approval_snapshot") or {}
    evaluations = snapshot.get("evaluations") or []
    approval_docs = snapshot.get("approval_docs") or []
    if not evaluations:
        return {
            "blocked": False,
            "status": "Not Started",
            "summary": "Commercial Approval is waiting for Supplier Quotation.",
            "reason": "Submitted Supplier Quotation is required before Commercial Approval can be evaluated.",
            "purchase_order_reason": "Supplier Quotation is required before Purchase Order can be evaluated.",
            "approval_docs": approval_docs,
            "required_count": 0,
            "approved_count": 0,
            "pending_count": 0,
            "rejected_count": 0,
            "skipped_count": 0,
        }

    required = [row for row in evaluations if row.get("approval_required")]
    skipped = [row for row in evaluations if not row.get("approval_required")]
    approved = [row for row in required if normalize_status((row.get("approval_doc") or {}).get("approval_status")) == APPROVAL_APPROVED_STATUS.lower()]
    rejected = [row for row in required if normalize_status((row.get("approval_doc") or {}).get("approval_status")) == APPROVAL_REJECTED_STATUS.lower()]
    pending = [row for row in required if row not in approved and row not in rejected]
    missing_benchmark = [row for row in required if row.get("benchmark_missing")]

    if not required:
        return {
            "blocked": False,
            "status": "Skipped",
            "summary": "Skipped. Quoted rate is within benchmark.",
            "reason": "All submitted Supplier Quotation rows are at or below the benchmark rate, so Commercial Approval is not required.",
            "purchase_order_reason": "Commercial Approval is skipped because quoted rate is within benchmark.",
            "approval_docs": approval_docs,
            "required_count": 0,
            "approved_count": 0,
            "pending_count": 0,
            "rejected_count": 0,
            "skipped_count": len(skipped),
        }

    if rejected:
        return {
            "blocked": True,
            "status": "Rejected",
            "summary": "Commercial Approval rejected. Purchase Order remains blocked.",
            "reason": "At least one required Commercial Approval record is rejected.",
            "purchase_order_reason": "Purchase Order is blocked because Commercial Approval is rejected.",
            "approval_docs": approval_docs,
            "required_count": len(required),
            "approved_count": len(approved),
            "pending_count": len(pending),
            "rejected_count": len(rejected),
            "skipped_count": len(skipped),
        }

    if pending:
        summary = "Commercial Approval is required before Purchase Order."
        reason = "Quoted rate is higher than benchmark for at least one Supplier Quotation row."
        if missing_benchmark:
            summary = "Commercial Approval is required because benchmark rate is missing or quoted rate is higher than benchmark."
            reason = "Benchmark rate is missing or quoted rate is higher than benchmark for at least one Supplier Quotation row."
        return {
            "blocked": True,
            "status": "In Progress",
            "summary": summary,
            "reason": reason,
            "purchase_order_reason": summary,
            "approval_docs": approval_docs,
            "required_count": len(required),
            "approved_count": len(approved),
            "pending_count": len(pending),
            "rejected_count": 0,
            "skipped_count": len(skipped),
        }

    return {
        "blocked": False,
        "status": "Completed",
        "summary": "Commercial Approval completed. Purchase Order can proceed.",
        "reason": "All required Commercial Approval records are approved.",
        "purchase_order_reason": "All required Commercial Approval records are approved.",
        "approval_docs": approval_docs,
        "required_count": len(required),
        "approved_count": len(approved),
        "pending_count": 0,
        "rejected_count": 0,
        "skipped_count": len(skipped),
    }


def get_purchase_order_action(context):
    commercial_gate = get_commercial_approval_gate(context)
    if commercial_gate["blocked"]:
        if commercial_gate["approval_docs"]:
            return make_action(
                "open_existing",
                doctype=COMMERCIAL_APPROVAL_DOCTYPE,
                docnames=get_names(commercial_gate["approval_docs"]),
                source_doctype="Supplier Quotation",
                source_name=context["supplier_quotations"][0]["name"] if context["supplier_quotations"] else None,
                message=commercial_gate["summary"],
            )
        return make_action(
            "blocked",
            doctype=COMMERCIAL_APPROVAL_DOCTYPE,
            docnames=[],
            source_doctype="Supplier Quotation",
            source_name=context["supplier_quotations"][0]["name"] if context["supplier_quotations"] else None,
            message=commercial_gate["summary"],
        )

    if context["purchase_orders"]:
        message = None
        material_request = context["material_request"]
        overview = context["overview"]
        if (
            normalize_status(material_request.get("status")) == "stopped"
            and flt(overview.get("mr_balance_qty")) == 0
            and flt(overview.get("po_open_balance")) > 0
        ):
            purchase_order_names = get_names(context["purchase_orders"])
            quantity_uom = overview.get("quantity_uom")
            quantity_suffix = f" {quantity_uom}" if quantity_uom else ""
            message = (
                f"{round(flt(overview['po_open_balance']), 3)}{quantity_suffix} remains outstanding "
                f"against Purchase Order {', '.join(purchase_order_names)}. "
                "Close Purchase Order if remaining quantity will not be received."
            )
        return make_action(
            "open_existing",
            doctype="Purchase Order",
            docnames=get_names(context["purchase_orders"]),
            source_doctype="Material Request",
            source_name=context["material_request"].name,
            message=message,
        )

    if context["supplier_quotations"]:
        supplier_quotation_name = context["supplier_quotations"][0]["name"]
        return make_action(
            "mapped_doc",
            doctype="Purchase Order",
            docnames=[],
            source_doctype="Supplier Quotation",
            source_name=supplier_quotation_name,
            method="erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order",
            message="Create Purchase Order from linked Supplier Quotation.",
        )

    return make_action(
        "mapped_doc",
        doctype="Purchase Order",
        docnames=[],
        source_doctype="Material Request",
        source_name=context["material_request"].name,
        method="erpnext.stock.doctype.material_request.material_request.make_purchase_order",
    )


def get_commercial_approval_action(context, gate):
    if not context["supplier_quotations"]:
        return make_action(
            "blocked",
            doctype=COMMERCIAL_APPROVAL_DOCTYPE,
            docnames=[],
            source_doctype="Supplier Quotation",
            source_name=None,
            message="Submitted Supplier Quotation is required before Commercial Approval can be evaluated.",
        )

    if gate["approval_docs"]:
        return make_action(
            "open_existing",
            doctype=COMMERCIAL_APPROVAL_DOCTYPE,
            docnames=get_names(gate["approval_docs"]),
            source_doctype="Supplier Quotation",
            source_name=context["supplier_quotations"][0]["name"],
            message=gate["summary"],
        )

    return make_action(
        "blocked",
        doctype=COMMERCIAL_APPROVAL_DOCTYPE,
        docnames=[],
        source_doctype="Supplier Quotation",
        source_name=context["supplier_quotations"][0]["name"],
        message=gate["summary"],
    )


def get_supplier_ack_action(context):
    if context["supplier_quotations"]:
        return make_action(
            "open_existing",
            doctype="Supplier Quotation",
            docnames=get_names(context["supplier_quotations"]),
            source_doctype="Request for Quotation" if context["rfqs"] else "Material Request",
            source_name=(context["rfqs"][0]["name"] if context["rfqs"] else context["material_request"].name),
        )

    if context["rfqs"]:
        rfq_name = context["rfqs"][0]["name"]
        return make_action(
            "mapped_doc",
            doctype="Supplier Quotation",
            docnames=[],
            source_doctype="Request for Quotation",
            source_name=rfq_name,
            method="erpnext.buying.doctype.request_for_quotation.request_for_quotation.make_supplier_quotation_from_rfq",
            message="Create Supplier Quotation from linked Request for Quotation.",
        )

    return make_action(
        "blocked",
        doctype="Supplier Quotation",
        docnames=[],
        source_doctype="Material Request",
        source_name=context["material_request"].name,
        message="Create RFQ first.",
    )


def get_purchase_receipt_action(context):
    purchase_orders = context["purchase_orders"]
    if not purchase_orders:
        return make_action(
            "blocked",
            doctype="Purchase Receipt",
            docnames=[],
            source_doctype="Purchase Order",
            source_name=None,
            message="Purchase Order is required before Purchase Receipt.",
        )
    if is_import_shipment_blocking_purchase_receipt(context):
        draft_doc = get_latest_pending_import_shipment_doc(context)
        return make_action(
            "blocked",
            doctype="Purchase Receipt",
            docnames=[],
            source_doctype=LC_IMPORT_SHIPMENT_DOCTYPE if draft_doc else "Purchase Order",
            source_name=draft_doc.get("name") if draft_doc else first_non_empty(row.get("purchase_order") for row in get_overseas_import_requirements(context)),
            message=(
                "Submit the linked LC / Import Shipment before Purchase Receipt."
                if draft_doc
                else "LC / Import Shipment is required before Purchase Receipt for overseas supplier."
            ),
        )
    if len(purchase_orders) > 1:
        return make_action(
            "info",
            doctype="Purchase Receipt",
            docnames=[],
            source_doctype="Purchase Order",
            source_name=None,
            message="Multiple Purchase Orders are linked. Open the Purchase Order stage and choose the correct document.",
        )
    purchase_order_name = purchase_orders[0]["name"]
    return make_action(
        "mapped_doc",
        doctype="Purchase Receipt",
        docnames=[],
        source_doctype="Purchase Order",
        source_name=purchase_order_name,
        method="erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
        message="Create Purchase Receipt from linked Purchase Order.",
    )


def get_import_shipment_action(context):
    purchase_orders = context["purchase_orders"]
    docs = context.get("import_shipments") or []
    if docs:
        return make_action(
            "open_existing",
            doctype=LC_IMPORT_SHIPMENT_DOCTYPE,
            docnames=get_names(docs),
            source_doctype="Purchase Order",
            source_name=docs[0].get("purchase_order") if docs else None,
        )
    if not purchase_orders:
        return make_action(
            "blocked",
            doctype=LC_IMPORT_SHIPMENT_DOCTYPE,
            docnames=[],
            source_doctype="Purchase Order",
            source_name=None,
            message="Purchase Order is required before LC / Import Shipment.",
        )

    overseas_requirements = get_overseas_import_requirements(context)
    if not overseas_requirements:
        return make_action(
            "info",
            doctype=LC_IMPORT_SHIPMENT_DOCTYPE,
            docnames=[],
            source_doctype="Purchase Order",
            source_name=purchase_orders[0]["name"] if purchase_orders else None,
            message="LC / Import Shipment is not applicable for local suppliers.",
        )
    if len(overseas_requirements) > 1:
        return make_action(
            "info",
            doctype=LC_IMPORT_SHIPMENT_DOCTYPE,
            docnames=[],
            source_doctype="Purchase Order",
            source_name=None,
            message="Multiple overseas Purchase Orders are linked. Open the Purchase Order stage and choose the correct document.",
        )

    purchase_order_name = overseas_requirements[0]["purchase_order"]
    route_options = {
        "purchase_order": purchase_order_name,
        "material_request": overseas_requirements[0].get("material_request"),
        "request_for_quotation": overseas_requirements[0].get("request_for_quotation"),
        "supplier_quotation": overseas_requirements[0].get("supplier_quotation"),
        "supplier": overseas_requirements[0].get("supplier"),
        "overseas_supplier": cint(overseas_requirements[0].get("overseas_supplier")),
        "item_code": overseas_requirements[0].get("item_code"),
        "item_name": overseas_requirements[0].get("item_name"),
        "qty": overseas_requirements[0].get("qty"),
        "uom": overseas_requirements[0].get("uom"),
        "po_date": overseas_requirements[0].get("po_date"),
        "required_by": overseas_requirements[0].get("required_by"),
        "payment_terms": overseas_requirements[0].get("payment_terms"),
        "currency": overseas_requirements[0].get("currency"),
    }
    route_options = {key: value for key, value in route_options.items() if value not in (None, "", [])}
    return make_action(
        "new_doc",
        doctype=LC_IMPORT_SHIPMENT_DOCTYPE,
        docnames=[],
        source_doctype="Purchase Order",
        source_name=purchase_order_name,
        mapped_method="calco_erp.calco_purchase.lc_import_shipment.make_lc_import_shipment_from_purchase_order",
        route_options=route_options,
        message="Create LC / Import Shipment from linked overseas Purchase Order.",
    )


def get_quality_inspection_action(context):
    receipts = context["non_return_receipts"]
    submitted_receipts = [row for row in receipts if cint(row.get("docstatus")) == 1]
    if not receipts:
        return make_action(
            "blocked",
            doctype="Quality Inspection",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=None,
            message="Purchase Receipt is required before Incoming QC.",
        )
    if not submitted_receipts:
        return make_action(
            "blocked",
            doctype="Quality Inspection",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=receipts[0]["name"] if receipts else None,
            message="Submit the Purchase Receipt before creating Incoming QC.",
        )
    if len(submitted_receipts) > 1:
        return make_action(
            "info",
            doctype="Quality Inspection",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=None,
            message="Multiple Purchase Receipts are linked. Open the Purchase Receipt stage and choose the correct document.",
        )
    receipt_name = submitted_receipts[0]["name"]
    route_options = {
        "inspection_type": "Incoming",
        "reference_type": "Purchase Receipt",
        "reference_name": receipt_name,
    }
    receipt_rows = [row for row in context["purchase_receipt_rows"] if row.get("parent") == receipt_name]
    if len(receipt_rows) == 1:
        row = receipt_rows[0]
        route_options["item_code"] = row.get("item_code")
        if row.get("batch_no"):
            route_options["batch_no"] = row.get("batch_no")
    return make_action(
        "create_from",
        doctype="Quality Inspection",
        docnames=[],
        source_doctype="Purchase Receipt",
        source_name=receipt_name,
        route=["Form", "Quality Inspection", "new-quality-inspection"],
        route_options=route_options,
        message="Create Quality Inspection from linked Purchase Receipt.",
    )


def get_rm_qc_decision_action(context):
    active_decisions = get_active_rm_qc_decisions(context)
    if active_decisions:
        return make_action(
            "open_existing",
            doctype="RM QC Decision",
            docnames=get_names(active_decisions),
            source_doctype="Quality Inspection",
            source_name=get_latest_submitted_quality_inspection(context).get("name") if get_latest_submitted_quality_inspection(context) else None,
        )
    inspections = get_nonaccepted_submitted_quality_inspections(context)
    if not inspections:
        return make_action(
            "blocked",
            doctype="RM QC Decision",
            docnames=[],
            source_doctype="Quality Inspection",
            source_name=get_latest_submitted_quality_inspection(context).get("name") if get_latest_submitted_quality_inspection(context) else None,
            message="RM QC Decision is required only after non-accepted Incoming QC.",
        )
    if len(inspections) > 1:
        return make_action(
            "info",
            doctype="RM QC Decision",
            docnames=[],
            source_doctype="Quality Inspection",
            source_name=None,
            message="Multiple non-accepted Quality Inspections are linked. Open the Incoming QC stage and choose the correct document.",
        )
    inspection = inspections[0]
    return make_action(
        "create_from",
        doctype="RM QC Decision",
        docnames=[],
        source_doctype="Quality Inspection",
        source_name=inspection.get("name"),
        method="calco_erp.calco_quality.doctype.rm_qc_decision.rm_qc_decision.create_rm_qc_decision_from_inspection",
        args={"name": inspection.get("name")},
        message="Create RM QC Decision for non-accepted Incoming QC.",
    )


def get_deviation_action(context):
    if is_return_to_supplier_route(context):
        latest = get_latest_submitted_rm_qc_decision(context)
        return make_action(
            "blocked",
            doctype="RM Deviation Approval",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=latest.get("name") if latest else None,
            message="RM Deviation Approval is skipped when RM QC Decision is Return to Supplier.",
        )
    if is_deviation_rejected_return_route(context):
        rejected_deviation = next(
            (
                row
                for row in context["deviations"]
                if cint(row.get("docstatus")) == 1 and normalize_status(row.get("approval_status")) == "rejected"
            ),
            None,
        )
        return make_action(
            "open_existing",
            doctype="RM Deviation Approval",
            docnames=get_names([rejected_deviation]) if rejected_deviation else [],
            source_doctype="RM Deviation Approval",
            source_name=rejected_deviation.get("name") if rejected_deviation else None,
            message="Rejected RM Deviation Approval requires Purchase Return and Supplier CAPA follow-up.",
        )
    active_deviations = [row for row in context["deviations"] if cint(row.get("docstatus")) < 2]
    if active_deviations:
        return make_action(
            "open_existing",
            doctype="RM Deviation Approval",
            docnames=get_names(active_deviations),
            source_doctype="RM QC Decision",
            source_name=active_deviations[0].get("rm_qc_decision"),
        )
    submitted_decision = get_latest_submitted_deviation_required_decision(context)
    if submitted_decision:
        return make_action(
            "create_from",
            doctype="RM Deviation Approval",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=submitted_decision.get("name"),
            method="calco_erp.calco_quality.doctype.rm_qc_decision.rm_qc_decision.create_rm_deviation_from_decision",
            args={"name": submitted_decision.get("name")},
            message="Create RM Deviation Approval from Deviation Required RM QC Decision.",
        )

    draft_decision = get_latest_draft_deviation_required_decision(context)
    if draft_decision:
        return make_action(
            "blocked",
            doctype="RM Deviation Approval",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=draft_decision.get("name"),
            message="Submit RM QC Decision with Deviation Required before opening RM Deviation Approval.",
        )

    latest_active_decision = get_latest_active_rm_qc_decision(context)
    if latest_active_decision:
        return make_action(
            "blocked",
            doctype="RM Deviation Approval",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=latest_active_decision.get("name"),
            message="Current RM QC Decision is {0}. Change it to Deviation Required if you want the deviation approval route.".format(
                latest_active_decision.get("decision") or latest_active_decision.get("status") or "not set"
            ),
        )

    if not has_deviation_required_decision(context):
        return make_action(
            "blocked",
            doctype="RM Deviation Approval",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=None,
            message="Create and submit RM QC Decision with Deviation Required before RM Deviation Approval.",
        )
    return make_action(
        "blocked",
        doctype="RM Deviation Approval",
        docnames=[],
        source_doctype="RM QC Decision",
        source_name=None,
        message="Create and submit RM QC Decision with Deviation Required before RM Deviation Approval.",
    )


def get_rm_release_action(context):
    if is_return_to_supplier_route(context):
        latest = get_latest_submitted_rm_qc_decision(context)
        return make_action(
            "blocked",
            doctype="RM Release Note",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=latest.get("name") if latest else None,
            message="RM Release Note is not required when RM QC Decision is Return to Supplier.",
        )
    if is_deviation_rejected_return_route(context):
        rejected_deviation = next(
            (
                row
                for row in context["deviations"]
                if cint(row.get("docstatus")) == 1 and normalize_status(row.get("approval_status")) == "rejected"
            ),
            None,
        )
        return make_action(
            "blocked",
            doctype="RM Release Note",
            docnames=[],
            source_doctype="RM Deviation Approval",
            source_name=rejected_deviation.get("name") if rejected_deviation else None,
            message="RM Release Note is skipped because rejected deviation approval requires Purchase Return.",
        )
    if is_hold_route(context):
        latest = get_latest_submitted_rm_qc_decision(context)
        return make_action(
            "blocked",
            doctype="RM Release Note",
            docnames=[],
            source_doctype="RM QC Decision",
            source_name=latest.get("name") if latest else None,
            message="RM Release Note is blocked while RM QC Decision is Hold for Review.",
        )
    existing_release = get_preferred_rm_release_note(context["release_notes"])
    if existing_release:
        return make_action(
            "open_existing",
            doctype="RM Release Note",
            docnames=[existing_release.get("name")],
            source_doctype="Quality Inspection",
            source_name=existing_release.get("name"),
            message=(
                "Open the submitted RM Release Note already linked to this chain."
                if cint(existing_release.get("docstatus")) == 1
                else "Open the existing draft RM Release Note instead of creating another one."
            ),
        )
    accepted_qi = next((row for row in get_submitted_quality_inspections(context) if quality_inspection_result(row) == "accepted"), None)
    if accepted_qi:
        route_options = {
            "custom_quality_inspection": accepted_qi.get("name"),
            "custom_purchase_receipt": accepted_qi.get("reference_name"),
            "item_code": accepted_qi.get("item_code"),
            "batch_no": accepted_qi.get("batch_no") or "",
        }
        receipt_rows = [row for row in context["purchase_receipt_rows"] if row.get("parent") == accepted_qi.get("reference_name")]
        if receipt_rows:
            route_options["release_qty"] = receipt_rows[0].get(PR_ACCEPTED_QTY_FIELD) or receipt_rows[0].get("received_qty") or receipt_rows[0].get("qty")
        return make_action(
            "create_from",
            doctype="RM Release Note",
            docnames=[],
            source_doctype="Quality Inspection",
            source_name=accepted_qi.get("name"),
            route=["Form", "RM Release Note", "new-rm-release-note"],
            route_options=route_options,
            message="Create RM Release Note directly from accepted Incoming QC.",
        )

    approved_deviation = next((row for row in context["deviations"] if normalize_status(row.get("approval_status")) == "approved"), None)
    if approved_deviation:
        return make_action(
            "create_from",
            doctype="RM Release Note",
            docnames=[],
            source_doctype="RM Deviation Approval",
            source_name=approved_deviation.get("name"),
            route=["Form", "RM Release Note", "new-rm-release-note"],
            route_options={
                "custom_rm_deviation_approval": approved_deviation.get("name"),
                "custom_quality_inspection": approved_deviation.get("quality_inspection"),
                "custom_purchase_receipt": approved_deviation.get("purchase_receipt"),
                "rm_qc_decision": approved_deviation.get("rm_qc_decision"),
                "item_code": approved_deviation.get("item_code"),
                "batch_no": approved_deviation.get("batch_no") or "",
                "release_qty": approved_deviation.get("approved_qty") or approved_deviation.get("rejected_qty"),
            },
            message="Create RM Release Note from approved deviation.",
        )

    return make_action(
        "blocked",
        doctype="RM Release Note",
        docnames=[],
        source_doctype="Quality Inspection",
        source_name=None,
        message="Accepted Incoming QC or approved RM Deviation Approval is required before RM Release Note.",
    )


def get_purchase_return_action(context):
    if context["return_receipts"]:
        return make_action(
            "open_existing",
            doctype="Purchase Receipt",
            docnames=get_names(context["return_receipts"]),
            source_doctype="Purchase Receipt",
            source_name=context["return_receipts"][0].get("return_against"),
            message="Open linked Purchase Return.",
        )
    if requires_purchase_return_route(context) and context["non_return_receipts"]:
        source_name = context["non_return_receipts"][0]["name"]
        return make_action(
            "mapped_doc",
            doctype="Purchase Receipt",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=source_name,
            method="calco_erp.calco_quality.purchase_receipt_qc.make_rejected_qty_purchase_return",
            message=(
                "Create Purchase Return after rejected RM Deviation Approval."
                if is_deviation_rejected_return_route(context)
                else "Create Purchase Return from rejected Purchase Receipt."
            ),
        )
    return make_action(
        "blocked",
        doctype="Purchase Receipt",
        docnames=[],
        source_doctype="Purchase Receipt",
        source_name=context["non_return_receipts"][0]["name"] if context["non_return_receipts"] else None,
        message="Purchase Return is required only for Return to Supplier or rejected deviation return routes.",
    )


def get_purchase_invoice_action(context):
    if context["purchase_invoices"]:
        return make_action(
            "open_existing",
            doctype="Purchase Invoice",
            docnames=get_names(context["purchase_invoices"]),
            source_doctype="Purchase Receipt" if context["non_return_receipts"] else "Purchase Order",
            source_name=(context["non_return_receipts"][0]["name"] if context["non_return_receipts"] else context["purchase_orders"][0]["name"] if context["purchase_orders"] else None),
        )

    if has_rejected_pr_rows(context) and not context["release_notes"]:
        return make_action(
            "blocked",
            doctype="Purchase Invoice",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=None,
            message="Purchase Invoice is blocked while rejected RM is pending approval or return.",
        )

    if context["non_return_receipts"]:
        receipt_name = context["non_return_receipts"][0]["name"]
        return make_action(
            "mapped_doc",
            doctype="Purchase Invoice",
            docnames=[],
            source_doctype="Purchase Receipt",
            source_name=receipt_name,
            method="erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
            message="Create Purchase Invoice from linked Purchase Receipt.",
        )

    if context["purchase_orders"]:
        order_name = context["purchase_orders"][0]["name"]
        return make_action(
            "mapped_doc",
            doctype="Purchase Invoice",
            docnames=[],
            source_doctype="Purchase Order",
            source_name=order_name,
            method="erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
            message="Create Purchase Invoice from linked Purchase Order.",
        )

    return make_action(
        "blocked",
        doctype="Purchase Invoice",
        docnames=[],
        source_doctype=None,
        source_name=None,
        message="Purchase Receipt or Purchase Order is required before Purchase Invoice.",
    )


def warehouses_label(key: str) -> str:
    mapping = {
        "released": "Stores - CPPL",
    }
    return mapping.get(key, key)


def make_stage(key, label, status, summary, documents, details=None, sections=None, reason=None, source_rows=None, action=None, debug=None):
    payload = {
        "key": key,
        "label": label,
        "status": status,
        "color": STAGE_COLORS.get(status, "grey"),
        "summary": summary,
        "reason": reason or summary,
        "documents": documents,
        "document_names": [doc.get("name") for doc in documents if doc.get("name")],
        "details": details or [],
        "sections": sections or [],
        "source_rows": source_rows or [],
        "action": action,
        "debug": debug or {},
    }
    if action:
        payload.update(
            {
                "action_type": action.get("action_type"),
                "doctype": action.get("doctype"),
                "docnames": action.get("docnames", []),
                "source_doctype": action.get("source_doctype"),
                "source_name": action.get("source_name"),
                "mapped_method": action.get("mapped_method") or action.get("method"),
                "route": action.get("route"),
                "message": action.get("message"),
                "blocked_message": action.get("blocked_message") or action.get("message"),
            }
        )
    else:
        payload.update(
            {
                "action_type": None,
                "doctype": None,
                "docnames": [],
                "source_doctype": None,
                "source_name": None,
                "mapped_method": None,
                "route": None,
                "message": None,
                "blocked_message": None,
            }
        )
    return payload


def detail(label, value):
    return {"label": label, "value": value}


def make_action(action_type, **kwargs):
    payload = {"action_type": action_type}
    if "method" in kwargs and "mapped_method" not in kwargs:
        kwargs["mapped_method"] = kwargs["method"]
    if action_type in {"blocked", "info"} and "message" in kwargs and "blocked_message" not in kwargs:
        kwargs["blocked_message"] = kwargs["message"]
    payload.update(kwargs)
    return payload


def build_document_entries(doctype: str, docs, row_map=None):
    entries = []
    row_map = row_map or {}
    for doc in docs:
        if not doc:
            continue
        name = doc.get("name")
        doc_date = get_doc_display_date(doctype, doc)
        detail_parts = []
        if doc.get("supplier"):
            detail_parts.append(f"Supplier {doc['supplier']}")
        if doc.get("batch_no"):
            detail_parts.append(f"Batch {doc['batch_no']}")
        if name in row_map:
            qty = sum(flt(row.get("qty") or row.get("received_qty") or 0) for row in row_map[name])
            if qty:
                detail_parts.append(f"Qty {round(qty, 3)}")
        entries.append(
            {
                "label": doctype,
                "doctype": doctype,
                "name": name,
                "status": format_doc_status(doc),
                "date": doc_date,
                "aging_days": get_aging_days(doc_date),
                "detail": " | ".join(part for part in detail_parts if part),
                "route": ["Form", doctype, name],
            }
        )
    return entries


def get_doc_display_date(doctype: str, doc) -> str:
    fieldname = DOC_DATE_FIELDS.get(doctype)
    if not fieldname:
        return ""
    value = doc.get(fieldname)
    if not value:
        return ""
    try:
        return formatdate(getdate(value))
    except Exception:
        return str(value)


def get_aging_days(doc_date: str) -> str:
    if not doc_date:
        return ""
    try:
        return str(date_diff(today(), getdate(doc_date)))
    except Exception:
        return ""


def format_doc_status(doc) -> str:
    explicit = (doc.get("approval_status") or doc.get("decision") or doc.get("status") or "").strip()
    if explicit:
        return explicit
    if cint(doc.get("docstatus")) == 1:
        return "Submitted"
    if cint(doc.get("docstatus")) == 2:
        return "Cancelled"
    return "Draft"


def derive_docs_stage_status(docs, *, downstream_success=False) -> str:
    if not docs:
        return "Completed" if downstream_success else "Not Started"

    labels = [normalize_status(format_doc_status(row)) for row in docs]
    if downstream_success or any(is_successful_stage_doc(row) for row in docs):
        return "Completed"

    active_attempts = [
        row
        for row, label in zip(docs, labels)
        if cint(row.get("docstatus")) < 2
        and label not in {"rejected", "cancelled", "stopped"}
    ]
    active_labels = [normalize_status(format_doc_status(row)) for row in active_attempts]
    if active_attempts and all(
        label in {"hold", "review required", "pending operations approval"}
        for label in active_labels
    ):
        return "On Hold"
    if active_attempts:
        return "In Progress"
    return "Rejected"


def derive_qi_stage_status(docs) -> str:
    if any(cint(row.get("docstatus")) == 0 for row in docs):
        return "In Progress"

    statuses = [quality_inspection_result(row) for row in docs]
    if any(status == "rejected" for status in statuses):
        return "Rejected"
    if any(status in {"review required", "hold"} for status in statuses):
        return "On Hold"
    return "Completed"


def derive_rm_qc_decision_stage_status(docs) -> str:
    statuses = [normalize_status(row.get("decision") or row.get("status")) for row in docs]
    if any(status == "rejected" for status in statuses):
        return "Rejected"
    if any(status == "hold" for status in statuses):
        return "On Hold"
    if any(cint(row.get("docstatus")) == 0 for row in docs):
        return "In Progress"
    return "Completed"


def derive_release_stage_status(docs) -> str:
    if any(normalize_status(row.get("status")) == "released" and cint(row.get("docstatus")) == 1 for row in docs):
        return "Completed"
    if any(cint(row.get("docstatus")) == 0 for row in docs):
        return "In Progress"
    return derive_docs_stage_status(docs)


def get_preferred_rm_release_note(docs):
    submitted = [
        row
        for row in docs or []
        if cint(row.get("docstatus")) == 1 and normalize_status(row.get("status")) == "released"
    ]
    if submitted:
        submitted.sort(key=lambda row: (row.get("creation") or "", row.get("name") or ""))
        return submitted[0]

    drafts = [row for row in docs or [] if cint(row.get("docstatus")) == 0]
    if drafts:
        drafts.sort(key=lambda row: (row.get("modified") or row.get("creation") or "", row.get("name") or ""), reverse=True)
        return drafts[0]

    active = [row for row in docs or [] if cint(row.get("docstatus")) < 2]
    if active:
        active.sort(key=lambda row: (row.get("modified") or row.get("creation") or "", row.get("name") or ""), reverse=True)
        return active[0]

    return None


def derive_invoice_stage_status(docs) -> str:
    if any(normalize_status(row.get("status")) in {"overdue", "unpaid", "partly paid"} for row in docs):
        return "In Progress"
    return derive_docs_stage_status(docs)


def normalize_status(value) -> str:
    return (value or "").strip().lower()


def active_documents(docs) -> list[dict]:
    return [doc for doc in docs or [] if cint(doc.get("docstatus")) != 2]


def rows_for_parents(rows, parent_names) -> list[dict]:
    allowed = set(parent_names or [])
    return [row for row in rows or [] if row.get("parent") in allowed]


def parent_status(docs, parent_name) -> str:
    return next(
        (doc.get("status") for doc in docs or [] if doc.get("name") == parent_name),
        "",
    )


def filter_release_notes_for_chain(
    docs,
    linked_purchase_receipts: list[str],
    linked_quality_inspections: list[str],
    linked_rm_qc_decisions: list[str],
    linked_deviations: list[str],
):
    linked_purchase_receipt_set = set(linked_purchase_receipts or [])
    linked_quality_inspection_set = set(linked_quality_inspections or [])
    linked_rm_qc_decision_set = set(linked_rm_qc_decisions or [])
    linked_deviation_set = set(linked_deviations or [])
    filtered_docs = []
    debug = {}

    for doc in docs or []:
        reasons = []
        if doc.get("custom_purchase_receipt") in linked_purchase_receipt_set:
            reasons.append(f"custom_purchase_receipt -> {doc.get('custom_purchase_receipt')}")
        if doc.get("custom_quality_inspection") in linked_quality_inspection_set:
            reasons.append(f"custom_quality_inspection -> {doc.get('custom_quality_inspection')}")
        if doc.get("rm_qc_decision") in linked_rm_qc_decision_set:
            reasons.append(f"rm_qc_decision -> {doc.get('rm_qc_decision')}")
        if doc.get("custom_rm_deviation_approval") in linked_deviation_set:
            reasons.append(f"custom_rm_deviation_approval -> {doc.get('custom_rm_deviation_approval')}")

        if not reasons:
            continue

        filtered_docs.append(doc)
        debug[doc.get("name")] = reasons

    return filtered_docs, debug


def get_child_rows(doctype: str, filter_sets: list[dict], fields: list[str]) -> list[dict]:
    rows = []
    selected_fields = ["name", "parent"] + filter_existing_fields(doctype, fields)
    for filters in filter_sets:
        if not filters:
            continue
        child_rows = frappe.get_all(doctype, filters=filters, fields=selected_fields, limit_page_length=0)
        rows.extend(child_rows)
    return merge_named_records(rows)


def get_parent_docs(parent_doctype: str, child_rows: list[dict], fields: list[str]) -> list[dict]:
    return get_docs(parent_doctype, [{"name": ("in", get_parents(child_rows))}], fields)


def get_docs(doctype: str, filter_sets: list[dict], fields: list[str], linked_receipt_names=None) -> list[dict]:
    docs = []
    selected_fields = ["name", "docstatus"] + filter_existing_fields(doctype, fields)
    for filters in filter_sets:
        if not filters:
            continue
        docs.extend(frappe.get_all(doctype, filters=filters, fields=selected_fields, limit_page_length=0))
    docs = merge_named_records(docs)
    if doctype == "Purchase Invoice" and linked_receipt_names is not None:
        docs = [doc for doc in docs if purchase_invoice_links_receipt(doc.name, linked_receipt_names)]
    return docs


def filter_existing_fields(doctype: str, fields: list[str]) -> list[str]:
    meta = frappe.get_meta(doctype)
    selected = []
    for fieldname in fields:
        if fieldname in ("name", "parent", "docstatus"):
            continue
        if meta.get_field(fieldname):
            selected.append(fieldname)
    return selected


def purchase_invoice_links_receipt(purchase_invoice: str, receipt_names: list[str]) -> bool:
    if not receipt_names:
        return False
    return bool(
        frappe.db.exists(
            "Purchase Invoice Item",
            {"parent": purchase_invoice, "purchase_receipt": ("in", receipt_names)},
        )
    )


def get_payment_entries_for_invoices(invoice_names: list[str]) -> list[dict]:
    if not invoice_names:
        return []
    refs = frappe.get_all(
        "Payment Entry Reference",
        filters={"reference_doctype": "Purchase Invoice", "reference_name": ("in", invoice_names)},
        fields=["parent", "reference_name", "allocated_amount"],
        limit_page_length=0,
    )
    payment_entries = get_docs(
        "Payment Entry",
        [{"name": ("in", unique_non_empty(row.get("parent") for row in refs))}],
        ["status", "posting_date", "party", "paid_amount", "received_amount"],
    )
    return payment_entries


def get_supplier_matrix_rows(item_codes: list[str]) -> list[dict]:
    if not item_codes or not frappe.db.exists("DocType", "Supplier Approval Matrix"):
        return []
    normalized = unique_non_empty((code or "").strip().upper() for code in item_codes if code)
    if not normalized:
        return []
    return frappe.get_all(
        "Supplier Approval Matrix",
        filters={"item_code": ("in", normalized)},
        fields=["item_code", "supplier", "supplier_type", "approval_status", "payment_terms"],
        limit_page_length=0,
    )


def get_supplier_display_name(supplier: str | None) -> str:
    if not supplier:
        return ""
    return frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier


def get_supplier_matrix_summary(matrix_rows: list[dict], supplier: str | None, item_codes: list[str]) -> dict[str, str]:
    if not supplier or not matrix_rows:
        return {}
    normalized_items = {((code or "").strip().upper()) for code in item_codes if code}
    supplier_keys = {
        "".join(ch for ch in (supplier or "").upper() if ch.isalnum()),
        "".join(ch for ch in get_supplier_display_name(supplier).upper() if ch.isalnum()),
    }
    rows = [
        row
        for row in matrix_rows
        if (
            (
                row.get("supplier") == supplier
                or "".join(ch for ch in (row.get("supplier") or "").upper() if ch.isalnum()) in supplier_keys
                or "".join(ch for ch in get_supplier_display_name(row.get("supplier")).upper() if ch.isalnum()) in supplier_keys
                or "".join(ch for ch in ((get_supplier_display_name(row.get("supplier")).split() or [""])[0]).upper() if ch.isalnum()) in supplier_keys
            )
            and (not normalized_items or (row.get("item_code") or "").strip().upper() in normalized_items)
        )
    ]
    if not rows:
        return {}
    types = unique_non_empty(row.get("supplier_type") for row in rows)
    statuses = unique_non_empty(row.get("approval_status") for row in rows)
    payment_terms = unique_non_empty(row.get("payment_terms") for row in rows)
    return {
        "supplier_name": get_supplier_display_name(supplier),
        "supplier_type": ", ".join(types),
        "approval_status": ", ".join(statuses),
        "payment_terms": ", ".join(payment_terms),
    }


def get_payment_schedule_rows_for_orders(order_names: list[str]) -> list[dict]:
    if not order_names or not frappe.db.exists("DocType", "Payment Schedule"):
        return []
    return frappe.get_all(
        "Payment Schedule",
        filters={"parenttype": "Purchase Order", "parent": ("in", order_names)},
        fields=[
            "name",
            "parent",
            *filter_existing_fields(
                "Payment Schedule",
                ["due_date", "payment_term", "description", "invoice_portion", "payment_amount"],
            ),
        ],
        order_by="due_date asc, idx asc",
        limit_page_length=0,
    )


def current_user_has_finance_role() -> bool:
    return bool(set(frappe.get_roles(frappe.session.user)).intersection(FINANCE_ROLES))


def get_payment_due_context(context: dict[str, object]) -> dict[str, object]:
    purchase_orders = context.get("purchase_orders") or []
    payment_schedule_rows = context.get("payment_schedule_rows") or []
    non_return_receipts = context.get("non_return_receipts") or []
    purchase_invoices = context.get("purchase_invoices") or []
    payment_entries = context.get("payment_entries") or []

    due_dates = [getdate(row.get("due_date")) for row in payment_schedule_rows if row.get("due_date")]
    due_date = min(due_dates) if due_dates else None
    payment_terms = first_non_empty(row.get("payment_terms_template") for row in purchase_orders)
    supplier = first_non_empty(
        [row.get("supplier") for row in purchase_orders + non_return_receipts + purchase_invoices if row.get("supplier")]
    )
    purchase_receipt_name = first_non_empty(row.get("name") for row in non_return_receipts)
    purchase_order_name = first_non_empty(row.get("name") for row in purchase_orders)

    completed = bool(payment_entries) and all(cint(row.get("docstatus")) == 1 for row in payment_entries)
    if completed:
        status = "Completed"
    elif due_date:
        status = "Pending Finance" if getdate(today()) >= due_date else "Not Due"
    elif purchase_order_name or purchase_receipt_name:
        status = "Not Due"
    else:
        status = "Not Started"

    if completed:
        message = "Supplier payment has been recorded."
    elif due_date and getdate(today()) >= due_date:
        message = "Payment due. Pending Finance action."
    elif due_date:
        message = "Payment not due as per supplier payment terms."
    elif purchase_order_name or purchase_receipt_name:
        message = "Payment not due as per supplier payment terms."
    else:
        message = "Payment timeline will begin after procurement documents are created."

    source_name = purchase_receipt_name or purchase_order_name
    source_doctype = "Purchase Receipt" if purchase_receipt_name else ("Purchase Order" if purchase_order_name else None)
    details = [
        detail("Supplier", supplier),
        detail("Payment Terms", payment_terms),
        detail("Due Date", formatdate(due_date) if due_date else None),
    ]
    details = [row for row in details if row.get("value")]

    return {
        "status": status,
        "message": message,
        "supplier": supplier,
        "payment_terms": payment_terms,
        "due_date": due_date,
        "source_doctype": source_doctype,
        "source_name": source_name,
        "details": details,
    }


def get_batch_balances(batch_no: str, item_codes: list[str]) -> dict[str, float]:
    from calco_erp.calco_quality.rm_warehouse_flow import get_batch_balances_by_warehouse

    balances = defaultdict(float)
    for item_code in item_codes:
        for warehouse, qty in get_batch_balances_by_warehouse(item_code, batch_no).items():
            balances[warehouse] += flt(qty)
    return dict(balances)


def find_material_requests_for_traceability(query: str, search_by: str, limit: int) -> list[str]:
    if not query:
        return frappe.get_all("Material Request", filters={"material_request_type": "Purchase", "docstatus": ("<", 2)}, pluck="name", limit_page_length=limit, order_by="modified desc")

    keys = ["Any"] if search_by == "Any" else [search_by]
    mr_names = set()

    if "Any" in keys or search_by == "MR":
        mr_names.update(
            frappe.get_all(
                "Material Request",
                filters={
                    "material_request_type": "Purchase",
                    "name": ("like", f"%{query}%"),
                    "docstatus": ("<", 2),
                },
                pluck="name",
                limit_page_length=limit,
            )
        )

    po_rows = []
    if "Any" in keys or search_by == "PO":
        po_rows.extend(
            frappe.get_all(
                "Purchase Order Item",
                filters={"parent": ("like", f"%{query}%")},
                fields=["material_request"],
                limit_page_length=limit,
            )
        )

    pr_filters = []
    if "Any" in keys or search_by == "PR":
        pr_filters.append({"parent": ("like", f"%{query}%")})
    if "Any" in keys or search_by == "Batch No":
        pr_filters.append({"batch_no": ("like", f"%{query}%")})
    if "Any" in keys or search_by == "Item Code":
        pr_filters.append({"item_code": ("like", f"%{query}%")})
    pr_rows = get_child_rows("Purchase Receipt Item", pr_filters, ["purchase_order", "batch_no"])
    po_rows.extend(
        get_child_rows(
            "Purchase Order Item",
            [{"parent": ("in", unique_non_empty(row.get("purchase_order") for row in pr_rows))}],
            ["material_request"],
        )
    )

    if "Any" in keys or search_by == "Supplier":
        purchase_orders = frappe.get_all(
            "Purchase Order",
            filters={"supplier": ("like", f"%{query}%")},
            fields=["name"],
            limit_page_length=limit,
        )
        po_rows.extend(
            get_child_rows(
                "Purchase Order Item",
                [{"parent": ("in", get_names(purchase_orders))}],
                ["material_request"],
            )
        )

    if "Any" in keys or search_by == "Item Code":
        po_rows.extend(
            get_child_rows(
                "Purchase Order Item",
                [{"item_code": ("like", f"%{query}%")}],
                ["material_request"],
            )
        )

    mr_names.update(unique_non_empty(row.get("material_request") for row in po_rows))
    return sorted(mr_names)[:limit]


def group_rows_by_parent(rows, allowed_parents=None):
    parent_map = defaultdict(list)
    allowed_parents = set(allowed_parents or [])
    for row in rows:
        parent = row.get("parent")
        if not parent:
            continue
        if allowed_parents and parent not in allowed_parents:
            continue
        parent_map[parent].append(row)
    return parent_map


def make_quantity_source(doctype, parent_name, row_name, item_code, qty, batch_no=None, extra=None):
    payload = {
        "doctype": doctype,
        "name": parent_name or "",
        "row_name": row_name or "",
        "item_code": item_code or "",
        "qty": round(flt(qty or 0), 3),
    }
    if batch_no:
        payload["batch_no"] = batch_no
    if extra:
        payload.update(extra)
    return payload


def get_names(rows) -> list[str]:
    return unique_non_empty(row.get("name") for row in rows)


def get_parents(rows) -> list[str]:
    return unique_non_empty(row.get("parent") for row in rows)


def merge_named_records(rows):
    merged = {}
    for row in rows:
        if row and row.get("name"):
            merged[row["name"]] = row
    return list(merged.values())


def unique_non_empty(values):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def first_non_empty(values):
    for value in values:
        if value:
            return value
    return ""


def get_po_row_received_qty(po_row, receipt_rows) -> float:
    """Exact submitted PR-detail evidence, converted to the PO's transaction UOM.

    No linked receipt means zero; cached PO.received_qty is not evidence.
    Callers subtract this from PO.qty, so convert PR stock UOM back to PO UOM.
    """
    factor = flt(po_row.get("conversion_factor") or 1)
    if factor <= 0:
        frappe.throw("Purchase Order conversion factor must be positive.")
    return round(sum(
        flt(row.get("received_qty") or row.get("qty")) * flt(row.get("conversion_factor") or 1)
        for row in receipt_rows
        if row.get("purchase_order_item") == po_row.get("name")
        and cint(row.get("docstatus", 1)) == 1
    ) / factor, 6)


def apply_chain_quantity_summary(overview) -> None:
    overview["po_balance_qty"] = overview["po_open_balance"]
    overview["po_has_open_balance"] = flt(overview["po_open_balance"]) > 0
    if overview.get("quantity_uom_conflict"):
        overview["po_cancelled_qty"] = None
        overview["po_closed_balance"] = None
        overview["po_open_balance"] = None
        overview["po_balance_qty"] = None
        overview["cancelled_qty"] = None
        overview["balance_qty"] = None
        return

    overview["cancelled_qty"] = round(
        flt(overview.get("mr_cancelled_qty")) + flt(overview.get("po_cancelled_qty")),
        3,
    )
    overview["balance_qty"] = round(
        flt(overview.get("mr_balance_qty")) + flt(overview.get("po_open_balance")),
        3,
    )
def is_successful_stage_doc(doc) -> bool:
    label = normalize_status(format_doc_status(doc))
    return (
        cint(doc.get("docstatus")) == 1
        and label not in {"rejected", "cancelled", "stopped"}
    )
