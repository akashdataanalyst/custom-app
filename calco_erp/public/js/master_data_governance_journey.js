frappe.provide("calco_erp.master_data_governance");
window.calco_erp = window.calco_erp || {};
window.calco_erp.master_data_governance = window.calco_erp.master_data_governance || {};

(function () {
  const HTML_FIELD = "journey_tracker_html";
  const STYLE_ID = "calco-governance-journey-style";
  const MAX_RENDER_RETRIES = 4;
  const FALLBACK_CLASS = "calco-governance-journey-mount";
  const RM_TRACKER_WRAPPER_ID = "calco-new-rm-request-journey-tracker";
  const SUPPLIER_TRACKER_WRAPPER_ID = "calco-new-supplier-request-journey-tracker";
  const LEGACY_HOST_CLASS = "calco-governance-journey-host";
  const UNSAVED_MESSAGES = {
    "New Supplier Request": __("Please save the Supplier Request first."),
    "New RM Request": __("Please save the RM Request first."),
  };
  const SUPPLIER_CREATION_BLOCK_MESSAGE = __("Proposed suppliers must be created through the Supplier Approval Request workflow.");
  const REQUEST_SUPPLIER_FIELDS = {
    "New RM Request": "preferred_supplier",
    "New Supplier Request": "supplier_name",
  };
  const QUALITY_PARAMETER_DOCTYPE = "Item Quality Inspection Parameter";
  const GOVERNANCE_DOCUMENT_DOCTYPE = "Supplier Governance Document";
  const GOVERNANCE_DOCUMENT_FIELDNAMES = [
    "document_key",
    "document_type",
    "category",
    "document_scope",
    "rm_reference",
    "requirement",
    "applicable",
    "rule_reason",
    "available",
    "attachment",
    "document_number",
    "issue_date",
    "expiry_date",
    "verification_status",
    "verified_by",
    "verified_on",
    "verification_remarks",
  ];
  const GOVERNANCE_DOCUMENT_IMMUTABLE_FIELDS = new Set([
    "document_key",
    "document_type",
    "category",
    "document_scope",
    "rm_reference",
    "requirement",
    "applicable",
    "rule_reason",
    "available",
    "verified_by",
    "verified_on",
  ]);
  const GOVERNANCE_DOCUMENT_LIST_FIELDS = new Set([
    "document_type",
    "rm_reference",
    "applicable",
    "attachment",
    "document_number",
    "issue_date",
    "expiry_date",
    "verification_status",
  ]);
  const QUALITY_PARAMETER_FIELDNAMES = [
    "specification",
    "value",
    "numeric",
    "min_value",
    "max_value",
    "formula_based_criteria",
    "acceptance_formula",
  ];
  const QUALITY_PARAMETER_LIST_FIELDS = new Set([
    "specification",
    "value",
    "numeric",
    "min_value",
    "max_value",
  ]);
  let pendingSupplierRequestPrefill = null;

  const CONFIG = {
    "New RM Request": {
      title: __("New RM Request Tracker"),
      subtitle: __("Approval and ERP creation journey for raw material onboarding"),
      method: "calco_erp.calco_purchase.master_data_governance_journey.get_new_rm_request_tracker",
      titleField: "rm_code",
      sectionField: "journey_section",
      anchorField: "requester_section",
      summarySections: [
        {
          title: __("Request Summary"),
          fields: ["rm_code", "rm_name", "category", "description", "stock_uom", "preferred_supplier"],
        },
        {
          title: __("Planning Defaults"),
          fields: [
            "current_season",
            "manual_lead_time_days",
            "safety_days",
            "review_period_days",
            "minimum_order_qty",
            "purchase_pack_size",
          ],
        },
      ],
      requestFields: [
        "rm_code",
        "rm_name",
        "category",
        "description",
        "stock_uom",
        "preferred_supplier",
        "current_season",
        "manual_lead_time_days",
        "safety_days",
        "review_period_days",
        "minimum_order_qty",
        "purchase_pack_size",
        "purchase_lead_time_days",
        "purchase_moq",
        "purchase_pack_size",
        "commercial_remarks",
        "purchase_decision",
      ],
      hiddenFields: [
        "technical_review_section",
        "technical_review_remarks",
        "existing_alternative_available",
        "recommended_material_type",
        "application_suitability",
        "technical_approval_attachment",
        "technical_decision",
        "document_readiness_section",
        "tds_attachment",
        "msds_attachment",
        "tc_coa_attachment",
        "sample_available",
        "sample_required",
        "sample_quantity_kg",
        "sample_received_by_quality",
        "sample_received_date",
        "document_readiness_remarks",
        "document_readiness_decision",
        "quality_review_section",
        "msds_available",
        "tds_available",
        "coa_available",
        "required_incoming_tests",
        "no_incoming_inspection_required",
        "incoming_inspection_parameters",
        "quality_review_remarks",
        "quality_approval_attachment",
        "quality_decision",
        "purchase_review_section",
        "purchase_lead_time_days",
        "purchase_moq",
        "purchase_pack_size",
        "commercial_remarks",
        "purchase_decision",
      ],
      stageFields: {
        "Technical Review": [
          "technical_review_remarks",
          "existing_alternative_available",
          "recommended_material_type",
          "application_suitability",
          "technical_approval_attachment",
          "technical_decision",
        ],
        "Document & Sample Readiness": [
          "tds_attachment",
          "msds_attachment",
          "tc_coa_attachment",
          "sample_available",
          "sample_required",
          "sample_quantity_kg",
          "sample_received_by_quality",
          "sample_received_date",
          "document_readiness_remarks",
          "document_readiness_decision",
        ],
        "Quality Review": [
          "msds_available",
          "tds_available",
          "coa_available",
          "required_incoming_tests",
          "no_incoming_inspection_required",
          "incoming_inspection_parameters",
          "quality_review_remarks",
          "quality_approval_attachment",
          "quality_decision",
        ],
        "Purchase Review": [
          "purchase_lead_time_days",
          "purchase_moq",
          "purchase_pack_size",
          "commercial_remarks",
          "purchase_decision",
        ],
      },
      readOnlySections: {
        "Quality Review": [
          {
            title: __("Document Readiness"),
            fields: [
              "tds_attachment",
              "msds_attachment",
              "tc_coa_attachment",
              "document_readiness_remarks",
              "document_readiness_decision",
            ],
          },
          {
            title: __("Sample Readiness"),
            fields: [
              "sample_available",
              "sample_required",
              "sample_quantity_kg",
              "sample_received_by_quality",
              "sample_received_date",
            ],
          },
        ],
      },
      reviewStages: {
        technical_review: {
          stageStatus: "Technical Review",
          approveAction: "Technical Approve",
          approveLabel: __("Approve Technical Review"),
        },
        document_sample_readiness: {
          stageStatus: "Document & Sample Readiness",
          approveAction: "Document Readiness Complete",
          approveLabel: __("Mark Complete"),
        },
        quality_review: {
          stageStatus: "Quality Review",
          approveAction: "Quality Approve",
          approveLabel: __("Approve Quality Review"),
        },
        purchase_review: {
          stageStatus: "Purchase Review",
          approveAction: "Purchase Approve",
          approveLabel: __("Approve Purchase Review"),
        },
      },
      erpFields: ["created_item", "created_planning_parameter", "created_supplier_matrix", "creation_log"],
      erpStageKeys: ["erp_item_creation", "rm_planning_parameter", "supplier_approval_matrix", "completed"],
    },
    "New Supplier Request": {
      title: __("Supplier Approval Request Tracker"),
      subtitle: __("Approval and governance outputs journey for supplier onboarding"),
      method: "calco_erp.calco_purchase.master_data_governance_journey.get_new_supplier_request_tracker",
      titleField: "supplier_name",
      sectionField: "journey_section",
      anchorField: "supplier_section",
      summarySections: [
        {
          title: __("Request Summary"),
          fields: [
            "supplier_source",
            "supplier_name",
            "proposed_supplier_name",
            "supplier_type",
            "payment_terms",
            "lead_time_days",
            "supplier_rating",
            "effective_date",
            "expiry_date",
          ],
        },
        {
          title: __("Requested RM Coverage"),
          fields: ["supplier_request_items"],
        },
      ],
      requestFields: [
        "supplier_source",
        "supplier_name",
        "proposed_supplier_name",
        "supplier_type",
        "supplier_origin",
        "msme_status",
        "tax_tds_declaration_required",
        "nda_required",
        "code_of_conduct_required",
        "payment_terms",
        "lead_time_days",
        "supplier_rating",
        "effective_date",
        "expiry_date",
        "supplier_request_items",
      ],
      hiddenFields: [
        "quality_review_section",
        "certificates_checked",
        "quality_audit_required",
        "supplier_quality_remarks",
        "supplier_quality_decision",
        "purchase_review_section",
        "supplier_purchase_lead_time",
        "supplier_purchase_moq",
        "supplier_purchase_pack_size",
        "supplier_purchase_payment_terms",
        "default_currency",
        "incoterm",
        "freight_delivery_terms",
        "commercial_terms",
        "supplier_purchase_remarks",
        "supplier_purchase_decision",
        "supplier_master_data_section",
        "proposed_address_line1",
        "proposed_address_line2",
        "proposed_city",
        "proposed_state",
        "proposed_country",
        "proposed_pincode",
        "primary_contact_name",
        "primary_contact_email",
        "primary_contact_mobile",
        "bank_account_name",
        "bank",
        "bank_account_no",
        "bank_branch_code",
        "supplier_documents_section",
        "supplier_documents",
        "supplier_rm_documents_section",
        "supplier_rm_documents",
        "management_review_section",
        "strategic_supplier",
        "risk_remarks",
        "final_approval_decision",
      ],
      stageFields: {
        "Quality Review": [
          "certificates_checked",
          "quality_audit_required",
          "supplier_quality_remarks",
          "supplier_quality_decision",
          "supplier_rm_documents",
        ],
        "Purchase Review": [
          "supplier_purchase_lead_time",
          "supplier_purchase_moq",
          "supplier_purchase_pack_size",
          "supplier_purchase_payment_terms",
          "default_currency",
          "incoterm",
          "freight_delivery_terms",
          "commercial_terms",
          "supplier_purchase_remarks",
          "supplier_purchase_decision",
          "supplier_origin",
          "msme_status",
          "tax_tds_declaration_required",
          "nda_required",
          "code_of_conduct_required",
          "proposed_address_line1",
          "proposed_address_line2",
          "proposed_city",
          "proposed_state",
          "proposed_country",
          "proposed_pincode",
          "primary_contact_name",
          "primary_contact_email",
          "primary_contact_mobile",
          "bank_account_name",
          "bank",
          "bank_account_no",
          "bank_branch_code",
          "supplier_documents",
          "supplier_rm_documents",
        ],
        "Management Review": [
          "strategic_supplier",
          "risk_remarks",
          "final_approval_decision",
          "supplier_documents",
          "supplier_rm_documents",
        ],
      },
      stageReadOnlyFields: {
        "Purchase Review": ["supplier_rm_documents"],
        "Management Review": ["supplier_documents", "supplier_rm_documents"],
      },
      reviewStages: {
        quality_review: {
          stageStatus: "Quality Review",
          approveAction: "Quality Approve",
          approveLabel: __("Approve Quality Review"),
        },
        purchase_review: {
          stageStatus: "Purchase Review",
          approveAction: "Purchase Approve",
          approveLabel: __("Approve Purchase Review"),
        },
        management_review: {
          stageStatus: "Management Review",
          approveAction: "Management Approve",
          approveLabel: __("Approve Management Review"),
        },
      },
      erpFields: ["created_supplier", "created_matrix_rows", "created_planning_parameters", "creation_log"],
      erpStageKeys: ["supplier_master_creation", "supplier_approval_matrix", "rm_planning_link", "completed"],
    },
  };

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }

    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .calco-governance-journey {
        padding: 16px;
        border: 1px solid var(--border-color);
        border-radius: 18px;
        background:
          radial-gradient(circle at top right, rgba(21, 101, 192, 0.08), transparent 30%),
          linear-gradient(180deg, rgba(248, 250, 252, 0.98), rgba(255, 255, 255, 1));
      }
      .calco-governance-journey__header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 14px;
      }
      .calco-governance-journey__title {
        font-size: 16px;
        font-weight: 700;
      }
      .calco-governance-journey__subtitle {
        font-size: 12px;
        color: var(--text-muted);
      }
      .calco-governance-journey__tag {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.05);
        padding: 5px 10px;
        font-size: 11px;
        font-weight: 700;
      }
      .calco-governance-journey__flow {
        display: flex;
        align-items: stretch;
        gap: 12px;
        overflow-x: auto;
        padding-bottom: 4px;
      }
      .calco-governance-journey__stage {
        position: relative;
        min-width: 210px;
        border: 1px solid var(--border-color);
        border-radius: 14px;
        background: var(--fg-color);
        padding: 14px 16px;
        text-align: left;
        transition: border-color 0.2s ease, transform 0.2s ease;
        box-shadow: inset 0 4px 0 var(--stage-color, #94a3b8);
      }
      .calco-governance-journey__stage:hover {
        border-color: var(--stage-color, var(--primary));
        transform: translateY(-1px);
      }
      .calco-governance-journey__stage-label {
        font-size: 13px;
        font-weight: 700;
        margin-bottom: 8px;
      }
      .calco-governance-journey__stage-status {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        margin-bottom: 8px;
        background: var(--stage-bg, rgba(148, 163, 184, 0.18));
        color: var(--stage-text, #334155);
      }
      .calco-governance-journey__stage-role {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        margin-bottom: 8px;
      }
      .calco-governance-journey__stage-summary {
        font-size: 12px;
        line-height: 1.45;
        color: var(--text-muted);
        min-height: 52px;
      }
      .calco-governance-journey__stage-docs {
        font-size: 11px;
        color: var(--text-muted);
        margin-top: 10px;
        margin-bottom: 10px;
      }
      .calco-governance-journey__stage-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
      }
      .calco-governance-journey__stage-button {
        border: 1px solid rgba(21, 101, 192, 0.16);
        background: rgba(21, 101, 192, 0.08);
        color: #0f4c91;
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
      }
      .calco-governance-journey__stage-button--ghost {
        border-color: var(--border-color);
        background: rgba(148, 163, 184, 0.08);
        color: #475569;
      }
      .calco-governance-journey__connector {
        flex: 0 0 28px;
        align-self: center;
        height: 2px;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(148, 163, 184, 0.6), rgba(148, 163, 184, 0.18));
      }
      .calco-governance-journey__placeholder,
      .calco-governance-journey__empty {
        padding: 18px 4px;
        color: var(--text-muted);
        font-size: 13px;
      }
      .calco-governance-journey__stage[data-color="grey"] {
        --stage-color: #94a3b8;
        --stage-bg: rgba(148, 163, 184, 0.16);
        --stage-text: #475569;
      }
      .calco-governance-journey__stage[data-color="blue"] {
        --stage-color: #1565c0;
        --stage-bg: rgba(21, 101, 192, 0.12);
        --stage-text: #0f4c91;
      }
      .calco-governance-journey__stage[data-color="green"] {
        --stage-color: #2e7d32;
        --stage-bg: rgba(46, 125, 50, 0.12);
        --stage-text: #1f5b24;
      }
      .calco-governance-journey__stage[data-color="red"] {
        --stage-color: #c62828;
        --stage-bg: rgba(198, 40, 40, 0.12);
        --stage-text: #8f1d1d;
      }
      .calco-governance-journey__stage[data-color="orange"] {
        --stage-color: #dd6b20;
        --stage-bg: rgba(221, 107, 32, 0.12);
        --stage-text: #9c4221;
      }
      .calco-governance-review__summary {
        display: grid;
        gap: 14px;
        margin-bottom: 16px;
      }
      .calco-governance-review__summary-card,
      .calco-governance-review__audit,
      .calco-governance-review__erp {
        border: 1px solid var(--border-color);
        border-radius: 14px;
        background: rgba(15, 23, 42, 0.03);
        padding: 14px;
      }
      .calco-governance-review__summary-title,
      .calco-governance-review__audit-title,
      .calco-governance-review__erp-title {
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        margin-bottom: 10px;
      }
      .calco-governance-review__summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 10px 14px;
      }
      .calco-governance-review__summary-item-label {
        font-size: 11px;
        color: var(--text-muted);
        margin-bottom: 4px;
      }
      .calco-governance-review__summary-item-value {
        font-size: 13px;
        line-height: 1.45;
        font-weight: 600;
        white-space: pre-wrap;
      }
      .calco-governance-review__table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
      }
      .calco-governance-review__table th,
      .calco-governance-review__table td {
        border-bottom: 1px solid var(--border-color);
        padding: 8px 10px;
        text-align: left;
        vertical-align: top;
      }
      .calco-governance-review__audit-list,
      .calco-governance-review__erp-list {
        display: grid;
        gap: 8px;
      }
      .calco-governance-review__audit-label,
      .calco-governance-review__erp-label {
        color: var(--text-muted);
        margin-right: 6px;
      }
      .calco-governance-review__section-focus {
        animation: calcoGovernanceSectionPulse 1.8s ease;
      }
      @keyframes calcoGovernanceSectionPulse {
        0% {
          box-shadow: 0 0 0 0 rgba(21, 101, 192, 0.28);
        }
        100% {
          box-shadow: 0 0 0 14px rgba(21, 101, 192, 0);
        }
      }
      @media (max-width: 767px) {
        .calco-governance-journey {
          padding: 14px;
        }
        .calco-governance-journey__stage {
          min-width: 176px;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function getConfig(doctype) {
    return CONFIG[doctype];
  }

  function prepareWrapper(frm, wrapper) {
    if (!wrapper || !wrapper.length) {
      return wrapper;
    }

    const wrapperId = {
      "New RM Request": RM_TRACKER_WRAPPER_ID,
      "New Supplier Request": SUPPLIER_TRACKER_WRAPPER_ID,
    }[frm.doctype];
    if (!wrapperId) {
      return wrapper;
    }

    const root = $(frm.wrapper || document.body);
    wrapper.attr("id", wrapperId);
    wrapper.attr("data-governance-wrapper", frappe.scrub(frm.doctype));
    root
      .find(`#${wrapperId}, .${FALLBACK_CLASS}, .${LEGACY_HOST_CLASS}`)
      .not(wrapper)
      .remove();
    return wrapper;
  }

  function getWrapper(frm) {
    const config = getConfig(frm.doctype);
    const htmlWrapper = frm.fields_dict[HTML_FIELD] && frm.fields_dict[HTML_FIELD].$wrapper;
    if (htmlWrapper && htmlWrapper.length && htmlWrapper.is(":visible")) {
      return prepareWrapper(frm, htmlWrapper);
    }

    const sectionWrapper = config && config.sectionField && frm.fields_dict[config.sectionField] && frm.fields_dict[config.sectionField].$wrapper
      ? $(frm.fields_dict[config.sectionField].$wrapper)
      : null;
    const anchorWrapper = config && config.anchorField && frm.fields_dict[config.anchorField] && frm.fields_dict[config.anchorField].$wrapper
      ? $(frm.fields_dict[config.anchorField].$wrapper)
      : null;

    let wrapper = null;
    if (anchorWrapper && anchorWrapper.length) {
      wrapper = anchorWrapper.prev(`.${FALLBACK_CLASS}`);
      if (!wrapper.length) {
        wrapper = $(`<div class="${FALLBACK_CLASS}" data-doctype="${frappe.utils.escape_html(frm.doctype)}"></div>`);
        wrapper.insertBefore(anchorWrapper);
      }
      return prepareWrapper(frm, wrapper);
    }

    if (sectionWrapper && sectionWrapper.length) {
      wrapper = sectionWrapper.siblings(`.${FALLBACK_CLASS}`);
      if (!wrapper.length) {
        wrapper = $(`<div class="${FALLBACK_CLASS}" data-doctype="${frappe.utils.escape_html(frm.doctype)}"></div>`);
        wrapper.insertAfter(sectionWrapper);
      }
      return prepareWrapper(frm, wrapper);
    }

    return prepareWrapper(frm, htmlWrapper || null);
  }

  function isUnsavedDocument(frm) {
    return !!(frm && (frm.is_new?.() || frm.doc?.__islocal));
  }

  function getUnsavedMessage(frm) {
    return UNSAVED_MESSAGES[frm && frm.doctype] || __("Please save this document first.");
  }

  function isTemporaryDocumentName(name) {
    return typeof name === "string" && name.startsWith("new-");
  }

  function getRequestSupplierField(frm) {
    return frm && REQUEST_SUPPLIER_FIELDS[frm.doctype];
  }

  function applyExistingSupplierOnly(frm) {
    const fieldname = getRequestSupplierField(frm);
    if (!fieldname || !frm.fields_dict[fieldname]) {
      return;
    }

    const field = frm.fields_dict[fieldname];
    field.df.only_select = true;
    field.df.get_query = () => ({
      filters: {
        disabled: 0,
      },
      only_select: true,
    });
    frm.set_query(fieldname, () => ({
      filters: {
        disabled: 0,
      },
      only_select: true,
    }));

    if (field.$wrapper && field.$wrapper.length) {
      field.$wrapper.attr("data-calco-existing-supplier-only", "1");
    }
    if (field.refresh) {
      field.refresh();
    }
  }

  function applySupplierSourceUI(frm) {
    if (frm.doctype !== "New Supplier Request" || !frm.fields_dict.supplier_source) {
      return;
    }

    const proposed = frm.doc.supplier_source === "Proposed Supplier";
    const outputStage = ["ERP Creation", "Completed"].includes(frm.doc.status);
    frm.toggle_display("proposed_supplier_name", proposed);
    frm.toggle_reqd("proposed_supplier_name", proposed && !frm.doc.supplier_name);
    frm.toggle_display("supplier_name", !proposed || outputStage);
    frm.toggle_reqd("supplier_name", !proposed);
    frm.set_df_property("supplier_name", "read_only", proposed && !outputStage ? 1 : frm.doc.status === "Completed" ? 1 : 0);
    frm.set_df_property("proposed_supplier_name", "read_only", frm.doc.status === "Completed" ? 1 : 0);
  }

  async function validateExistingSupplierOnly(frm) {
    const fieldname = getRequestSupplierField(frm);
    const supplier = fieldname && frm.doc[fieldname];
    if (!fieldname || !supplier) {
      return;
    }

    const response = await frappe.db.get_value("Supplier", supplier, "name");
    if (response && response.message && response.message.name) {
      return;
    }

    frappe.msgprint({
      title: __("Supplier Onboarding Required"),
      message: SUPPLIER_CREATION_BLOCK_MESSAGE,
      indicator: "orange",
    });
    await frm.set_value(fieldname, "");
  }

  function showSaveFirstMessage(frm) {
    frappe.msgprint({
      title: __("Document Not Ready"),
      message: getUnsavedMessage(frm),
      indicator: "orange",
    });
  }

  function openTrackedDocument(frm, doctype, name) {
    if (!doctype || !name) {
      showSaveFirstMessage(frm);
      return;
    }

    if (isTemporaryDocumentName(String(name))) {
      showSaveFirstMessage(frm);
      return;
    }

    frappe.set_route("Form", doctype, name);
  }

  function escapeHtml(value) {
    return frappe.utils.escape_html(String(value || ""));
  }

  function getFieldDefinition(frm, fieldname) {
    return (frm.fields_dict[fieldname] && frm.fields_dict[fieldname].df)
      || frappe.meta.get_docfield(frm.doctype, fieldname, frm.doc.name)
      || null;
  }

  function getFieldLabel(frm, fieldname) {
    const df = getFieldDefinition(frm, fieldname);
    return (df && df.label) || fieldname;
  }

  function formatValue(frm, fieldname, value) {
    if (value === null || value === undefined || value === "") {
      return __("Not set");
    }

    if (fieldname === "supplier_request_items") {
      const rows = Array.isArray(value) ? value : [];
      if (!rows.length) {
        return __("No requested RM rows.");
      }
      const body = rows.map((row) => `
        <tr>
          <td>${escapeHtml(row.item_code || row.proposed_rm_code || "-")}</td>
          <td>${escapeHtml(row.approval_status || "Approved")}</td>
          <td>${escapeHtml(row.lead_time || "-")}</td>
          <td>${escapeHtml(row.payment_terms || "-")}</td>
        </tr>
      `).join("");
      return `
        <table class="calco-governance-review__table">
          <thead>
            <tr>
              <th>${__("Item / Proposed RM")}</th>
              <th>${__("Approval Status")}</th>
              <th>${__("Lead Time")}</th>
              <th>${__("Payment Terms")}</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      `;
    }

    const df = getFieldDefinition(frm, fieldname);
    if (df && df.fieldtype === "Attach" && value) {
      const href = escapeHtml(value);
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${href}</a>`;
    }
    if (df && df.fieldtype === "Date" && value) {
      return escapeHtml(frappe.datetime.str_to_user(value));
    }

    if (df && ["Small Text", "Text", "Long Text"].includes(df.fieldtype)) {
      return escapeHtml(value).replace(/\n/g, "<br>");
    }

    return escapeHtml(value);
  }

  function getPurchaseReviewFieldFilters(doctype, stageStatus) {
    if (doctype === "New RM Request" && stageStatus === "Purchase Review") {
      return [
        "purchase_lead_time_days",
        "purchase_moq",
        "purchase_pack_size",
        "commercial_remarks",
        "purchase_decision",
      ];
    }
    return null;
  }

  function getHiddenSummaryFields(doctype, stageStatus) {
    if (doctype === "New RM Request" && stageStatus === "Purchase Review") {
      return ["preferred_supplier"];
    }
    return [];
  }

  function renderSummaryHtml(frm, stageStatus) {
    const config = getConfig(frm.doctype);
    const hiddenFields = getHiddenSummaryFields(frm.doctype, stageStatus);
    return (config.summarySections || []).map((section) => {
      const sectionFields = (section.fields || []).filter((fieldname) => !hiddenFields.includes(fieldname));
      if (!sectionFields.length) {
        return "";
      }
      const content = sectionFields.map((fieldname) => `
        <div>
          <div class="calco-governance-review__summary-item-label">${escapeHtml(getFieldLabel(frm, fieldname))}</div>
          <div class="calco-governance-review__summary-item-value">${formatValue(frm, fieldname, frm.doc[fieldname])}</div>
        </div>
      `).join("");

      return `
        <div class="calco-governance-review__summary-card">
          <div class="calco-governance-review__summary-title">${escapeHtml(section.title)}</div>
          <div class="calco-governance-review__summary-grid">${content}</div>
        </div>
      `;
    }).join("");
  }

  function renderReadOnlySectionsHtml(frm, stageStatus) {
    const config = getConfig(frm.doctype);
    const sections = ((config.readOnlySections || {})[stageStatus]) || [];
    return sections.map((section) => {
      const content = (section.fields || []).map((fieldname) => `
        <div>
          <div class="calco-governance-review__summary-item-label">${escapeHtml(getFieldLabel(frm, fieldname))}</div>
          <div class="calco-governance-review__summary-item-value">${formatValue(frm, fieldname, frm.doc[fieldname])}</div>
        </div>
      `).join("");

      return `
        <div class="calco-governance-review__summary-card">
          <div class="calco-governance-review__summary-title">${escapeHtml(section.title)}</div>
          <div class="calco-governance-review__summary-grid">${content}</div>
        </div>
      `;
    }).join("");
  }

  function renderAuditHtml(stage) {
    const audit = stage.audit || {};
    const items = audit.items || [];
    if (!audit.reviewed_by && !audit.reviewed_on && !audit.decision && !items.length) {
      return "";
    }

    const metaItems = [
      audit.reviewed_by ? `<div><span class="calco-governance-review__audit-label">${__("Reviewed By")}:</span>${escapeHtml(audit.reviewed_by)}</div>` : "",
      audit.reviewed_on ? `<div><span class="calco-governance-review__audit-label">${__("Reviewed On")}:</span>${escapeHtml(audit.reviewed_on)}</div>` : "",
      audit.decision ? `<div><span class="calco-governance-review__audit-label">${__("Decision")}:</span>${escapeHtml(audit.decision)}</div>` : "",
    ].filter(Boolean).join("");

    const checklist = items.map((item) => `
      <div><span class="calco-governance-review__audit-label">${escapeHtml(item.label)}:</span>${escapeHtml(item.value)}</div>
    `).join("");

    return `
      <div class="calco-governance-review__audit">
        <div class="calco-governance-review__audit-title">${__("Review Audit")}</div>
        <div class="calco-governance-review__audit-list">
          ${metaItems}
          ${checklist}
        </div>
      </div>
    `;
  }

  function renderErpHtml(frm, stage) {
    const config = getConfig(frm.doctype);
    const rows = (config.erpFields || []).map((fieldname) => {
      const value = frm.doc[fieldname];
      return `
        <div>
          <span class="calco-governance-review__erp-label">${escapeHtml(getFieldLabel(frm, fieldname))}:</span>
          ${formatValue(frm, fieldname, value)}
        </div>
      `;
    }).join("");

    const documents = (stage.documents || []).map((doc) => `
      <div>
        <span class="calco-governance-review__erp-label">${escapeHtml(doc.doctype)}:</span>
        <a href="#" class="calco-governance-doc-link" data-doctype="${escapeHtml(doc.doctype)}" data-name="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</a>
      </div>
    `).join("");

    return `
      <div class="calco-governance-review__erp">
        <div class="calco-governance-review__erp-title">${escapeHtml(stage.label)}</div>
        <div class="calco-governance-review__erp-list">
          ${rows}
          ${documents || `<div>${__("No linked records available yet.")}</div>`}
        </div>
      </div>
    `;
  }

  function renderState(frm, message) {
    const wrapper = getWrapper(frm);
    const config = getConfig(frm.doctype);
    if (!wrapper || !config) {
      return;
    }

    wrapper.empty();
    wrapper.html(`
      <section class="calco-governance-journey">
        <div class="calco-governance-journey__header">
          <div>
            <div class="calco-governance-journey__title">${escapeHtml(config.title)}</div>
            <div class="calco-governance-journey__subtitle">${escapeHtml(config.subtitle)}</div>
          </div>
          <div class="calco-governance-journey__tag">${__("Focused reviews")}</div>
        </div>
        <div class="calco-governance-journey__placeholder">${escapeHtml(message)}</div>
      </section>
    `);
  }

  function renderVisibleTrackerError(frm, message, detail) {
    const wrapper = getWrapper(frm);
    const config = getConfig(frm.doctype);
    if (!wrapper || !config) {
      return;
    }

    wrapper.html(`
      <section class="calco-governance-journey">
        <div class="calco-governance-journey__header">
          <div>
            <div class="calco-governance-journey__title">${escapeHtml(config.title)}</div>
            <div class="calco-governance-journey__subtitle">${escapeHtml(config.subtitle)}</div>
          </div>
          <div class="calco-governance-journey__tag">${__("Focused reviews")}</div>
        </div>
        <div class="calco-governance-journey__stage" data-color="orange">
          <div class="calco-governance-journey__stage-label">${__("Tracker Error")}</div>
          <div class="calco-governance-journey__stage-status">${__("Attention Needed")}</div>
          <div class="calco-governance-journey__stage-summary">${escapeHtml(message)}</div>
          <div class="calco-governance-journey__stage-docs">${escapeHtml(detail || __("No additional detail available."))}</div>
        </div>
      </section>
    `);
  }

  function getActionLabel(stage, frm) {
    const config = getConfig(frm.doctype);
    if ((config.erpStageKeys || []).includes(stage.key)) {
      return __("Open");
    }
    if ((config.reviewStages || {})[stage.key]) {
      return stage.status === "In Progress" ? __("Review") : __("Open");
    }
    return __("Details");
  }

  function normalizeStage(stage, index) {
    const row = stage || {};
    return {
      key: row.key || `stage_${index + 1}`,
      label: row.label || __("Unnamed Stage"),
      status: row.status || __("Unknown"),
      owner_role: row.owner_role || __("Owner not set"),
      summary: row.summary || "",
      color: row.color || "grey",
      documents: Array.isArray(row.documents) ? row.documents : [],
      audit: row.audit || {},
      message: row.message || "",
    };
  }

  function buildStageHtml(frm, stage, index, stageCount) {
    try {
      const connector = index < stageCount - 1 ? `<div class="calco-governance-journey__connector"></div>` : "";
      const docCount = (stage.documents || []).length;
      const stageActions = isUnsavedDocument(frm)
        ? ""
        : `
          <div class="calco-governance-journey__stage-actions">
            <button
              type="button"
              class="calco-governance-journey__stage-button"
              data-action-stage-key="${escapeHtml(stage.key)}"
            >
              ${escapeHtml(getActionLabel(stage, frm))}
            </button>
            <button
              type="button"
              class="calco-governance-journey__stage-button calco-governance-journey__stage-button--ghost"
              data-detail-stage-key="${escapeHtml(stage.key)}"
            >
              ${__("Details")}
            </button>
          </div>
        `;
      return `
        <div
          class="calco-governance-journey__stage"
          data-stage-key="${escapeHtml(stage.key)}"
          data-color="${escapeHtml(stage.color || "grey")}"
        >
          <div class="calco-governance-journey__stage-label">${escapeHtml(stage.label)}</div>
          <div class="calco-governance-journey__stage-status">${escapeHtml(stage.status)}</div>
          <div class="calco-governance-journey__stage-role">${escapeHtml(stage.owner_role || __("Owner not set"))}</div>
          <div class="calco-governance-journey__stage-summary">${escapeHtml(stage.summary || "")}</div>
          <div class="calco-governance-journey__stage-docs">
            ${docCount ? __("{0} linked record(s)", [docCount]) : __("No linked records yet")}
          </div>
          ${stageActions}
        </div>
        ${connector}
      `;
    } catch (error) {
      console.error("Governance tracker stage render failed", { stage, error });
      return `
        <div class="calco-governance-journey__stage" data-color="orange">
          <div class="calco-governance-journey__stage-label">${escapeHtml(stage.label || __("Stage Render Error"))}</div>
          <div class="calco-governance-journey__stage-status">${__("Error")}</div>
          <div class="calco-governance-journey__stage-summary">${escapeHtml(error.message || __("Unable to render this stage."))}</div>
        </div>
      `;
    }
  }

  function buildFlowHtml(frm, stages) {
    if (!Array.isArray(stages) || !stages.length) {
      return `<div class="calco-governance-journey__empty">${__("No stages available.")}</div>`;
    }

    return stages
      .map((stage, index) => buildStageHtml(frm, normalizeStage(stage, index), index, stages.length))
      .join("");
  }

  function getStage(frm, stageKey) {
    return ((frm.__governance_journey_data || {}).stages || []).find((row) => row.key === stageKey);
  }

  function toggleFieldList(frm, fields, show) {
    (fields || []).forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) {
        frm.toggle_display(fieldname, show);
      }
    });
  }

  function syncMainFormState(frm) {
    const config = getConfig(frm.doctype);
    if (!config) {
      return;
    }

    const isDraft = (frm.doc.status || "Draft") === "Draft";
    (config.requestFields || []).forEach((fieldname) => {
      if (frm.fields_dict[fieldname]) {
        frm.set_df_property(fieldname, "read_only", isDraft ? 0 : 1);
      }
    });

    Object.entries(config.stageFields || {}).forEach(([stageStatus, fieldnames]) => {
      const isActiveStage = (frm.doc.status || "") === stageStatus;
      fieldnames.forEach((fieldname) => {
        if (frm.fields_dict[fieldname]) {
          frm.set_df_property(fieldname, "read_only", isActiveStage ? 0 : 1);
          frm.set_df_property(fieldname, "reqd", 0);
        }
      });
    });

    toggleFieldList(frm, config.hiddenFields, false);

    if (frm.doctype === "New Supplier Request") {
      const editableTableByStage = {
        supplier_documents: "Purchase Review",
        supplier_rm_documents: "Quality Review",
      };
      Object.entries(editableTableByStage).forEach(([fieldname, stage]) => {
        const field = frm.fields_dict[fieldname];
        if (!field) {
          return;
        }
        frm.set_df_property(fieldname, "read_only", frm.doc.status === stage ? 0 : 1);
        if (field.grid) {
          field.grid.cannot_add_rows = true;
          field.grid.cannot_delete_rows = true;
          field.grid.refresh();
        }
      });
    }
  }

  function getDialogTitle(frm, stage) {
    const config = getConfig(frm.doctype);
    const keyValue =
      frm.doc[(config && config.titleField) || "name"]
      || (frm.doctype === "New Supplier Request" ? frm.doc.proposed_supplier_name : "")
      || frm.doc.name;
    return `${stage.label} - ${keyValue}`;
  }

  function getStageRequiredFields(stage) {
    return Array.isArray(stage && stage.required_fields) ? stage.required_fields : [];
  }

  function qualityParameterFlag(value) {
    return Number(value || 0) === 1;
  }

  function normalizeQualityParameterRow(row) {
    return {
      specification: row.specification || "",
      value: row.value || "",
      numeric: qualityParameterFlag(row.numeric) ? 1 : 0,
      min_value: row.min_value === "" || row.min_value == null ? null : Number(row.min_value),
      max_value: row.max_value === "" || row.max_value == null ? null : Number(row.max_value),
      formula_based_criteria: qualityParameterFlag(row.formula_based_criteria) ? 1 : 0,
      acceptance_formula: row.acceptance_formula || "",
    };
  }

  function refreshQualityParameterMode(control) {
    const row = control.doc || {};
    const gridRow = control.grid_row;
    const numeric = qualityParameterFlag(row.numeric);
    const formulaBased = qualityParameterFlag(row.formula_based_criteria);

    row.numeric = numeric ? 1 : 0;
    row.formula_based_criteria = formulaBased ? 1 : 0;

    if (control.df.fieldname === "numeric") {
      if (numeric) {
        row.value = "";
      } else {
        row.min_value = null;
        row.max_value = null;
      }
    }

    if (!gridRow) {
      return;
    }
    gridRow.toggle_editable("value", !numeric && !formulaBased);
    gridRow.toggle_editable("min_value", numeric && !formulaBased);
    gridRow.toggle_editable("max_value", numeric && !formulaBased);
    gridRow.toggle_display("acceptance_formula", formulaBased);
    gridRow.toggle_editable("acceptance_formula", formulaBased);
    ["value", "min_value", "max_value", "acceptance_formula"].forEach((fieldname) => {
      gridRow.refresh_field(fieldname);
    });
  }

  function getQualityParameterDialogFields(readOnly) {
    const childMeta = frappe.get_meta(QUALITY_PARAMETER_DOCTYPE);
    const childFields = (childMeta && childMeta.fields) || [];
    const gridColumns = {
      specification: 2,
      value: 2,
      numeric: 1,
      min_value: 2,
      max_value: 2,
    };

    return QUALITY_PARAMETER_FIELDNAMES.map((fieldname) => {
      const source = childFields.find((field) => field.fieldname === fieldname);
      if (!source) {
        return null;
      }

      const field = {
        fieldname: source.fieldname,
        fieldtype: source.fieldtype,
        label: fieldname === "value" ? __("Specification") : source.label,
        options: source.options,
        default: ["numeric", "formula_based_criteria"].includes(fieldname)
          ? Number(source.default || 0)
          : source.default,
        precision: source.precision,
        description: source.description,
        fetch_from: source.fetch_from,
        reqd: source.reqd,
        in_list_view: QUALITY_PARAMETER_LIST_FIELDS.has(fieldname) ? 1 : 0,
        columns: gridColumns[fieldname] || 0,
        read_only: readOnly || source.read_only ? 1 : 0,
      };

      if (fieldname === "specification") {
        field.only_select = 1;
      }

      if (fieldname === "acceptance_formula") {
        field.depends_on = (doc) => qualityParameterFlag(doc.formula_based_criteria);
      }

      if (!readOnly) {
        if (fieldname === "value") {
          field.read_only_depends_on = (doc) =>
            qualityParameterFlag(doc.numeric) || qualityParameterFlag(doc.formula_based_criteria);
        } else if (["min_value", "max_value"].includes(fieldname)) {
          field.read_only_depends_on = (doc) =>
            !qualityParameterFlag(doc.numeric) || qualityParameterFlag(doc.formula_based_criteria);
        } else if (fieldname === "acceptance_formula") {
          field.read_only_depends_on = (doc) =>
            !qualityParameterFlag(doc.formula_based_criteria);
        }

        if (["numeric", "formula_based_criteria"].includes(fieldname)) {
          field.change = function () {
            refreshQualityParameterMode(this);
          };
        }
      }
      return field;
    }).filter(Boolean);
  }

  function getQualityParameterDialogData(frm, fieldname) {
    return (frm.doc[fieldname] || []).map((row) => normalizeQualityParameterRow(row));
  }

  function normalizeGovernanceDocumentRow(row) {
    const normalized = {};
    GOVERNANCE_DOCUMENT_FIELDNAMES.forEach((fieldname) => {
      normalized[fieldname] = row[fieldname] ?? "";
    });
    normalized.applicable = Number(row.applicable || 0);
    normalized.available = Number(row.available || 0);
    return normalized;
  }

  function getGovernanceDocumentDialogFields(readOnly) {
    const childMeta = frappe.get_meta(GOVERNANCE_DOCUMENT_DOCTYPE);
    const childFields = (childMeta && childMeta.fields) || [];
    const gridColumns = {
      document_type: 2,
      rm_reference: 1,
      applicable: 1,
      attachment: 1,
      document_number: 2,
      issue_date: 1,
      expiry_date: 1,
      verification_status: 1,
    };
    return GOVERNANCE_DOCUMENT_FIELDNAMES.map((fieldname) => {
      const source = childFields.find((field) => field.fieldname === fieldname);
      if (!source) {
        return null;
      }
      return {
        fieldname,
        fieldtype: source.fieldtype,
        label: source.label,
        options: source.options,
        default: source.default,
        description: source.description,
        hidden: source.hidden ? 1 : 0,
        reqd: source.reqd ? 1 : 0,
        in_list_view: GOVERNANCE_DOCUMENT_LIST_FIELDS.has(fieldname) ? 1 : 0,
        columns: gridColumns[fieldname] || 0,
        read_only: readOnly || GOVERNANCE_DOCUMENT_IMMUTABLE_FIELDS.has(fieldname) ? 1 : 0,
      };
    }).filter(Boolean);
  }

  function getGovernanceDocumentDialogData(frm, fieldname) {
    return (frm.doc[fieldname] || []).map((row) => normalizeGovernanceDocumentRow(row));
  }

  function buildDialogField(frm, fieldname, readOnly, requiredFields = []) {
    const required = !readOnly && requiredFields.includes(fieldname);
    const df = getFieldDefinition(frm, fieldname);
    const dialogField = {
      fieldtype: (df && df.fieldtype) || "Data",
      fieldname,
      label: getFieldLabel(frm, fieldname),
      options: df && df.options,
      precision: df && df.precision,
      read_only: readOnly ? 1 : 0,
      reqd: required ? 1 : 0,
    };

    if (dialogField.fieldtype === "Table" && dialogField.options === QUALITY_PARAMETER_DOCTYPE) {
      dialogField.fields = getQualityParameterDialogFields(readOnly);
      dialogField.data = getQualityParameterDialogData(frm, fieldname);
      dialogField.cannot_add_rows = readOnly ? 1 : 0;
      dialogField.cannot_delete_rows = readOnly ? 1 : 0;
    } else if (dialogField.fieldtype === "Table" && dialogField.options === GOVERNANCE_DOCUMENT_DOCTYPE) {
      dialogField.fields = getGovernanceDocumentDialogFields(readOnly);
      dialogField.data = getGovernanceDocumentDialogData(frm, fieldname);
      dialogField.cannot_add_rows = 1;
      dialogField.cannot_delete_rows = 1;
    }
    return dialogField;
  }

  function getDialogFieldValue(dialog, fieldname) {
    const field = dialog.fields_dict[fieldname];
    if (!field || field.df.fieldtype !== "Table") {
      return dialog.get_value(fieldname);
    }

    const rows = (field.grid.get_data() || []).filter(Boolean);
    if (field.df.options === QUALITY_PARAMETER_DOCTYPE) {
      return rows.map((row) => normalizeQualityParameterRow(row));
    }
    if (field.df.options === GOVERNANCE_DOCUMENT_DOCTYPE) {
      return rows.map((row) => normalizeGovernanceDocumentRow(row));
    }
    return rows;
  }

  function getStageReadOnlyFields(config, stageStatus) {
    return new Set(((config.stageReadOnlyFields || {})[stageStatus]) || []);
  }

  function buildStageDialogFields(frm, config, stageStatus, stageFields, readOnly, requiredFields) {
    const stageReadOnlyFields = getStageReadOnlyFields(config, stageStatus);
    const fields = [];
    stageFields.forEach((fieldname) => {
      if (["supplier_documents", "supplier_rm_documents"].includes(fieldname)) {
        fields.push({
          fieldtype: "Section Break",
          label: fieldname === "supplier_documents" ? __("Supplier Documents") : __("Supplier-RM Documents"),
        });
      }
      fields.push(
        buildDialogField(
          frm,
          fieldname,
          readOnly || stageReadOnlyFields.has(fieldname),
          requiredFields,
        ),
      );
    });
    return fields;
  }

  function collectDialogValues(dialog, fieldnames) {
    const values = {};
    (fieldnames || []).forEach((fieldname) => {
      values[fieldname] = getDialogFieldValue(dialog, fieldname);
    });
    return values;
  }

  async function submitStageReview(frm, dialog, stage, action) {
    const config = getConfig(frm.doctype);
    const stageConfig = (config.reviewStages || {})[stage.key];
    const explicitStageFields = getPurchaseReviewFieldFilters(frm.doctype, stageConfig.stageStatus);
    const fieldnames = explicitStageFields || (config.stageFields || {})[stageConfig.stageStatus] || [];
    const stageReadOnlyFields = getStageReadOnlyFields(config, stageConfig.stageStatus);
    const values = collectDialogValues(
      dialog,
      fieldnames.filter((fieldname) => !stageReadOnlyFields.has(fieldname)),
    );

    const response = await frappe.call({
      method: "calco_erp.calco_purchase.master_data_governance_journey.save_stage_review",
      args: {
        doctype: frm.doctype,
        name: frm.doc.name,
        values,
        action: action || "",
      },
      freeze: true,
      freeze_message: action ? __("Applying review action...") : __("Saving review..."),
    });

    if (
      Object.prototype.hasOwnProperty.call(values, "incoming_inspection_parameters") &&
      response.message &&
      response.message.incoming_inspection_parameter_count !==
        values.incoming_inspection_parameters.length
    ) {
      frappe.throw(__("Incoming inspection parameters were not persisted. Please retry."));
    }
    const governanceDocumentCounts = {
      supplier_documents: "supplier_document_count",
      supplier_rm_documents: "supplier_rm_document_count",
    };
    Object.entries(governanceDocumentCounts).forEach(([fieldname, responseField]) => {
      if (
        Object.prototype.hasOwnProperty.call(values, fieldname)
        && response.message
        && response.message[responseField] !== values[fieldname].length
      ) {
        frappe.throw(__("Supplier governance documents were not persisted. Please retry."));
      }
    });

    dialog.hide();
    await frm.reload_doc();
    frappe.show_alert({
      message: action ? __("Review updated and workflow action applied.") : __("Review saved."),
      indicator: "green",
    });
  }

  function addDialogButtons(frm, dialog, stage) {
    const config = getConfig(frm.doctype);
    const stageConfig = (config.reviewStages || {})[stage.key];
    const readOnly = stage.status !== "In Progress";

    dialog.set_primary_action(readOnly ? __("Close") : __("Save Review"), async () => {
      if (readOnly) {
        dialog.hide();
        return;
      }
      await submitStageReview(frm, dialog, stage, "");
    });

    if (readOnly) {
      return;
    }

    const footer = dialog.$wrapper.find(".modal-footer");
    if (frm.doctype === "New RM Request" && stage.key === "purchase_review") {
      const supplierRequestButton = $(
        `<button type="button" class="btn btn-secondary btn-sm">${escapeHtml(
          __("Open / Create Supplier Approval Request"),
        )}</button>`,
      );
      footer.append(supplierRequestButton);
      supplierRequestButton.on("click", () => {
        openSupplierRequestFromRm(frm);
      });
    }

    const approveButton = $(`<button type="button" class="btn btn-primary btn-sm">${escapeHtml(stageConfig.approveLabel)}</button>`);
    const rejectButton = $(`<button type="button" class="btn btn-danger btn-sm">${__("Reject")}</button>`);

    footer.prepend(rejectButton);
    footer.prepend(approveButton);

    approveButton.on("click", async () => {
      await submitStageReview(frm, dialog, stage, stageConfig.approveAction);
    });
    rejectButton.on("click", async () => {
      await submitStageReview(frm, dialog, stage, "Reject");
    });
  }

  async function openReviewDialog(frm, stageKey) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }
    const stage = getStage(frm, stageKey);
    const config = getConfig(frm.doctype);
    const stageConfig = stage && (config.reviewStages || {})[stage.key];
    if (!stage || !stageConfig) {
      return;
    }

    if (
      frm.doctype === "New Supplier Request"
      && stageConfig.stageStatus === "Purchase Review"
    ) {
      await frappe.call({
        method: "calco_erp.calco_purchase.master_data_governance_journey.ensure_supplier_document_checklist",
        args: { name: frm.doc.name },
        freeze: true,
        freeze_message: __("Preparing supplier document checklist..."),
      });
      await frm.reload_doc();
    }

    const readOnly = stage.status !== "In Progress";
    const explicitStageFields = getPurchaseReviewFieldFilters(frm.doctype, stageConfig.stageStatus);
    const stageFields = explicitStageFields || (config.stageFields || {})[stageConfig.stageStatus] || [];
    const stageRequiredFields = getStageRequiredFields(stage);
    const isPurchaseReview =
      frm.doctype === "New RM Request" && stageConfig.stageStatus === "Purchase Review";
    const dialog = new frappe.ui.Dialog({
      title: getDialogTitle(frm, stage),
      fields: [
        { fieldtype: "HTML", fieldname: "summary_html" },
        ...buildStageDialogFields(
          frm,
          config,
          stageConfig.stageStatus,
          stageFields,
          readOnly,
          stageRequiredFields,
        ),
        { fieldtype: "HTML", fieldname: "audit_html" },
      ],
      size: "large",
    });

    dialog.show();
    dialog.fields_dict.summary_html.$wrapper.html(renderSummaryHtml(frm, stageConfig.stageStatus));
    dialog.fields_dict.summary_html.$wrapper.append(renderReadOnlySectionsHtml(frm, stageConfig.stageStatus));
    dialog.fields_dict.audit_html.$wrapper.html(renderAuditHtml(stage));

    stageFields.forEach((fieldname) => {
      const field = dialog.fields_dict[fieldname];
      if (!field || field.df.fieldtype === "Table") {
        return;
      }
      dialog.set_value(fieldname, frm.doc[fieldname]);
    });

    addDialogButtons(frm, dialog, stage);
  }

  function openErpDialog(frm, stageKey) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }
    const stage = getStage(frm, stageKey);
    if (!stage) {
      return;
    }

    const dialog = new frappe.ui.Dialog({
      title: getDialogTitle(frm, stage),
      fields: [{ fieldtype: "HTML", fieldname: "content" }],
      size: "large",
    });
    dialog.show();
    dialog.fields_dict.content.$wrapper.html(`
      ${renderSummaryHtml(frm)}
      ${renderErpHtml(frm, stage)}
    `);
    dialog.$wrapper.on("click", ".calco-governance-doc-link", (event) => {
      event.preventDefault();
      const target = event.currentTarget;
      openTrackedDocument(frm, target.dataset.doctype, target.dataset.name);
      dialog.hide();
    });
  }

  function buildDocumentsHtml(documents) {
    if (!documents.length) {
      return `<div class="calco-governance-journey__empty">${__("No linked documents found.")}</div>`;
    }

    return documents.map((doc) => `
      <div class="calco-governance-review__summary-card">
        <div class="calco-governance-review__summary-grid">
          <div>
            <div class="calco-governance-review__summary-item-label">${escapeHtml(doc.doctype)}</div>
            <div class="calco-governance-review__summary-item-value">
              <a href="#" class="calco-governance-doc-link" data-doctype="${escapeHtml(doc.doctype)}" data-name="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</a>
            </div>
          </div>
          <div>
            <div class="calco-governance-review__summary-item-label">${__("Status")}</div>
            <div class="calco-governance-review__summary-item-value">${escapeHtml(doc.status || __("Open"))}</div>
          </div>
          <div>
            <div class="calco-governance-review__summary-item-label">${__("Detail")}</div>
            <div class="calco-governance-review__summary-item-value">${escapeHtml(doc.detail || __("No extra details"))}</div>
          </div>
        </div>
      </div>
    `).join("");
  }

  function openStageDialog(frm, stageKey) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }
    const stage = getStage(frm, stageKey);
    if (!stage) {
      return;
    }

    const dialog = new frappe.ui.Dialog({
      title: getDialogTitle(frm, stage),
      fields: [{ fieldtype: "HTML", fieldname: "content" }],
      size: "large",
    });
    dialog.show();

    dialog.fields_dict.content.$wrapper.html(`
      ${renderSummaryHtml(frm)}
      ${renderAuditHtml(stage)}
      ${buildDocumentsHtml(stage.documents || [])}
    `);

    dialog.$wrapper.on("click", ".calco-governance-doc-link", (event) => {
      event.preventDefault();
      const target = event.currentTarget;
      openTrackedDocument(frm, target.dataset.doctype, target.dataset.name);
      dialog.hide();
    });
  }

  function showBlockedStageMessage(stage) {
    const message = stage.status === "Stopped"
      ? __("Request rejected. No further action allowed.")
      : __("This stage will open after the previous review is approved.");
    frappe.msgprint(message);
  }

  function openStageFromDetails(frm, stageKey) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }
    const stage = getStage(frm, stageKey);
    const config = getConfig(frm.doctype);
    if (!stage || !config) {
      return;
    }

    if (["Not Started", "Stopped"].includes(stage.status)) {
      showBlockedStageMessage(stage);
      return;
    }

    if ((config.reviewStages || {})[stage.key]) {
      if (stage.status === "Completed" || stage.status === "Rejected") {
        openStageDialog(frm, stageKey);
        return;
      }
      openReviewDialog(frm, stageKey);
      return;
    }

    if ((config.erpStageKeys || []).includes(stage.key)) {
      openErpDialog(frm, stageKey);
      return;
    }

    openStageDialog(frm, stageKey);
  }

  function executeStageAction(frm, stageKey) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }
    const stage = getStage(frm, stageKey);
    const config = getConfig(frm.doctype);
    if (!stage || !config) {
      return;
    }

    if ((config.reviewStages || {})[stage.key]) {
      if (["Not Started", "Stopped"].includes(stage.status)) {
        showBlockedStageMessage(stage);
        return;
      }
      openReviewDialog(frm, stageKey);
      return;
    }

    if ((config.erpStageKeys || []).includes(stage.key)) {
      if (["Not Started", "Stopped"].includes(stage.status)) {
        showBlockedStageMessage(stage);
        return;
      }
      openErpDialog(frm, stageKey);
      return;
    }

    openStageDialog(frm, stageKey);
  }

  function getSupplierRequestPrefillPayload(frm) {
    return {
      __create_from_rm_request: 1,
      source_rm_request: frm.doc.name,
      source_rm_code: frm.doc.rm_code || "",
      source_rm_name: frm.doc.rm_name || "",
      source_category: frm.doc.category || "",
      source_stock_uom: frm.doc.stock_uom || "",
      expected_purchase_rate: frm.doc.purchase_target_rate || 0,
      expected_lead_time_days: frm.doc.purchase_lead_time_days || 0,
      expected_moq: frm.doc.purchase_moq || 0,
      expected_purchase_pack_size: frm.doc.purchase_pack_size || 0,
      commercial_remarks: frm.doc.commercial_remarks || "",
      supplier_request_items: [
        {
          item_code: frm.doc.created_item || "",
          proposed_rm_code: frm.doc.created_item ? "" : frm.doc.rm_code || "",
          linked_rm_request: frm.doc.created_item ? "" : frm.doc.name,
          approval_status: "Approved",
          lead_time: frm.doc.purchase_lead_time_days || 0,
          payment_terms: "",
          supplier_rating: 0,
          effective_date: "",
          expiry_date: "",
        },
      ],
    };
  }

  async function getLinkedSupplierRequestName(frm) {
    if (frm.doc.supplier_request) {
      return frm.doc.supplier_request;
    }
    if (frm.__linked_supplier_request) {
      return frm.__linked_supplier_request;
    }
    const response = await frappe.db.get_value(
      "New Supplier Request",
      { source_rm_request: frm.doc.name },
      "name",
    );
    const name = response?.message?.name || "";
    frm.__linked_supplier_request = name;
    return name;
  }

  async function openSupplierRequestFromRm(frm) {
    if (isUnsavedDocument(frm)) {
      showSaveFirstMessage(frm);
      return;
    }

    const linkedSupplierRequest = await getLinkedSupplierRequestName(frm);
    if (linkedSupplierRequest) {
      frappe.set_route("Form", "New Supplier Request", linkedSupplierRequest);
      return;
    }

    const payload = getSupplierRequestPrefillPayload(frm);
    if (!payload.source_rm_code) {
      frappe.msgprint({
        title: __("Create Supplier Approval Request"),
        message: __("RM Code is required before creating a Supplier Approval Request."),
        indicator: "orange",
      });
      return;
    }

    const { supplier_request_items, __create_from_rm_request, ...routeOptions } = payload;
    pendingSupplierRequestPrefill = {
      ...payload,
      supplier_request_items: normalizeSupplierRequestItemsOption(supplier_request_items),
    };
    frappe.new_doc("New Supplier Request", routeOptions);
  }

  function normalizeSupplierRequestItemsOption(itemsOption) {
    if (!itemsOption) {
      return [];
    }
    if (Array.isArray(itemsOption)) {
      return itemsOption;
    }
    if (typeof itemsOption === "string") {
      try {
        const parsed = frappe.utils.parse_json(itemsOption);
        return Array.isArray(parsed) ? parsed : [];
      } catch (error) {
        console.warn("Unable to parse supplier request route items", {
          route_items: itemsOption,
          error,
        });
      }
    }
    return [];
  }

  function populateSupplierRequestFromRoute(frm) {
    const pendingMatchesDocument =
      pendingSupplierRequestPrefill &&
      frm.doc.source_rm_request === pendingSupplierRequestPrefill.source_rm_request;
    const options = pendingMatchesDocument ? pendingSupplierRequestPrefill : frappe.route_options;
    const fromRmRequest = !!options?.__create_from_rm_request;
    const existingRmProposedSupplier =
      options?.supplier_source === "Proposed Supplier"
      && !!options?.source_rm_code
      && !options?.source_rm_request;
    if (!options || (!fromRmRequest && !existingRmProposedSupplier) || !frm.is_new()) {
      if (pendingSupplierRequestPrefill && !pendingMatchesDocument) {
        pendingSupplierRequestPrefill = null;
      }
      return;
    }

    if (existingRmProposedSupplier) {
      [
        ["supplier_source", "Proposed Supplier"],
        ["proposed_supplier_name", options.proposed_supplier_name],
        ["supplier_type", options.supplier_type],
        ["supplier_name", ""],
        ["source_rm_code", options.source_rm_code],
      ].forEach(([fieldname, value]) => {
        if (frm.fields_dict[fieldname]) {
          frm.set_value(fieldname, value ?? "");
        }
      });

      if (
        frm.fields_dict.supplier_request_items
        && !frm.doc.supplier_request_items?.length
      ) {
        frm.add_child("supplier_request_items", {
          item_code: options.source_rm_code,
          proposed_rm_code: "",
          linked_rm_request: "",
          approval_status: "Approved",
        });
        frm.refresh_field("supplier_request_items");
      }

      frappe.route_options = {};
      applySupplierSourceUI(frm);
      return;
    }

    const fields = [
      ["source_rm_request", options.source_rm_request],
      ["source_rm_code", options.source_rm_code],
      ["source_rm_name", options.source_rm_name],
      ["source_category", options.source_category],
      ["source_stock_uom", options.source_stock_uom],
      ["expected_purchase_rate", options.expected_purchase_rate || 0],
      ["expected_lead_time_days", options.expected_lead_time_days || 0],
      ["expected_moq", options.expected_moq || 0],
      ["expected_purchase_pack_size", options.expected_purchase_pack_size || 0],
      ["commercial_remarks", options.commercial_remarks],
    ];

    fields.forEach(([fieldname, value]) => {
      if (frm.fields_dict[fieldname]) {
        frm.set_value(fieldname, value ?? "");
      }
    });

    const rows = normalizeSupplierRequestItemsOption(options.supplier_request_items);
    if (rows.length && frm.fields_dict.supplier_request_items && !frm.doc.supplier_request_items?.length) {
      rows.forEach((row) => {
        if (!row || (!row.item_code && !row.proposed_rm_code)) {
          return;
        }
        frm.add_child("supplier_request_items", {
          item_code: row.item_code || "",
          proposed_rm_code: row.proposed_rm_code || "",
          linked_rm_request: row.linked_rm_request || "",
          approval_status: row.approval_status || "Approved",
          supplier_rating: row.supplier_rating || 0,
          lead_time: row.lead_time || 0,
          payment_terms: row.payment_terms || "",
          effective_date: row.effective_date || "",
          expiry_date: row.expiry_date || "",
        });
      });
      frm.refresh_field("supplier_request_items");
    }

    pendingSupplierRequestPrefill = null;
    frappe.route_options = {};
  }

  function renderTracker(frm, data) {
    const wrapper = getWrapper(frm);
    const config = getConfig(frm.doctype);
    if (!wrapper || !config) {
      return;
    }
    if (isUnsavedDocument(frm)) {
      renderState(frm, getUnsavedMessage(frm));
      return;
    }

    wrapper.empty();
    wrapper.html(`
      <section class="calco-governance-journey">
        <div class="calco-governance-journey__header">
          <div>
            <div class="calco-governance-journey__title">${escapeHtml(config.title)}</div>
            <div class="calco-governance-journey__subtitle">${escapeHtml(config.subtitle)}</div>
          </div>
          <div class="calco-governance-journey__tag">${__("Focused reviews")}</div>
        </div>
        <div class="calco-governance-journey__flow">${buildFlowHtml(frm, data.stages || [])}</div>
      </section>
    `);

    wrapper.find("[data-action-stage-key]").on("click", (event) => {
      event.stopPropagation();
      executeStageAction(frm, event.currentTarget.dataset.actionStageKey);
    });
    wrapper.find("[data-detail-stage-key]").on("click", (event) => {
      event.stopPropagation();
      openStageFromDetails(frm, event.currentTarget.dataset.detailStageKey);
    });
    wrapper.find("[data-stage-key]").on("click", (event) => {
      const target = event.target;
      if (target.closest("[data-action-stage-key]") || target.closest("[data-detail-stage-key]")) {
        return;
      }
      executeStageAction(frm, event.currentTarget.dataset.stageKey);
    });
  }

  function scheduleTrackerRetry(frm) {
    frm.__governance_tracker_retry_count = (frm.__governance_tracker_retry_count || 0) + 1;
    if (frm.__governance_tracker_retry_count > MAX_RENDER_RETRIES) {
      console.error("Governance tracker wrapper was not available after retries", {
        doctype: frm.doctype,
        name: frm.doc.name,
      });
      frappe.show_alert({
        message: __("Journey tracker could not mount on the form. Please refresh the page."),
        indicator: "orange",
      });
      return;
    }

    setTimeout(() => loadTracker(frm), 250);
  }

  async function loadTracker(frm) {
    ensureStyle();
    applyExistingSupplierOnly(frm);
    syncMainFormState(frm);
    const config = getConfig(frm.doctype);
    const wrapper = getWrapper(frm);
    if (!config) {
      return;
    }
    if (!wrapper) {
      scheduleTrackerRetry(frm);
      return;
    }

    frm.__governance_tracker_retry_count = 0;

    if (frm.is_new()) {
      renderState(frm, getUnsavedMessage(frm));
      return;
    }

    renderState(frm, __("Loading governance journey..."));

    try {
      if (isUnsavedDocument(frm)) {
        renderState(frm, getUnsavedMessage(frm));
        return;
      }
      const response = await frappe.call({
        method: config.method,
        args: { name: frm.doc.name },
        freeze: false,
      });
      frm.__governance_journey_data = response.message || {};
      if (!Array.isArray(frm.__governance_journey_data.stages)) {
        renderVisibleTrackerError(
          frm,
          __("The journey payload did not include a valid stages list."),
          JSON.stringify({
            status: frm.__governance_journey_data.overall_status || "",
            keys: Object.keys(frm.__governance_journey_data || {}),
          }),
        );
        return;
      }
      renderTracker(frm, frm.__governance_journey_data);
    } catch (error) {
      console.error("Governance tracker load failed", error);
      renderVisibleTrackerError(
        frm,
        error.message || __("Unable to load the governance journey right now."),
        error.exc_type || error.name || "",
      );
    }
  }

  window.calco_erp.master_data_governance.loadTracker = loadTracker;
  window.calco_erp.master_data_governance.syncMainFormState = syncMainFormState;
  window.renderMasterDataJourney = async function renderMasterDataJourney(frm, payload) {
    ensureStyle();
    applyExistingSupplierOnly(frm);
    syncMainFormState(frm);
    if (payload) {
      frm.__governance_journey_data = payload;
      renderTracker(frm, payload);
      return payload;
    }
    await loadTracker(frm);
    return frm.__governance_journey_data || {};
  };
  window.syncMasterDataJourneyState = syncMainFormState;

  frappe.ui.form.on("New RM Request", {
    refresh(frm) {
      applyExistingSupplierOnly(frm);
      loadTracker(frm);
    },
    onload_post_render(frm) {
      applyExistingSupplierOnly(frm);
      loadTracker(frm);
    },
    preferred_supplier(frm) {
      validateExistingSupplierOnly(frm);
    },
    status(frm) {
      syncMainFormState(frm);
    },
  });

  frappe.ui.form.on("New Supplier Request", {
    refresh(frm) {
      applyExistingSupplierOnly(frm);
      applySupplierSourceUI(frm);
      loadTracker(frm);
    },
    onload_post_render(frm) {
      applyExistingSupplierOnly(frm);
      populateSupplierRequestFromRoute(frm);
      applySupplierSourceUI(frm);
    },
    status(frm) {
      applySupplierSourceUI(frm);
      syncMainFormState(frm);
    },
    supplier_source(frm) {
      if (frm.doc.supplier_source === "Existing Supplier" && frm.doc.proposed_supplier_name) {
        frm.set_value("proposed_supplier_name", "");
      }
      if (
        frm.doc.supplier_source === "Proposed Supplier"
        && frm.doc.supplier_name
        && !["ERP Creation", "Completed"].includes(frm.doc.status)
      ) {
        frm.set_value("supplier_name", "");
      }
      applySupplierSourceUI(frm);
    },
    supplier_name(frm) {
      validateExistingSupplierOnly(frm);
      if (!frm.doc.supplier_name) {
        return;
      }

      frappe.db.get_value("Supplier", frm.doc.supplier_name, ["payment_terms"]).then((response) => {
        const values = response && response.message;
        if (!values) {
          return;
        }
        if (values.payment_terms && !frm.doc.payment_terms) {
          frm.set_value("payment_terms", values.payment_terms);
        }
      });
    },
  });
})();
