"""Targeted PLAN/APPLY; no business adoption, fixtures or historical changes."""
import json
from pathlib import Path
import frappe

def sync(apply=False):
    frappe.only_for("System Manager")
    if isinstance(apply, str): apply = apply.lower() in ("true", "1")
    folder = Path(__file__).parents[1]/"calco_management_review/doctype/task_timeliness_appraisal_evidence"
    spec = json.loads((folder/"task_timeliness_appraisal_evidence.json").read_text())
    changes = []
    if not frappe.db.exists("DocType", spec["name"]):
        changes.append(dict(doctype="DocType", name=spec["name"]))
        if apply:
            from frappe.modules.import_file import import_doc
            before_patch, before_import = frappe.flags.in_patch, frappe.flags.in_import
            try:
                frappe.flags.in_patch = True
                import_doc(spec, pre_process=lambda d: setattr(d.flags,"do_not_update_json",True))
            finally: frappe.flags.in_patch, frappe.flags.in_import = before_patch, before_import
    else:
        meta = frappe.get_meta(spec["name"])
        if not meta.istable or [(x.fieldname,x.fieldtype) for x in meta.fields] != [(x["fieldname"],x["fieldtype"]) for x in spec["fields"]]:
            frappe.throw("Conflicting Task Timeliness evidence schema; explicit review required.")
    definition = dict(dt="Appraisal", fieldname="custom_task_timeliness_evidence", label="Task Timeliness Evidence",
        fieldtype="Table", options=spec["name"], insert_after="goal_score_percentage", read_only=1, no_copy=1, allow_on_submit=0)
    name="Appraisal-"+definition["fieldname"]
    if not frappe.db.exists("Custom Field",name):
        changes.append(dict(doctype="Custom Field",name=name))
        if apply: frappe.get_doc(dict(doctype="Custom Field",**definition)).insert()
    else:
        doc=frappe.get_doc("Custom Field",name)
        if any((doc.get(k) or 0)!=(v or 0) for k,v in definition.items()): frappe.throw("Conflicting Task Timeliness Appraisal field; explicit review required.")
    if apply: frappe.clear_cache()
    return dict(mode="APPLY" if apply else "PLAN",changes=changes)
