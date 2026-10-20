import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zec_v63_github as z


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(z, 'STATE_FILE', root/'state.json'), patch.object(z, 'CSV_FILE', root/'trades.csv')]
        for p in self.patches:
            p.start()
        z.EVENTS.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_corrupt_and_schema_fail_closed_with_exact_backup(self):
        for raw in ('{broken', '{}', 'null', '{"version":"V6.3","closed_trades":[]}'):
            z.STATE_FILE.write_text(raw)
            with self.assertRaises(RuntimeError):
                z.load_state()
            self.assertEqual(z.STATE_FILE.read_text(), raw)
            self.assertIn(raw, [p.read_text() for p in z.STATE_FILE.parent.glob('*.bak')])

    def test_missing_state_with_history_stops(self):
        z.CSV_FILE.write_text('history')
        with self.assertRaises(RuntimeError):
            z.load_state()

    def test_atomic_replace_failure_preserves_state(self):
        s = z.default_state()
        z.save_state(s)
        old = z.STATE_FILE.read_bytes()
        with patch.object(z.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                z.save_state(s)
        self.assertEqual(z.STATE_FILE.read_bytes(), old)
        self.assertEqual(list(z.STATE_FILE.parent.glob('*.tmp')), [])

    def test_outbox_replay_is_idempotent_after_interruption(self):
        s = z.default_state()
        s['last_processed_15m_ts'] = z.BAR15
        row = ['SIGNAL','long',z.fmt_ts(z.BAR15)] + ['']*9 + [5,0]
        z.append_csv(row)
        with patch.object(z, 'flush_csv', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                z.commit_state(s)
        s = z.load_state()
        with patch.object(z, 'save_state', side_effect=OSError('interrupted after csv')):
            with self.assertRaises(OSError):
                z.flush_csv(s)
        z.flush_csv(z.load_state())
        with z.CSV_FILE.open() as f:
            self.assertEqual(len(list(csv.reader(f))), 2)
        self.assertNotIn('csv_outbox', z.load_state())

    def test_all_timeframes_stale_future_gaps(self):
        for duration in (z.BAR15, z.BAR1H, z.BAR4H):
            now = 1000*duration + 4*60000
            bars = [(i*duration,100,102,98,101) for i in range(997,1000)]
            z.validate_market(bars, duration, now)
            for invalid in (bars[:-1], bars+[(1000*duration,100,102,98,101)], bars[::2]):
                with self.assertRaises(RuntimeError):
                    z.validate_market(invalid, duration, now)

    def test_fresh_run_then_repeat_has_no_duplicate_events(self):
        now = int(z.datetime.now(z.timezone.utc).timestamp()*1000)
        def bars(duration):
            end = ((now-3*60000)//duration)*duration
            return [(end-i*duration,100,101,99,100) for i in range(300,0,-1)]
        data = {'15m':bars(z.BAR15),'1H':bars(z.BAR1H),'4H':bars(z.BAR4H)}
        with patch.object(z, 'recent_closed', side_effect=lambda bar:data[bar]):
            z.main()
            first = z.load_state()
            z.main()
        second = z.load_state()
        self.assertEqual(first['last_processed_15m_ts'],second['last_processed_15m_ts'])
        self.assertEqual(second['closed_trades'], [])
        self.assertEqual(second['last_processed_close_ts'], data['15m'][-1][0]+z.BAR15)
        self.assertFalse(z.CSV_FILE.exists())

    def test_repository_log_matches_state(self):
        root = Path(z.__file__).parent
        state = z.validate_state(json.loads((root/'tests/fixtures/state.json').read_text()))
        with (root/'tests/fixtures/trades.csv').open() as f:
            exits = [r for r in csv.DictReader(f) if r['event'] == 'EXIT']
        self.assertEqual(len(exits), len(state['closed_trades']))
        for row, trade in zip(exits, state['closed_trades']):
            self.assertEqual(float(row['net_r']), trade['net_r'])
            self.assertEqual(row['entry_utc'], z.fmt_ts(trade['entry_candle_ts']))
        self.assertAlmostEqual(z.stats(state['closed_trades'])[4], state['max_drawdown'])

    def test_stale_main_does_not_change_files_or_trade(self):
        s = z.default_state()
        z.save_state(s)
        old = z.STATE_FILE.read_bytes()
        bars = [(i*z.BAR15,100,102,98,101) for i in range(300)]
        with patch.object(z, 'recent_closed', return_value=bars), patch.object(z, 'maybe_enter') as enter:
            with self.assertRaises(RuntimeError):
                z.main()
            enter.assert_not_called()
        self.assertEqual(z.STATE_FILE.read_bytes(), old)
        self.assertFalse(z.CSV_FILE.exists())


if __name__ == '__main__':
    unittest.main()
