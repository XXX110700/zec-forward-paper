"""Bark threshold alerts, separate from strategy and paper trading state."""
import argparse
import json
import os
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
    if not isinstance(data['pending'],list) or len(data['pending']) > 2:
        raise ValueError('Invalid pending alert queue')
    seen = set()
    for event in data['pending']:
        side = event['side']
        event_ts = event['ts']
        if (side not in SIDES or side in seen or type(event_ts) is not int or event_ts % z.BAR15
                or ts is None or not 0 < event_ts <= ts or event['id'] != f'{side}:{event_ts}'
                or type(event['score']) is not int or not THRESHOLD <= event['score'] <= 6
                or not data['above'][side] or not isinstance(event['position'],str)):
            raise ValueError('Invalid pending alert event')
        seen.add(side)
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


def prepare(now=None):
    if not os.environ.get('BARK_PUSH_URL','').strip():
        print('Bark not configured; notifications disabled')
        return
    now = current_ms() if now is None else now
    ts,scores,position = snapshot(now)
    ledger = load_ledger()
    if ledger['last_ts'] is not None and ts < ledger['last_ts']:
        raise RuntimeError('Paper cursor behind alert cursor; reconcile before notifying')
    if ts == ledger['last_ts'] and scores != ledger['scores']:
        raise RuntimeError('Scores changed for an already observed candle')
    # Drop obsolete alerts if the direction fell below threshold, or >30m old.
    ledger['pending'] = [e for e in ledger['pending']
                         if scores[e['side']] >= THRESHOLD and 0 <= now-(e['ts']+z.BAR15) <= MAX_AGE_MS]
    if ts != ledger['last_ts']:
        for side in SIDES:
            above = scores[side] >= THRESHOLD
            if above and not ledger['above'][side]:
                ledger['pending'].append({'id':f'{side}:{ts}','ts':ts,'side':side,
                                          'score':scores[side],'position':position})
            ledger['above'][side] = above
        ledger['last_ts'],ledger['scores'] = ts,scores
    save_ledger(ledger)
    print(f'Bark prepared: {len(ledger["pending"])} pending notification(s)')


def endpoint():
    # Accept server/key only; never store it in JSON or print it in errors.
    try:
        p = urlsplit(os.environ.get('BARK_PUSH_URL','').strip())
        segments = p.path.strip('/').split('/')
        if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment
                or len(segments) != 1 or not segments[0] or '/' in unquote(segments[0])):
            raise ValueError('invalid')
        return p.scheme+'://'+p.netloc+'/push',unquote(segments[0])
    except ValueError:
        raise RuntimeError('BARK_PUSH_URL must be an HTTPS server/key address, without example message') from None


def send(title,body):
    url,key = endpoint()
    try:
        response = requests.post(url,json={'device_key':key,'title':title,'body':body,
                                 'group':'ZEC-V6.3','url':DASHBOARD},timeout=15,allow_redirects=False)
        if response.status_code != 200 or response.json().get('code') != 200:
            raise ValueError('rejected')
    except Exception:
        # requests exceptions and response bodies can contain secret URLs/keys.
        raise RuntimeError('Bark delivery failed; check service, secret and notification permissions') from None


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
        if scores[event['side']] < THRESHOLD or not 0 <= age <= MAX_AGE_MS:
            ledger['pending'].remove(event)
            save_ledger(ledger)
            continue
        label = '多头' if event['side']=='long' else '空头'
        close = datetime.fromtimestamp((event['ts']+z.BAR15)/1000,timezone(timedelta(hours=8)))
        body = (f'{label}确认 {event["score"]}/6，达到提醒门槛 5/6\n'
                f'K线收盘：{close:%Y-%m-%d %H:%M} 北京时间\n'
                f'模拟状态：{event["position"]}\n仅策略分数提醒，不代表已入场，不会下真实订单。\n'
                f'事件：{event["id"]}')
        try:
            send(f'ZEC V6.3 {label}达标',body)
        except RuntimeError:
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
    except Exception:
        print('Bark notification step failed. Check setup; no secret values are logged.',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
