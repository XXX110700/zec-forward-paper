import unittest
from datetime import datetime,timezone
from unittest.mock import Mock
import requests
import zec_news as n
NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)

def feed(title='Zcash upgrade launched',link='https://example.com/news',date='Wed, 21 Oct 2026 10:00:00 GMT'):
    return f'<rss><channel><item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()

class NewsTests(unittest.TestCase):
    def test_sentiment_uncertainty_and_conflict(self):
        for title,want in [('Zcash upgrade','偏利好'),('ZEC delisted','偏利空'),('ZEC market update','中性'),('Zcash may get approval','待核实'),('Zcash upgrade vulnerability','待核实'),('ZEC not approved','待核实')]:
            self.assertEqual(n.classify(title)[0],want)

    def test_relevance_dates_links_and_html(self):
        self.assertEqual(len(n.parse_feed(feed(),'media','media',NOW)),1)
        for content in (feed('Bitcoin upgrade'),feed(link='javascript:alert(1)'),feed(date='invalid'),feed(date='Wed, 21 Oct 2020 10:00:00 GMT'),feed(date='Wed, 21 Oct 2026 14:00:00 GMT')):
            self.assertEqual(n.parse_feed(content,'media','media',NOW),[])
        self.assertEqual(n.clean('&lt;b&gt;News&lt;/b&gt;'),'News')
        with self.assertRaises(ValueError):n.parse_feed(b'<html/>','a','media',NOW)

    def test_dedup_and_source_failures_preserve_previous(self):
        get=Mock(return_value=Mock(content=feed(),raise_for_status=Mock()))
        data=n.collect(now=NOW,get=get)
        self.assertEqual(data['status'],'ok');self.assertEqual(len(data['items']),1)
        get.side_effect=requests.Timeout('offline')
        failed=n.collect(data,now=NOW,get=get)
        self.assertEqual(failed['status'],'error');self.assertEqual(failed['items'],data['items'])
        self.assertEqual(failed['last_success_at'],data['last_success_at'])
        self.assertTrue(all(s['status']=='error' for s in failed['sources']))

    def test_partial_and_malformed_xml(self):
        calls=iter([Mock(content=feed(),raise_for_status=Mock())]+[Mock(content=b'not xml',raise_for_status=Mock())]*5)
        data=n.collect(now=NOW,get=lambda *a,**kw:next(calls))
        self.assertEqual(data['status'],'partial');self.assertEqual(len(data['items']),1)
