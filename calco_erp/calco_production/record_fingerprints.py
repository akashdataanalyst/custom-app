"""Versioned, field-precision fingerprints for controlled infrastructure records.

Opaque source JSON remains exact evidence. Only metadata-typed business numbers
(and the explicit quantity Data field) are normalized; identities stay strings.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import frappe

VERSION = 'record-fp-v2'


def numeric(df, value, doc):
    from frappe.model.meta import get_field_precision
    precision = get_field_precision(df, doc)
    try:
        number = Decimal(str(value if value not in (None, '') else 0))
        if not number.is_finite():
            raise InvalidOperation
        with localcontext() as ctx:
            ctx.prec = max(38, len(number.as_tuple().digits) + precision + 4)
            value = number.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_EVEN)
        if not value:
            value = abs(value)
        return format(value, 'f')
    except (InvalidOperation, ValueError, TypeError):
        frappe.throw('Invalid numeric controlled evidence.')


def body(doc):
    from calco_erp.calco_production import receipt_policy_infrastructure as infra
    result = infra.record_body(doc)
    for df in doc.meta.fields:
        if df.fieldname not in result:
            continue
        if df.fieldtype in ('Float', 'Currency', 'Percent'):
            result[df.fieldname] = numeric(df, doc.get(df.fieldname), doc)
        elif df.fieldtype == 'Table':
            rows = []
            meta = frappe.get_meta(df.options)
            for row in doc.get(df.fieldname) or []:
                values = {}
                for child in meta.fields:
                    if not child.fieldname:
                        continue
                    value = row.get(child.fieldname)
                    quantity_data = df.options == 'Production Closure Material' and child.fieldname == 'final_actual_qty'
                    if quantity_data and value in (None, ''):
                        frappe.throw('Explicit final physical quantity is required.')
                    if child.fieldtype in ('Float', 'Currency', 'Percent') or quantity_data:
                        spec = meta.get_field('accounted_qty') if quantity_data else child
                        values[child.fieldname] = numeric(spec, value, row)
                    else:
                        values[child.fieldname] = infra.field_value(child, value)
                rows.append(values)
            if df.options == 'Production Closure Material':
                rows.sort(key=lambda r: (r['item_code'], r['batch_no']))
            result[df.fieldname] = rows
    return dict(version=VERSION, doctype=doc.doctype, name=doc.name, business=result)


def fingerprint(doc):
    from calco_erp.calco_production import receipt_policy_infrastructure as infra
    return VERSION + ':' + infra.fingerprint(body(doc))


def verified(doc):
    """Exact proof first; never accept a legacy hash merely on an epsilon match."""
    from calco_erp.calco_production import receipt_policy_infrastructure as infra
    stored = doc.get('fingerprint') or ''
    if stored.startswith(VERSION + ':'):
        return stored == fingerprint(doc)
    if stored == infra.fingerprint(infra.record_body(doc)):
        return True
    if doc.doctype != 'Production Batch Closure':
        return False
    source = infra.payload(doc.source_snapshot)
    if source.get('version') != 'physical-batch-closure-v1':
        return False
    # The original hash authenticates every identity, source JSON, version and
    # measurement. Recover only pre-persistence issued/accounted numeric values.
    recovered = frappe.get_doc(deepcopy(doc.as_dict()))
    for row in recovered.materials:
        evidence = infra.payload(row.source_evidence)
        if any(evidence.get(k) != row.get(k) for k in ('item_code', 'batch_no')):
            return False
        for field in ('issued_qty', 'accounted_qty'):
            if field not in evidence:
                return False
            row.set(field, evidence[field])
    if infra.fingerprint(infra.record_body(recovered)) != stored:
        return False
    return body(recovered) == body(doc)


def seal(doc):
    if not verified(doc):
        frappe.throw('Controlled evidence fingerprint mismatch.')
    return dict(algorithm=VERSION, original_fingerprint=doc.fingerprint,
        semantic_fingerprint=fingerprint(doc), record=doc.name, revision=doc.revision)
