import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zec_v63_github as z
import recover_paper as recovery
from paper_audit import reconcile

ROOT = Path(z.__file__).parent


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv = self.root/'trades.csv'
        self.raw = (ROOT/'tests/fixtures/trades.csv').read_bytes()
        self.csv.write_bytes(self.raw)
        self.state = json.loads((ROOT/'tests/fixtures/state.json').read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_records_and_stats(self):
        result = reconcile(self.state,self.csv)
        self.assertEqual(result['closed_trades'],9)
        self.assertEqual(result['event_count'],27)
        self.assertAlmostEqual(result['total_r'],1.758447)

    def test_audit_never_creates_backup_or_calls_network(self):
        sf = self.root/'state.json'
        for raw in (json.dumps(self.state), '{bad json', '{}'):
            sf.write_text(raw)
            before = {p.name:p.read_bytes() for p in self.root.iterdir()}
            with patch.object(z,'STATE_FILE',sf), patch.object(z,'CSV_FILE',self.csv), \
                    patch.object(z,'recent_closed') as network, patch.object(z,'load_state') as loader:
                if raw == json.dumps(self.state):
                    self.assertEqual(recovery.audit()['closed_trades'],9)
                else:
                    with self.assertRaises(ValueError):
                        recovery.audit()
                network.assert_not_called()
                loader.assert_not_called()
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        sf.unlink()
        with patch.object(z,'STATE_FILE',sf), patch.object(z,'CSV_FILE',self.csv):
            with self.assertRaises(FileNotFoundError):
                recovery.audit()
        self.assertFalse(sf.exists())

    def test_missing_duplicate_tampered_header_and_sequence(self):
        rows = list(csv.reader(io.StringIO(self.raw.decode())))
        bad = [rows[:-1], rows+[rows[-1]], rows[1:], [rows[0],rows[2],rows[1]]+rows[3:]]
        tampered = copy.deepcopy(rows)
        tampered[-1][11] = '999'
        bad.append(tampered)
        for variant in bad:
            with self.subTest(variant=variant[-1]):
                with self.csv.open('w',newline='') as f:
                    csv.writer(f).writerows(variant)
                before = self.csv.read_bytes()
                with self.assertRaises(RuntimeError):
                    reconcile(self.state,self.csv)
                self.assertEqual(self.csv.read_bytes(),before)

    def test_state_duplicate_or_bad_drawdown_stops(self):
        for key,value in [('closed_trades',self.state['closed_trades']*2),('max_drawdown',99)]:
            invalid = dict(self.state)
            invalid[key] = value
            with self.assertRaises(RuntimeError):
                reconcile(invalid,self.csv)

    def test_pending_and_open_trade_lifecycle(self):
        state = copy.deepcopy(self.state)
        trade = state['closed_trades'].pop()
        for k in ('exit_ts','exit_price','reason','gross_r','net_r'):
            trade.pop(k)
        state['open_trade'] = trade
        state.pop('max_drawdown')
        rows = list(csv.reader(io.StringIO(self.raw.decode())))
        with self.csv.open('w',newline='') as f:
            csv.writer(f).writerows(rows[:-1])
        reconcile(state,self.csv)
        state['open_trade'] = None
        state['pending_signal'] = {k:trade[k] for k in ('side','signal_ts','long_confirm','short_confirm')}
        state['pending_signal'].update(signal_close=100,atr=1)
        with self.csv.open('w',newline='') as f:
            csv.writer(f).writerows(rows[:-2])
        reconcile(state,self.csv)

    def test_conflicting_and_duplicate_outbox_stops(self):
        row = list(csv.reader(io.StringIO(self.raw.decode())))[-1]
        reconcile(self.state,self.csv,[row])  # exact interrupted-write retry
        with self.assertRaises(RuntimeError):
            reconcile(self.state,self.csv,[row,row])
        row[11] = '123'
        with self.assertRaises(RuntimeError):
            reconcile(self.state,self.csv,[row])

    def test_generated_full_trade_commits_and_reconciles(self):
        state = z.default_state()
        signal = 100*z.BAR15
        state['last_processed_15m_ts'] = signal+z.BAR15
        state['pending_signal'] = {'side':'long','signal_ts':signal,'signal_close':100,
                                   'atr':1,'long_confirm':5,'short_confirm':0}
        sf = self.root/'state.json'
        cf = self.root/'new.csv'
        with patch.object(z,'STATE_FILE',sf),patch.object(z,'CSV_FILE',cf):
            z.EVENTS.clear()
            z.append_csv(['SIGNAL','long',z.fmt_ts(signal)]+['']*9+[5,0])
            bar = (signal+z.BAR15,100,104,99,103)
            z.maybe_enter(state,bar)
            z.maybe_exit(state,bar)
            state['max_drawdown'] = z.stats(state['closed_trades'])[4]
            z.commit_state(state)
            result = reconcile(z.load_state(),cf)
            self.assertEqual(result['closed_trades'],1)
            self.assertEqual(result['event_count'],3)
            self.assertEqual(z.load_state()['closed_trades'][0]['reason'],'TP2')
        z.EVENTS.clear()

    def test_main_rejects_bad_log_before_network_or_write(self):
        self.csv.write_bytes(self.raw+self.raw.splitlines(keepends=True)[-1])
        sf = self.root/'state.json'
        sf.write_text(json.dumps(self.state))
        before = (sf.read_bytes(),self.csv.read_bytes())
        with patch.object(z,'STATE_FILE',sf),patch.object(z,'CSV_FILE',self.csv),patch.object(z,'recent_closed') as api:
            with self.assertRaises(RuntimeError):
                z.main()
            api.assert_not_called()
        self.assertEqual((sf.read_bytes(),self.csv.read_bytes()),before)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sf = self.root/'zec_v63_paper_state.json'
        self.cf = self.root/'zec_v63_trades.csv'
        self.current = recovery.now_ms()
        self.cursor = ((self.current-3*60000)//z.BAR15-10)*z.BAR15
        state = z.default_state()
        state['last_processed_15m_ts'] = self.cursor
        self.sf.write_text(json.dumps(state))
        self.patches = [patch.object(z,'STATE_FILE',self.sf),patch.object(z,'CSV_FILE',self.cf)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def bars(self,bar,start,current):
        duration = recovery.PERIODS[bar]
        end = ((current-3*60000)//duration)*duration
        return [(ts,100,101,99,100) for ts in range(start,end,duration)]

    def prepare(self):
        package = self.root/'package'
        with patch.object(recovery,'history',side_effect=self.bars):
            recovery.prepare(package)
        return package

    def test_prepare_replay_isolated_and_cannot_overwrite(self):
        before = self.sf.read_bytes()
        package = self.prepare()
        recovery.replay(package)
        result = json.loads((package/'result.json').read_text())
        self.assertEqual(result['status'],'complete')
        candidate = json.loads((package/'candidate'/self.sf.name).read_text())
        self.assertEqual(candidate['recovery']['mode'],'historical_recovery')
        self.assertGreater(candidate['last_processed_15m_ts'],self.cursor)
        self.assertEqual(self.sf.read_bytes(),before)
        self.assertFalse(self.cf.exists())
        with self.assertRaises(FileExistsError):
            recovery.replay(package)
        with self.assertRaises(RuntimeError):
            recovery.prepare(package)

    def test_checksum_and_stale_package_rejected(self):
        package = self.prepare()
        candles = package/'input/candles.json'
        original = candles.read_bytes()
        candles.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'checksum'):
            recovery.replay(package)
        candles.write_bytes(original)
        with patch.object(recovery,'now_ms',return_value=self.current+z.BAR4H):
            with self.assertRaises(RuntimeError):
                recovery.replay(package)
        self.assertFalse((package/'candidate').exists())

    def test_replay_tp_refresh_reconciles_without_touching_sources(self):
        state = z.default_state()
        signal = self.cursor-z.BAR15
        state.update(last_processed_15m_ts=self.cursor,
                     pending_signal=dict(side='long',signal_ts=signal,signal_close=100,
                                         atr=2,long_confirm=5,short_confirm=0))
        z.EVENTS.clear()
        z.append_csv(['SIGNAL','long',z.fmt_ts(signal)]+['']*9+[5,0])
        z.maybe_enter(state,(self.cursor,100,101,99,100))
        z.commit_state(state)
        before = (self.sf.read_bytes(),self.cf.read_bytes())
        def history(bar,start,current):
            bars = self.bars(bar,start,current)
            if bar == '15m':
                bars[-1] = (bars[-1][0],100,104,99,100)
            return bars
        package = self.root/'tp-package'
        with patch.object(recovery,'history',side_effect=history):
            recovery.prepare(package)
        with patch.object(z,'confirmations',side_effect=lambda side,*args: 3 if side=='long' else 4), \
                patch.object(z,'recent_closed') as network:
            recovery.replay(package)
            network.assert_not_called()
        candidate = json.loads((package/'candidate'/self.sf.name).read_text())
        self.assertEqual(candidate['tp_confirmation_refresh']['reason'],'TP1')
        self.assertEqual(candidate['tp_confirmation_refresh']['count'],1)
        self.assertEqual(candidate['recovery']['mode'],'historical_recovery')
        self.assertEqual(reconcile(candidate,package/'candidate'/self.cf.name)['event_count'],3)
        self.assertEqual((self.sf.read_bytes(),self.cf.read_bytes()),before)

    def test_failed_replay_never_writes_source_or_complete_result(self):
        package = self.prepare()
        before = self.sf.read_bytes()
        with patch.object(z,'main',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):
                recovery.replay(package)
        self.assertEqual(self.sf.read_bytes(),before)
        self.assertFalse((package/'result.json').exists())
        self.assertEqual(z.STATE_FILE,self.sf)

    def test_missing_decision_warmup_stops_before_events(self):
        def recent(bar):
            duration = recovery.PERIODS[bar]
            end = ((self.current-3*60000)//duration)*duration
            return [(ts,100,101,99,100) for ts in range(end-300*duration,end,duration)]
        market = {bar:recent(bar) for bar in recovery.PERIODS}
        state = z.default_state()
        state['last_processed_15m_ts'] = market['15m'][0][0]
        self.sf.write_text(json.dumps(state))
        before = self.sf.read_bytes()
        with patch.object(z,'maybe_enter') as enter:
            with self.assertRaisesRegex(RuntimeError,'warmup'):
                z.main(candle_source=lambda bar:market[bar])
            enter.assert_not_called()
        self.assertEqual(self.sf.read_bytes(),before)

    def test_too_old_state_stops_before_download(self):
        state = json.loads(self.sf.read_text())
        state['last_processed_15m_ts'] -= recovery.MAX_OUTAGE
        self.sf.write_text(json.dumps(state))
        with patch.object(recovery,'history') as download:
            with self.assertRaises(RuntimeError):
                recovery.prepare(self.root/'package')
            download.assert_not_called()

    def test_history_pagination_duplicate_gap_and_no_progress(self):
        current = 1000*z.BAR15+4*60000
        recent = [(i*z.BAR15,100,101,99,100) for i in range(997,1000)]
        page = [[str(i*z.BAR15),'100','101','99','100','0','0','0','1'] for i in (996,995,994)]
        with patch.object(z,'recent_closed',return_value=recent), patch.object(recovery.time,'sleep'), patch.object(z,'api',return_value=page) as api:
            result = recovery.history('15m',994*z.BAR15,current)
            self.assertEqual(len(result),6)
            self.assertEqual(api.call_args.args[1]['after'],str(997*z.BAR15))
        for broken in ([],page[::2],[[str(997*z.BAR15),'100','101','99','100','0','0','0','1']]):
            with patch.object(z,'recent_closed',return_value=recent),patch.object(recovery.time,'sleep'),patch.object(z,'api',return_value=broken):
                with self.assertRaises(RuntimeError):
                    recovery.history('15m',994*z.BAR15,current)


if __name__ == '__main__':
    unittest.main()
