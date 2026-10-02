"""Authenticated dashboard planning evidence on standard Work Orders; no PP factory."""
from __future__ import annotations
import hashlib
import hmac
import json
import secrets
from contextvars import ContextVar

import frappe
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime
from calco_erp.inventory.availability import get_production_warehouses

ORIGIN = "fg-dashboard-v1"
PREFIX = "custom_fg_planning_"
FIELDS = [PREFIX + name for name in ("origin", "identity", "snapshot", "events", "signature")]
TOKEN = PREFIX + "context"
REASON = PREFIX + "change_reason"
_INTERNAL = ContextVar("fg_planning_internal", default=False)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sign(value):
    key = frappe.local.conf.get("encryption_key")
    if not key:
        frappe.throw("Site encryption key is required for authenticated planning context.")
    return hmac.new(key.encode(), encode(value).encode(), hashlib.sha256).hexdigest()


def is_dashboard(doc):
    return doc.get(FIELDS[0]) == ORIGIN


def is_dashboard_name(name):
    return bool(name and frappe.db.get_value("Work Order", name, FIELDS[0]) == ORIGIN)


def compatible_boms(item, company):
    from calco_erp.calco_production.planning_work_order import _get_controlled_boms, _filter_enabled_boms
    rows = _filter_enabled_boms(_get_controlled_boms(item))
    values = frappe.get_all("BOM", filters={"item": item, "company": company, "docstatus": 1, "is_active": 1}, fields=["name", "is_default"])
    valid = {r.name: cint(r.is_default) for r in values}
    rows = [r for r in rows if r["bom_no"] in valid]
    names = sorted({r["bom_no"] for r in rows})
    defaults = [name for name in names if valid[name]]
    item_default = frappe.db.get_value("Item", item, "default_bom")
    conflict = len(defaults) > 1 or (defaults and item_default and item_default != defaults[0])
    suggested = ""
    if not conflict:
        suggested = defaults[0] if len(defaults) == 1 else names[0] if len(names) == 1 else ""
    return {"candidates": rows, "suggested": suggested, "ambiguous": bool(conflict or (len(names) > 1 and not suggested))}


@frappe.whitelist()
def creation_context(company, item_code, month):
    from calco_erp.calco_production import fg_dashboard as dashboard
    frappe.has_permission("Work Order", "create", throw=True)
    data = dashboard.build(company, item_code)
    row = next((r for r in data["rows"] if r["key"] == month), None)
    if not row:
        frappe.throw("Select an FG within the current three-month planning horizon.")
    if row["balance_to_plan"] <= 0:
        frappe.throw("No Balance to Plan remains for this month.")
    blockers = [e for e in data["exceptions"] if e.get("item_code") == item_code and "conversion" in e.get("reason", "").lower()]
    if blockers:
        frappe.throw("Resolve forecast UOM conversion before planning this FG.")
    boms = compatible_boms(item_code, company)
    if not boms["candidates"]:
        frappe.throw("No compatible active submitted Compounding BOM is available.")
    payload = {"origin": ORIGIN, "identity": secrets.token_hex(20), "planner": frappe.session.user,
               "opened_on": str(now_datetime()), "company": company, "item_code": item_code,
               "stock_uom": row["stock_uom"], "month": month, "displayed_balance": row["balance_to_plan"],
               "calculation_version": data["version"], "sources": row["sources"],
               "requested_start": max(month, str(now_datetime().date())) + " 08:00:00", "required_date": row["end"]}
    return {"context": encode({"payload": payload, "signature": sign(payload)}), "row": row, "boms": boms,
            "warehouses": data["warehouses"], "production_warehouses": get_production_warehouses(company)}


def lock_item(doc):
    # Item-level transaction lock covers shared inventory and all calendar buckets.
    frappe.db.sql("select name from `tabItem` where name=%s for update", (doc.production_item,))


def _contents(doc):
    return {"origin": doc.get(FIELDS[0]), "identity": doc.get(FIELDS[1]),
            "snapshot": json.loads(doc.get(FIELDS[2]) or "{}"),
            "events": json.loads(doc.get(FIELDS[3]) or "[]")}


def verify(doc):
    try:
        content = _contents(doc)
        valid = is_dashboard(doc) and content["identity"] and hmac.compare_digest(sign(content), cstr(doc.get(FIELDS[4])))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        frappe.throw("Invalid or altered FG planning provenance.")
    return content


def _seal(doc, content):
    doc.set(FIELDS[2], encode(content["snapshot"]))
    doc.set(FIELDS[3], encode(content["events"]))
    doc.set(FIELDS[4], sign(content))


def revision(doc):
    values = {k: cstr(doc.get(k)) for k in ("company", "production_item", "stock_uom", "bom_no", "planned_start_date", "planned_end_date", "expected_delivery_date", "custom_machine", "custom_production_line")}
    values["qty"] = flt(doc.qty)
    if doc.get("custom_fg_requested_start"):
        values["custom_fg_requested_start"] = cstr(doc.get("custom_fg_requested_start"))
    return values


def normalized_line_values(values):
    values = dict(values)
    line = values.get("custom_production_line") or values.get("custom_machine") or ""
    if not values.get("custom_production_line"):
        values["custom_production_line"] = line
    if not values.get("custom_machine"):
        values["custom_machine"] = line
    return values


def revision_matches(doc, event, content):
    if event["revision"] == revision_hash(doc):
        return True
    values = event.get("values") or next((e.get("values") for e in content["events"]
        if e["revision"] == event["revision"] and e.get("values")), None)
    return bool(values and normalized_line_values(values) == normalized_line_values(revision(doc)))


def normalize_line_fields(doc, previous=None):
    line, machine = cstr(doc.get("custom_production_line")), cstr(doc.get("custom_machine"))
    if line and machine and line != machine:
        old = normalized_line_values(previous or {})
        changed = [field for field, value in [("custom_production_line", line), ("custom_machine", machine)] if value != old.get(field)]
        if previous and len(changed) == 1:
            line = doc.get(changed[0])
        else:
            frappe.throw("Production Line and Machine must identify the same approved production line.")
    else:
        line = line or machine
    if line:
        doc.custom_production_line = line
        doc.custom_machine = line
    return line


def revision_hash(doc):
    return hashlib.sha256(encode(revision(doc)).encode()).hexdigest()


def _event(doc, content, kind, **values):
    content["events"].append({"sequence": len(content["events"]) + 1, "event": kind,
                              "user": frappe.session.user, "on": str(now_datetime()),
                              "revision": revision_hash(doc), **values})
    _seal(doc, content)


def initialize(doc, method=None):
    supplied = doc.get(TOKEN)
    if not supplied and doc.get("amended_from") and is_dashboard_name(doc.amended_from):
        original = frappe.get_doc("Work Order", doc.amended_from)
        original.check_permission("read")
        if cint(original.docstatus) != 2:
            frappe.throw("Only a cancelled dashboard Work Order can be amended.")
        previous = verify(original)
        from calco_erp.calco_production.fg_dashboard import month_key
        context = creation_context(doc.company, doc.production_item, max(previous["snapshot"]["month"], month_key(now_datetime())))
        envelope = json.loads(context["context"])
        envelope["payload"]["amended_from"] = original.name
        envelope["payload"]["original_planning_month"] = previous["snapshot"]["month"]
        envelope["signature"] = sign(envelope["payload"])
        supplied = encode(envelope)
    if not supplied:
        if any(doc.get(f) for f in FIELDS):
            frappe.throw("Planning provenance cannot be copied or supplied directly. Use Add Work Order.")
        return
    try:
        envelope = json.loads(supplied)
        payload = envelope["payload"]
        valid = hmac.compare_digest(sign(payload), envelope["signature"])
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid or payload.get("origin") != ORIGIN or payload.get("planner") != frappe.session.user:
        frappe.throw("Invalid dashboard creation context.")
    age = (now_datetime() - get_datetime(payload["opened_on"])).total_seconds()
    if age < 0 or age > 7200:
        frappe.throw("Dashboard context expired. Refresh the FG Dashboard and open Add Work Order again.")
    if doc.company != payload["company"] or doc.production_item != payload["item_code"]:
        frappe.throw("Company/FG differs from dashboard context. Open Add Work Order for the intended FG.")
    lock_item(doc)
    used = frappe.db.exists("Work Order", {FIELDS[1]: payload["identity"]}) or frappe.db.exists("Deleted Document", {"deleted_doctype": "Work Order", "data": ("like", "%" + payload["identity"] + "%")})
    if used:
        frappe.throw("This Add Work Order request already has a saved Work Order. Open the existing document.")
    doc.set(FIELDS[0], ORIGIN)
    doc.set(FIELDS[1], payload["identity"])
    payload["initial_requested_start"] = cstr(doc.get("custom_fg_requested_start") or doc.planned_start_date or payload.get("requested_start"))
    content = {"origin": ORIGIN, "identity": payload["identity"], "snapshot": payload, "events": []}
    _seal(doc, content)
    doc.set(TOKEN, None)
    from calco_erp.calco_production.work_order_control import _set_default_execution_state
    _set_default_execution_state(doc)


def _allocation_check(doc):
    from calco_erp.calco_production import fg_dashboard as dashboard
    if not doc.planned_end_date:
        frappe.throw("Planned End Date is required to place FG supply in a calendar month.")
    if get_datetime(doc.planned_end_date) < get_datetime(doc.planned_start_date):
        frappe.throw("Planned End Date cannot precede Planned Start Date.")
    data = dashboard.build(doc.company, doc.production_item, exclude_wo=None if doc.is_new() else doc.name)
    target = max(dashboard.month_key(doc.planned_end_date), data["months"][0]["key"])
    row = next((r for r in data["rows"] if r["key"] == target), None)
    if not row:
        frappe.throw("Plan completion within the displayed three-month horizon.")
    _category, qty = dashboard.wo_supply(doc)
    if qty > flt(row["balance_to_plan"]) + 1e-9:
        frappe.throw(f"Stale or excess allocation: current Balance to Plan is {row['balance_to_plan']:g} {row['stock_uom']}; requested outstanding supply is {qty:g}. Refresh planning and reduce quantity or revise the demand through its authority.")
    return {"balance_at_save": row["balance_to_plan"], "calculated_on": data["as_of"], "sources": row["sources"]}


def validate(doc, method=None):
    old = None if doc.is_new() else frappe.db.get_value("Work Order", doc.name, FIELDS, as_dict=True)
    if old and any(doc.get(f) != old.get(f) for f in FIELDS) and not _INTERNAL.get():
        frappe.throw("Historical planning provenance cannot be edited.")
    if not is_dashboard(doc):
        if doc.get(TOKEN) or (old and old.get(FIELDS[0])):
            frappe.throw("Invalid planning origin transition.")
        return
    content = verify(doc)
    if doc.get("production_plan") or doc.get("production_plan_item"):
        frappe.throw("Dashboard-origin Work Orders use direct provenance and cannot acquire a Production Plan.")
    snapshot = content["snapshot"]
    if (doc.company, doc.production_item, frappe.db.get_value("Item", doc.production_item, "stock_uom")) != (snapshot["company"], snapshot["item_code"], snapshot["stock_uom"]):
        frappe.throw("Company, FG and stock UOM must match immutable planning identity.")
    if cstr(doc.get("stock_uom")) != snapshot["stock_uom"]:
        frappe.throw("Work Order UOM must match its FG planning stock UOM.")
    boms = compatible_boms(doc.production_item, doc.company)
    candidates = [r for r in boms["candidates"] if r["bom_no"] == doc.bom_no]
    if not candidates:
        frappe.throw("Select a compatible active submitted Compounding BOM.")
    if not any(r.operation == "Compounding / Extrusion" for r in doc.get("operations") or []):
        frappe.throw("Reload BOM operations: the controlled Compounding / Extrusion operation is required.")
    from calco_erp.calco_production.fg_dashboard import warehouses
    expected_warehouses = warehouses(doc.company)
    if doc.fg_warehouse != expected_warehouses["FG Quarantine"]:
        frappe.throw("Dashboard Work Orders must manufacture into the company FG Quarantine warehouse.")
    last = next((r for r in reversed(content["events"]) if r["event"] == "Revision"), None)
    line = normalize_line_fields(doc, last.get("values") if last else None)
    lines = {r["production_line"] for r in candidates}
    if line and line not in lines:
        frappe.throw("BOM is incompatible with the selected production line.")
    if not line and len(lines) == 1 and not last:
        line = next(iter(lines))
        doc.custom_production_line = line
        doc.custom_machine = line
    if not line:
        frappe.throw("Select Production Line/Machine before saving the planning revision.")
    from calco_erp.calco_production.fg_schedule_preview import resolve
    if cint(doc.docstatus) == 0 or getattr(doc, "_action", None) == "submit":
        schedule = resolve(doc, check_only=cint(doc.docstatus) == 1 or _INTERNAL.get() == "approval_check")
    else:
        schedule = None
    changed = not last or not revision_matches(doc, last, content)
    lock_item(doc)
    calculation = _allocation_check(doc)
    if changed:
        if last and any(e["event"] == "Approved" for e in content["events"]) and not cstr(doc.get(REASON)).strip():
            frappe.throw("Planning Change Reason is required for quantity, BOM, production line/machine or schedule revisions.")
        _event(doc, content, "Revision", values=revision(doc), reason=doc.get(REASON) or ("Unapproved planning recalculation" if last else "Initial standard Work Order save"),
               schedule_preview={k:v for k,v in schedule.items() if k != "operation_rows"} if schedule else None, **calculation)
        doc.set(REASON, None)
    if _INTERNAL.get() == "approve":
        _event(doc, content, "Approved", authority="Customer Service planning approval")


def authority(doc, bom_no=None):
    """Return None for legacy; caller retains the exact existing PP/PPI checks."""
    if not is_dashboard(doc):
        return None
    blockers = []
    content = verify(doc)
    latest_revision = max((e["sequence"] for e in content["events"] if e["event"] == "Revision"), default=0)
    approvals = [e for e in content["events"] if e["event"] == "Approved" and e["sequence"] > latest_revision and (
        revision_matches(doc, e, content) or any(s["event"] == "ScheduledWithinApproval" and s.get("approval_sequence") == e["sequence"] and s["revision"] == revision_hash(doc) for s in content["events"]))]
    if not approvals:
        blockers.append("Current Work Order planning revision is not approved by planning authority.")
    if bom_no and bom_no != doc.bom_no:
        blockers.append("Requested BOM differs from the approved planning revision.")
    if doc.get("production_plan") or doc.get("production_plan_item"):
        blockers.append("Dashboard provenance cannot be combined with PP/PPI lineage.")
    if flt(doc.qty) <= 0:
        blockers.append("Approved production quantity must be positive.")
    return {"ready": not blockers, "blockers": blockers, "origin": ORIGIN,
            "approval": approvals[-1] if approvals else None}


def can_approve():
    from calco_erp.calco_production.planning_release import CUSTOMER_SERVICE_ROLES
    return bool(CUSTOMER_SERVICE_ROLES.intersection(frappe.get_roles()))


@frappe.whitelist()
def approve(work_order):
    if not can_approve():
        frappe.throw("Customer Service planning authority is required.", frappe.PermissionError)
    doc = frappe.get_doc("Work Order", work_order)
    doc.check_permission("read")
    if not is_dashboard(doc) or cint(doc.docstatus) == 2:
        frappe.throw("Only active dashboard-origin Work Orders can be approved here.")
    lock_item(doc)
    doc.reload()
    before = {k: v for k, v in doc.as_dict().items() if not k.startswith(PREFIX)}
    content = verify(doc)
    events_before = encode(content["events"])
    marker = _INTERNAL.set("approval_check")
    try:
        validate(doc)
    finally:
        _INTERNAL.reset(marker)
    after = {k: v for k, v in doc.as_dict().items() if not k.startswith(PREFIX)}
    if before != after or encode(verify(doc)["events"]) != events_before:
        frappe.throw("Save the complete production configuration before planning approval.")
    _event(doc, content, "Approved", authority="Customer Service planning approval", values=revision(doc))
    # Evidence-only write: never re-run the standard Work Order save lifecycle here.
    frappe.db.set_value("Work Order", doc.name, {FIELDS[3]: doc.get(FIELDS[3]), FIELDS[4]: doc.get(FIELDS[4])})

    return {"work_order": doc.name, "approved_revision": revision_hash(doc)}


def before_submit(doc, method=None):
    if not is_dashboard(doc):
        return
    lock_item(doc)
    _allocation_check(doc)
    content = verify(doc)
    if not authority(doc)["ready"] and can_approve():
        _event(doc, content, "Approved", authority="Customer Service planning approval at standard Submit")
    check = authority(doc)
    if not check["ready"]:
        frappe.throw(" ".join(check["blockers"]))
    _event(doc, content, "Submitted", authority="Standard Work Order submit permission")


def protect_delete(doc, method=None):
    if not is_dashboard(doc):
        return
    if cint(doc.docstatus) != 0:
        frappe.throw("Submitted/cancelled dashboard Work Orders must remain as planning audit evidence.")
    # Native Deleted Document retains this signed history after standard Draft deletion.
    # Context expires after two hours; replay checks both active and deleted documents.
    _event(doc, verify(doc), "DraftDeleted", authority="Standard Work Order delete permission")


def ensure_setup():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    fields = []
    for name, label, kind in [(FIELDS[0], "Planning Origin", "Data"), (FIELDS[1], "Planning Request Identity", "Data"),
                              (FIELDS[2], "Original Planning Context", "Long Text"), (FIELDS[3], "Planning Audit Events", "Long Text"),
                              (FIELDS[4], "Planning Evidence Signature", "Data"), (TOKEN, "Dashboard Creation Context", "Long Text"),
                              (REASON, "Planning Change Reason", "Small Text")]:
        fields.append({"fieldname": name, "label": label, "fieldtype": kind, "insert_after": fields[-1]["fieldname"] if fields else "production_plan",
                       "read_only": int(name not in {TOKEN, REASON}), "no_copy": 1,
                       "hidden": int(name != REASON), "allow_on_submit": 1,
                       "depends_on": "eval:doc.custom_fg_planning_origin==\"fg-dashboard-v1\"" if name == REASON else "",
                       "unique": int(name == FIELDS[1]), "search_index": int(name == FIELDS[0])})
    create_custom_fields({"Work Order": fields}, update=True)
    frappe.clear_cache(doctype="Work Order")



def after_submit(doc, method=None):
    if not is_dashboard(doc):
        return
    content = verify(doc)
    approved = next((e for e in reversed(content["events"]) if e["event"] == "Approved"), None)
    original = next((e["values"] for e in reversed(content["events"]) if e["event"] == "Revision"), None)
    if not approved or not original:
        frappe.throw("Missing planning approval evidence at submission.")
    current = revision(doc)
    if revision_matches(doc, approved, content):
        return
    dates = {"planned_start_date", "planned_end_date"}
    same = all(normalized_line_values(current)[k] == normalized_line_values(original)[k] for k in current if k not in dates)
    inside = get_datetime(current["planned_start_date"]) >= get_datetime(original["planned_start_date"]) and get_datetime(current["planned_end_date"]) <= get_datetime(original["planned_end_date"])
    if not same or not inside:
        frappe.throw("ERPNext scheduled this WO outside its approved planning window. Revise the Draft schedule and obtain planning approval again.")
    _allocation_check(doc)
    _event(doc, content, "ScheduledWithinApproval", approval_sequence=approved["sequence"], values=current,
           reason="Standard ERPNext scheduling within approved window")
    frappe.db.set_value("Work Order", doc.name, {f: doc.get(f) for f in FIELDS}, update_modified=False)


def assert_execution_allowed(work_order):
    if not is_dashboard_name(work_order):
        return
    doc = frappe.get_doc("Work Order", work_order)
    result = authority(doc)
    if cint(doc.docstatus) != 1 or not result["ready"]:
        frappe.throw("Controlled production requires a submitted Work Order with approval of its current planning revision.")



def before_cancel(doc, method=None):
    if is_dashboard(doc):
        _event(doc, verify(doc), "Cancelled", authority="Standard Work Order cancellation")


def _record_status(work_order, result):
    if is_dashboard_name(work_order):
        doc = frappe.get_doc("Work Order", work_order)
        lock_item(doc)
        doc.reload()
        _event(doc, verify(doc), "StatusChanged", status=doc.status, authority="Standard Work Order lifecycle")
        frappe.db.set_value("Work Order", doc.name, {f: doc.get(f) for f in FIELDS}, update_modified=False)
    return result


@frappe.whitelist()
def stop_unstop(work_order, status):
    from calco_erp.calco_production.material_reservation_submission import stop_unstop_with_reservation_guard
    if is_dashboard_name(work_order):
        doc = frappe.get_doc("Work Order", work_order)
        doc.check_permission("write")
        lock_item(doc)
        if status != "Stopped":
            doc.status = status
            _allocation_check(doc)
    return _record_status(work_order, stop_unstop_with_reservation_guard(work_order, status))


@frappe.whitelist()
def close_work_order(work_order, status):
    from erpnext.manufacturing.doctype.work_order.work_order import close_work_order as standard_close
    if is_dashboard_name(work_order):
        doc = frappe.get_doc("Work Order", work_order)
        doc.check_permission("write")
        lock_item(doc)
        if status != "Closed":
            doc.status = status
            _allocation_check(doc)
    return _record_status(work_order, standard_close(work_order, status))





@frappe.whitelist()
def restore_approved_line_alias(work_order):
    """Restore only a missing alias from signed, otherwise unchanged approval evidence."""
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator is required for this audited compatibility repair.", frappe.PermissionError)
    doc = frappe.get_doc("Work Order", work_order)
    doc.check_permission("write")
    lock_item(doc)
    doc.reload()
    if doc.docstatus != 0 or not is_dashboard(doc):
        frappe.throw("Only a dashboard Draft Work Order can receive this compatibility repair.")
    content = verify(doc)
    approval = authority(doc).get("approval")
    if not approval:
        frappe.throw("The current configuration must still match its signed approval.")
    before = revision(doc)
    if bool(doc.get("custom_machine")) == bool(doc.get("custom_production_line")):
        frappe.throw("Repair requires exactly one missing line alias.")
    normalize_line_fields(doc)
    if not revision_matches(doc, approval, content):
        frappe.throw("Restoration would change the approved production configuration.")
    _event(doc, content, "LineAliasRestored", values=revision(doc), previous_values=before,
           reason="System compatibility repair: populate the missing alias from the signed approved line; no planning change.")
    marker = _INTERNAL.set("line_alias_repair")
    try:
        doc.save()
    finally:
        _INTERNAL.reset(marker)
    return {"work_order": doc.name, "line": doc.custom_machine, "approval_preserved": authority(doc)["ready"]}
