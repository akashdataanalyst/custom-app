"""Reviewed prospective metadata; no automatic apply/migration hook."""
import json
from pathlib import Path
import frappe

ORDER = ['Forecasting', 'Sales Forecast', 'Item Lead Time', 'Master Production Schedule',
         'Production Plan', 'Production Planning Report']


def ordered_items(items):
    items = [dict(row) for row in items]
    start = next((i for i,row in enumerate(items) if row.get('type')=='Section Break'
                  and row.get('label')=='Material Planning'), None)
    if start is None:
        return items
    end = start+1
    while end < len(items) and items[end].get('child'):
        end += 1
    ranks = {label:i for i,label in enumerate(ORDER)}
    items[start+1:end] = sorted(items[start+1:end], key=lambda row:ranks.get(row.get('label'),len(ranks)))
    for index,row in enumerate(items,1):
        row["idx"] = index
    return items


def definitions():
    rows=json.loads(Path(__file__).with_name('bom_metadata.json').read_text())
    from calco_erp.calco_purchase.purchase_journey import CLIENT_SCRIPT_NAME, purchase_journey_client_script
    rows.append(dict(doctype='Client Script',name=CLIENT_SCRIPT_NAME,child=False,document=dict(
        doctype='Client Script',name=CLIENT_SCRIPT_NAME,dt='Material Request',view='Form',enabled=1,
        script=purchase_journey_client_script())))
    # Frappe v16 validates title_field against DocFields; 'name' is a standard
    # identity, not a DocField. Blank retains native name-as-title behavior.
    rows.append(dict(doctype='Property Setter',name='Production Requirement-main-title_field',child=False,apply_priority=1.5,
        document=dict(doctype='Property Setter',name='Production Requirement-main-title_field',
            doc_type='Production Requirement',doctype_or_field='DocType',property='title_field',
            property_type='Data',value='')))
    rows.append(dict(doctype='Custom Field',name='Production Requirement-custom_rm_planning_snapshot',child=False,
        document=dict(doctype='Custom Field',name='Production Requirement-custom_rm_planning_snapshot',
            dt='Production Requirement',fieldname='custom_rm_planning_snapshot',label='Released RM Planning Evidence',
            fieldtype='Long Text',read_only=1,no_copy=1,hidden=1)))
    for field,kind,label,unique in [
        ('custom_rm_planning_key','Data','Planning Procurement Request',1),
        ('custom_rm_planning_evidence','Long Text','Planning Procurement Evidence',0)]:
        name='Material Request-'+field
        rows.append(dict(doctype='Custom Field',name=name,child=False,document=dict(
            doctype='Custom Field',name=name,dt='Material Request',fieldname=field,
            fieldtype=kind,label=label,read_only=1,no_copy=1,hidden=1,unique=unique)))
    if frappe.db.exists('Workspace Sidebar','Manufacturing'):
        current=frappe.get_doc('Workspace Sidebar','Manufacturing').as_dict()
        # Project only ordering/content; preserve permissions, unknown links,
        # sections and all existing child attributes. PLAN fingerprint guards race.
        rows.append(dict(doctype='Workspace Sidebar',name='Manufacturing',child=False,
            document=dict(doctype='Workspace Sidebar',name='Manufacturing',items=ordered_items(current.get('items',[])))))
    return rows
