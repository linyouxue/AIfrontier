from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_heat import FILENAME, refresh_heat


class PaperHeatTest(unittest.TestCase):
    def test_pages_versions_and_failure_preserve_last_complete_snapshot(self):
        now = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)
        calls = []

        def row(identifier, votes, published):
            if len(published) == 10:
                published += "T00:00:00Z"
            return {"paper": {"id": identifier, "upvotes": votes, "publishedAt": published}}

        def fetch(month, page):
            calls.append((month, page))
            if month == "2026-11" and page == 0:
                return [row(f"2611.{i:05d}", i, "2026-11-18") for i in range(100)]
            if month == "2026-11":
                return [row("2611.10101v2", 19, "2026-09-04")]
            return [row("2611.10101", 7, "2026-09-04"), row("2608.99999", 75, "2026-08-17"),
                    row("2608.99998", 1, "2026-08-17T17:00:00Z")]

        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            self.assertTrue(refresh_heat(output, now=now, fetch=fetch))
            path = output / FILENAME
            snapshot = path.read_bytes()
            votes = json.loads(snapshot)["votes"]
            self.assertEqual((len(votes), votes["2611.10101"], votes["2608.99998"]), (102, 19, 1))
            self.assertEqual(calls, [("2026-11", 0), ("2026-11", 1), ("2026-10", 0), ("2026-09", 0), ("2026-08", 0)])
            self.assertFalse(refresh_heat(output, now=now, fetch=lambda *_: {"error": "unavailable"}))
            self.assertEqual(path.read_bytes(), snapshot)
