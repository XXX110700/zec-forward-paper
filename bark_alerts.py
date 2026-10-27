"""Bark threshold alerts, separate from strategy and paper trading state."""
import argparse
import json
import math
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, unquote
import requests
import zec_v63_github as z

LEDGER = Path(__file__).with_name('zec_v63_bark_state.json')
THRESHOLD = 5
MAX_AGE_MS = 30*60*1000
DASHBOARD = 'https://xxx110700.github.io/zec-forward-paper/'
SIDES = ('long','short')


def current_ms():
    return int(datetime.now(timezone.utc).timestamp()*1000)


def blank():
    return {'version':1, 'last_ts':None, 'above':dict.fromkeys(SIDES,False),
            'scores':None, 'pending':[]}


def validate_ledger(data):
    if not isinstance(data,dict) or data.get('version') != 1:
        raise ValueError('Invalid alert ledger')
    ts = data['last_ts']
    if ts is not None and (type(ts) is not int or ts <= 0 or ts % z.BAR15):
        raise ValueError('Invalid alert cursor')
    if set(data['above']) != set(SIDES) or any(type(data['above'][s]) is not bool for s in SIDES):
        raise ValueError('Invalid threshold flags')
    scores = data['scores']
    if ts is not None and (not isinstance(scores,dict) or set(scores) != set(SIDES)
                          or any(type(scores[s]) is not int or not 0 <= scores[s] <= 6 for s in SIDES)):
        raise ValueError('Invalid alert scores')
    if ts is None and (scores is not None or any(data['above'].values()) or data['pending']):
        raise ValueError('Unexpected alert data without cursor')
    if scores is not None and any(data['above'][s] != (scores[s] >= THRESHOLD) for s in SIDES):
        raise ValueError('Threshold ledger inconsistent')
    if not isinstance(data['pending'],list) or len(data['pending']) > 16:
        raise ValueError('Invalid pending alert queue')
    if 'lifecycle_after' in data:
        start = data['lifecycle_after']
        if type(start) is not int or start <= 0 or start % z.BAR15 or ts is None or start > ts:
            raise ValueError('Invalid lifecycle baseline')
        ids = data.get('lifecycle_seen')
        if (not isinstance(ids,list) or len(ids) > 64 or len(set(ids)) != len(ids)
                or any(not isinstance(v,str) or not re.fullmatch(r'(signal|entry):(long|short):[0-9]+(:[0-9]+)?',v) for v in ids)):
            raise ValueError('Invalid lifecycle identities')
    elif 'lifecycle_seen' in data:
        raise ValueError('Lifecycle baseline missing')
    seen = set()
    for event in data['pending']:
        side = event['side']
        event_ts = event['ts']
        kind = event.get('kind','threshold')
        if (side not in SIDES or event['id'] in seen or type(event_ts) is not int or event_ts % z.BAR15
                or ts is None or not 0 < event_ts <= ts
                or type(event['score']) is not int or not THRESHOLD <= event['score'] <= 6
                or not isinstance(event['position'],str)):
            raise ValueError('Invalid pending alert event')
        if kind == 'threshold':
            if event['id'] != f'{side}:{event_ts}' or not data['above'][side]:
                raise ValueError('Invalid threshold event')
        elif kind in ('signal','entry'):
            signal = event['signal_ts']
            if (type(signal) is not int or signal <= 0 or signal % z.BAR15
                    or event['id'] not in data.get('lifecycle_seen',[])):
                raise ValueError('Invalid lifecycle event')
            identity = f'signal:{side}:{signal}' if kind == 'signal' else f'entry:{side}:{signal}:{event_ts}'
            if event['id'] != identity or (kind == 'signal' and signal != event_ts) or (kind == 'entry' and signal >= event_ts):
                raise ValueError('Invalid lifecycle chronology')
            if kind == 'entry' and any(type(event.get(k)) not in (int,float) or not math.isfinite(event[k]) or event[k] <= 0 for k in ('entry','sl','tp1','tp2')):
                raise ValueError('Invalid lifecycle prices')
        else:
            raise ValueError('Unknown alert kind')
        seen.add(event['id'])
    return data


def load_ledger():
    if not LEDGER.exists():
        return blank()
    try:
        return validate_ledger(json.loads(LEDGER.read_text(encoding='utf-8')))
    except (ValueError,KeyError,TypeError) as exc:
        backup = LEDGER.with_name(LEDGER.name+'.corrupt-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.bak')
        shutil.copy2(LEDGER,backup)
        raise RuntimeError('Alert ledger invalid; backup preserved, delivery stopped') from None


def save_ledger(data):
    validate_ledger(data)
    z.atomic_write(LEDGER,json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))


def snapshot(now):
    state = z.load_state()
    ts = state['last_processed_15m_ts']
    if type(ts) is not int or ts % z.BAR15:
        raise RuntimeError('No valid processed candle for alerting')
    close = ts+z.BAR15
    if not 0 <= now-close <= MAX_AGE_MS or state.get('last_processed_close_ts',close) != close:
        raise RuntimeError('Candle stale/future; no alert sent')
    try:
        checked = datetime.fromisoformat(state['updated_at'])
        if checked.tzinfo is None or not 0 <= now-int(checked.timestamp()*1000) <= MAX_AGE_MS:
            raise ValueError('stale')
    except (ValueError,KeyError,TypeError):
        raise RuntimeError('Backend check time invalid/stale; no alert sent') from None
    scores = {side:state.get(side+'_confirm') for side in SIDES}
    if any(type(v) is not int or not 0 <= v <= 6 for v in scores.values()):
        raise RuntimeError('Invalid confirmation scores; no alert sent')
    trade,pending = state['open_trade'],state['pending_signal']
    position = ('模拟'+('多单' if trade['side']=='long' else '空单')+'持仓' if trade else
                '有待入场模拟信号' if pending else '空仓等待')
    return ts,scores,position


def lifecycle_events(state):
    """Derive identities from pending, open AND closed trades, including same-bar exits."""
    events = {}
    trades = state['closed_trades'] + ([state['open_trade']] if state['open_trade'] else [])
    for item in trades + ([state['pending_signal']] if state['pending_signal'] else []):
        side, signal = item['side'], item['signal_ts']
        score = item.get(side+'_confirm')
        if (type(signal) is not int or signal % z.BAR15 or type(score) is not int
                or not THRESHOLD <= score <= 6):
            raise RuntimeError('Invalid lifecycle source')
        event = {'kind':'signal','id':f'signal:{side}:{signal}', 'side':side,
                 'ts':signal,'signal_ts':signal,'score':score,'position':'新待入场模拟信号'}
        events[event['id']] = event
        if 'entry_candle_ts' in item:
            entry_ts = item['entry_candle_ts']
            if type(entry_ts) is not int or entry_ts % z.BAR15 or entry_ts <= signal:
                raise RuntimeError('Invalid entry chronology')
            event = dict(event,kind='entry',id=f'entry:{side}:{signal}:{entry_ts}',ts=entry_ts,
                         position='已模拟平仓' if 'exit_ts' in item else '已有模拟持仓')
            event.update({k:item[k] for k in ('entry','sl','tp1','tp2')})
            events[event['id']] = event
    return sorted(events.values(),key=lambda e:(e['ts'],e['kind'],e['side']))


def eligible(event, now, scores):
    fresh = 0 <= now-(event['ts']+z.BAR15) <= MAX_AGE_MS
    return fresh and (event.get('kind','threshold') != 'threshold' or scores[event['side']] >= THRESHOLD)


def prepare(now=None):
    if not os.environ.get('BARK_PUSH_URL','').strip():
        print('Bark not configured; notifications disabled')
        return
    now = current_ms() if now is None else now
    ts,scores,position = snapshot(now)
    state = z.load_state()
    if state['last_processed_15m_ts'] != ts:
        raise RuntimeError('State changed during alert preparation')
    events = lifecycle_events(state)
    ledger = load_ledger()
    if ledger['last_ts'] is not None and ts < ledger['last_ts']:
        raise RuntimeError('Paper cursor behind alert cursor; reconcile before notifying')
    if ts == ledger['last_ts'] and scores != ledger['scores']:
        raise RuntimeError('Scores changed for an already observed candle')
    # Drop obsolete alerts if the direction fell below threshold, or >30m old.
    ledger['pending'] = [e for e in ledger['pending'] if eligible(e,now,scores)]
    # First activation adopts the current candle as a baseline, never replays history.
    if 'lifecycle_after' not in ledger:
        ledger['lifecycle_after'],ledger['lifecycle_seen'] = ts,[]
    new_events = [e for e in events if ledger['lifecycle_after'] < e['ts'] <= ts
                  and eligible(e,now,scores) and e['id'] not in ledger['lifecycle_seen']]
    # A new signal already explains this direction's threshold crossing; send one message.
    signal_sides = {e['side'] for e in new_events if e['kind'] == 'signal'}
    if ts != ledger['last_ts']:
        for side in SIDES:
            above = scores[side] >= THRESHOLD
            if above and not ledger['above'][side] and side not in signal_sides:
                ledger['pending'].append({'id':f'{side}:{ts}','ts':ts,'side':side,
                                          'score':scores[side],'position':position})
            ledger['above'][side] = above
        ledger['last_ts'],ledger['scores'] = ts,scores
    for event in new_events:
        ledger['pending'].append(event)
        ledger['lifecycle_seen'].append(event['id'])
    ledger['lifecycle_seen'] = ledger['lifecycle_seen'][-64:]
    save_ledger(ledger)
    print(f'Bark prepared: {len(ledger["pending"])} pending notification(s)')


class BarkError(RuntimeError):
    """Only fixed diagnostic messages; never include configuration or server text."""


def endpoint():
    raw = os.environ.get('BARK_PUSH_URL','').strip()
    if not raw:
        raise BarkError('BARK_CONFIG_MISSING: add repository Secret BARK_PUSH_URL')
    try:
        # Bark can copy either a URL or its complete curl example. Parse only;
        # never execute copied commands. Accept example title/body/query suffixes.
        if raw.startswith('curl '):
            urls = [token for token in shlex.split(raw) if token.startswith('https://')]
            if len(urls) != 1:
                raise ValueError('invalid')
            raw = urls[0]
        p = urlsplit(raw)
        segments = p.path.strip('/').split('/')
        key = unquote(segments[0])
        if (p.scheme != 'https' or not p.hostname or p.username or p.password
                or p.port is not None and not 1 <= p.port <= 65535
                or not re.fullmatch(r'[A-Za-z0-9_-]+',key)):
            raise ValueError('invalid')
        return p.scheme+'://'+p.netloc+'/push',key
    except (ValueError,IndexError):
        raise BarkError('BARK_CONFIG_FORMAT: use the HTTPS push URL copied from Bark, not just the key') from None


def send(title,body):
    url,key = endpoint()
    try:
        response = requests.post(url,json={'device_key':key,'title':title,'body':body,
                                 'group':'ZEC-V6.3','url':DASHBOARD},timeout=15,allow_redirects=False)
    except requests.exceptions.SSLError:
        raise BarkError('BARK_TLS_ERROR: certificate or TLS connection failed') from None
    except requests.exceptions.Timeout:
        raise BarkError('BARK_TIMEOUT: request timed out; the server may still have accepted it') from None
    except requests.exceptions.ConnectionError:
        raise BarkError('BARK_CONNECTION_ERROR: runner could not connect to Bark') from None
    except Exception:
        raise BarkError('BARK_REQUEST_ERROR: request failed; raw details hidden') from None
    if response.status_code != 200:
        # HTTP status is numeric; do not display Location headers or body.
        status = response.status_code if type(response.status_code) is int else 'unknown'
        raise BarkError(f'BARK_HTTP_ERROR: HTTP {status}; verify service and configuration')
    try:
        result = response.json()
    except Exception:
        raise BarkError('BARK_RESPONSE_ERROR: service returned invalid JSON') from None
    if not isinstance(result,dict) or result.get('code') != 200:
        code = result.get('code') if isinstance(result,dict) else None
        safe_code = code if type(code) is int else 'unknown'
        raise BarkError(f'BARK_REJECTED: service code {safe_code}; check device key and service')


def deliver(now=None):
    if not os.environ.get('BARK_PUSH_URL','').strip():
        print('Bark not configured; notifications disabled')
        return
    now = current_ms() if now is None else now
    ledger = load_ledger()
    ts,scores,_ = snapshot(now)
    if ts != ledger['last_ts'] or scores != ledger['scores']:
        raise RuntimeError('Prepare and persist alert queue before delivery')
    failures = 0
    for event in list(ledger['pending']):
        age = now-(event['ts']+z.BAR15)
        if not eligible(event,now,scores):
            ledger['pending'].remove(event)
            save_ledger(ledger)
            continue
        label = '多头' if event['side']=='long' else '空头'
        close = datetime.fromtimestamp((event['ts']+z.BAR15)/1000,timezone(timedelta(hours=8)))
        kind = event.get('kind','threshold')
        title = f'ZEC V6.3 {label}达标'
        body = (f'{label}确认 {event["score"]}/6，达到提醒门槛 5/6\n'
                f'K线收盘：{close:%Y-%m-%d %H:%M} 北京时间\n'
                f'模拟状态：{event["position"]}\n仅策略分数提醒，不代表已入场，不会下真实订单。\n'
                f'事件：{event["id"]}')
        if kind in ('signal','entry'):
            signal_time = datetime.fromtimestamp((event['signal_ts']+z.BAR15)/1000,timezone(timedelta(hours=8)))
            title = f'ZEC V6.3 {label}'+('新模拟信号' if kind == 'signal' else '已模拟入场')
            body = (f'信号确认 {event["score"]}/6\n信号收盘：{signal_time:%Y-%m-%d %H:%M} 北京时间\n')
            if kind == 'signal':
                body += '产生新的待入场信号，按原规则由下一根15分钟K线处理。\n'
            else:
                entered = datetime.fromtimestamp(event['ts']/1000,timezone(timedelta(hours=8)))
                body += (f'模拟入场：{entered:%Y-%m-%d %H:%M} 北京时间\n'
                         f'入场价 {event["entry"]:g}；止损 {event["sl"]:g}；'
                         f'TP1 {event["tp1"]:g}；TP2 {event["tp2"]:g}\n'
                         f'记录状态：{event["position"]}\n')
            body += '按已处理K线记录提醒，最新持仓以看板为准；不会下真实订单。\n事件：'+event['id']
        try:
            send(title,body)
        except RuntimeError as exc:
            if isinstance(exc,BarkError):
                print(str(exc))
            failures += 1
            print(f'Bark delivery unsuccessful for {event["id"]}; queued for next successful run')
            continue
        ledger['pending'].remove(event)
        save_ledger(ledger)
        print(f'Bark accepted notification {event["id"]}')
    if failures:
        raise RuntimeError('Some Bark notifications remain queued')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','deliver','test'])
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare()
        elif args.command == 'deliver':
            deliver()
        else:
            send('ZEC V6.3 推送测试','Bark 接入测试成功。此消息不是交易信号，不会下单。')
            print('Bark accepted test notification; confirm receipt on iPhone')
    except BarkError as exc:
        print(str(exc),file=sys.stderr)
        return 1
    except Exception:
        print('Bark notification step failed. Check setup; no secret values are logged.',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
