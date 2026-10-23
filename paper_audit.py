"""Read-only reconciliation; stable trade identity is (side, signal timestamp)."""
import csv
import math
from datetime import datetime, timezone

HEADER = ['event', 'side', 'signal_utc', 'entry_utc', 'entry', 'sl', 'tp1', 'tp2',
          'exit_utc', 'exit_price', 'reason', 'net_r', 'long_confirm', 'short_confirm']


def stamp(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def normalized(row):
    if len(row) != len(HEADER):
        raise ValueError('CSV row length mismatch')
    row = ['' if x is None else str(x) for x in row]
    if row[0] not in ('SIGNAL', 'ENTRY', 'EXIT') or row[1] not in ('long', 'short'):
        raise ValueError('Unknown CSV event/side')
    for i in (4, 5, 6, 7, 9, 11, 12, 13):
        if row[i] != '':
            n = float(row[i])
            if not math.isfinite(n):
                raise ValueError('Nonfinite CSV value')
            row[i] = n
    return tuple(row)


def reconcile(state, csv_path, pending=()):
    """Overlay only exact outbox retries, then compare every event and lifecycle.

    No repairs or writes occur here. An invalid log must never be rebuilt silently.
    """
    try:
        rows = []
        if csv_path.exists():
            with csv_path.open(newline='', encoding='utf-8') as f:
                reader = csv.reader(f, strict=True)
                if next(reader, None) != HEADER:
                    raise ValueError('CSV header mismatch')
                rows = [normalized(row) for row in reader]
        def key(row):
            return row[0], row[1], row[2]
        found = {}
        order = []
        for row in rows:
            if key(row) in found:
                raise ValueError(f'Duplicate event: {key(row)}')
            found[key(row)] = row
            order.append(key(row))
        queued = set()
        for raw in pending:
            row = normalized(raw)
            if key(row) in queued:
                raise ValueError("Duplicate event inside outbox")
            queued.add(key(row))
            if key(row) in found:
                if row != found[key(row)]:
                    raise ValueError('Outbox conflicts with CSV event')
            else:
                found[key(row)] = row
                order.append(key(row))

        expected = {}
        identities = set()
        cursor = state['last_processed_15m_ts']
        previous_exit = None
        trades = list(state['closed_trades'])
        if state['open_trade']:
            trades.append(state['open_trade'])
        if state['pending_signal']:
            trades.append(state['pending_signal'])
        for t in trades:
            identity = (t['side'], t['signal_ts'])
            if identity in identities:
                raise ValueError(f'Duplicate trade identity: {identity}')
            identities.add(identity)
            if cursor is None or t['signal_ts'] > cursor or t['signal_ts'] % 900000:
                raise ValueError('Signal beyond cursor or off candle boundary')
            if previous_exit is not None and t['signal_ts'] < previous_exit:
                raise ValueError('Overlapping/out-of-order trades')
            for field in ('long_confirm', 'short_confirm'):
                if type(t[field]) is not int or not 0 <= t[field] <= 6:
                    raise ValueError('Invalid trade confirmation')
            side, signal = t['side'], stamp(t['signal_ts'])
            confirms = [t['long_confirm'], t['short_confirm']]
            expected[('SIGNAL', side, signal)] = normalized(['SIGNAL',side,signal]+['']*9+confirms)
            if 'entry_candle_ts' not in t:
                continue
            entry_ts = t['entry_candle_ts']
            if not t['signal_ts'] < entry_ts <= cursor or entry_ts % 900000:
                raise ValueError('Entry timestamp inconsistent')
            common = [side,signal,stamp(entry_ts),t['entry'],t['sl'],t['tp1'],t['tp2']]
            expected[('ENTRY',side,signal)] = normalized(['ENTRY']+common+['']*4+confirms)
            if 'exit_ts' in t:
                if not entry_ts <= t['exit_ts'] <= cursor or t['exit_ts'] % 900000:
                    raise ValueError('Exit timestamp inconsistent')
                previous_exit = t['exit_ts']
                expected[('EXIT',side,signal)] = normalized(['EXIT']+common+[
                    stamp(t['exit_ts']),t['exit_price'],t['reason'],t['net_r']]+confirms)
        if set(expected) != set(found):
            raise ValueError(f'Missing/extra events: missing={set(expected)-set(found)}, extra={set(found)-set(expected)}')
        for k in expected:
            if expected[k] != found[k]:
                raise ValueError(f'State/CSV event mismatch: {k}')
        event_times = []
        for k in order:
            row = found[k]
            time_field = {'SIGNAL':2, 'ENTRY':3, 'EXIT':8}[row[0]]
            event_times.append(datetime.strptime(row[time_field], '%Y-%m-%d %H:%M UTC'))
        if event_times != sorted(event_times):
            raise ValueError('CSV events out of chronological order')
        positions = {k:i for i,k in enumerate(order)}
        for side, signal_ts in identities:
            signal = stamp(signal_ts)
            life = [(event,side,signal) for event in ('SIGNAL','ENTRY','EXIT')]
            indices = [positions[k] for k in life if k in positions]
            if indices != sorted(indices):
                raise ValueError('CSV lifecycle order mismatch')
        values = [t['net_r'] for t in state['closed_trades']]
        equity = peak = drawdown = 0.0
        for r in values:
            equity += r
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
        if 'max_drawdown' in state and not math.isclose(state['max_drawdown'], drawdown, abs_tol=1e-9):
            raise ValueError('State drawdown mismatch')
        return {'closed_trades':len(values), 'total_r':sum(values), 'max_drawdown':drawdown,
                'event_count':len(found), 'identity':'side + signal_ts'}
    except (ValueError, TypeError, KeyError, csv.Error, OverflowError) as exc:
        raise RuntimeError(f'State/log reconciliation failed: {exc}. Restore verified files; no automatic repair.') from exc
