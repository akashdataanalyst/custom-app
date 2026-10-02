"""Prospective and explicitly enrolled native Purchase assignments; no historical sweep.

Activation is a site-local, explicit versioned configuration, never an app default.
A parent-row lock serializes calls. Native ToDo handles document assignment/audit;
this service refuses implicit sharing and never replaces a human assignment.
"""
import hashlib
from decimal import Decimal

import frappe
from frappe.utils import get_datetime, getdate, nowdate, now_datetime
from calco_erp.planning_upgrade.purchase_policy import POLICY, VERSION, due_date

CONFIG_KEY = "calco_purchase_assignments"
STAGES = {"Material Request": "mr-open", "Purchase Commercial Approval": "pca",
          "Purchase Order": "po-open", "Quality Inspection": "qc-open",
          "Purchase Receipt": "grn-bill"}
DESCRIPTIONS = {
 "mr-open": "Waiting for PO: raise RFQ within 2 days and PO within 7 days of MR",
 "pca": "Commercial approval: decide within 1 working day",
 "po-open": "Awaiting delivery: follow up supplier 3 days before required-by date; escalate if 3+ days late",
 "qc-open": "QC review: accept/reject within 2 days of receipt",
 "grn-bill": "Awaiting supplier bill: book Purchase Invoice within 7 days of GRN; escalate after 15 days",
}
TERMINAL = {"Stopped", "Closed", "Cancelled", "Completed"}


def configuration():
    conf = frappe.conf.get(CONFIG_KEY) or {}
    if not isinstance(conf, dict) or conf.get("enabled") != 1:
        return None
    if conf.get("version") != VERSION or not conf.get("effective_from"):
        return None
    # Site binding prevents copied config from activating another environment.
    if conf.get("site") != frappe.local.site or conf.get("database") != frappe.conf.db_name:
        return None
    return conf


def eligible_creation(doc, conf):
    return bool(doc.creation and get_datetime(doc.creation) >= get_datetime(conf["effective_from"]))


ENROLLMENT = "Purchase Assignment Enrollment"


def enrollment_name(doctype, name):
    return hashlib.sha256((doctype + "|" + name).encode()).hexdigest()


def historically_enrolled(doc):
    # Missing targeted schema is fail-closed for creation, not for task cleanup.
    return bool(frappe.db.table_exists(ENROLLMENT) and frappe.db.sql(
        "SELECT name FROM `tabPurchase Assignment Enrollment` WHERE name=%s FOR UPDATE",
        (enrollment_name(doc.doctype, doc.name),)))


def creation_eligible(doc, conf):
    return eligible_creation(doc, conf) or historically_enrolled(doc)


def validate_enrollment(doc):
    """Immutable enrollment; all authority is checked again under the source lock."""
    frappe.only_for(("System Manager", "Purchase Manager"))
    conf = configuration()
    if not conf or doc.reference_doctype not in STAGES:
        frappe.throw("Active site-bound assignment policy and supported document required")
    if not (doc.reason or "").strip():
        frappe.throw("Historical enrollment reason is required")
    source = frappe.get_doc(doc.reference_doctype, doc.reference_name, for_update=True)
    source.check_permission("read")
    if eligible_creation(source, conf):
        frappe.throw("Prospective documents do not require historical enrollment")
    state = requirement(source)
    if not state["needed"] or state.get("blocker"):
        frappe.throw("Document is not in an eligible active Purchase stage")
    owner = (conf.get("owners") or {}).get(state["stage"])
    user = frappe.db.get_value("User", owner, ["enabled", "user_type"], as_dict=True) if owner else None
    if not user or not user.enabled or user.user_type != "System User" or not frappe.has_permission(doc=source, user=owner, ptype="read"):
        frappe.throw("Configured stage owner is unavailable or lacks document permission")
    holidays = calendar_dates(state["company"], state["start"]) if state["stage"] == "pca" and state["start"] else None
    if not due_date(state["stage"], state["start"], state["required"], holidays):
        frappe.throw("SLA date or approved holiday calendar requires review")
    if frappe.db.exists("ToDo", {"reference_type":source.doctype,"reference_name":source.name,"status":"Open"}):
        frappe.throw("Existing open assignment must be preserved; enrollment not required")
    if frappe.db.exists("ToDo", {"reference_type":source.doctype,"reference_name":source.name,
                                "description":["like", "%"+marker(state["stage"],source.doctype,source.name)+"%"]}):
        frappe.throw("Previous service task requires controlled reassignment review")
    doc.enrolled_by = frappe.session.user
    doc.enrolled_on = now_datetime()
    doc.policy_version = VERSION
    doc.activation_boundary = conf["effective_from"]
    doc.reason = doc.reason.strip()


@frappe.whitelist()
def enroll_historical(reference_doctype, reference_name, reason):
    """No commit and no task creation: enrollment participates in caller transaction."""
    frappe.only_for(("System Manager", "Purchase Manager"))
    if reference_doctype not in STAGES or not (reason or "").strip():
        frappe.throw("Supported document and enrollment reason required")
    # Same parent lock as sync serializes enrollment retries and hooks.
    source = frappe.get_doc(reference_doctype, reference_name, for_update=True)
    source.check_permission("read")
    name = enrollment_name(reference_doctype, reference_name)
    if historically_enrolled(source):
        return dict(status="Already Enrolled", name=name)
    doc = frappe.get_doc(dict(doctype=ENROLLMENT, reference_doctype=reference_doctype,
        reference_name=reference_name, reason=reason)).insert(ignore_permissions=True)
    return dict(status="Enrolled", name=doc.name)


def decimal(value):
    return Decimal(str(value or 0))


def _query(sql, values):
    return frappe.db.sql(sql, values, as_dict=True)


def requirement(doc):
    """Current business state, not presence in a historical document chain."""
    stage = STAGES[doc.doctype]
    if doc.docstatus == 2 or doc.get("status") in TERMINAL:
        return dict(stage=stage, needed=False)
    company = doc.get("company")
    start = doc.get("transaction_date") or doc.get("posting_date") or doc.creation
    required = None
    needed = False
    blocker = None
    if stage == "mr-open":
        if doc.docstatus == 1 and doc.material_request_type == "Purchase":
            # Native MR ordered_qty is in the MR stock UOM and accounts for
            # ERPNext's linked-document conversion/status updater. Do not sum
            # alternate PO item stock UOMs as though they were interchangeable.
            needed = any(decimal(r.stock_qty) > decimal(r.ordered_qty)
                         for r in doc.get("items", []))
    elif stage == "po-open":
        if doc.docstatus == 1 and doc.get("status") != "Delivered":
            rows = _query("""SELECT p.stock_qty,p.schedule_date,COALESCE(SUM(r.stock_qty),0) received
              FROM `tabPurchase Order Item` p
              LEFT JOIN `tabPurchase Receipt Item` r ON r.purchase_order_item=p.name
                AND r.parent IN (SELECT name FROM `tabPurchase Receipt` WHERE docstatus=1 AND is_return=0)
              WHERE p.parent=%s GROUP BY p.name,p.stock_qty,p.schedule_date""", (doc.name,))
            pending = [r for r in rows if decimal(r.stock_qty) > decimal(r.received)]
            needed = bool(pending)
            dates = [getdate(r.schedule_date) for r in pending if r.schedule_date]
            required = min(dates) if dates and len(dates)==len(pending) else None
    elif stage == "grn-bill":
        needed = doc.docstatus == 1 and not doc.get("is_return") and doc.get("status") == "To Bill"
    elif stage == "pca":
        source = frappe.db.get_value("Supplier Quotation", doc.get("supplier_quotation"),
                                     ["company", "docstatus", "status"], as_dict=True)
        needed = doc.get("approval_status") in ("Draft", "Reopened") and bool(source and source.docstatus == 1 and source.status != "Cancelled")
        company = source.company if source else None
        # Reopened is a new decision interval; original creation remains cohort authority.
        start = doc.get("reopened_date") if doc.get("approval_status") == "Reopened" else doc.creation
    elif stage == "qc-open":
        needed = doc.get("inspection_type") == "Incoming" and doc.get("status") == "Review Required"
        if needed and doc.get("reference_type") == "Purchase Receipt":
            source = frappe.db.get_value("Purchase Receipt", doc.get("reference_name"),
                                         ["company", "posting_date", "docstatus", "is_return"], as_dict=True)
            if source and source.docstatus != 2 and not source.is_return:
                company, start = source.company, source.posting_date
            else:
                needed = False
        elif needed:
            blocker = "Incoming QC needs a Purchase Receipt reference for its receipt-based SLA"
    return dict(stage=stage, needed=needed, company=company, start=start, required=required, blocker=blocker)


def calendar_dates(company, start):
    conf = configuration() or {}
    calendar = (conf.get("holiday_lists") or {}).get(company)
    calendar = calendar or (frappe.db.get_value("Company", company, "default_holiday_list") if company else None)
    if not calendar:
        return None
    doc = frappe.get_doc("Holiday List", calendar)
    holidays = [row.holiday_date for row in doc.holidays]
    target = due_date("pca", start, holidays=holidays)
    if not (getdate(doc.from_date) <= getdate(start) <= target <= getdate(doc.to_date)):
        return None
    return holidays


def marker(stage, doctype, name):
    key = hashlib.sha256((VERSION+"|"+stage+"|"+doctype+"|"+name).encode()).hexdigest()
    return "[Calco Purchase Assignment " + key + "]"


def _blocked(message):
    frappe.msgprint("Purchase assignment requires review: " + message, alert=True, indicator="orange")
    frappe.logger("purchase_assignments", allow_site=True).warning(message)
    return dict(status="Review Required", reason=message)


def sync(doc, method=None):
    conf = configuration()
    if not conf or doc.doctype not in STAGES:
        return dict(status="Not eligible")
    # No SQL mutation. Serialize concurrent native assign calls on this source.
    frappe.db.sql("SELECT name FROM `tab" + doc.doctype + "` WHERE name=%s FOR UPDATE", (doc.name,))
    current = frappe.get_doc(doc.doctype, doc.name, for_update=True)
    current.check_permission("read")
    state = requirement(current)
    stage = state["stage"]
    tag = marker(stage, current.doctype, current.name)
    todos = frappe.db.sql("""SELECT name,status,allocated_to,description FROM tabToDo
        WHERE reference_type=%s AND reference_name=%s ORDER BY name FOR UPDATE""",
        (current.doctype,current.name),as_dict=True)
    managed = [row for row in todos if tag in (row.description or "")]
    from frappe.desk.form import assign_to
    if not state["needed"]:
        for row in managed:
            if row.status == "Open":
                from calco_erp.task_timeliness.completion import observing
                with observing("Purchase Service", current.doctype, current.name,
                               cancelled=current.docstatus == 2 or current.get("status") in ("Cancelled", "Stopped")):
                    assign_to.set_status(current.doctype, current.name, todo=row.name,
                                         assign_to=row.allocated_to, status="Closed")
        return dict(status="Satisfied", closed=[row.name for row in managed if row.status=="Open"])
    if not creation_eligible(current, conf) and not managed:
        return dict(status="Not eligible")
    # Native/human assignment takes precedence. Never reclaim, duplicate or auto-reassign.
    if any(row.status == "Open" for row in todos):
        return dict(status="Existing assignment", todos=[row.name for row in todos if row.status=="Open"])
    if managed:
        return _blocked("Previously closed/cancelled assignment for " + current.name + " requires controlled reassignment")
    if state.get("blocker"):
        return _blocked(state["blocker"] + ": " + current.name)
    owner = (conf.get("owners") or {}).get(stage)
    user = frappe.db.get_value("User", owner, ["enabled", "user_type"], as_dict=True) if owner else None
    if not user or not user.enabled or user.user_type != "System User":
        return _blocked("Enabled System User owner is not configured for " + stage)
    if not frappe.has_permission(doc=current, user=owner, ptype="read"):
        return _blocked("Configured owner lacks source-document permission for " + stage)
    holidays = calendar_dates(state["company"], state["start"]) if stage=="pca" and state["start"] else None
    due = due_date(stage, state["start"], state["required"], holidays)
    if not due:
        return _blocked("SLA start/required date or covered company holiday calendar missing for " + current.name)
    priority = "High" if due < getdate(nowdate()) else "Medium"
    args = dict(doctype=current.doctype, name=current.name, assign_to=[owner],
                description=DESCRIPTIONS[stage]+"<br>"+tag,
                date=str(max(due,getdate(nowdate()))), priority=priority)
    assign_to.add(args)
    return dict(status="Created", owner=owner, due_date=args["date"], priority=priority)


def on_related_change(doc, method=None):
    """Recheck only exact linked, prospective source records; never sweep history."""
    if not configuration():
        return
    refs = set()
    if doc.doctype == "Purchase Order":
        refs.update(("Material Request", r.material_request) for r in doc.get("items", []) if r.get("material_request"))
    elif doc.doctype == "Purchase Receipt":
        refs.update(("Purchase Order", r.purchase_order) for r in doc.get("items", []) if r.get("purchase_order"))
    elif doc.doctype == "Purchase Invoice":
        refs.update(("Purchase Receipt", r.purchase_receipt) for r in doc.get("items", []) if r.get("purchase_receipt"))
    elif doc.doctype == "Supplier Quotation":
        refs.update(("Purchase Commercial Approval", n) for n in frappe.get_all(
            "Purchase Commercial Approval", filters={"supplier_quotation": doc.name}, pluck="name"))
    for doctype, name in sorted(refs):
        source = frappe.get_doc(doctype, name)
        # A caller without read authority must not gain assignment access indirectly.
        if frappe.has_permission(doc=source, ptype="read"):
            sync(source)
