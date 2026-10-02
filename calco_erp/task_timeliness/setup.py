"""Explicit targeted PLAN/APPLY only; no broad migrate or historical backfill."""
import json
from pathlib import Path
import frappe
from calco_erp.task_timeliness.completion import FIELDS

def definitions():
    types = ("Datetime", "Link", "Data", "Check")
    labels = ("First Governed Completion", "Completed By", "Completion Authority", "Completion Review Required")
    return [dict(dt="ToDo",fieldname=field,label=labels[i],fieldtype=types[i],
        insert_after="status" if i==0 else FIELDS[i-1],read_only=1,no_copy=1,
        options="User" if i==1 else "",default="0" if i==3 else "") for i,field in enumerate(FIELDS)]

def report_definition():
    path=Path(__file__).parents[1]/"calco_management_review/report/task_timeliness_by_employee/task_timeliness_by_employee.json"
    return json.loads(path.read_text())

def sync(apply=False):
    frappe.only_for("System Manager")
    if isinstance(apply,str):
        apply=apply.lower() in ("1","true")
    changes=[]
    for spec in definitions():
        name="ToDo-"+spec["fieldname"]
        existing=frappe.db.exists("Custom Field",name)
        doc=frappe.get_doc("Custom Field",name) if existing else None
        if doc and doc.fieldtype != spec["fieldtype"]:
            frappe.throw("Conflicting completion field type: "+name)
        diff={k:v for k,v in spec.items() if not doc or (doc.get(k) or "") != (v or "")}
        if diff:
            changes.append(dict(doctype="Custom Field",name=name,changes=diff))
            if apply:
                if doc: doc.update(diff);doc.save()
                else: frappe.get_doc(dict(doctype="Custom Field",**spec)).insert()
    spec=report_definition();doc=frappe.get_doc("Report",spec["name"])
    keys=("module","report_type","is_standard","query","report_script","javascript","filters")
    diff={k:spec.get(k) for k in keys if (doc.get(k) or None)!=(spec.get(k) or None)}
    if diff:
        changes.append(dict(doctype="Report",name=doc.name,changes=diff))
        if apply:
            # Native standard Report validation explicitly permits controlled app patches.
            # Preserve permissions/roles; no global developer-mode or migrate change.
            previous = frappe.flags.in_patch
            try:
                frappe.flags.in_patch = True
                doc.update(diff);doc.save()
            finally:
                frappe.flags.in_patch = previous
    if apply:
        frappe.clear_cache(doctype="ToDo")
        frappe.clear_cache(doctype="Report")
        frappe.clear_cache()  # Refresh merged app hooks after the scoped source installation.
    return dict(mode="APPLY" if apply else "PLAN",changes=changes)
