"""Opt-in percentage authority on a single native BOM; never backfill history."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import frappe
from frappe.utils import cint
MODE = 'custom_percentage_formulation'
PERCENT = 'custom_formulation_percent'
QUANTUM = Decimal('0.000000001')
VERSION = 'percentage-formulation-v1'
# Management-approved display sequence only. Never reorder native BOM children.
CATEGORY_DISPLAY_SEQUENCE = (
    'Polymer PA', 'Polymer PBT', 'Polymer PC', 'Polymer PE/PP',
    'Polymer POM', 'Polymer Styrene', 'Polymer', 'Glass Fibre',
    'Mineral', 'Additive', 'Pigment', 'Packing',
)
_CATEGORY_RANK = {name: index for index, name in enumerate(CATEGORY_DISPLAY_SEQUENCE)}


def presentation_sort_key(row):
    # Unknown future categories remain distinct after the controlled sequence.
    return (_CATEGORY_RANK.get(row['category'], len(CATEGORY_DISPLAY_SEQUENCE)),
            row['category'], -number(row['percent']), row['item_code'], row['idx'])


def number(value):
    try:
        n = Decimal(str(value if value not in (None, '') else 0))
    except (InvalidOperation, ValueError):
        frappe.throw('Enter a finite formulation quantity.')
    if not n.is_finite():
        frappe.throw('Enter a finite formulation quantity.')
    return n


def category(item):
    return (frappe.db.get_value('Item', item, 'custom_rm_category') or '').strip()


def percentage(value):
    n = number(value)
    canonical = n.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    if abs(n-canonical) > Decimal('0.000000000001'):
        frappe.throw('Percentage supports nine decimal places. Review excess precision; do not round the formulation silently.')
    if canonical <= 0 or canonical > 100:
        frappe.throw('Formulation Percentage must be greater than zero and at most 100.')
    return canonical


def guard_after_submit(doc, method=None):
    old = doc.get_doc_before_save()
    if old and (old.get(MODE) != doc.get(MODE) or
                [(r.name, r.get(PERCENT)) for r in old.items] != [(r.name, r.get(PERCENT)) for r in doc.items]):
        frappe.throw('Submitted formulation requires a controlled successor revision.')


def validate(doc, method=None):
    if not cint(doc.get(MODE)):
        return
    old = doc.get_doc_before_save()
    if old and old.docstatus == 1:
        guard_after_submit(doc)
        return
    if doc.docstatus == 2:
        return
    if doc.uom != 'Kg' or number(doc.quantity) != 100:
        frappe.throw('New percentage formulations require a reviewed 100 Kg executable basis. Historical bases remain unchanged.')
    if frappe.db.get_value('Item', doc.item, 'stock_uom') != 'Kg':
        frappe.throw('Percentage formulation requires FG stock UOM Kg.')
    total = Decimal(0)
    for row in doc.get('items') or []:
        cat = category(row.item_code)
        if not cat:
            frappe.throw('RM Category requires master review for Item {0}.'.format(row.item_code))
        if cat == 'Packing':
            if number(row.get(PERCENT)):
                frappe.throw('Packing is Extra. Retain native Qty/UOM, not a formulation percentage.')
            if number(row.qty) <= 0:
                frappe.throw('Packing requires positive native Qty and valid UOM.')
            continue
        if row.uom != 'Kg' or row.stock_uom != 'Kg' or number(row.conversion_factor) != 1:
            frappe.throw('Formulation material {0} requires Kg and conversion factor 1.'.format(row.item_code))
        value = percentage(row.get(PERCENT))
        total += value
        row.set(PERCENT, float(value))
        row.qty = float(value)
        row.stock_qty = float(value)
    if doc.docstatus == 1 and total != Decimal(100):
        frappe.throw('Formulation Total must equal 100%; current total is {0}%. Packing is excluded.'.format(total))


@frappe.whitelist()
def presentation(document):
    doc = frappe.get_doc(frappe.parse_json(document))
    if doc.doctype != 'BOM':
        frappe.throw('BOM required.')
    doc.check_permission('read' if not doc.is_new() else 'create')
    basis = number(doc.quantity)
    managed = cint(doc.get(MODE)) and doc.docstatus == 0
    total = Decimal(0)
    costs = {'Formulation': Decimal(0), 'Packing': Decimal(0), 'Unclassified': Decimal(0)}
    rows = []
    for row in doc.get('items') or []:
        cat = category(row.item_code)
        role = 'Packing' if cat == 'Packing' else 'Formulation' if cat else 'Unclassified'
        pct = (number(row.get(PERCENT)) if managed else number(row.stock_qty)*100/basis) if role=='Formulation' and basis>0 else None
        if pct is not None:
            total += pct
        costs[role] += number(row.base_amount)
        rows.append(dict(name=row.name, idx=row.idx, item_code=row.item_code, category=cat or 'Classification required',
            role=role, percent=str(pct) if pct is not None else '', qty=row.qty, uom=row.uom))
    rows.sort(key=presentation_sort_key)
    return dict(version=VERSION, rows=rows, total=str(total), basis=str(basis),
        complete=not any(r['role']=='Unclassified' for r in rows), currency=frappe.db.get_value('Company', doc.company, 'default_currency'),
        formulation_cost_per_kg=str(costs['Formulation']/basis if basis>0 else 0),
        packing_cost=str(costs['Packing']), unclassified_cost=str(costs['Unclassified']), total_material_cost=str(sum(costs.values())))


def definitions():
    """Targeted metadata only. No Item/BOM backfill and no routing changes."""
    result = []
    def field(dt, name, **values):
        result.append(dict(doctype='Custom Field', name=dt+'-'+name, child=False,
            document=dict(doctype='Custom Field', name=dt+'-'+name, dt=dt, fieldname=name, **values)))
    field('BOM', MODE, label='Percentage Formulation', fieldtype='Check', default='0',
        insert_after='quantity', description='New reviewed 100 Kg formulations; Packing remains Extra in the same BOM.')
    field('BOM', 'custom_formulation_view', label='Formulation and Packing', fieldtype='HTML', insert_after='items')
    field('BOM Item', PERCENT, label='Formulation Percentage', fieldtype='Float', precision='9', insert_after='qty')
    cf = frappe.get_doc('Custom Field', 'Item-custom_rm_category')
    options = (cf.options or '').splitlines()
    if 'Packing' not in options:
        options.append('Packing')
    field('Item', 'custom_rm_category', label=cf.label, fieldtype=cf.fieldtype, options='\n'.join(options))
    for name in ('qty', 'stock_qty'):
        result.append(dict(doctype='Property Setter', name='BOM Item-'+name+'-precision', child=False,
            document=dict(doctype='Property Setter', name='BOM Item-'+name+'-precision', doc_type='BOM Item',
                doctype_or_field='DocField', field_name=name, property='precision', property_type='Select', value='9')))
    return result
