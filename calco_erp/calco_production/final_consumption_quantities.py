"""Exact final-consumption quantity planning, independent of stock posting.

Only additional measured actual is entered. Provisional WIP relief is never
carried forward as measured consumption. Posting remains a separate authority.
"""
from decimal import Decimal, InvalidOperation


class FinalConsumptionError(ValueError):
    pass


def quantity(value, label):
    if value is None or not str(value).strip():
        raise FinalConsumptionError(f'{label} must be explicitly entered; enter 0 for zero.')
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FinalConsumptionError(f'{label} must be a finite non-negative quantity.') from exc
    if not result.is_finite() or result < 0:
        raise FinalConsumptionError(f'{label} must be a finite non-negative quantity.')
    return result


def plan(materials, measurements):
    """Return measured totals and unresolved deltas keyed by exact RM/batch.

    `prior_actual_qty` must come from the immutable initial confirmation baseline
    when correcting a confirmation, not from the prior version's final quantity.
    `accounted_qty` is current net physical WIP relief, including provisional
    relief and any legitimate, explicitly referenced restoration.
    """
    expected = {}
    for source in materials:
        key = source.get('item_code'), source.get('batch_no')
        if not all(key) or key in expected:
            raise FinalConsumptionError('Each authoritative RM item and batch must occur once.')
        expected[key] = source
    if not expected:
        raise FinalConsumptionError('No authoritative material lineage is available.')
    seen = set()
    result = []
    for supplied in measurements:
        if set(supplied) != {'item_code', 'batch_no', 'additional_actual_qty'}:
            raise FinalConsumptionError('Enter only RM item, batch and additional actual quantity.')
        key = supplied['item_code'], supplied['batch_no']
        if key not in expected or key in seen:
            raise FinalConsumptionError('Every authoritative RM batch must be measured exactly once.')
        seen.add(key)
        source = expected[key]
        prior = quantity(source.get('prior_actual_qty'), 'Prior confirmed actual')
        provisional = quantity(source.get('provisional_qty'), 'Provisional relief')
        accounted = quantity(source.get('accounted_qty'), 'Net WIP relief')
        issued = quantity(source.get('issued_qty'), 'Issued quantity')
        remaining = quantity(source.get('remaining_qty'), 'Remaining physical WIP')
        entered = quantity(supplied.get('additional_actual_qty'), 'Additional Consumed Qty')
        final = prior + entered
        delta = final - accounted
        if final > issued:
            raise FinalConsumptionError('Final actual exceeds issued material. Management Review Required.')
        if delta > remaining:
            raise FinalConsumptionError('Consumption exceeds attributable remaining WIP. Management Review Required.')
        result.append(dict(item_code=key[0], batch_no=key[1],
            prior_actual_qty=str(prior), provisional_qty=str(provisional),
            additional_actual_qty=str(entered), final_actual_qty=str(final),
            accounted_qty=str(accounted), stock_delta=str(delta),
            disposition='Restoration Required' if delta < 0 else 'Consume Delta' if delta > 0 else 'Matched'))
    if seen != set(expected):
        raise FinalConsumptionError('Enter Additional Consumed Qty for every RM batch, including explicit zero.')
    return sorted(result, key=lambda row: (row['item_code'], row['batch_no']))
