"""Explicit, bounded historical recovery into an isolated candidate directory only."""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
import zec_v63_github as z
from paper_audit import reconcile

PERIODS = {'15m':z.BAR15, '1H':z.BAR1H, '4H':z.BAR4H}
MAX_OUTAGE = 30*24*60*60*1000
MAX_PAGES = 60


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now_ms():
    return int(z.datetime.now(z.timezone.utc).timestamp()*1000)


def history(bar, start, current):
    """Confirmed public candles; after pages strictly backwards (100/page)."""
    duration = PERIODS[bar]
    newest = z.recent_closed(bar)
    z.validate_market(newest, duration, current)
    candles = {row[0]:row for row in newest}
    for _ in range(MAX_PAGES):
        oldest = min(candles)
        if oldest <= start:
            result = [candles[k] for k in sorted(candles) if k >= start]
            if result[0][0] != start:
                raise RuntimeError('Recovery warmup boundary missing')
            z.validate_market(result, duration, current)
            return result
        time.sleep(0.15)  # Below OKX history-candles 20 requests / 2 seconds.
        rows = z.api('/api/v5/market/history-candles', {
            'instId':z.INST, 'bar':bar, 'limit':'100', 'after':str(oldest)})
        page = []
        for row in rows:
            if len(row) >= 9 and str(row[8]) == '1':
                page.append((int(row[0]), *map(float,row[1:5])))
        if not page or min(row[0] for row in page) >= oldest:
            raise RuntimeError('History unavailable/pagination did not advance')
        for row in page:
            if row[0] in candles and candles[row[0]] != row:
                raise RuntimeError('Conflicting historical candle')
            candles[row[0]] = row
    raise RuntimeError('Recovery page limit exceeded; stop for manual investigation')


def prepare(output):
    if output.exists():
        raise RuntimeError('Use a new recovery directory; never overwrite a previous recovery')
    if not z.STATE_FILE.exists():
        raise RuntimeError('Recovery requires an existing verified state, never initializes history')
    # Snapshot once; detect concurrent source changes again after downloading.
    source = {z.STATE_FILE.name:z.STATE_FILE.read_bytes()}
    if z.CSV_FILE.exists():
        source[z.CSV_FILE.name] = z.CSV_FILE.read_bytes()
    state = z.validate_state(json.loads(source[z.STATE_FILE.name]))
    reconcile(state,z.CSV_FILE,state.get('csv_outbox',[]))
    last = state['last_processed_15m_ts']
    current = now_ms()
    if last is None or not 0 <= current-last <= MAX_OUTAGE:
        raise RuntimeError('Recovery requires a cursor within the last 30 days; do not reset it')
    market = {}
    for bar,duration in PERIODS.items():
        # At least 300 warmup candles before the first unprocessed decision.
        start = ((last+z.BAR15)//duration-301)*duration
        market[bar] = history(bar,start,current)
    market_bytes = json.dumps(market,allow_nan=False).encode()
    for name,data in source.items():
        if (z.STATE_FILE.parent/name).read_bytes() != data:
            raise RuntimeError('Source changed during preparation; retry from a fresh snapshot')
    if (z.CSV_FILE.name in source) != z.CSV_FILE.exists():
        raise RuntimeError('CSV appeared/disappeared during preparation')
    output.mkdir(parents=True)
    inputs = output/'input'
    inputs.mkdir()
    for name,data in source.items():
        (inputs/name).write_bytes(data)
    (inputs/'candles.json').write_bytes(market_bytes)
    manifest = {'mode':'historical_recovery', 'prepared_at_ms':current,
                'source_cursor':last, 'latest_15m_open':market['15m'][-1][0],
                'sha256':{**{k:digest(v) for k,v in source.items()},
                          'candles.json':digest(market_bytes)},
                'note':'Isolated historical recalculation; review manually, never auto-promote.'}
    z.atomic_write(output/'manifest.json',json.dumps(manifest,indent=2))
    print(f'Prepared recovery package: {output}; production files untouched')


def replay(package):
    manifest = json.loads((package/'manifest.json').read_text())
    if manifest.get('mode') != 'historical_recovery':
        raise RuntimeError('Invalid recovery manifest')
    names = {z.STATE_FILE.name,z.CSV_FILE.name,'candles.json'}
    if not set(manifest['sha256']).issubset(names) or not {z.STATE_FILE.name,'candles.json'}.issubset(manifest['sha256']):
        raise RuntimeError('Invalid recovery input names')
    inputs = package/'input'
    for name,expected in manifest['sha256'].items():
        if digest((inputs/name).read_bytes()) != expected:
            raise RuntimeError(f'Recovery input checksum mismatch: {name}')
    market = json.loads((inputs/'candles.json').read_text())
    current = now_ms()
    if manifest['prepared_at_ms'] > current:
        raise RuntimeError('Future recovery preparation time')
    for bar,duration in PERIODS.items():
        z.validate_market(market[bar],duration,current)
    original_state,original_csv = z.STATE_FILE,z.CSV_FILE
    candidate = package/'candidate'
    candidate.mkdir()  # Exclusive: never replay over an earlier candidate.
    try:
        z.STATE_FILE = candidate/original_state.name
        z.CSV_FILE = candidate/original_csv.name
        for name in (original_state.name,original_csv.name):
            if name in manifest['sha256']:
                shutil.copy2(inputs/name,candidate/name)
        state = z.load_state()
        state['recovery'] = {'mode':'historical_recovery','source_cursor':manifest['source_cursor'],
                             'prepared_at_ms':manifest['prepared_at_ms'],
                             'note':'Historical replay; not an uninterrupted forward run'}
        z.save_state(state)
        z.main(candle_source=lambda bar:market[bar])
        audit = reconcile(z.load_state(),z.CSV_FILE)
        z.atomic_write(package/'result.json',json.dumps({'status':'complete','audit':audit,
                       'candidate_sha256':{p.name:digest(p.read_bytes()) for p in (z.STATE_FILE,z.CSV_FILE) if p.exists()},
                       'note':'No production promotion performed'},indent=2))
        print('Recovery candidate complete. Review RECOVERY.md before any manual promotion.')
    finally:
        z.STATE_FILE,z.CSV_FILE = original_state,original_csv
        z.EVENTS.clear()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','replay','audit'])
    parser.add_argument('--directory',type=Path)
    args = parser.parse_args()
    if args.command == 'audit':
        state = z.load_state()
        print(json.dumps(reconcile(state,z.CSV_FILE,state.get('csv_outbox',[])),indent=2))
    elif not args.directory:
        parser.error('--directory required for prepare/replay')
    elif args.command == 'prepare':
        prepare(args.directory)
    else:
        replay(args.directory)
