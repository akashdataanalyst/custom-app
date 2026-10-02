"""Batched, due-date-cohort management report. All times are Frappe site-local."""
from collections import defaultdict
from datetime import date
import frappe
from frappe.utils import getdate, now_datetime
from calco_erp.task_timeliness.completion import FIELDS, governed_kind

NAME = "Task Timeliness by Employee"
ROLES = {"HR Manager", "HR User", "System Manager"}

def quarter(day):
    day = getdate(day)
    month = ((day.month - 1) // 3) * 3 + 1
    start = date(day.year, month, 1)
    end = date(day.year + (month == 10), 1 if month == 10 else month + 3, 1)
    from datetime import timedelta
    return start, end - timedelta(days=1)

def period(filters, today):
    defaults = quarter(today)
    filters = filters or {}
    start, end = getdate(filters.get("from_date") or defaults[0]), getdate(filters.get("to_date") or defaults[1])
    if start > end:
        frappe.throw("From Date must not be after To Date")
    return start, end

def bucket(row, today):
    if not row.get("date"):
        return "missing_due"
    if row.get("status") == "Cancelled" or "Cancelled source" in (row.get(FIELDS[2]) or ""):
        return "cancelled"
    if row.get(FIELDS[3]):
        return "review_required"
    if row.get("status") == "Closed":
        completed = row.get(FIELDS[0])
        if not completed:
            return "unknown_completion"
        return "on_time" if getdate(completed) <= getdate(row["date"]) else "late"
    return "overdue" if getdate(row["date"]) < getdate(today) else "pending"

def aggregate(todos, users, employees, today, include_administrator=False):
    # Group employees by exact login identity; never pick one arbitrarily.
    mapping = defaultdict(list)
    for employee in employees:
        if employee.get("user_id"):
            mapping[employee["user_id"]].append(employee)
    result = {}
    for todo in todos:
        user = todo.get("allocated_to")
        if not user or (user == "Administrator" and not include_administrator):
            continue
        if user not in result:
            matches = mapping[user]
            u = users.get(user) or {}
            result[user] = dict(user=user, employee=matches[0].get("employee_name") if len(matches)==1 else user,
                employee_mapping="Mapped" if len(matches)==1 else "Ambiguous" if matches else "No Employee",
                user_state="Missing User" if not u else "Disabled" if not u.get("enabled") else u.get("user_type"),
                **{key:0 for key in ("total_due","on_time","late","overdue","pending","unknown_completion",
                    "manual_other","missing_due","cancelled","review_required")})
        out = result[user]
        kind = todo.get(FIELDS[2]) or governed_kind(todo)
        governed = kind in ("Purchase Service", "Native Task", "Purchase Service / Cancelled source")
        if not todo.get("date"):
            out["missing_due"] += 1
        elif not governed:
            out["manual_other"] += 1
        else:
            out["total_due"] += 1
            out[bucket(todo,today)] += 1
    for out in result.values():
        denominator = out["on_time"]+out["late"]+out["overdue"]
        out["on_time_percent"] = round(100*out["on_time"]/denominator,1) if denominator else None
    return sorted(result.values(), key=lambda x:(x["on_time_percent"] is None,x["on_time_percent"] or 0,x["user"]))

def require_management():
    if frappe.session.user != "Administrator" and not ROLES.intersection(frappe.get_roles()):
        frappe.throw("Not permitted to view task timeliness", frappe.PermissionError)
    if frappe.session.user != "Administrator":
        from calco_erp.task_timeliness.access import require_desk_user
        require_desk_user()

def execute(filters=None):
    require_management()
    now = now_datetime()  # Frappe converts to System Settings time_zone, not DB/server timezone.
    start,end = period(filters,now)
    todos = frappe.db.sql("""SELECT name,allocated_to,reference_type,reference_name,description,status,date,
        custom_calco_completed_on,custom_calco_completed_by,custom_calco_completion_authority,
        custom_calco_completion_reopened FROM tabToDo
        WHERE allocated_to IS NOT NULL AND allocated_to!='' AND allocated_to!='Administrator'
        AND (date BETWEEN %s AND %s OR date IS NULL)""",(start,end),as_dict=True)
    ids = sorted({t.allocated_to for t in todos})
    users = {u.name:u for u in frappe.get_all("User",filters={"name":["in",ids]},fields=["name","enabled","user_type"],limit_page_length=0)} if ids else {}
    employees = frappe.get_all("Employee",filters={"user_id":["in",ids]},fields=["name","employee_name","user_id","status"],limit_page_length=0) if ids else []
    data = aggregate(todos,users,employees,now)
    columns = [dict(fieldname="employee",label="Employee",fieldtype="Data",width=170),
               dict(fieldname="user",label="User",fieldtype="Link",options="User",width=200)]
    for field,label in (("total_due","Total Due"),("on_time","On Time"),("late","Late"),("overdue","Overdue"),
       ("pending","Pending"),("unknown_completion","Unknown Completion"),("on_time_percent","On-Time %"),
       ("manual_other","Manual / Other (Unscored)"),("cancelled","Cancelled / Ended"),
       ("review_required","Reopened / Changed (Unscored)"),("missing_due","No Due Date (All Dates)")):
        columns.append(dict(fieldname=field,label=label,fieldtype="Percent" if field=="on_time_percent" else "Int",width=130))
    columns += [dict(fieldname="employee_mapping",label="Employee Mapping",fieldtype="Data",width=120),
                dict(fieldname="user_state",label="User State",fieldtype="Data",width=120)]
    message = ("Due-date cohort; current status as of " + str(now) + " (site timezone). Due through end of local date. "
        "On-Time % = On Time / (On Time + Late + Overdue). Pending, unknown, cancelled, reopened/changed and manual/other are unscored. "
        "No-due counts cover all dates and are excluded from Total Due. Disabled/Website/unmapped users remain identified, not silently dropped. "
        "This report does not update appraisals; historical completion is never inferred from modified.")
    return columns,data,message
