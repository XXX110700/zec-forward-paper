import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from contextlib import redirect_stdout
from datetime import datetime,timezone
import bark_alerts as b
import zec_v63_github as z


class BarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ts = 1792522800000
        self.now = self.ts+z.BAR15+60000
        self.sf,self.lf = self.root/'state.json',self.root/'alerts.json'
        self.state = z.default_state()
        self.state.update(last_processed_15m_ts=self.ts,long_confirm=4,short_confirm=1,
                          updated_at=datetime.fromtimestamp(self.now/1000,timezone.utc).isoformat())
        self.patches = [patch.object(z,'STATE_FILE',self.sf),patch.object(z,'CSV_FILE',self.root/'trades.csv'),
                        patch.object(b,'LEDGER',self.lf),
                        patch.dict(os.environ,{'BARK_PUSH_URL':'https://api.day.app/private-test-key'})]
        for p in self.patches:
            p.start()
        self.write()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def write(self,long=None,short=None,advance=False):
        if advance:
            self.ts += z.BAR15
            self.now += z.BAR15
        if long is not None:
            self.state['long_confirm']=long
        if short is not None:
            self.state['short_confirm']=short
        self.state['last_processed_15m_ts']=self.ts
        self.state['updated_at']=datetime.fromtimestamp(self.now/1000,timezone.utc).isoformat()
        self.sf.write_text(json.dumps(self.state))

    def test_crossing_continuous_rearm_and_directions(self):
        b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'],[])
        self.write(5,1,True)
        b.prepare(self.now)
        with patch.object(b,'send') as send:
            b.deliver(self.now)
            send.assert_called_once()
        self.write(6,1,True)
        b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'],[])
        self.write(4,1,True)
        b.prepare(self.now)
        self.write(5,5,True)
        b.prepare(self.now)
        self.assertEqual({e['side'] for e in b.load_ledger()['pending']},{'long','short'})

    def test_repeat_same_candle_after_sent_does_not_duplicate(self):
        self.write(5)
        b.prepare(self.now)
        with patch.object(b,'send') as send:
            b.deliver(self.now)
            b.prepare(self.now)
            b.deliver(self.now)
            send.assert_called_once()

    def test_delivery_failure_retry_partial_and_obsolete(self):
        self.write(5,5)
        b.prepare(self.now)
        with patch.object(b,'send',side_effect=[None,RuntimeError('network')]):
            with self.assertRaises(RuntimeError):
                b.deliver(self.now)
        self.assertEqual([e['side'] for e in b.load_ledger()['pending']],['short'])
        with patch.object(b,'send') as send:
            b.deliver(self.now)
            send.assert_called_once()
        self.assertEqual(b.load_ledger()['pending'],[])
        self.write(1,1,True)
        b.prepare(self.now)
        self.write(5,1,True)
        b.prepare(self.now)
        self.write(4,1,True)
        b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'],[])

    def test_expired_event_is_not_delivered(self):
        self.write(5)
        b.prepare(self.now)
        self.write(5,1,True)
        self.write(5,1,True)
        b.prepare(self.now)
        with patch.object(b,'send') as send:
            b.deliver(self.now)
            send.assert_not_called()

    def test_disabled_skips_without_creating_or_sending(self):
        with patch.dict(os.environ,{'BARK_PUSH_URL':''}),patch.object(b,'send') as send:
            b.prepare(self.now)
            b.deliver(self.now)
            send.assert_not_called()
        self.assertFalse(self.lf.exists())

    def test_stale_future_and_bad_scores_fail_without_ledger(self):
        for field,value in [('last_processed_15m_ts',self.ts-10*z.BAR15),
                            ('last_processed_15m_ts',self.ts+z.BAR15),
                            ('updated_at','2020-01-01T00:00:00+00:00'),
                            ('long_confirm',True),('short_confirm',7),
                            ('last_processed_close_ts',self.ts)]:
            bad=copy.deepcopy(self.state)
            bad[field]=value
            self.sf.write_text(json.dumps(bad))
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                b.prepare(self.now)
            self.assertFalse(self.lf.exists())

    def test_corrupt_ledger_backed_up_and_stopped(self):
        for raw in ('broken','{}','null'):
            self.lf.write_text(raw)
            with self.assertRaises(RuntimeError):
                b.prepare(self.now)
            self.assertEqual(self.lf.read_text(),raw)
            self.assertTrue(any(p.read_text()==raw for p in self.root.glob('*.bak')))

    def test_out_of_order_and_changed_same_candle_stop(self):
        b.prepare(self.now)
        self.write(5)
        with self.assertRaises(RuntimeError):
            b.prepare(self.now)
        self.write(4)
        self.state['last_processed_15m_ts'] -= z.BAR15
        self.sf.write_text(json.dumps(self.state))
        with self.assertRaises(RuntimeError):
            b.prepare(self.now)

    def test_delivery_requires_prepared_snapshot(self):
        with self.assertRaises(RuntimeError):
            b.deliver(self.now)

    def test_https_json_and_server_ack_required(self):
        response=Mock(status_code=200)
        response.json.return_value={'code':200}
        with patch.object(b.requests,'post',return_value=response) as post:
            b.send('title','body')
            self.assertEqual(post.call_args.args[0],'https://api.day.app/push')
            self.assertEqual(post.call_args.kwargs['json']['device_key'],'private-test-key')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
        for response in (Mock(status_code=302),Mock(status_code=500)):
            with patch.object(b.requests,'post',return_value=response),self.assertRaises(RuntimeError):
                b.send('title','body')
        response=Mock(status_code=200)
        response.json.return_value={'code':400,'message':'private-test-key'}
        with patch.object(b.requests,'post',return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                b.send('title','body')
            self.assertNotIn('private-test-key',str(caught.exception))

    def test_bad_secret_and_network_errors_never_exposed(self):
        for url in ('http://api.day.app/key','https://api.day.app/',
                    'https://api.day.app/key%20secret','https://user:pass@api.day.app/key'):
            with patch.dict(os.environ,{'BARK_PUSH_URL':url}),self.assertRaises(RuntimeError):
                b.endpoint()
        with patch.object(b.requests,'post',side_effect=RuntimeError('private-test-key')):
            with self.assertRaises(RuntimeError) as caught:
                b.send('title','body')
            self.assertNotIn('private-test-key',str(caught.exception))
        self.write(5)
        b.prepare(self.now)
        self.assertNotIn('private-test-key',self.lf.read_text())

    def test_copied_full_urls_and_curl_examples(self):
        examples = [
            'https://api.day.app/private-test-key',
            'https://api.day.app/private-test-key/title/body?group=example&ttl=600',
            'curl -X GET https://api.day.app/private-test-key/title/body?group=example&ttl=600',
            'curl -X GET "https://api.day.app/private-test-key/title/body?group=example&ttl=600"',
        ]
        for value in examples:
            with patch.dict(os.environ,{'BARK_PUSH_URL':value}):
                self.assertEqual(b.endpoint(),('https://api.day.app/push','private-test-key'))

    def test_diagnostics_classify_failure_without_secrets(self):
        for exc,code in [(b.requests.exceptions.SSLError('private-test-key'),'BARK_TLS_ERROR'),
                         (b.requests.exceptions.Timeout('private-test-key'),'BARK_TIMEOUT'),
                         (b.requests.exceptions.ConnectionError('private-test-key'),'BARK_CONNECTION_ERROR')]:
            with patch.object(b.requests,'post',side_effect=exc):
                with self.assertRaises(b.BarkError) as caught:
                    b.send('title','body')
                self.assertIn(code,str(caught.exception))
                self.assertNotIn('private-test-key',str(caught.exception))
        response=Mock(status_code=200)
        response.json.side_effect=ValueError('private-test-key')
        with patch.object(b.requests,'post',return_value=response):
            with self.assertRaisesRegex(b.BarkError,'BARK_RESPONSE_ERROR'):
                b.send('title','body')
        response.json.side_effect=None
        response.json.return_value={'code':400,'message':'private-test-key'}
        with patch.object(b.requests,'post',return_value=response):
            with self.assertRaisesRegex(b.BarkError,'BARK_REJECTED: service code 400') as caught:
                b.send('title','body')
            self.assertNotIn('private-test-key',str(caught.exception))

    def test_message_includes_direction_score_beijing_and_paper_status(self):
        self.write(1,6)
        b.prepare(self.now)
        before=self.sf.read_bytes()
        with patch.object(b,'send') as send:
            b.deliver(self.now)
            title,body=send.call_args.args
            self.assertIn('空头',title)
            for text in ('6/6','北京时间','空仓等待','不会下真实订单'):
                self.assertIn(text,body)
        self.assertEqual(self.sf.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
