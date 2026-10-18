"""Snapshot diagnostics for research patterns; never an order or V6 input."""
import math


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def pattern_state(row):
    """Compare like-basis pattern prices only; legacy close is a labelled proxy."""
    direction = row.get('chart_pattern_shadow_direction')
    price = number(row.get('chart_pattern_shadow_source_close'))
    basis = 'pattern_input'
    if price is None:
        price = number(row.get('official_adjusted_close_price'))
        basis = 'legacy_adjusted_close_proxy'
    entry = number(row.get('chart_pattern_shadow_entry'))
    stop = number(row.get('chart_pattern_shadow_stop'))
    target = number(row.get('chart_pattern_shadow_target'))
    valid = direction in ('bullish', 'bearish') and all(v is not None and v > 0 for v in (price, entry, stop, target))
    reached = invalidated = distant = None
    if valid:
        reached = price >= target if direction == 'bullish' else price <= target
        invalidated = price <= stop if direction == 'bullish' else price >= stop
        # Geometry diagnostic, not a trading threshold: more than two pattern
        # heights away from the confirmation line cannot be a nearby setup.
        height = abs(target-entry)
        distant = abs(price-entry) > 2*height if height > 0 else None
    age = number(row.get('chart_pattern_shadow_extrapolation_bars'))
    stale = age is not None and age > 15
    state = ('unknown' if not valid else 'target_crossed' if reached else
             'invalidated' if invalidated else 'historical' if stale or distant else 'reference')
    return {'chart_pattern_shadow_lifecycle': state,
            'chart_pattern_shadow_target_crossed': reached,
            'chart_pattern_shadow_invalidated': invalidated,
            'chart_pattern_shadow_distant': distant,
            'chart_pattern_shadow_comparison_basis': basis,
            'chart_pattern_shadow_comparison_price': price,
            'chart_pattern_shadow_active_reference': valid and not any((reached, invalidated, stale, distant)) and basis == 'pattern_input'}
