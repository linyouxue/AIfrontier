from datetime import datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_social import collect, x_posts


class AccountUpdates(unittest.TestCase):
    def test_account_feed_reads_original_atom_dates_and_rejects_wrong_owner(self):
        owner, note = "a" * 24, "b" * 24
        profile = "https://www.xiaohongshu.com/user/profile/" + owner
        registry = {"x": [], "xiaohongshu": [{"id": owner, "name": "博主", "baseline": []}]}
        document = f'''<feed xmlns="http://www.w3.org/2005/Atom"><title>博主</title>
          <link href="{profile}"/><entry><title>技术笔记</title><author><name>博主</name></author>
          <link href="https://www.xiaohongshu.com/explore/{note}"/><published>2020-01-01T01:00:00Z</published>
          <updated>{datetime.now(timezone.utc).isoformat()}</updated></entry></feed>'''
        url = "https://feeds.example/account"
        with patch.dict("os.environ", {"AIFRONTIER_ACCOUNT_FEEDS": json.dumps({profile: url})}), \
             patch("social_event_sources.read", side_effect=[document, document.replace(owner, "c"*24)]) as request:
            source, = collect(registry, {}, None)["sources"]
            self.assertEqual((source["status"], source["verifiedNotes"], source["discovered"]), ("ok", 1, 0))
            self.assertEqual(request.call_args.args, (url,))
            failed, = collect(registry, {}, None)["sources"]
            self.assertEqual((failed["status"], failed["verifiedNotes"]), ("failed", 0))

    def test_x_feed_fallback_excludes_reposts_and_quoted_body(self):
        now = format_datetime(datetime.now(timezone.utc))
        text = "We measured GPU inference latency across model sizes and shared the benchmark results. " * 3
        def item(owner, identifier, first):
            url = f"https://x.com/{owner}/status/{identifier}"
            return (f"<item><link>{url}</link><guid>{url}</guid><pubDate>{now}</pubDate>"
                    f"<description><![CDATA[<p>{first}</p><blockquote>{text}</blockquote>]]></description></item>")
        rss = ('<rss><channel><link>https://fxtwitter.com/lab</link>' + item("lab", 1, text)
               + item("other", 2, text) + item("lab", 3, "Sharing") + "</channel></rss>")
        with patch("collect_social.read", side_effect=[HTTPError("test", 404, "", {}, None), rss]):
            self.assertEqual([row["id"] for row in x_posts("Lab")], ["x:1"])

    def test_fxembed_keeps_owned_originals_and_dates(self):
        now = datetime.now(timezone.utc)
        row = {"type": "status", "id": "123", "author": {"screen_name": "Lab"},
               "url": "https://x.com/Lab/status/123", "created_timestamp": now.timestamp(),
               "text": "Our benchmark measures AI inference latency across several new models. " * 3}
        page = {"code": 200, "results": [row, {**row, "author": {"screen_name": "Other"}},
                                        {**row, "reposted_by": {"name": "Lab"}}, {**row, "replying_to": {"id": "1"}}]}
        with patch("collect_social.read", return_value=json.dumps(page)):
            self.assertEqual([p["id"] for p in x_posts("lab")], ["x:123"])

    def test_notes_survive_expired_login_and_known_baseline_is_not_a_new_update(self):
        account, old, new = "a" * 24, "b" * 24, "c" * 24
        registry = {"x": [], "xiaohongshu": [{"id": account, "name": "博主", "baseline": [old]}]}
        stamp = format_datetime(datetime.now(timezone.utc))
        def item(key, author="博主", date=stamp):
            return (f"<item><link>https://www.xiaohongshu.com/explore/{key}?xsec_token=private</link>"
                    f"<guid>https://www.xiaohongshu.com/explore/{key}</guid><title>技术笔记</title>"
                    f"<author>{author}</author><pubDate>{date}</pubDate></item>")
        channel = (f"<rss><channel><link>https://www.xiaohongshu.com/user/profile/{account}</link>"
                   "<title>博主 - 笔记 • 小红书 / RED</title>")
        body = item(old) + item("d" * 24, date="") + item("e" * 24, "别人") + item(new)
        with patch("collect_social.read", return_value=channel + body + "</channel></rss>"):
            first = collect(registry, {}, "http://localhost")
            second = collect(registry, first, "http://localhost")
        self.assertEqual(len(first["items"]), 2)
        self.assertNotIn("xsec_token", str(first))
        self.assertEqual((first["sources"][0]["status"], first["sources"][0]["inserted"]), ("partial", 1))
        self.assertEqual(second["sources"][0]["inserted"], 0)
        with patch("collect_social.read", return_value="<html>login</html>"):
            failed = collect(registry, second, "http://localhost")
        self.assertEqual(failed["sources"][0]["status"], "failed")
        self.assertEqual(failed["items"], first["items"])
