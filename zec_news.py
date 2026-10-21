"""Public RSS headlines only; no trading-state imports or writes."""
import hashlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import requests

OUTPUT = Path(__file__).with_name('zec_news.json')
SOURCES = [
    ('Electric Coin Company', 'official', 'https://electriccoin.co/feed/'),
    ('Zcash Foundation', 'official', 'https://zfnd.org/feed/'),
    ('Cointelegraph', 'media', 'https://cointelegraph.com/rss'),
    ('CoinDesk', 'media', 'https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml'),
    ('Google News EN', 'aggregation', 'https://news.google.com/rss/search?q=Zcash+OR+ZEC+when:7d&hl=en-US&gl=US&ceid=US:en'),
    ('Google News 中文', 'aggregation', 'https://news.google.com/rss/search?q=Zcash+OR+ZEC+when:7d&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'),
]
POS = ('approved', 'approval', 'adoption', 'launch', 'upgrade', 'listed', 'listing', 'investment', '获批', '批准', '上线', '升级', '增持')
NEG = ('delist', 'ban', 'hack', 'exploit', 'vulnerability', 'lawsuit', 'freeze', '下架', '禁止', '攻击', '漏洞', '诉讼', '冻结')
UNCERTAIN = ('rumor', 'rumour', 'reportedly', 'may ', 'could ', 'proposal', 'proposed', 'not ', 'denied', '传闻', '或将', '拟', '未获', '否认')


def clean(value):
    return re.sub(r'\s+', ' ', re.sub('<[^>]*>', ' ', html.unescape(value or ''))).strip()


def safe_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('Unsafe article URL')
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))


def classify(title):
    text = title.lower()
    def matched(word):
        return bool(re.search(r'\b' + re.escape(word), text)) if word.isascii() else word in text
    positive = [word for word in POS if matched(word)]
    negative = [word for word in NEG if matched(word)]
    uncertain = [word for word in UNCERTAIN if matched(word)]
    if uncertain or (positive and negative):
        return '待核实', '标题含不确定表述或正负因素并存，需阅读原文核实'
    if negative:
        return '偏利空', '标题关键词：' + '、'.join(negative)
    if positive:
        return '偏利好', '标题关键词：' + '、'.join(positive)
    return '中性', '标题未命中明确方向关键词'


def parse_feed(content, name, kind, now):
    root = ET.fromstring(content)
    if root.tag not in ('rss', '{http://www.w3.org/2005/Atom}feed', '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'):
        raise ValueError('Not an RSS/Atom feed')
    rows = root.findall('.//item') + root.findall('{http://www.w3.org/2005/Atom}entry')
    out = []
    for row in rows[:250]:
        def field(tag):
            return row.findtext(tag) or row.findtext('{http://www.w3.org/2005/Atom}' + tag) or ''
        title = clean(field('title'))[:300]
        if kind != 'official' and not re.search(r'\b(?:zcash|zec)\b', title, re.I):
            continue
        try:
            raw_date = field('pubDate') or field('published') or field('updated')
            try:
                published = parsedate_to_datetime(raw_date)
            except (ValueError, TypeError):
                published = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            if published.tzinfo is None:
                continue
            published = published.astimezone(timezone.utc)
            if not now - timedelta(days=7) <= published <= now + timedelta(minutes=5):
                continue
            link = field('link')
            if not link:
                atom = row.find('{http://www.w3.org/2005/Atom}link')
                link = atom.get('href', '') if atom is not None else ''
            link = safe_url(link)
            publisher = clean(field('source')) or name
            sentiment, basis = classify(title)
            key = re.sub(r'[^\w\u4e00-\u9fff]', '', title.lower())
            out.append(dict(id=hashlib.sha256(key.encode()).hexdigest()[:20], title=title,
                            url=link, source=publisher[:100], feed=name, kind=kind,
                            published_at=published.isoformat(), event_time=None,
                            sentiment=sentiment, basis=basis,
                            verification='官方发布' if kind == 'official' else '未独立核实'))
        except (ValueError, TypeError, OverflowError):
            continue
    return out


def collect(previous=None, now=None, get=requests.get):
    now = now or datetime.now(timezone.utc)
    previous = previous or {}
    items, health = [], []
    for name, kind, url in SOURCES:
        try:
            response = get(url, timeout=15, headers={'User-Agent': 'zec-forward-paper-news/1.0'})
            response.raise_for_status()
            if len(response.content) > 4_000_000:
                raise ValueError('Feed too large')
            batch = parse_feed(response.content, name, kind, now)
            items.extend(batch)
            health.append(dict(name=name, status='ok', matched=len(batch)))
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            health.append(dict(name=name, status='error', error=type(exc).__name__))
            # Keep previous headlines only for unavailable feeds, retaining their dates.
            for old in previous.get('items', []):
                if old.get('feed') == name:
                    try:
                        date = datetime.fromisoformat(old['published_at'])
                        if now - timedelta(days=7) <= date <= now:
                            items.append(old)
                    except (KeyError, ValueError, TypeError):
                        pass
    unique = {}
    for item in items:
        unique.setdefault(item['id'], item)
    successful = sum(s['status'] == 'ok' for s in health)
    return dict(version=1, checked_at=now.isoformat(),
                last_success_at=now.isoformat() if successful else previous.get('last_success_at'),
                status='ok' if successful == len(SOURCES) else 'partial' if successful else 'error',
                sources=health, items=sorted(unique.values(), key=lambda x:x['published_at'], reverse=True)[:50])


def main():
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    data = collect(previous)
    fd, name = tempfile.mkstemp(dir=OUTPUT.parent, prefix=OUTPUT.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, OUTPUT)
    finally:
        if os.path.exists(name): os.unlink(name)
    print(f"News: {data['status']}, {len(data['items'])} headlines")


if __name__ == '__main__':
    main()
