from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from xml.sax.saxutils import escape


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from collect_events import EventSource, EventSourceBatch, collect_events
from event_sources import EvidenceCandidate
from social_event_sources import social_sources


class SocialEventTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)
        self.documents = {}

        class FeedServer(BaseHTTPRequestHandler):
            def do_GET(request):
                if request.path not in self.documents:
                    request.send_error(401)
                    return
                request.send_response(200)
                request.end_headers()
                request.wfile.write(self.documents[request.path].encode("utf-8"))

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FeedServer)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"
        self.published = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
            hour=17, minute=6, second=0, microsecond=0
        )

    def collect(self, official=(), **options):
        sources = social_sources(EventSource, EventSourceBatch, **options)
        collect_events(self.output, official, extra_sources=sources)
        return json.loads((self.output / "events.json").read_text(encoding="utf-8"))

    def test_unconfigured_and_session_failures_are_distinguishable_from_an_empty_feed(self):
        disabled = self.collect()
        self.assertEqual({source["id"]: source["status"] for source in disabled["sources"]},
                         {"xiaohongshu": "unconfigured", "weibo": "unconfigured"})

        self.documents["/weibo"] = (
            '<rss version="2.0"><channel><title>示例的微博</title>'
            '<link>https://weibo.com/1234567890/</link></channel></rss>'
        )
        config = {"xiaohongshu_url": self.base + "/xiaohongshu", "weibo_url": self.base + "/weibo"}
        statuses = {source["id"]: source for source in self.collect(**config)["sources"]}
        self.assertEqual(statuses["xiaohongshu"]["status"], "failed")
        self.assertEqual(statuses["xiaohongshu"]["error"], "HTTP 401")
        self.assertEqual(statuses["weibo"]["status"], "ok")
        self.assertEqual(statuses["weibo"]["checked"], 0)
        last_success = statuses["weibo"]["lastSuccessAt"]

        self.documents["/weibo"] = "<html><title>Sign in</title></html>"
        statuses = {source["id"]: source for source in self.collect(**config)["sources"]}
        self.assertEqual(statuses["weibo"]["status"], "failed")
        self.assertEqual(statuses["weibo"]["lastSuccessAt"], last_success)

    def test_both_platforms_preserve_attributed_originals_of_one_existing_event(self):
        original = "https://openai.com/index/introducing-example-kit/"
        published_date = (self.published + timedelta(hours=8)).date().isoformat()
        official = EventSource("official-fixture", "official", lambda: [EvidenceCandidate(
            original, "Introducing Example Kit", "OpenAI News", self.published.isoformat(),
            "Example Kit is now available.", verified=True,
        )])
        self.documents["/xiaohongshu"] = f'''<feed xmlns="http://www.w3.org/2005/Atom">
            <title>示例笔记作者</title>
            <link rel="alternate" href="https://www.xiaohongshu.com/user/profile/623456789abc0123456789ab"/>
            <entry>
              <title>Example Kit 正式发布</title>
              <id>https://www.xiaohongshu.com/explore/6123456789abc0123456789a</id>
              <link href="https://www.xiaohongshu.com/discovery/item/6123456789abc0123456789a?utm_source=example"/>
              <author><name>示例笔记作者</name></author>
              <published>{self.published.isoformat()}</published>
              <updated>{datetime.now(timezone.utc).isoformat()}</updated>
              <summary type="html">{escape(f'发布原文 <a href="{original}">官网公告</a>')}</summary>
            </entry></feed>'''
        weibo_entry = f'''<item>
            <title>Example Kit 正式发布</title>
            <link>https://m.weibo.cn/status/AbCdE1234</link>
            <guid>https://weibo.com/1234567890/AbCdE1234</guid>
            <dc:creator>示例博主</dc:creator>
            <pubDate>{self.published:%a, %d %b %Y %H:%M:%S +0000}</pubDate>
            <description>发布原文 {original}?utm_source=example</description>
          </item>'''
        self.documents["/weibo"] = f'''<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
            <channel><title>示例博主的微博</title><link>https://weibo.com/1234567890/</link>
              {weibo_entry}{weibo_entry.replace('m.weibo.cn/status/', 'weibo.com/1234567890/')}
            </channel></rss>'''
        config = {"xiaohongshu_url": self.base + "/xiaohongshu", "weibo_url": self.base + "/weibo"}
        result = self.collect([official], **config)
        item, = result["items"]
        self.assertEqual(item["original"], original)
        self.assertEqual([(e["url"], e["source"], e["published"]) for e in item["evidence"][1:]], [
            ("https://www.xiaohongshu.com/explore/6123456789abc0123456789a", "示例笔记作者", published_date),
            ("https://weibo.com/1234567890/AbCdE1234", "示例博主", published_date),
        ])
        self.assertEqual({s["id"]: s["linked"] for s in result["sources"] if s["platform"] != "official"},
                         {"xiaohongshu": 1, "weibo": 1})
        for evidence in item["evidence"][1:]:
            self.assertEqual(evidence["publishedAt"], self.published.isoformat())
            self.assertEqual(evidence["discoveredAt"], item["collected"])
        self.assertEqual(self.collect([official], **config)["items"], [item])

    def test_only_owner_confirmed_original_announcements_can_create_events(self):
        profile = "https://weibo.com/u/1234567890"
        timestamp = self.published.isoformat()

        def item(author, link, description):
            return f'''<item><title>新一代 Demo AI 模型</title><link>{link}</link>
              <author>{author}</author><pubDate>{timestamp}</pubDate>
              <description>{escape(description)}</description></item>'''

        self.documents["/weibo"] = f'''<rss version="2.0"><channel>
          <title>示例实验室的微博</title><link>{profile}</link>
          {item("示例实验室", "https://weibo.com/1234567890/aBc123",
                "我们今天正式发布 Demo AI 模型。")}
          {item("示例实验室", "https://weibo.com/1234567890/aBc124",
                "转发 <blockquote>我们今天正式发布 Demo AI 模型。</blockquote>")}
          {item("其他作者", "https://weibo.com/1234567890/aBc125",
                "我们今天正式发布 Demo AI 模型。")}
          {item("示例实验室", "https://weibo.com/2345678901/aBc126",
                "我们今天正式发布 Demo AI 模型。")}
          {item("示例实验室", "https://weibo.com.example.invalid/1234567890/aBc127",
                "我们今天正式发布 Demo AI 模型。")}
        </channel></rss>'''
        config = {"weibo_url": self.base + "/weibo"}
        self.assertEqual(self.collect(**config)["items"], [])
        result = self.collect(**config, official_profiles={profile: "示例实验室"})
        event, = result["items"]
        self.assertEqual(event["original"], "https://weibo.com/1234567890/aBc123")
        self.assertEqual(event["announcedAt"], (self.published + timedelta(hours=8)).date().isoformat())
        self.assertEqual(event["evidence"][0]["source"], "示例实验室")
        self.assertEqual(result["sources"][1]["status"], "partial")

    def test_quoted_and_hidden_links_cannot_supply_an_event_reference(self):
        original = "https://openai.com/index/introducing-example-kit/"
        official = EventSource("official-fixture", "official", lambda: [EvidenceCandidate(
            original, "Introducing Example Kit", "OpenAI News", self.published.isoformat(),
            "Example Kit is now available.", verified=True,
        )])
        hidden_target = f'<a href="https://example.invalid/landing">{original}</a>'
        quoted_target = f'<blockquote><a href="{original}">官方公告</a></blockquote>'
        self.documents["/xiaohongshu"] = f'''<rss version="2.0"><channel>
          <title>示例笔记作者 - 笔记 • 小红书 / RED</title>
          <link>https://www.xiaohongshu.com/user/profile/623456789abc0123456789ab</link>
          <item><title>论坛日常</title>
          <link>https://www.xiaohongshu.com/explore/6123456789abc0123456789a</link>
          <author>示例笔记作者</author><pubDate>{self.published.isoformat()}</pubDate>
          <description>{escape(hidden_target + quoted_target)}</description></item>
        </channel></rss>'''
        result = self.collect([official], xiaohongshu_url=self.base + "/xiaohongshu")
        event, = result["items"]
        self.assertEqual([evidence["url"] for evidence in event["evidence"]], [original])
        self.assertEqual(result["sources"][1]["discovered"], 1)
        self.assertEqual(result["sources"][1]["linked"], 0)

    def test_weibo_mobile_detail_preserves_native_id_and_requires_row_level_ownership(self):
        profile = "https://weibo.com/u/1234567890"
        verified = "https://m.weibo.cn/detail/5123456789012345"
        unattributed = "https://m.weibo.cn/detail/5123456789012346"
        bad_owner = "https://m.weibo.cn/detail/5123456789012347"

        def item(link, author_uri):
            return f'''<entry><title>Demo AI 模型</title><id>{link}</id>
              <link href="{link}"/><author><name>示例实验室</name>{author_uri}</author>
              <published>{self.published.isoformat()}</published>
              <summary>我们今天正式发布 Demo AI 模型。</summary></entry>'''

        self.documents["/weibo"] = f'''<feed xmlns="http://www.w3.org/2005/Atom">
          <title>示例实验室的微博</title><link href="{profile}"/>
          {item(verified, f'<uri>{profile}</uri>')}
          {item(unattributed, "")}
          {item(bad_owner, "<uri>https://weibo.com/u/2345678901</uri>")}
        </feed>'''
        result = self.collect(weibo_url=self.base + "/weibo", official_profiles={profile: "示例实验室"})
        event, = result["items"]
        self.assertEqual(event["original"], verified)
        self.assertEqual(result["sources"][1]["status"], "partial")

    def test_configured_xiaohongshu_profile_can_publish_its_own_announcement(self):
        profile = "https://www.xiaohongshu.com/user/profile/623456789abc0123456789ab"
        post = f"{profile}/6123456789abc0123456789a"
        self.documents["/xiaohongshu"] = f'''<rss version="2.0"><channel>
          <title>示例实验室</title><link>{profile}</link>
          <item><title>Demo AI 模型</title><link>{post}</link>
          <author>示例实验室</author><pubDate>{self.published.isoformat()}</pubDate>
          <description>我们今天正式发布 Demo AI 模型。</description></item>
        </channel></rss>'''
        command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve().parents[1] / "scripts" / "collect_events.py"),
                   "--output", str(self.output), "--source", "xiaohongshu", "--no-highlights"]
        for name in ("", "示例实验室官方"):
            result = subprocess.run(command, capture_output=True, encoding="utf-8", env={
                **os.environ, "XIAOHONGSHU_FEED_URL": self.base + "/xiaohongshu",
                "XIAOHONGSHU_OFFICIAL_PROFILE": profile, "XIAOHONGSHU_OFFICIAL_NAME": name,
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            events = json.loads((self.output / "events.json").read_text(encoding="utf-8"))["items"]
            self.assertEqual([event["source"] for event in events], [name] if name else [])

    def test_new_post_linked_only_to_an_expired_event_is_not_counted_as_public(self):
        original = "https://openai.com/index/introducing-example-kit/"
        native_post = "https://m.weibo.cn/detail/5123456789012346"
        old = self.published - timedelta(days=100)
        primary = EventSource("official-fixture", "official", lambda: [EvidenceCandidate(
            original, "Introducing Example Kit", "OpenAI News", old.isoformat(), "Original release.",
            verified=True, announced_at=old.date().isoformat(), release_key="example-kit",
        )])
        recent = EventSource("recent-reference", "official", lambda: [EvidenceCandidate(
            original, "Example Kit", "OpenAI News", self.published.isoformat(), "Recent reference.",
            verified=True, release_key="example-kit",
        )])
        self.documents["/weibo"] = f'''<rss version="2.0"><channel>
          <title>示例博主的微博</title><link>https://weibo.com/1234567890/</link>
          <item><title>Example Kit</title><link>{native_post}</link>
          <author>示例博主</author><pubDate>{self.published.isoformat()}</pubDate>
          <description>发布原文 {original}</description></item>
        </channel></rss>'''
        result = self.collect([recent, primary], weibo_url=self.base + "/weibo")

        self.assertEqual(result["items"], [])
        sources = {status["id"]: status for status in result["sources"]}
        self.assertEqual(sources["recent-reference"]["inserted"], 0)
        self.assertEqual((sources["weibo"]["linked"], sources["weibo"]["linkedUrls"]), (0, []))


if __name__ == "__main__":
    unittest.main()
