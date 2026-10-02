"""Explicit management versus session-owned self-service; no identity from input."""
import frappe
from frappe.utils import now_datetime
from calco_erp.task_timeliness import report as engine
from calco_erp.task_timeliness.completion import FIELDS, governed_kind

LABELS = dict(on_time="On Time", late="Late", overdue="Overdue", pending="Pending",
    unknown_completion="Unknown Completion", cancelled="Cancelled / Ended",
    review_required="Reopened / Changed", missing_due="No Due Date")

def require_desk_user():
    user = frappe.session.user
    info = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
    if user == "Guest" or not info or not info.enabled or info.user_type != "System User":
        frappe.throw("An enabled Desk user is required.", frappe.PermissionError)
    return user

def dates(filters):
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    filters = filters or {}
    # Deliberately discard identity, route, SQL, pagination and other arguments.
    return {k:filters[k] for k in ("from_date", "to_date") if k in filters}

def population(user, filters):
    now = now_datetime()
    start, end = engine.period(dates(filters), now)
    rows = frappe.db.sql("""SELECT name,allocated_to,reference_type,reference_name,description,status,date,
        custom_calco_completed_on,custom_calco_completed_by,custom_calco_completion_authority,
        custom_calco_completion_reopened FROM tabToDo
        WHERE allocated_to=%s AND (date BETWEEN %s AND %s OR date IS NULL)
        ORDER BY date,name""", (user,start,end), as_dict=True)
    return rows, now, start, end

def details(rows, now):
    result = []
    for row in rows:
        if governed_kind(row) == "Manual / Other" and row.get(FIELDS[2]) not in (
                "Purchase Service", "Native Task", "Purchase Service / Cancelled source"):
            continue
        result.append(dict(assignment=row.name, reference_type=row.reference_type,
            reference=row.reference_name, due_date=row.date, completed_on=row.get(FIELDS[0]),
            status=row.status, classification=LABELS[engine.bucket(row,now)]))
    return result

@frappe.whitelist()
def my_timeliness(filters=None, **ignored):
    user = require_desk_user()  # Always session identity, including Administrator.
    employees = frappe.get_all("Employee", filters={"user_id":user,"status":"Active"},
        fields=["name","employee_name","user_id","status"], limit_page_length=0)
    if len(employees)>1:
        frappe.throw("Multiple active Employee records are linked to your ERP user. Contact HR.")
    if not employees:
        return dict(message="No active Employee record is linked to your ERP user.",
            employee=None, summary=None, assignments=[])
    rows, now, start, end = population(user, filters)
    summary = engine.aggregate(rows,{user:dict(enabled=1,user_type="System User")},employees,now,include_administrator=True)
    # Administrator has no special self-service view of other people's data.
    empty = {k:0 for k in ("total_due","on_time","late","overdue","pending","unknown_completion",
        "manual_other","missing_due","cancelled","review_required")}
    empty["on_time_percent"]=None
    return dict(employee=employees[0], summary=summary[0] if summary else empty,
        assignments=details(rows,now), from_date=str(start),to_date=str(end),as_of=str(now),
        message="Own assignments only. Manual/other, missing due dates and unknown completion are unscored. No historical completion is inferred from modified.")

@frappe.whitelist()
def management_assignments(user, filters=None, **ignored):
    engine.require_management()
    if not user or user=="Administrator":
        frappe.throw("Select a reported User.")
    rows, now, start, end = population(user, filters)
    return dict(user=user,assignments=details(rows,now),from_date=str(start),to_date=str(end))
