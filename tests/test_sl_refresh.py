import unittest
from unittest.mock import patch
import test_tp_refresh
import zec_v63_github as z

class SLRefreshTests(test_tp_refresh.TPRefreshTests):
    def test_stop_reconfirms_both_sides_without_forced_reversal(self):
        now=int(z.datetime.now(z.timezone.utc).timestamp()*1000)
        def bars(duration):
            end=((now-3*60000)//duration)*duration
            return [(end-i*duration,100,101,99,100) for i in range(300,0,-1)]
        for side in ('long','short'):
            for long_score,short_score,expected in ((5,1,'long'),(1,5,'short'),(3,4,None),(5,5,None)):
                data={k:bars(d) for k,d in [('15m',z.BAR15),('1H',z.BAR1H),('4H',z.BAR4H)]}
                ts=data['15m'][-1][0]
                data['15m'][-1]=(ts,100,111 if side=='short' else 101,89 if side=='long' else 99,100)
                s=self.state(side);s['last_processed_15m_ts']=ts-z.BAR15
                s['open_trade'].update(signal_ts=ts-2*z.BAR15,entry_candle_ts=ts-z.BAR15)
                self.seed_entry_log(s);z.save_state(s)
                with patch.object(z,'confirmations',side_effect=lambda direction,*args: long_score if direction=='long' else short_score) as scores:
                    z.main(candle_source=lambda bar:data[bar]);first=z.load_state()
                    self.assertEqual(scores.call_count,2)
                    self.assertEqual(first['closed_trades'][-1]['reason'],'SL')
                    self.assertIsNone(first['open_trade'])
                    self.assertEqual(first['pending_signal']['side'] if first['pending_signal'] else None,expected)
                    marker=first['sl_confirmation_refresh']
                    self.assertEqual((marker['long_confirm'],marker['short_confirm']),(long_score,short_score))
                    z.main(candle_source=lambda bar:data[bar]);second=z.load_state()
                    self.assertEqual(scores.call_count,2)
                    self.assertEqual(marker,second['sl_confirmation_refresh'])
                broken=dict(second,sl_confirmation_refresh=dict(marker,reason='TP1'))
                with self.assertRaises(ValueError):z.validate_state(broken)

if __name__=='__main__':unittest.main()
