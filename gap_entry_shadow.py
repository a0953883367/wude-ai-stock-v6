"""Prospective paired gap-entry control inside the existing weight experiment.

No orders, historical backfill, alternative ranking, or automatic promotion.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo
from weight_price_evidence import evidence_valid

ZONE = ZoneInfo('Asia/Taipei')
POLICY = {'version': 1, 'source_model': 'base_0', 'capital_twd': 1000000,
          'planned_positions': 10, 'minimum_official_prices': 9,
          'gap_cutoff_pct': 2.0, 'round_trip_cost_pct': 0.685,
          'preliminary_sessions': 20, 'manual_review_sessions': 60,
          'cash_earns_interest': False, 'cash_is_reallocated': False,
          'price_basis': 'signal_official_close_to_next_official_open',
          'simulation_only': True, 'promotion_allowed': False}


def _at(value):
    try:
        at = datetime.fromisoformat(value)
        return at.replace(tzinfo=ZONE) if at.tzinfo is None else at.astimezone(ZONE)
    except (ValueError, TypeError):
        return None


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def empty_state(updated_at):
    return {'policy': deepcopy(POLICY), 'activated_at': updated_at, 'updated_at': updated_at,
            'status': 'waiting_for_verified_future_signal', 'pending': None,
            'days': [], 'invalid_days': [], 'blocked_events': [], 'completed_sessions': 0,
            'summary': {'baseline_net_profit_twd': None, 'gap_cash_net_profit_twd': None,
                        'incremental_net_profit_twd': None}, 'promotion_allowed': False}


def _block(state, reason, snapshot_id='', target=''):
    key = _digest([reason, snapshot_id, target])
    if not any(x['event_id'] == key for x in state['blocked_events']):
        state['blocked_events'].append({'event_id': key, 'reason': reason,
            'snapshot_id': snapshot_id, 'target_session': target, 'observed_at': state['updated_at']})
    state['last_block_reason'] = reason


def _finish(state):
    days = state['days']
    n = len(days)
    state['completed_sessions'] = n
    state['remaining_to_preliminary'] = max(0, 20-n)
    state['remaining_to_manual_review'] = max(0, 60-n)
    state['summary'] = {key: round(sum(d[key] for d in days), 2) if days else None
        for key in ('baseline_net_profit_twd', 'gap_cash_net_profit_twd', 'incremental_net_profit_twd')}
    state['summary']['skipped_gap_positions'] = sum(d['skipped_gap_positions'] for d in days)
    state['summary']['comparison_basis'] = 'paired_daily_trials_not_compounded_account_return'
    state['status'] = ('manual_review_only' if n >= 60 else 'preliminary_review_only' if n >= 20
                       else 'collecting' if state['pending'] or n else 'waiting_for_verified_future_signal')
    state['promotion_allowed'] = False
    return state


def update(existing, source_model, rows, updated_at, *, intraday=False):
    state = deepcopy(existing) if existing else empty_state(updated_at)
    state['updated_at'] = updated_at
    if state.get('policy') != POLICY:
        state['status'] = 'policy_mismatch_manual_review'
        state['promotion_allowed'] = False
        return state
    now = _at(updated_at)
    if not now:
        _block(state, 'invalid_runtime_timestamp')
        return _finish(state)
    if intraday or len(state['days']) >= 60:
        return _finish(state)
    row_map = {r.get('symbol'): r for r in rows}
    pending = state['pending']
    if pending:
        unsigned = {k:v for k,v in pending.items() if k != 'receipt_sha256'}
        if pending.get('receipt_sha256') != _digest(unsigned):
            state['status'] = 'receipt_mismatch_manual_review'
            state['promotion_allowed'] = False
            return state
        target = pending['execution_session_date']
        priced, missing = [], []
        for pick in pending['picks']:
            proof = (row_map.get(pick['symbol']) or {}).get('shadow_price_evidence') or {}
            if evidence_valid(proof, target):
                priced.append((pick, proof))
            else:
                missing.append(pick['symbol'])
        if len(priced) >= 9 and now.date().isoformat() >= target:
            positions = []
            for pick in pending['picks']:
                proof = next((proof for frozen,proof in priced if frozen['symbol'] == pick['symbol']), None)
                available = proof is not None
                gap = (proof['open']/pick['signal_close']-1)*100 if available else None
                skip = available and proof['open']/pick['signal_close'] > 1.02
                allocation = pick['allocation_twd']
                gross = allocation*(proof['close']/proof['open']-1) if available else 0.0
                cost = allocation*.00685 if available else 0.0
                positions.append({'symbol':pick['symbol'], 'name':pick.get('name'),
                    'allocation_twd':allocation, 'signal_close':pick['signal_close'],
                    'official_open':proof['open'] if available else None,
                    'official_close':proof['close'] if available else None,
                    'gap_pct':round(gap, 4) if available else None, 'data_available':available,
                    'baseline_executed':available, 'gap_cash_executed':available and not skip,
                    'cash_reason':'gap_gt_2pct' if skip else 'official_price_missing' if not available else None,
                    'baseline_gross_profit_twd':round(gross, 2), 'baseline_cost_twd':round(cost, 2),
                    'baseline_net_profit_twd':round(gross-cost, 2),
                    'gap_cash_gross_profit_twd':round(gross, 2) if not skip else 0.0,
                    'gap_cash_cost_twd':round(cost, 2) if not skip else 0.0,
                    'gap_cash_net_profit_twd':round(gross-cost, 2) if not skip else 0.0,
                    'official_price_evidence':deepcopy(proof)})
            baseline = round(sum(p['baseline_net_profit_twd'] for p in positions), 2)
            filtered = round(sum(p['gap_cash_net_profit_twd'] for p in positions), 2)
            if not any(d['execution_session_date'] == target for d in state['days']):
                state['days'].append({'sample_id':pending['sample_id'], 'signal_session_date':pending['signal_session_date'],
                    'execution_session_date':target, 'frozen_at':pending['frozen_at'],
                    'source_created_at':pending['source_created_at'], 'source_snapshot_id':pending['source_snapshot_id'],
                    'frozen_receipt':deepcopy(pending), 'settled_at':updated_at, 'positions':positions,
                    'available_prices':len(priced), 'missing_symbols':missing,
                    'baseline_net_profit_twd':baseline, 'gap_cash_net_profit_twd':filtered,
                    'incremental_net_profit_twd':round(filtered-baseline, 2),
                    'skipped_gap_positions':sum(p['cash_reason']=='gap_gt_2pct' for p in positions),
                    'baseline_cash_twd':sum(p['allocation_twd'] for p in positions if not p['baseline_executed']),
                    'gap_cash_twd':sum(p['allocation_twd'] for p in positions if not p['gap_cash_executed'])})
            state['pending'] = None
        elif now.date().isoformat() > target:
            state['invalid_days'].append({'reason':'expired_missing_exact_session_prices', 'missing_symbols':missing,
                'pending':deepcopy(pending), 'invalidated_at':updated_at})
            state['pending'] = None
        else:
            _block(state, 'waiting_for_exact_session_official_prices', pending['source_snapshot_id'], target)
    if state['pending'] or len(state['days']) >= 60:
        return _finish(state)
    source = (source_model or {}).get('pending') or {}
    picks = source.get('picks') or []
    signal, snapshot = source.get('signal_session_date'), source.get('snapshot_id')
    if len(picks) != 10 or len({p.get('symbol') for p in picks}) != 10 or not signal or not snapshot:
        _block(state, 'missing_frozen_source_signal', snapshot or '')
        return _finish(state)
    proofs = [(row_map.get(p['symbol']) or {}).get('shadow_price_evidence') or {} for p in picks]
    if not all(evidence_valid(p, signal) for p in proofs):
        _block(state, 'signal_official_close_evidence_missing', snapshot)
        return _finish(state)
    targets = {p.get('next_session_date') for p in proofs}
    if len(targets) != 1 or not all(isinstance(t, str) for t in targets):
        _block(state, 'next_official_session_not_verified', snapshot)
        return _finish(state)
    target = targets.pop()
    opening, close, created = _at(target+'T09:00:00'), _at(signal+'T13:30:00'), _at(source.get('created_at'))
    if not opening or not close or not created or not close <= created <= now < opening or target <= signal:
        _block(state, 'signal_not_frozen_before_target_open', snapshot, target)
        return _finish(state)
    sample_id = _digest([POLICY, snapshot, target])
    if any(d['sample_id'] == sample_id or d['execution_session_date'] == target for d in state['days']) or any(d['pending']['sample_id'] == sample_id for d in state['invalid_days']):
        return _finish(state)
    if not all(isinstance(p.get('allocation_twd'), (float,int)) and math.isfinite(p['allocation_twd']) and p['allocation_twd'] == 100000 for p in picks):
        _block(state, 'source_allocation_mismatch', snapshot, target)
        return _finish(state)
    frozen = {'sample_id':sample_id, 'source_snapshot_id':snapshot, 'source_created_at':source['created_at'],
        'signal_session_date':signal, 'execution_session_date':target, 'frozen_at':updated_at,
        'policy':deepcopy(POLICY), 'clock_evidence_status':'recorded_not_independently_verified',
        'picks':[{**deepcopy(pick), 'signal_close':proof['close'], 'signal_price_evidence':deepcopy(proof)} for pick,proof in zip(picks,proofs)]}
    frozen['receipt_sha256'] = _digest(frozen)
    state['pending'] = frozen
    state.pop('last_block_reason', None)
    return _finish(state)
