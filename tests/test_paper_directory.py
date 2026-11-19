from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_topics import TAXONOMY, research_paths
from build_preview import known_announcements
from publish_papers import publish, ranked


class PaperDirectoryTest(unittest.TestCase):
    def test_reuses_verified_metadata_but_refetches_replacements_and_new_categories(self):
        event = {"date":"2026-11-18", "category":"cs.RO", "type":"cross"}
        paper = dict(categories=["cs.RO"], primaryCategory="cs.RO", updatedAt="2026-11-18",
                     version="v1", announcements=[event])
        self.assertTrue(known_announcements(paper, [event | {"type":"new-or-cross"}]))
        self.assertFalse(known_announcements(paper, [event | {"type":"replace", "version":"v2"}]))
        self.assertFalse(known_announcements(paper, [event | {"category":"cs.AI"}]))

    def test_batches_votes_missing_signal_ties_and_retention(self):
        rows = [{"id": k, "published": day} for k, day in [
            ("older", "2026-11-08"), ("unknown", "2026-11-18"), ("zero", "2026-11-12"),
            ("hot", "2026-11-09"), ("recent", "2026-11-18"), ("expired", "2026-08-17")]]
        votes = {"older": 900, "hot": 7, "recent": 7, "zero": 0}
        order = ranked(rows, votes, date(2026, 11, 18))
        self.assertEqual([p["id"] for p in order], ["recent", "hot", "zero", "unknown", "older"])

    def test_cross_listed_paper_has_one_rank_and_full_abstract_is_searchable(self):
        row = dict(id="2611.12345", title="Efficient systems", source="arXiv · Author",
                   summary="Large language models use retrieval-augmented decoding and a KV cache.",
                   categories=["cs.CL", "cs.IR"], published="2026-11-17", announcements=[])
        row["paths"] = research_paths(row, True)
        checked = "2026-11-18T01:00:00+00:00"
        state = dict(papers=[row], taxonomy=TAXONOMY, checkedAt=checked, announcementDate="2026-11-18")
        with TemporaryDirectory() as tmp:
            site = Path(tmp)
            output = site / "papers"
            for name in ("wechat", "blogs", "social", "events", "podcasts"):
                (site / (name+".json")).write_text(json.dumps({"items":[], "sources":[{"checkedAt":checked,"status":"ok","inserted":0}]}))
            publish(site, output, state, {"checkedAt":checked, "votes":{row["id"]:5}},
                    datetime(2026,11,18,2,tzinfo=timezone.utc))
            index = json.loads((output / "index-data.json").read_bytes())
            self.assertEqual(len(index["rows"]), 1)
            self.assertGreaterEqual(len(index["rows"][0][1]), 2)
            self.assertEqual(index["rows"][0][-1], 5)
            checks = json.loads((site / "collection-status.json").read_bytes())["modules"]
            for name in ("events", "podcasts"):
                self.assertEqual(checks[name], {"checkedAt":checked,"status":"ok","inserted":0})
            self.assertIn("theme-switch", (site / "index.html").read_text("utf-8"))
            self.assertIn("#/arxiv", (output / "index.html").read_text("utf-8"))
            self.assertIn("cache", (site / "paper-abstracts" / "0000.json").read_text("utf-8"))
            from search_index import shard_for, tokens
            term = tokens("cache")[0]
            shard = json.loads((site / "search-index" / f"{shard_for(term):02x}.json").read_bytes())
            self.assertIn(term, shard["terms"])
