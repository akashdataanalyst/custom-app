from __future__ import annotations

from math import ceil, isclose


FG_MT_SLAB_SIZE = 5.0
FG_KG_PER_MT = 1000.0
FG_INTEGER_TOLERANCE = 0.000001


def get_quantity_multiplier(manufacturing_qty_mt) -> int:
    if manufacturing_qty_mt is None or manufacturing_qty_mt <= 0:
        return 0
    return int(ceil(float(manufacturing_qty_mt) / FG_MT_SLAB_SIZE))


def get_required_test_count(quantity_multiplier: int, sample_size, frequency_value) -> int:
    if quantity_multiplier <= 0 or sample_size is None or sample_size <= 0 or frequency_value is None or frequency_value <= 0:
        return 0

    required_tests = float(quantity_multiplier) * float(sample_size) * float(frequency_value)
    rounded_tests = round(required_tests)
    if isclose(required_tests, rounded_tests, abs_tol=FG_INTEGER_TOLERANCE):
        return int(rounded_tests)
    return int(ceil(required_tests))
