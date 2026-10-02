"""Exact matrix-registration duplicate control; never performs supplier approval."""
import frappe
from frappe import _

OPEN_STATES = ("Draft", "Quality Review", "Purchase Review", "Management Review")
REASON = "Cancelled as duplicate — Supplier + RM Code already exists in Supplier Approval Matrix."
CONDITION = "doc.supplier_request_items and not [r for r in doc.supplier_request_items if not r.item_code or not frappe.db.get_value('Supplier Approval Matrix', {'supplier': doc.supplier_name or doc.proposed_supplier_name, 'item_code': r.item_code}, 'name')]"


def matches(doc, lock=False):
    # Exact document identity only. No fuzzy names, status/date filters or approval inference.
    supplier = doc.get("supplier_name") or doc.get("proposed_supplier_name")
    if not supplier or not frappe.db.exists("Supplier", supplier):
        return []
    result = []
    for row in doc.get("supplier_request_items") or []:
        item = row.get("item_code")
        found = frappe.db.sql(
            "select name, supplier, item_code from `tabSupplier Approval Matrix` "
            "where supplier=%s and item_code=%s order by name" + (" for update" if lock else ""),
            (supplier, item), as_dict=True,
        ) if item else []
        result.append({"item_code": item, "matrix": [m.name for m in found
                       if m.supplier == supplier and m.item_code == item]})
    return result


def assert_all_registered(rows):
    if not rows or any(not r["matrix"] for r in rows):
        frappe.throw(_("Duplicate cancellation requires an existing Supplier Approval Matrix record for EVERY requested RM Code."))


def _evidence(doc):
    data = doc.as_dict()
    for key in ("status", "modified", "modified_by", "__onload", "__unsaved"):
        data.pop(key, None)
    for value in data.values():
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    row.pop("modified", None)
                    row.pop("modified_by", None)
    return data


def validate_cancellation(doc):
    """Called before normalization/checklist regeneration; only state may change."""
    before = doc.get_doc_before_save()
    if doc.get("status") != "Cancelled" and not (before and before.get("status") == "Cancelled"):
        return False
    if doc.is_new() or not before:
        frappe.throw(_("Save an open request before cancelling a duplicate."))
    if frappe.session.user != "Administrator" and "Purchase Manager" not in frappe.get_roles():
        frappe.throw(_("Only Purchase Manager may cancel a duplicate request."), frappe.PermissionError)
    locked = frappe.db.sql("select status, modified from `tabNew Supplier Request` where name=%s for update", (doc.name,), as_dict=True)
    if not locked or str(locked[0].modified) != str(before.modified):
        frappe.throw(_("Request changed. Reload before cancelling."))
    if before.status not in OPEN_STATES or doc.status != "Cancelled" or doc.docstatus != 0:
        frappe.throw(_("Only an open, non-submittable request can be cancelled as duplicate. Cancelled evidence is immutable."))
    if _evidence(doc) != _evidence(before):
        frappe.throw(_("Duplicate cancellation cannot change request evidence. Save or discard other edits first."))
    previous_children = {row.name: row for row in before.get_all_children()}
    for row in doc.get_all_children():
        row.modified = previous_children[row.name].modified
        row.modified_by = previous_children[row.name].modified_by
    rows = matches(doc, lock=True)
    assert_all_registered(rows)
    doc.flags.duplicate_cancellation_rows = rows
    return True


def cancellation_audit(doc):
    rows = doc.flags.get("duplicate_cancellation_rows")
    if not rows:
        frappe.throw(_("Duplicate cancellation was not validated."))
    from frappe.utils import escape_html
    refs = "; ".join(r["item_code"] + ": " + ", ".join(r["matrix"]) for r in rows)
    doc.add_comment("Info", escape_html(REASON + " " + refs))


def validate_registered_combinations(doc):
    rows = matches(doc)
    duplicate = [r for r in rows if r["matrix"]]
    if duplicate:
        detail = "; ".join(r["item_code"] + ": " + ", ".join(r["matrix"]) for r in duplicate)
        frappe.throw(_("Supplier + RM Code already registered in Supplier Approval Matrix: {0}. Remove these duplicate lines explicitly; only genuinely new combinations may proceed. No lines were removed automatically.").format(detail))


def install_workflow():
    """Targeted, idempotent metadata only. No migrate, master sync or request changes."""
    if frappe.session.user != "Administrator":
        frappe.throw(_("Administrator required for workflow installation."), frappe.PermissionError)
    for dt, name in (("Workflow State", "Cancelled"), ("Workflow Action Master", "Cancel Duplicate")):
        if not frappe.db.exists(dt, name):
            frappe.get_doc({"doctype": dt, ("workflow_state_name" if dt == "Workflow State" else "workflow_action_name"): name}).insert()
    name = frappe.db.get_value("Workflow", {"document_type": "New Supplier Request", "is_active": 1}, "name")
    if not name:
        frappe.throw(_("Active New Supplier Request workflow required."))
    workflow = frappe.get_doc("Workflow", name)
    changed = False
    cancelled = [s for s in workflow.states if s.state == "Cancelled"]
    if cancelled and (len(cancelled) != 1 or str(cancelled[0].doc_status) != "0" or cancelled[0].allow_edit != "Purchase Manager"):
        frappe.throw(_("Existing Cancelled state differs; review required."))
    if not any(s.state == "Cancelled" for s in workflow.states):
        workflow.append("states", {"state": "Cancelled", "doc_status": "0", "allow_edit": "Purchase Manager", "send_email": 0})
        changed = True
    for state in OPEN_STATES:
        if not any(s.state == state for s in workflow.states):
            continue
        existing = [t for t in workflow.transitions if t.state == state and t.action == "Cancel Duplicate"]
        expected = {"state": state, "action": "Cancel Duplicate", "next_state": "Cancelled", "allowed": "Purchase Manager", "allow_self_approval": 1, "condition": CONDITION}
        if existing:
            if len(existing) != 1 or any(existing[0].get(k) != value for k, value in expected.items()):
                frappe.throw(_("Existing Cancel Duplicate transition differs; review required."))
        else:
            workflow.append("transitions", expected)
            changed = True
    if changed:
        workflow.save()
    return {"workflow": name, "changed": changed}
