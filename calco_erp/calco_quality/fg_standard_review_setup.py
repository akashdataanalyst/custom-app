"""Explicit, idempotent installation of the Quality review entry point only."""
import json
import frappe

def install():
    if frappe.session.user != 'Administrator':
        frappe.throw('Administrator installation required.', frappe.PermissionError)
    frappe.reload_doc('calco_quality', 'doctype', 'quality_parameter_mapping_revision')
    frappe.reload_doc('calco_quality', 'page', 'fg_standard_review')
    workspace=frappe.get_doc('Workspace','Quality')
    changed=False
    if not any(r.link_to=='fg-standard-review' for r in workspace.shortcuts):
        workspace.append('shortcuts',{'label':'FG Standard Review','type':'Page','link_to':'fg-standard-review','color':'Grey'})
        changed=True
    content=json.loads(workspace.content or '[]')
    if not any(r.get('id')=='fg-standard-review' for r in content):
        content.append({'id':'fg-standard-review','type':'shortcut','data':{'shortcut_name':'FG Standard Review','col':3}})
        workspace.content=json.dumps(content);changed=True
    if changed:workspace.save()
    frappe.clear_cache(doctype='Workspace')
    return {'page':'fg-standard-review','workspace_changed':changed}
