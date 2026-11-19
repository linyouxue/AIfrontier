import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_events import EventSourceBatch
from x_event_sources import x_candidates


class XEventsTest(unittest.TestCase):
    def test_confirmed_reset_keeps_original_and_excludes_speculation_and_reposts(self):
        def post(number, text, **fields):
            return {"id": str(number), "type": "status", "author": {"screen_name": "thsottiaux"},
                    "url": f"https://x.com/thsottiaux/status/{number}", "created_timestamp": 1794960000,
                    "text": text, **fields}
        rows = [post(123, "The banked reset has been activated."),
                post(124, "Maybe I should bring back a banked reset."),
                post(125, "The banked reset has been activated.", reposted_by={"name": "someone"})]
        with patch("x_event_sources.read", return_value=json.dumps({"code": 200, "results": rows})):
            batch = x_candidates("thsottiaux", EventSourceBatch)
        self.assertEqual(batch.checked, 3)
        event, = batch.candidates
        self.assertTrue(event.verified)
        self.assertEqual(event.url, rows[0]["url"])
