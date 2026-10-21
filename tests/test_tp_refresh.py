import copy
import json
import unittest
from unittest.mock import patch
import test_safety
import zec_v63_github as z


class TPRefreshTests(unittest.TestCase):
    setUp = test_safety.SafetyTests.setUp
    tearDown = test_safety.SafetyTests.tearDown
    def trade(self, side='long'):
        return dict(side=side, signal_ts=z.BAR15, entry_candle_ts=2*z.BAR15,
                    entry=100, risk=10, sl=90 if side=='long' else 110,
                    tp1=112 if side=='long' else 88, tp2=120 if side=='long' else 80,
                    long_confirm=5, short_confirm=0)

    def state(self, side='long'):
        s=z.default_state()
        s.update(last_processed_15m_ts=z.BAR15, open_trade=self.trade(side))
        return s

    def test_tp1_tp2_both_directions_persist_and_replay_once(self):
        for side in ('long','short'):
            for reason, high, low in (('TP1',113,99),('TP2',121,99)) if side=='long' else (('TP1',101,87),('TP2',101,79)):
                s=self.state(side)
                t=z.maybe_exit(s,(2*z.BAR15,100,high,low,100))
                self.assertEqual(t['reason'],reason)
                z.record_tp_refresh(s,t,3*z.BAR15,2,5)
                s['last_processed_15m_ts']=2*z.BAR15
                z.commit_state(s)
                reloaded=z.load_state()
                z.record_tp_refresh(reloaded,t,3*z.BAR15,6,0)
                self.assertEqual(reloaded['tp_confirmation_refresh'],s['tp_confirmation_refresh'])
                self.assertEqual(reloaded['tp_confirmation_refresh']['count'],1)
                self.assertEqual((reloaded['tp_confirmation_refresh']['long_confirm'],reloaded['tp_confirmation_refresh']['short_confirm']),(2,5))
                self.assertIsNone(z.maybe_exit(reloaded,(2*z.BAR15,100,high,low,100)))

    def test_sl_floating_profit_and_same_bar_sl_priority(self):
        for high,low,reason in ((105,99,None),(101,89,'SL'),(121,89,'SL')):
            s=self.state()
            closed=z.maybe_exit(s,(2*z.BAR15,100,high,low,100))
            z.record_tp_refresh(s,closed,3*z.BAR15,5,0)
            self.assertNotIn('tp_confirmation_refresh',s)
            if reason: self.assertEqual(closed['reason'],reason)
            else: self.assertIsNotNone(s['open_trade'])

    def test_corrupt_marker_stops_without_overwriting(self):
        s=self.state()
        t=z.maybe_exit(s,(2*z.BAR15,100,113,99,100))
        z.record_tp_refresh(s,t,3*z.BAR15,2,5)
        s['last_processed_15m_ts']=2*z.BAR15
        for key,value in (('count',True),('event_id','unknown'),('long_confirm',float('nan')),('decision_ts',0),('reason','SL')):
            broken=copy.deepcopy(s);broken['tp_confirmation_refresh'][key]=value
            raw=json.dumps(broken);z.STATE_FILE.write_text(raw)
            with self.assertRaises(RuntimeError):z.load_state()
            self.assertEqual(z.STATE_FILE.read_text(),raw)

    def test_main_tp_refresh_then_same_candle_repeat(self):
        now=int(z.datetime.now(z.timezone.utc).timestamp()*1000)
        def bars(duration):
            end=((now-3*60000)//duration)*duration
            return [(end-i*duration,100,101,99,100) for i in range(300,0,-1)]
        fixed_now = z.datetime.fromtimestamp(now/1000, z.timezone.utc)
        data={'15m':bars(z.BAR15),'1H':bars(z.BAR1H),'4H':bars(z.BAR4H)}
        ts=data['15m'][-1][0]
        data['15m'][-1]=(ts,100,113,99,100)
        s=self.state();s['last_processed_15m_ts']=ts-z.BAR15
        z.save_state(s)
        with patch.object(z,'datetime', wraps=z.datetime) as clock, patch.object(z,'recent_closed',side_effect=lambda bar:data[bar]), patch.object(z,'confirmations',side_effect=lambda side,*args: 3 if side=='long' else 4) as scores:
            clock.now.return_value = fixed_now
            z.main(); first=z.load_state()
            self.assertEqual(scores.call_count,2)
            z.main(); second=z.load_state()
            self.assertEqual(scores.call_count,2)
        self.assertEqual(first['tp_confirmation_refresh'],second['tp_confirmation_refresh'])
        self.assertEqual(len(second['closed_trades']),1)
        self.assertEqual(second['tp_confirmation_refresh']['long_confirm'],3)
        self.assertEqual(second['tp_confirmation_refresh']['short_confirm'],4)
