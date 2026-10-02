from __future__ import annotations

import frappe


RM_QUALITY_TEMPLATE = "Calco Incoming RM QC"
RM_REQUEST_DOCTYPE = "New RM Request"
SUPPLIER_REQUEST_DOCTYPE = "New Supplier Request"
RM_REQUEST_WORKFLOW = "New RM Request Workflow"
SUPPLIER_REQUEST_WORKFLOW = "New Supplier Request Workflow"
TECHNICAL_ROLE = "Technical User"
MANAGEMENT_ROLE = "Management Reviewer"
RM_REQUEST_CLIENT_SCRIPT = "New RM Request Journey Tracker"
SUPPLIER_REQUEST_CLIENT_SCRIPT = "New Supplier Request Journey Tracker"
SUPPLIER_SOURCE_EXISTING = "Existing Supplier"
SUPPLIER_SOURCE_PROPOSED = "Proposed Supplier"
RESOLUTION_PENDING = "Pending"
RESOLUTION_RESOLVED = "Resolved"
RESOLUTION_MANUAL_REVIEW = "Manual Review"

SUPPLIER_IDENTITY_FIELDS = {
    "tax_id": ("tax_id", "proposed_supplier_tax_id", "supplier_tax_id"),
    "gstin": ("gstin", "proposed_supplier_gstin", "supplier_gstin"),
    "pan": ("pan", "pan_no", "proposed_supplier_pan", "supplier_pan"),
    "pan_no": ("pan_no", "pan", "proposed_supplier_pan", "supplier_pan"),
}

WORKFLOW_STYLES = {
    "Draft": "Info",
    "Technical Review": "Primary",
    "Document & Sample Readiness": "Primary",
    "Quality Review": "Warning",
    "Purchase Review": "Info",
    "Management Review": "Primary",
    "ERP Creation": "Success",
    "Completed": "Success",
    "Rejected": "Danger",
}


def normalize_identity_value(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def get_supplier_source(doc) -> str:
    source = (doc.get("supplier_source") or "").strip()
    if source in {SUPPLIER_SOURCE_EXISTING, SUPPLIER_SOURCE_PROPOSED}:
        return source
    if doc.get("supplier_name") or doc.get("created_supplier"):
        return SUPPLIER_SOURCE_EXISTING
    if doc.get("proposed_supplier_name"):
        return SUPPLIER_SOURCE_PROPOSED
    return SUPPLIER_SOURCE_EXISTING


def get_supplier_duplicate_candidates(doc) -> list[str]:
    if get_supplier_source(doc) != SUPPLIER_SOURCE_PROPOSED:
        return []

    candidates: set[str] = set()
    proposed_name = normalize_identity_value(doc.get("proposed_supplier_name"))
    if proposed_name:
        rows = frappe.db.sql(
            """
            select name
            from tabSupplier
            where lower(trim(ifnull(name, ''))) = %(supplier_name)s
               or lower(trim(ifnull(supplier_name, ''))) = %(supplier_name)s
            """,
            {"supplier_name": proposed_name},
            as_dict=True,
        )
        candidates.update(row.name for row in rows)

    request_meta = frappe.get_meta(SUPPLIER_REQUEST_DOCTYPE)
    supplier_meta = frappe.get_meta("Supplier")
    for supplier_field, request_fields in SUPPLIER_IDENTITY_FIELDS.items():
        if not supplier_meta.has_field(supplier_field):
            continue
        request_value = next(
            (
                doc.get(fieldname)
                for fieldname in request_fields
                if request_meta.has_field(fieldname) and doc.get(fieldname)
            ),
            None,
        )
        normalized_value = normalize_identity_value(request_value)
        if not normalized_value:
            continue
        rows = frappe.get_all(
            "Supplier",
            filters={supplier_field: request_value},
            pluck="name",
            limit_page_length=20,
        )
        candidates.update(rows)

    return sorted(candidates)


def get_supplier_request_resolution_context(doc) -> dict[str, object]:
    supplier = doc.get("created_supplier") or doc.get("supplier_name") or ""
    supplier_resolved = bool(supplier and frappe.db.exists("Supplier", supplier))
    candidates = [] if supplier_resolved else get_supplier_duplicate_candidates(doc)
    supplier_status = RESOLUTION_RESOLVED if supplier_resolved else RESOLUTION_PENDING
    if len(candidates) > 1:
        supplier_status = RESOLUTION_MANUAL_REVIEW

    rows = doc.get("supplier_request_items") or []
    if not rows and doc.get("name") and frappe.db.exists(SUPPLIER_REQUEST_DOCTYPE, doc.get("name")):
        fields = ["item_code"]
        child_meta = frappe.get_meta("Supplier Request Item")
        if child_meta.has_field("proposed_rm_code"):
            fields.append("proposed_rm_code")
        if child_meta.has_field("linked_rm_request"):
            fields.append("linked_rm_request")
        rows = frappe.get_all(
            "Supplier Request Item",
            filters={"parent": doc.get("name")},
            fields=fields,
            order_by="idx asc",
        )

    item_resolved = bool(rows) and all(
        row.get("item_code") and frappe.db.exists("Item", row.get("item_code"))
        for row in rows
    )
    item_status = RESOLUTION_RESOLVED if item_resolved else RESOLUTION_PENDING

    if supplier_status == RESOLUTION_MANUAL_REVIEW:
        status = RESOLUTION_MANUAL_REVIEW
    elif supplier_resolved and item_resolved:
        status = RESOLUTION_RESOLVED
    else:
        status = RESOLUTION_PENDING

    return {
        "status": status,
        "item_status": item_status,
        "supplier_status": supplier_status,
        "supplier": supplier if supplier_resolved else "",
        "supplier_candidates": candidates,
    }


def ensure_master_data_governance_setup():
    ensure_roles()
    ensure_workflow_states()
    ensure_workflow_action_masters()
    ensure_rm_request_workflow()
    ensure_supplier_request_workflow()
    ensure_governance_journey_client_scripts()
    frappe.clear_cache()


def ensure_roles():
    for role_name in (TECHNICAL_ROLE, MANAGEMENT_ROLE):
        if frappe.db.exists("Role", role_name):
            continue
        frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


def ensure_workflow_states():
    for state_name, style in WORKFLOW_STYLES.items():
        name = frappe.db.get_value("Workflow State", {"workflow_state_name": state_name}, "name")
        if name:
            doc = frappe.get_doc("Workflow State", name)
        else:
            doc = frappe.new_doc("Workflow State")
            doc.workflow_state_name = state_name
        doc.style = style
        doc.save(ignore_permissions=True)


def ensure_workflow_action_masters():
    actions = {
        "Submit Request",
        "Technical Approve",
        "Document Readiness Complete",
        "Quality Approve",
        "Purchase Approve",
        "Management Approve",
        "Reject",
    }
    for action_name in actions:
        if frappe.db.exists("Workflow Action Master", action_name):
            continue
        frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action_name}).insert(
            ignore_permissions=True
        )


def ensure_rm_request_workflow():
    states = [
        {"state": "Draft", "doc_status": 0, "allow_edit": TECHNICAL_ROLE},
        {"state": "Technical Review", "doc_status": 0, "allow_edit": TECHNICAL_ROLE},
        {"state": "Document & Sample Readiness", "doc_status": 0, "allow_edit": TECHNICAL_ROLE},
        {"state": "Quality Review", "doc_status": 0, "allow_edit": "Quality Manager"},
        {"state": "Purchase Review", "doc_status": 0, "allow_edit": "Purchase Manager"},
        {"state": "ERP Creation", "doc_status": 0, "allow_edit": "System Manager"},
        {"state": "Completed", "doc_status": 0, "allow_edit": "System Manager"},
        {"state": "Rejected", "doc_status": 0, "allow_edit": "System Manager"},
    ]
    transitions = [
        {"state": "Draft", "action": "Submit Request", "next_state": "Technical Review", "allowed": TECHNICAL_ROLE},
        {
            "state": "Technical Review",
            "action": "Technical Approve",
            "next_state": "Document & Sample Readiness",
            "allowed": TECHNICAL_ROLE,
        },
        {"state": "Technical Review", "action": "Reject", "next_state": "Rejected", "allowed": TECHNICAL_ROLE},
        {
            "state": "Document & Sample Readiness",
            "action": "Document Readiness Complete",
            "next_state": "Quality Review",
            "allowed": TECHNICAL_ROLE,
        },
        {"state": "Document & Sample Readiness", "action": "Reject", "next_state": "Rejected", "allowed": TECHNICAL_ROLE},
        {
            "state": "Quality Review",
            "action": "Quality Approve",
            "next_state": "Purchase Review",
            "allowed": "Quality Manager",
        },
        {"state": "Quality Review", "action": "Reject", "next_state": "Rejected", "allowed": "Quality Manager"},
        {
            "state": "Purchase Review",
            "action": "Purchase Approve",
            "next_state": "ERP Creation",
            "allowed": "Purchase Manager",
        },
        {"state": "Purchase Review", "action": "Reject", "next_state": "Rejected", "allowed": "Purchase Manager"},
    ]
    ensure_workflow(
        workflow_name=RM_REQUEST_WORKFLOW,
        document_type=RM_REQUEST_DOCTYPE,
        states=states,
        transitions=transitions,
    )


def ensure_supplier_request_workflow():
    states = [
        {"state": "Draft", "doc_status": 0, "allow_edit": "Purchase Manager"},
        {"state": "Quality Review", "doc_status": 0, "allow_edit": "Quality Manager"},
        {"state": "Purchase Review", "doc_status": 0, "allow_edit": "Purchase Manager"},
        {"state": "Management Review", "doc_status": 0, "allow_edit": MANAGEMENT_ROLE},
        {"state": "ERP Creation", "doc_status": 0, "allow_edit": "System Manager"},
        {"state": "Completed", "doc_status": 0, "allow_edit": "System Manager"},
        {"state": "Rejected", "doc_status": 0, "allow_edit": "System Manager"},
    ]
    transitions = [
        {"state": "Draft", "action": "Submit Request", "next_state": "Quality Review", "allowed": "Purchase Manager"},
        {
            "state": "Quality Review",
            "action": "Quality Approve",
            "next_state": "Purchase Review",
            "allowed": "Quality Manager",
        },
        {"state": "Quality Review", "action": "Reject", "next_state": "Rejected", "allowed": "Quality Manager"},
        {
            "state": "Purchase Review",
            "action": "Purchase Approve",
            "next_state": "Management Review",
            "allowed": "Purchase Manager",
        },
        {"state": "Purchase Review", "action": "Reject", "next_state": "Rejected", "allowed": "Purchase Manager"},
        {
            "state": "Management Review",
            "action": "Management Approve",
            "next_state": "ERP Creation",
            "allowed": MANAGEMENT_ROLE,
        },
        {"state": "Management Review", "action": "Reject", "next_state": "Rejected", "allowed": MANAGEMENT_ROLE},
    ]
    ensure_workflow(
        workflow_name=SUPPLIER_REQUEST_WORKFLOW,
        document_type=SUPPLIER_REQUEST_DOCTYPE,
        states=states,
        transitions=transitions,
    )


def ensure_workflow(*, workflow_name: str, document_type: str, states: list[dict], transitions: list[dict]):
    existing_name = frappe.db.get_value("Workflow", {"document_type": document_type}, "name")
    if existing_name:
        workflow = frappe.get_doc("Workflow", existing_name)
    else:
        workflow = frappe.new_doc("Workflow")

    workflow.workflow_name = workflow_name
    workflow.document_type = document_type
    workflow.workflow_state_field = "status"
    workflow.override_status = 0
    workflow.send_email_alert = 0
    workflow.enable_action_confirmation = 1
    workflow.is_active = 0
    workflow.set("states", [])
    for state in states:
        workflow.append("states", state)
    workflow.set("transitions", [])
    for transition in transitions:
        workflow.append("transitions", transition)
    workflow.is_active = 1
    workflow.save(ignore_permissions=True)
    frappe.clear_cache(doctype=document_type)
    frappe.cache.hdel("workflow", document_type)


def normalize_request_code(value: str | None) -> str:
    return (value or "").strip().upper()


def create_or_update_planning_parameter(
    *,
    item_code: str,
    preferred_supplier: str | None = None,
    current_season: str | None = None,
    manual_lead_time_days=None,
    safety_days=None,
    review_period_days=None,
    minimum_order_qty=None,
    purchase_pack_size=None,
) -> str:
    if not item_code:
        return ""
    existing = frappe.db.exists("RM Planning Parameter", item_code)
    if existing:
        doc = frappe.get_doc("RM Planning Parameter", existing)
    else:
        doc = frappe.new_doc("RM Planning Parameter")
        doc.item_code = item_code
    if preferred_supplier:
        doc.preferred_supplier = preferred_supplier
    if current_season:
        doc.current_season = current_season
    if manual_lead_time_days is not None:
        doc.manual_lead_time_days = manual_lead_time_days
    if safety_days is not None:
        doc.safety_days = safety_days
    if review_period_days is not None:
        doc.review_period_days = review_period_days
    if minimum_order_qty is not None:
        doc.minimum_order_qty = minimum_order_qty
    if purchase_pack_size is not None:
        doc.purchase_pack_size = purchase_pack_size
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.name


def create_or_update_supplier_matrix_row(
    *,
    item_code: str,
    supplier: str,
    supplier_type: str,
    approval_status: str,
    supplier_rating=None,
    lead_time=None,
    payment_terms: str | None = None,
    effective_date=None,
    expiry_date=None,
) -> str:
    effective_date = effective_date or frappe.utils.today()
    existing = frappe.db.get_value(
        "Supplier Approval Matrix",
        {"item_code": item_code, "supplier": supplier},
        "name",
    )
    if existing:
        doc = frappe.get_doc("Supplier Approval Matrix", existing)
    else:
        doc = frappe.new_doc("Supplier Approval Matrix")
        doc.item_code = item_code
        doc.supplier = supplier
    doc.supplier_type = supplier_type
    doc.approval_status = approval_status
    if supplier_rating is not None:
        doc.supplier_rating = supplier_rating
    if lead_time is not None:
        doc.lead_time = lead_time
    if payment_terms:
        doc.payment_terms = payment_terms
    doc.effective_date = effective_date
    if expiry_date:
        doc.expiry_date = expiry_date
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.name


def get_default_supplier_type_for_supplier(supplier: str) -> str:
    if not supplier:
        return "Local"
    supplier_type = frappe.db.get_value(
        "Supplier Approval Matrix",
        {"supplier": supplier, "supplier_type": ("is", "set")},
        "supplier_type",
    )
    return supplier_type or "Local"


def ensure_governance_journey_client_scripts():
    ensure_client_script(RM_REQUEST_CLIENT_SCRIPT, RM_REQUEST_DOCTYPE)
    disable_client_script(SUPPLIER_REQUEST_CLIENT_SCRIPT)


def disable_client_script(name: str):
    existing = frappe.db.exists("Client Script", name)
    if existing:
        frappe.db.set_value("Client Script", existing, "enabled", 0, update_modified=False)


def ensure_client_script(name: str, dt: str):
    script = build_governance_tracker_script(dt)
    existing = frappe.db.exists("Client Script", name)
    if existing:
        doc = frappe.get_doc("Client Script", existing)
    else:
        doc = frappe.new_doc("Client Script")
        doc.name = name
        doc.dt = dt
        doc.view = "Form"
    doc.script = script
    doc.enabled = 1
    doc.dt = dt
    doc.view = "Form"
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)


def force_update_governance_client_script(name: str, dt: str):
    script = build_governance_tracker_script(dt)
    if not frappe.db.exists("Client Script", name):
        ensure_client_script(name, dt)

    frappe.db.set_value("Client Script", name, "dt", dt, update_modified=False)
    frappe.db.set_value("Client Script", name, "view", "Form", update_modified=False)
    frappe.db.set_value("Client Script", name, "enabled", 1, update_modified=False)
    frappe.db.set_value("Client Script", name, "script", script, update_modified=True)
    frappe.db.commit()


def build_governance_tracker_script(dt: str) -> str:
    return f"""
const GOVERNANCE_ASSET_PATH = "/assets/calco_erp/js/master_data_governance_journey.js";
const GOVERNANCE_HOST_CLASS = "calco-governance-journey-host";
const GOVERNANCE_METHOD = "{'calco_erp.calco_purchase.master_data_governance_journey.get_new_rm_request_tracker' if dt == RM_REQUEST_DOCTYPE else 'calco_erp.calco_purchase.master_data_governance_journey.get_new_supplier_request_tracker'}";
const UNSAVED_MESSAGE_MAP = {{
    "{RM_REQUEST_DOCTYPE}": __("Please save the RM Request first."),
    "{SUPPLIER_REQUEST_DOCTYPE}": __("Please save the Supplier Request first."),
}};

function findVisibleSectionNode(frm, titleText) {{
    const root = $(frm.wrapper || document.body);
    const candidates = root.find(".section-head, .form-section .section-head, .section-head .section-title, .section-label, .form-page .section-head");

    for (const node of candidates.toArray()) {{
        const element = $(node);
        const text = (element.text() || "").trim();
        if (!element.is(":visible") || text !== titleText) {{
            continue;
        }}

        const section = element.closest(".form-section");
        if (section.length) {{
            return section;
        }}

        const column = element.closest(".section-body, .frappe-control, .form-column, .row");
        if (column.length) {{
            return column;
        }}

        return element;
    }}

    return $();
}}

function getGovernanceDebugAnchor(frm) {{
    const root = $(frm.wrapper || document.body);
    let wrapper = root.find(`.${{GOVERNANCE_HOST_CLASS}}`).first();
    if (wrapper.length) {{
        return wrapper;
    }}

    const journeySection = findVisibleSectionNode(frm, "Request Journey");
    if (journeySection.length) {{
        wrapper = $(`<div class="${{GOVERNANCE_HOST_CLASS}}"></div>`);
        wrapper.insertAfter(journeySection);
        return wrapper;
    }}

    const requestSummarySection = findVisibleSectionNode(frm, "Request Summary");
    if (requestSummarySection.length) {{
        wrapper = $(`<div class="${{GOVERNANCE_HOST_CLASS}}"></div>`);
        wrapper.insertBefore(requestSummarySection);
        return wrapper;
    }}

    const formLayout = root.find(".form-layout, .layout-main-section, .page-form").first();
    if (formLayout.length) {{
        wrapper = $(`<div class="${{GOVERNANCE_HOST_CLASS}}"></div>`);
        formLayout.prepend(wrapper);
        return wrapper;
    }}

    return null;
}}

function clearGovernanceMount(frm) {{
    const wrapper = getGovernanceDebugAnchor(frm);
    if (!wrapper) {{
        return;
    }}
    wrapper.empty();
}}

function isUnsavedDocument(frm) {{
    return !!(frm && (frm.is_new?.() || frm.doc?.__islocal));
}}

function getUnsavedMessage(frm) {{
    return UNSAVED_MESSAGE_MAP[frm?.doctype] || __("Please save this document first.");
}}

function renderUnsavedState(wrapper, frm) {{
    if (!wrapper || !wrapper.length) {{
        return;
    }}
    wrapper.html(`<div style="font-size:12px; color: var(--text-muted);">${{frappe.utils.escape_html(getUnsavedMessage(frm))}}</div>`);
}}

function getGovernanceObject() {{
    return window.calco_erp && window.calco_erp.master_data_governance;
}}

function getGovernanceRenderFunction() {{
    return window.renderMasterDataJourney;
}}

function loadGovernanceAsset() {{
    return new Promise((resolve) => {{
        frappe.require(GOVERNANCE_ASSET_PATH, () => {{
            resolve(getGovernanceRenderFunction() || getGovernanceObject() || null);
        }});
    }});
}}

async function fetchJourneyPayload(frm) {{
    const response = await frappe.call({{
        method: GOVERNANCE_METHOD,
        args: {{ name: frm.doc.name }},
        freeze: false,
    }});
    return response.message || {{}};
}}

function renderStageCards(frm, payload) {{
    const wrapper = getGovernanceDebugAnchor(frm);
    if (!wrapper) {{
        return;
    }}

    const stages = Array.isArray(payload.stages) ? payload.stages : [];
    if (!stages.length) {{
        wrapper.html(`<div style="font-size:12px; color: var(--text-muted);">No stages available.</div>`);
        return;
    }}

    const colorMap = {{
        "Completed": "#2e7d32",
        "In Progress": "#1565c0",
        "Rejected": "#c62828",
        "Stopped": "#64748b",
        "Not Started": "#94a3b8",
    }};

    wrapper.html(`
        <div style="display:flex; gap:12px; overflow-x:auto; padding-bottom:4px;">
            ${{stages.map((stage) => `
                <div style="min-width:210px; border:1px solid var(--border-color); border-radius:14px; background: var(--fg-color); padding:14px 16px; box-shadow: inset 0 4px 0 ${{colorMap[stage.status] || '#94a3b8'}};">
                    <div style="font-size:13px; font-weight:700; margin-bottom:8px;">${{frappe.utils.escape_html(stage.label || '')}}</div>
                    <div style="display:inline-flex; padding:4px 8px; border-radius:999px; font-size:11px; font-weight:700; margin-bottom:8px; background: rgba(15,23,42,0.06);">${{frappe.utils.escape_html(stage.status || '')}}</div>
                    <div style="font-size:11px; text-transform:uppercase; color: var(--text-muted); margin-bottom:8px;">${{frappe.utils.escape_html(stage.owner_role || '')}}</div>
                    <div style="font-size:12px; line-height:1.45; color: var(--text-muted);">${{frappe.utils.escape_html(stage.summary || '')}}</div>
                </div>
            `).join('')}}
        </div>
    `);
}}

frappe.ui.form.on("{dt}", {{
    async refresh(frm) {{
        let governance = getGovernanceRenderFunction() || getGovernanceObject();
        clearGovernanceMount(frm);
        if (isUnsavedDocument(frm)) {{
            renderUnsavedState(getGovernanceDebugAnchor(frm), frm);
            return;
        }}

        if (!governance) {{
            governance = await loadGovernanceAsset();
        }}

        if (window.syncMasterDataJourneyState) {{
            window.syncMasterDataJourneyState(frm);
        }} else if (governance && governance.syncMainFormState) {{
            governance.syncMainFormState(frm);
        }}

        const payload = await fetchJourneyPayload(frm);
        frm.__governance_journey_data = payload || {{}};

        if (typeof window.renderMasterDataJourney === "function") {{
            await window.renderMasterDataJourney(frm, payload);
            return;
        }}

        renderStageCards(frm, payload || {{}});
    }},
    async onload_post_render(frm) {{
        const governance = getGovernanceRenderFunction() || getGovernanceObject() || await loadGovernanceAsset();
        if (typeof window.renderMasterDataJourney === "function" && frm.__governance_journey_data) {{
            await window.renderMasterDataJourney(frm, frm.__governance_journey_data);
        }}
    }},
    status(frm) {{
        const governance = getGovernanceObject();
        if (window.syncMasterDataJourneyState) {{
            window.syncMasterDataJourneyState(frm);
        }} else if (governance && governance.syncMainFormState) {{
            governance.syncMainFormState(frm);
        }}
    }},
}});
"""
