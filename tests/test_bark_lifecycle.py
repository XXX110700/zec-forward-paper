import copy
from unittest.mock import patch
import unittest
import test_bark_alerts as original
import bark_alerts as b
import zec_v63_github as z

class LifecycleTests(unittest.TestCase):
    setUp = original.BarkTests.setUp
    tearDown = original.BarkTests.tearDown
    write = original.BarkTests.write
    def baseline(self):
        self.write(5,1)
        b.prepare(self.now)
        with patch.object(b,'send'):b.deliver(self.now)

    def signal(self,side='long'):
        self.state['pending_signal']={'side':side,'signal_ts':self.ts,'signal_close':100,
            'atr':2,'long_confirm':5 if side=='long' else 1,'short_confirm':5 if side=='short' else 1}
        self.write()

    def enter(self):
        p=self.state['pending_signal']
        self.state['pending_signal']=None
        self.state['open_trade']={'side':p['side'],'signal_ts':p['signal_ts'],
            'entry_candle_ts':self.ts,'entry':100,'risk':3,'sl':97,'tp1':103.6,'tp2':106,
            'long_confirm':p['long_confirm'],'short_confirm':p['short_confirm']}
        self.write()

    def test_new_signal_and_entry_when_score_stays_five(self):
        self.baseline();self.write(5,1,True);self.signal()
        b.prepare(self.now)
        self.assertEqual([x['kind'] for x in b.load_ledger()['pending']],['signal'])
        with patch.object(b,'send') as send:
            b.deliver(self.now);self.assertIn('新模拟信号',send.call_args.args[0])
        self.write(5,1,True);self.enter();b.prepare(self.now)
        with patch.object(b,'send') as send:
            b.deliver(self.now);self.assertIn('已模拟入场',send.call_args.args[0])
            self.assertIn('入场价 100',send.call_args.args[1])
            b.prepare(self.now);b.deliver(self.now);send.assert_called_once()

    def test_same_position_next_candle_is_not_new_entry(self):
        self.baseline();self.write(5,1,True);self.signal();b.prepare(self.now)
        with patch.object(b,'send'):b.deliver(self.now)
        self.write(5,1,True);self.enter();b.prepare(self.now)
        with patch.object(b,'send'):b.deliver(self.now)
        self.write(5,1,True);b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'],[])

    def test_new_signal_coalesces_threshold_crossing(self):
        b.prepare(self.now);self.write(5,1,True);self.signal();b.prepare(self.now)
        self.assertEqual([e.get('kind') for e in b.load_ledger()['pending']],['signal'])

    def test_closed_same_bar_entry_still_notified(self):
        self.baseline();self.write(5,1,True);self.signal();b.prepare(self.now)
        with patch.object(b,'send'):b.deliver(self.now)
        self.write(2,4,True);self.enter()
        t=self.state['open_trade'];t.update(exit_ts=self.ts,exit_price=97,gross_r=-1,net_r=-1.1,reason='SL')
        self.state['open_trade']=None;self.state['closed_trades'].append(t);self.write();b.prepare(self.now)
        with patch.object(b,'send') as send:
            b.deliver(self.now);send.assert_called_once();self.assertIn('已模拟平仓',send.call_args.args[1])

    def test_failed_delivery_retries_same_identity_once_then_stops(self):
        self.baseline();self.write(5,1,True);self.signal();b.prepare(self.now)
        identity=b.load_ledger()['pending'][0]['id']
        with patch.object(b,'send',side_effect=RuntimeError('fixed')):
            with self.assertRaises(RuntimeError):b.deliver(self.now)
        self.write(3,2,True);b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'][0]['id'],identity)
        with patch.object(b,'send') as send:
            b.deliver(self.now);b.prepare(self.now);b.deliver(self.now);send.assert_called_once()

    def test_short_new_signal_and_second_trade_are_distinct(self):
        self.baseline();self.write(1,5,True);self.signal('short');b.prepare(self.now)
        with patch.object(b,'send') as send:b.deliver(self.now);self.assertIn('空头',send.call_args.args[0])
        first=b.load_ledger()['lifecycle_seen'][:]
        self.state['pending_signal']=None;self.write(1,5,True);self.signal('short');b.prepare(self.now)
        self.assertNotIn(b.load_ledger()['pending'][0]['id'],first)

    def test_activation_does_not_replay_existing_signal(self):
        self.write(5,1);self.signal();b.prepare(self.now)
        self.assertFalse(any(e.get('kind')=='signal' for e in b.load_ledger()['pending']))
        self.write(5,1,True);self.enter();b.prepare(self.now)
        self.assertEqual([e['kind'] for e in b.load_ledger()['pending'] if e.get('kind')],['entry'])

    def test_expired_lifecycle_event_dropped(self):
        self.baseline();self.write(5,1,True);self.signal();b.prepare(self.now)
        self.state['pending_signal']=None
        for _ in range(3):self.write(5,1,True)
        b.prepare(self.now);self.assertEqual(b.load_ledger()['pending'],[])

    def test_future_lifecycle_does_not_create_notification(self):
        self.baseline();self.write(5,1,True);self.signal()
        self.state['pending_signal']['signal_ts']=self.ts+z.BAR15;self.write();b.prepare(self.now)
        self.assertEqual(b.load_ledger()['pending'],[])
