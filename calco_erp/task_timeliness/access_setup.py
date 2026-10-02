"""Targeted additive Page/workspace sync. Does not broaden management roles."""
import json
from pathlib import Path
import frappe

NAV = (("Management Review", "Task Timeliness", "Report", "Task Timeliness by Employee"),
       ("Performance", "My Task Timeliness", "Page", "my-task-timeliness"))

def sync(apply=False):
    frappe.only_for("System Manager")
    if isinstance(apply,str): apply=apply.lower() in ("true","1")
    changes=[]
    spec=json.loads((Path(__file__).parents[1]/"calco_management_review/page/my_task_timeliness/my_task_timeliness.json").read_text())
    existing=frappe.db.exists("Page",spec["name"])
    doc=frappe.get_doc("Page",spec["name"]) if existing else frappe.get_doc(spec)
    keys=("title","module","standard","system_page")
    if not existing or any(doc.get(k)!=spec.get(k) for k in keys) or sorted(x.role for x in doc.roles)!=["All"]:
        changes.append(dict(doctype="Page",name=spec["name"]))
        if apply:
            for k in keys:doc.set(k,spec[k])
            doc.set("roles",spec["roles"])
            previous=frappe.flags.in_patch
            try:
                frappe.flags.in_patch=True
                if existing:
                    doc.flags.do_not_update_json=True
                    doc.save()
                else:
                    # Standard Page creation uses native metadata import; no site developer mode.
                    from frappe.modules.import_file import import_doc
                    old_import=frappe.flags.in_import
                    try:
                        import_doc(dict(spec),pre_process=lambda d:setattr(d.flags,"do_not_update_json",True))
                    finally:frappe.flags.in_import=old_import
            finally:frappe.flags.in_patch=previous
    for workspace,label,kind,target in NAV:
        doc=frappe.get_doc("Workspace",workspace)
        content=json.loads(doc.content or "[]")
        has_shortcut=any(x.label==label and x.link_to==target and x.type==kind for x in doc.shortcuts)
        block_id="calco-task-timeliness-"+workspace.lower().replace(" ","-")
        has_block=any(x.get("id")==block_id for x in content)
        if not has_shortcut or not has_block:
            changes.append(dict(doctype="Workspace",name=workspace))
            if apply:
                if not has_shortcut:doc.append("shortcuts",dict(label=label,type=kind,link_to=target,doc_view="",color="Blue"))
                if not has_block:content.append(dict(id=block_id,type="shortcut",data=dict(shortcut_name=label,col=3)))
                doc.content=json.dumps(content);doc.save()
        sidebar=frappe.get_doc("Workspace Sidebar",workspace)
        if not any(x.label==label and x.link_to==target and x.link_type==kind for x in sidebar.items):
            changes.append(dict(doctype="Workspace Sidebar",name=workspace))
            if apply:
                sidebar.append("items",dict(label=label,type="Link",link_type=kind,link_to=target,child=0));sidebar.save()
    if apply:frappe.clear_cache()
    return dict(mode="APPLY" if apply else "PLAN",changes=changes)
