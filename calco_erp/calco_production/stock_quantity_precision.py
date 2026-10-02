"""Prospective stock quantity authority; no historical snapshot rewriting."""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP
import frappe

VERSION = 'native-stock-quantity-v1'
RULE = 'cumulative-largest-remainder-item-batch-v1'
KG_POLICY = 'production-kg-3dp-v1'
KG_PRECISION = 3


def decimal(value):
    value = Decimal(str(value or 0))
    if not value.is_finite():
        frappe.throw('Finite stock quantity required.')
    return value


def canonical(value):
    value = decimal(value)
    return format(value.normalize(), 'f') if value else '0'


def quantize(value, precision, method="Banker's Rounding (legacy)"):
    modes = {"Banker's Rounding (legacy)": ROUND_HALF_EVEN,
             "Banker's Rounding": ROUND_HALF_EVEN, "Commercial Rounding": ROUND_HALF_UP}
    if method not in modes:
        frappe.throw('Unsupported native rounding method; Management Review Required.')
    return decimal(value).quantize(Decimal(1).scaleb(-precision), rounding=modes[method])


def authority(document, detail):
    # Same parent precision call used by native StockEntry.set_transfer_qty().
    factor = decimal(detail.conversion_factor)
    if factor != 1 or detail.uom != detail.stock_uom:
        frappe.throw('Provisional stock precision requires stock-UOM entry with conversion factor 1; Management Review Required.')
    stock_precision = document.precision('transfer_qty', detail)
    qty_precision = document.precision('qty', detail)
    if stock_precision is None or qty_precision is None:
        frappe.throw('Native quantity precision could not be resolved.')
    whole = bool(frappe.get_cached_value('UOM', detail.stock_uom, 'must_be_whole_number'))
    precision = 0 if whole else min(stock_precision, qty_precision)
    if detail.stock_uom == 'Kg':
        if whole or precision < KG_PRECISION:
            frappe.throw('Native Kg stock precision must support 3 decimal places; Management Review Required. No settings were changed.')
        precision = KG_PRECISION
    return dict(doctype='Stock Entry Detail', field='transfer_qty', stock_uom=detail.stock_uom,
        entry_uom=detail.uom, conversion_factor='1', qty_precision=qty_precision,
        transfer_qty_precision=stock_precision, posting_precision=precision,
        must_be_whole_number=whole, operational_policy=KG_POLICY if detail.stock_uom == 'Kg' else VERSION,
        rounding_method=frappe.get_system_settings('rounding_method') or "Banker's Rounding (legacy)")


def apportion(rows, target):
    """Balanced rounding of uncovered quantities, with persistent per-batch carry.

    Largest fractional remainder first; stable Item/Batch identity breaks ties.
    WIP caps are physical constraints. No arbitrary row-order allocation.
    """
    if not rows:
        if decimal(target):frappe.throw('No provisional material entitlement.')
        return [], {'version':RULE,'residual':'0'}
    precisions={r['precision_authority']['posting_precision'] for r in rows}
    if len(precisions)!=1:
        frappe.throw('Mixed quantity precision requires an approved mass allocation; Management Review Required.')
    precision=precisions.pop();step=Decimal(1).scaleb(-precision)
    target=decimal(target)
    if target != quantize(target,precision):
        frappe.throw('Uncovered FG mass is not representable at native stock precision; Management Review Required.')
    result=[]
    for source in sorted(rows,key=lambda r:(r['item_code'],r['batch_no'])):
        row=dict(source);raw=decimal(row['calculated_qty']);carry=decimal(row.get('rounding_carry_in'))
        adjusted=raw+carry;cap=decimal(row['evidence_source']['available'])
        if adjusted<0:
            frappe.throw('Prior rounding exceeds this interval entitlement; Management Review Required.')
        floor=(adjusted/step).to_integral_value(rounding=ROUND_FLOOR)*step
        cap=(cap/step).to_integral_value(rounding=ROUND_FLOOR)*step
        row['_adjusted']=adjusted;row['_cap']=cap;row['_post']=min(floor,cap)
        row['calculated_qty']=canonical(raw);row['rounding_carry_in']=canonical(carry)
        row['posting_precision']=precision
        result.append(row)
    # First preserve each material's rounded share; then apportion that share
    # across its exact batches. Many batches must not bias another RM's share.
    groups={}
    for row in result:groups.setdefault(row['item_code'],[]).append(row)
    items=[]
    for item, members in sorted(groups.items()):
        adjusted=sum(r['_adjusted'] for r in members)
        cap=sum(r['_cap'] for r in members)
        items.append(dict(item_code=item,batch_no='',_adjusted=adjusted,_cap=cap,
            _post=min((adjusted/step).to_integral_value(rounding=ROUND_FLOOR)*step,cap)))
    def distribute(candidates,total):
        residual=total-sum(r['_post'] for r in candidates)
        units=residual/step
        if units<0 or units!=units.to_integral_value() or units>len(candidates):
            frappe.throw('Rounding residual cannot be allocated within formulation/WIP constraints; Management Review Required.')
        ranked=sorted(candidates,key=lambda r:(-(r['_adjusted']-r['_post']),r['item_code'],r['batch_no']))
        remaining=int(units)
        for row in ranked:
            if not remaining:break
            if row['_post']+step<=row['_cap']:
                row['_post']+=step;remaining-=1
        if remaining:frappe.throw('Insufficient exact batch WIP for canonical rounding; Management Review Required.')
        return residual
    residual=distribute(items,target)
    item_audit=[]
    for item in items:
        batch_residual=distribute(groups[item['item_code']],item['_post'])
        item_audit.append(dict(item_code=item['item_code'],postable_qty=canonical(item['_post']),
            adjusted_calculated_qty=canonical(item['_adjusted']),batch_residual=canonical(batch_residual)))
    for row in result:
        post=row.pop('_post');row.pop('_cap');row.pop('_adjusted')
        row['postable_qty']=row['provisional_qty']=canonical(post)
        row['rounding_difference']=canonical(post-decimal(row['calculated_qty']))
        row['rounding_carry_out']=canonical(decimal(row['calculated_qty'])+decimal(row['rounding_carry_in'])-post)
    return result,dict(version=RULE,precision=precision,target=canonical(target),
        floor_residual=canonical(residual),residual='0',tie_break='item_code,batch_no',item_allocation=item_audit,
        calculated_total=canonical(sum(decimal(r['calculated_qty']) for r in result)),
        postable_total=canonical(sum(decimal(r['postable_qty']) for r in result)))


def require_exact(value, expected, precision):
    # Normalization cannot hide a nonrepresentable value; compare exact decimals.
    observed=decimal(value);wanted=decimal(expected)
    if observed!=quantize(observed,precision) or wanted!=quantize(wanted,precision) or observed!=wanted:
        frappe.throw('Posted stock quantity differs from its frozen canonical authority.')


def operational_qty(value):
    """Explicit operator measurement; never treat blank as zero or hide excess precision."""
    from calco_erp.calco_production.receipt_policy_infrastructure import quantity
    measured = quantity(value)
    if measured != quantize(measured, KG_PRECISION):
        frappe.throw('Enter Final Actual Consumption in Kg with at most 3 decimal places.')
    return measured
