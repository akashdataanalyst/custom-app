"""Deterministic planning math. Future supply never participates in current capacity."""
from decimal import Decimal, ROUND_DOWN


def number(value):
    result=Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError('Non-finite planning quantity')
    return result


def allocate(requirements, materials, precision=3):
    """Inputs are already authorized released stock/commitment evidence.

    Does not infer release eligibility from warehouse names or PO schedules.
    Returns separate physical, commitment and future-supply measures.
    """
    pool={k:max(number(v['released'])-number(v['committed']),Decimal(0)) for k,v in materials.items()}
    expected_pool={k:max(number(v.get("expected_po")),Decimal(0)) for k,v in materials.items()}
    order=sorted(requirements,key=lambda r:(0 if number(r.get('sales_order_qty'))>0 else 1,
        str(r.get('required_date') or '9999-12-31'),
        {'High':0,'Normal':1,'Low':2}.get(r.get('priority'),1),r['key']))
    output=[]
    for row in order:
        requested=max(number(row['remaining']),Decimal(0))
        components={k:number(v) for k,v in row['components'].items()}
        if not components or any(v<=0 for v in components.values()):
            raise ValueError('Approved BOM has no valid positive material requirements')
        for k in components:
            if k not in materials:raise ValueError('Missing material eligibility evidence: '+k)
        buildable=min([requested]+[pool[k]/q for k,q in components.items()])
        buildable=buildable.quantize(Decimal(1).scaleb(-precision),rounding=ROUND_DOWN)
        details=[]
        for k,q in sorted(components.items()):
            need=requested*q;available=pool[k];short=max(need-available,Decimal(0));m=materials[k]
            expected=min(short,expected_pool[k])
            expected_pool[k]-=expected
            details.append(dict(item_code=k,required=str(need),released_physical=str(number(m['released'])),
                existing_commitments=str(number(m['committed'])),net_buildable_stock=str(available),
                current_shortage=str(short),expected_po_supply=str(expected),
                expected_receipt_date=m.get('expected_receipt_date'),production_eligible_eta=m.get('eligible_eta'),
                remaining_after_expected=str(max(short-expected,Decimal(0))),allocated_now=str(buildable*q),
                evidence=m.get('evidence',[])))
            pool[k]=max(available-buildable*q,Decimal(0))
        output.append(dict(key=row['key'],item_code=row['item_code'],bom=row['bom'],
            approved_remaining=str(requested),buildable_now=str(buildable),waiting_for_rm=str(requested-buildable),
            status='Ready' if buildable==requested else 'Partially Buildable' if buildable>0 else 'Waiting for RM',
            materials=details))
    return output
