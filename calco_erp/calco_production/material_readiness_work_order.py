from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


MRC_LINK_FIELD = "custom_material_readiness_check"
MRC_STATUS_FIELD = "custom_material_readiness_status"
MRC_SECTION_FIELD = "custom_material_readiness_section"
MRC_HTML_FIELD = "custom_material_readiness_summary"
CLIENT_SCRIPT_NAME = "Work Order Material Readiness Navigator"


def ensure_work_order_material_readiness_setup():
    ensure_custom_fields()
    disable_legacy_client_script()
    frappe.clear_cache()


def ensure_custom_fields():
    create_custom_fields(
        {
            "Work Order": [
                {
                    "fieldname": MRC_SECTION_FIELD,
                    "label": "Material Readiness",
                    "fieldtype": "Section Break",
                    "insert_after": "required_items",
                },
                {
                    "fieldname": MRC_STATUS_FIELD,
                    "label": "Material Readiness Status",
                    "fieldtype": "Data",
                    "insert_after": MRC_SECTION_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": MRC_LINK_FIELD,
                    "label": "Linked Material Readiness Check",
                    "fieldtype": "Link",
                    "options": "Material Readiness Check",
                    "insert_after": MRC_STATUS_FIELD,
                    "read_only": 1,
                },
                {
                    "fieldname": MRC_HTML_FIELD,
                    "label": "Material Readiness Summary",
                    "fieldtype": "HTML",
                    "insert_after": MRC_LINK_FIELD,
                },
            ]
        },
        update=True,
    )


def ensure_client_script():
    if not frappe.db.exists("DocType", "Client Script"):
        return

    script = """
(function () {
  const STATUS_FIELD = "custom_material_readiness_status";
  const LINK_FIELD = "custom_material_readiness_check";
  const HTML_FIELD = "custom_material_readiness_summary";
  const FALLBACK_CLASS = "calco-work-order-mrc-summary";

  function getSummaryWrapper(frm) {
    if (frm.fields_dict[HTML_FIELD] && frm.fields_dict[HTML_FIELD].$wrapper && frm.fields_dict[HTML_FIELD].$wrapper.is(":visible")) {
      return frm.fields_dict[HTML_FIELD].$wrapper;
    }

    const gridWrapper = frm.fields_dict.required_items && frm.fields_dict.required_items.grid && frm.fields_dict.required_items.grid.wrapper
      ? $(frm.fields_dict.required_items.grid.wrapper)
      : null;
    if (!gridWrapper || !gridWrapper.length) {
      return null;
    }

    let wrapper = gridWrapper.siblings(`.${FALLBACK_CLASS}`);
    if (!wrapper.length) {
      wrapper = $(`<div class="${FALLBACK_CLASS}" style="margin-top:16px;"></div>`);
      wrapper.insertAfter(gridWrapper);
    }
    return wrapper;
  }

  function setSummary(frm, context) {
    const wrapper = getSummaryWrapper(frm);
    if (!wrapper) {
      return;
    }

    const badgeColor = context.ready_status === "Ready"
      ? "#2e7d32"
      : context.linked_check
        ? "#ef6c00"
        : "#c62828";

    const linkedHtml = context.linked_check
      ? `<div style="margin-top:8px;"><a href="#" class="calco-open-mrc">${frappe.utils.escape_html(context.linked_check)}</a></div>`
      : `<div style="margin-top:8px; color:var(--text-muted);">${__("No Material Readiness Check linked yet.")}</div>`;

    const shortageHtml = context.shortage_summary
      ? `<div style="margin-top:10px; font-size:12px; white-space:pre-wrap; color:var(--text-muted);">${frappe.utils.escape_html(context.shortage_summary)}</div>`
      : "";

    wrapper.html(`
      <div style="padding:12px 14px; border:1px solid var(--border-color); border-radius:12px; background:rgba(248,250,252,0.9);">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <span style="display:inline-flex; padding:4px 8px; border-radius:999px; font-size:11px; font-weight:700; color:white; background:${badgeColor};">
            ${frappe.utils.escape_html(context.ready_status || "Not Started")}
          </span>
          <span style="font-size:12px; color:var(--text-muted);">${frappe.utils.escape_html(context.message || "")}</span>
        </div>
        ${linkedHtml}
        ${shortageHtml}
      </div>
    `);

    wrapper.find(".calco-open-mrc").on("click", (event) => {
      event.preventDefault();
      if (context.linked_check) {
        frappe.set_route("Form", "Material Readiness Check", context.linked_check);
      }
    });
  }

  async function refreshReadiness(frm) {
    if (frm.is_new()) {
      frm.set_intro(__("Save the Work Order to create Material Readiness Check."), "blue");
      frm.set_value(STATUS_FIELD, "");
      frm.set_value(LINK_FIELD, "");
      setSummary(frm, { ready_status: "Not Started", message: __("Save the Work Order to create Material Readiness Check.") });
      return;
    }

    const response = await frappe.call({
      method: "calco_erp.calco_production.material_readiness_work_order.get_work_order_material_readiness_context",
      args: { work_order: frm.doc.name },
      freeze: false,
    });

    const context = response.message || {};
    frm.__material_readiness_context = context;
    await frm.set_value(STATUS_FIELD, context.ready_status || "");
    await frm.set_value(LINK_FIELD, context.linked_check || "");
    setSummary(frm, context);

    if (context.ready_status === "Ready") {
      frm.set_intro(__("Material Readiness Check {0} is Ready.", [context.linked_check || "-"]), "green");
    } else if (context.linked_check) {
      frm.set_intro(__("Material Readiness Check {0} is currently {1}.", [context.linked_check, context.ready_status || __("Draft")]), "orange");
    } else {
      frm.set_intro(__("No Material Readiness Check exists yet for this Work Order."), "red");
    }

    frm.remove_custom_button(__("Create Material Readiness Check"), __("Material Readiness"));
    frm.remove_custom_button(__("Open Material Readiness Check"), __("Material Readiness"));

    if (context.linked_check) {
      frm.add_custom_button(__("Open Material Readiness Check"), () => {
        frappe.set_route("Form", "Material Readiness Check", context.linked_check);
      }, __("Material Readiness"));
    } else {
      frm.add_custom_button(__("Create Material Readiness Check"), async () => {
        const createResponse = await frappe.call({
          method: "calco_erp.calco_production.material_readiness_work_order.create_material_readiness_check",
          args: { work_order: frm.doc.name },
          freeze: true,
        });
        const doc = createResponse.message || {};
        if (doc.name) {
          frappe.set_route("Form", "Material Readiness Check", doc.name);
        }
      }, __("Material Readiness"));
    }
  }

  frappe.ui.form.on("Work Order", {
    refresh(frm) {
      refreshReadiness(frm).catch((error) => {
        frappe.msgprint(error.message || __("Unable to load Material Readiness context."));
      });
    },
  });
})();
""".strip()

    existing_name = frappe.db.get_value("Client Script", {"dt": "Work Order", "name": CLIENT_SCRIPT_NAME}, "name")
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

    frappe.get_doc(
        {
            "doctype": "Client Script",
            "name": CLIENT_SCRIPT_NAME,
            "dt": "Work Order",
            "view": "Form",
            "enabled": 1,
            "script": script,
        }
    ).insert(ignore_permissions=True)
    return CLIENT_SCRIPT_NAME


def disable_legacy_client_script():
    if not frappe.db.exists("DocType", "Client Script"):
        return

    existing_name = frappe.db.get_value(
        "Client Script",
        {"dt": "Work Order", "name": CLIENT_SCRIPT_NAME},
        "name",
    )
    if not existing_name:
        return

    doc = frappe.get_doc("Client Script", existing_name)
    if getattr(doc, "enabled", 1):
        doc.enabled = 0
        doc.save(ignore_permissions=True)


@frappe.whitelist()
def get_work_order_material_readiness_context(work_order: str) -> dict[str, object]:
    if not work_order:
        frappe.throw(_("Work Order is required."))

    rows = frappe.get_all(
        "Material Readiness Check",
        filters={"work_order": work_order},
        fields=["name", "status", "docstatus", "shortage_summary", "checked_by", "checked_on", "modified"],
        order_by="modified desc",
        limit_page_length=5,
    )
    linked = rows[0] if rows else None
    ready_check = next((row for row in rows if row.get("docstatus") == 1 and row.get("status") == "Ready"), None)

    if ready_check:
        return {
            "work_order": work_order,
            "linked_check": ready_check["name"],
            "ready_status": "Ready",
            "message": _("Material Readiness Check is submitted and ready for Work Order submission."),
            "shortage_summary": ready_check.get("shortage_summary") or "",
        }

    if linked:
        return {
            "work_order": work_order,
            "linked_check": linked["name"],
            "ready_status": linked.get("status") or "Draft",
            "message": _("Material Readiness Check {0} is required and currently {1}.").format(
                linked["name"], linked.get("status") or _("Draft")
            ),
            "shortage_summary": linked.get("shortage_summary") or "",
        }

    return {
        "work_order": work_order,
        "linked_check": "",
        "ready_status": "Not Started",
        "message": _("No Material Readiness Check exists yet for this Work Order."),
        "shortage_summary": "",
    }


@frappe.whitelist()
def create_material_readiness_check(work_order: str) -> dict[str, str]:
    if not work_order:
        frappe.throw(_("Work Order is required."))

    existing = frappe.get_all(
        "Material Readiness Check",
        filters={"work_order": work_order, "docstatus": ("<", 2)},
        fields=["name", "status", "docstatus"],
        order_by="modified desc",
        limit_page_length=1,
    )
    if existing:
        return {"name": existing[0]["name"]}

    doc = frappe.get_doc(
        {
            "doctype": "Material Readiness Check",
            "work_order": work_order,
        }
    )
    doc.insert(ignore_permissions=True)
    return {"name": doc.name}
