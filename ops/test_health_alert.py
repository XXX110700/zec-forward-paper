import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
spec=importlib.util.spec_from_file_location('health_alert',Path(__file__).with_name('health_alert.py'))
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
class AlertTests(unittest.TestCase):
    def test_fresh_state_sends_nothing(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';path.write_text(json.dumps({'updated_at':now.isoformat()}))
            sender=Mock();self.assertFalse(h.check(path,sender,now));sender.assert_not_called()
    def test_stale_state_sends_fixed_message_only(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';path.write_text(json.dumps({'updated_at':(now-timedelta(minutes=31)).isoformat(),'private':'DO_NOT_SEND'}))
            sender=Mock();self.assertTrue(h.check(path,sender,now));sender.assert_called_once()
            self.assertNotIn('DO_NOT_SEND',str(sender.call_args))
    def test_invalid_and_future_are_alerts(self):
        now=datetime.now(timezone.utc)
        for state in ({},[],{'updated_at':'bad'},{'updated_at':(now+timedelta(minutes=2)).isoformat()}):
            self.assertIsNotNone(h.reason(state,now))
    def test_delivery_error_not_claimed_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):h.check(Path(tmp)/'missing',Mock(side_effect=RuntimeError('fixed')))
if __name__=='__main__':unittest.main()
