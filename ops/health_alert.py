"""Check public paper-state freshness and notify through the existing Bark sender."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def reason(state, now):
    try:
        updated = datetime.fromisoformat(state['updated_at'])
        if updated.tzinfo is None:
            return 'invalid_state_time'
        age = (now-updated).total_seconds()
        if age < -60:
            return 'future_state_time'
        if age > 1800:
            return 'state_stale'
        return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'invalid_state_time'


def check(path, send, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        state = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        problem = 'state_unreadable'
    else:
        problem = reason(state, now)
    if problem is None:
        print('HEALTH_OK: no notification required')
        return False
    # Only fixed diagnostic labels leave the runner; never include file contents.
    send('ZEC 平台运行告警', '策略状态超过30分钟未更新或时间异常，请检查AWS运行日志。\n'
         '诊断：'+problem+'\n此消息不是交易信号，不会下单。')
    print('HEALTH_ALERT_ACCEPTED: '+problem)
    return True


def main():
    from bark_alerts import send
    try:
        sent = check(ROOT/'zec_v63_paper_state.json', send)
        if sent:
            Path('ops-alert-accepted').write_text('accepted\n')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('alert_sent='+str(sent).lower()+'\n')
        return 0
    except Exception:
        print('HEALTH_ALERT_FAILED: details hidden; no success marker written', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
