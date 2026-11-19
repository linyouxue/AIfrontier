from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))


def rss(title, rows):
    items = []
    for headline, url, published, category, summary in rows:
        items.append(
            f'<item><title>{escape(headline)}</title><link>{escape(url)}</link>'
            f'<pubDate>{published}</pubDate><category>{category}</category>'
            f'<description>{escape(summary)}</description></item>'
        )
    return f'<rss version="2.0"><channel><title>{title}</title>{"".join(items)}</channel></rss>'


class EventTest(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        documents = self.documents

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in documents:
                    self.send_error(503)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(documents[self.path].encode("utf-8"))

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if payload.get("model") != "baidu/ernie-5.5-flash-preview:free" or payload.get("max_tokens", 0) > 2300:
                    self.send_error(400)
                    return
                self.do_GET()

            def log_message(self, *_):
                pass

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name) / "AIfrontier"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def collect(self, *arguments, allow_highlights=False):
        model_args = ["--highlights-url", self.base + "/summary"] if allow_highlights else ["--no-highlights"]
        result = subprocess.run([
            sys.executable, "-X", "utf8", str(ROOT / "scripts" / "collect_events.py"),
            "--output", str(self.output), *model_args, *arguments,
        ], capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "OPENROUTER_API_KEY": "test-fixture" if allow_highlights else ""})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads((self.output / "events.json").read_text(encoding="utf-8"))

    def test_official_release_and_digest_share_one_event_with_first_collection(self):
        today = datetime.now(SHANGHAI).date()
        published = today - timedelta(days=2)
        timestamp = f"{(published - timedelta(days=1)):%a, %d %b %Y} 18:47:00 +0000"
        original = "https://openai.com/index/introducing-example-kit/"
        older = "https://openai.com/index/introducing-old-kit/"
        unverified = "https://example.com/rumor"
        self.documents["/official"] = rss("OpenAI News", [
            ("Introducing Example Kit", original, timestamp, "Product", "Example Kit is now available for API users."),
            ("Introducing Old Kit", older, (today - timedelta(days=125)).isoformat(), "Product", "A published release."),
        ])
        self.documents["/digest"] = rss("AI News Digest", [
            ("Different RSS headline", original.rstrip("/") + "?utm_source=digest", published.isoformat(),
             "openai", "A digest pointer to the same original."),
            ("Unverified rumor", unverified, published.isoformat(), "openai", "Anonymous accounts report a release."),
        ])
        args = ("--source", "openai-news", "--source", "ai-news-digest",
                "--openai-rss-url", self.base + "/official", "--digest-rss-url", self.base + "/digest")
        first = self.collect(*args)
        self.assertEqual(len(first["items"]), 1)
        item = first["items"][0]
        self.assertEqual((item["title"], item["original"], item["published"]),
                         ("Introducing Example Kit", original, published.isoformat()))
        self.assertEqual((item["announcedAt"], item["availableAt"]), (published.isoformat(), published.isoformat()))
        self.assertEqual(item["evidence"][0]["sources"], ["OpenAI News", "AI News Digest"])
        self.assertEqual(self.collect(*args)["items"], [item])

        del self.documents["/official"]
        self.documents["/digest"] = '<html><title>Upstream error</title></html>'
        third = self.collect(*args)
        self.assertEqual(third["items"], [item])
        self.assertEqual({source["id"]: source["status"] for source in third["sources"]}, {
            "openai-news": "failed", "ai-news-digest": "failed",
        })

    def test_anthropic_release_notes_add_opening_date_and_keep_different_releases_separate(self):
        today = datetime.now(SHANGHAI).date()
        announced = (today - timedelta(days=6)).strftime("%b %d, %Y")
        available = today - timedelta(days=2)
        self.documents["/news"] = f'''<li data-listing-row="claude-1">
          <a class="row_title-link" href="/claude-fable-and-mythos-5-2"><h3>Introducing Claude Fable 5.2 and Claude Mythos 5.2</h3></a>
          <a class="row_details-description"><p>A new pair of language models.</p></a>
          <time>{announced}</time></li>
          <li data-listing-row="claude-2">
          <a class="row_title-link" href="/claude-fable-5-1"><h3>Introducing Claude Fable 5.1</h3></a>
          <a class="row_details-description"><p>An earlier release, covered in its own article.</p></a>
          <time>{announced}</time></li>'''
        self.documents["/notes.md"] = f'''---
title: Claude Platform release notes
url: https://platform.claude.com/docs/en/release-notes/overview
---
### {available:%B %d, %Y}

* We’ve launched **Claude Fable 5.2** (`claude-fable-5-2`). **Claude Mythos 5.2** offers the same capabilities only to verified organizations. Claude Fable 5.2 is available on the Claude API.
* Claude Fable 5.1 supports an additional watermark option.
'''
        output = self.collect("--source", "anthropic-news", "--source", "anthropic-release-notes",
                              "--anthropic-news-url", self.base + "/news", "--anthropic-notes-url", self.base + "/notes.md")
        self.assertEqual(len(output["items"]), 2)
        releases = {item["title"]: item for item in output["items"]}
        fable = releases["Introducing Claude Fable 5.2 and Claude Mythos 5.2"]
        self.assertEqual(fable["announcedAt"], (today - timedelta(days=6)).isoformat())
        self.assertEqual(fable["availableAt"], available.isoformat())
        self.assertEqual({entry["source"] for entry in fable["evidence"]}, {"Anthropic Newsroom", "Claude Platform Release Notes"})
        self.assertIsNone(releases["Introducing Claude Fable 5.1"].get("availableAt"))

    def test_deepseek_updates_keep_distinct_releases_and_link_the_official_announcement(self):
        today = datetime.now(SHANGHAI).date()
        release_date = today - timedelta(days=3)
        canonical = f"/zh-cn/news/news{release_date:%y%m%d}"
        self.documents["/updates"] = f'''<article>
          <h1>更新日志</h1><h2 id="时间-{release_date}">时间: {release_date}</h2>
          <h3 id="model-release">DeepSeek Example 模型发布</h3>
          <p>DeepSeek Example 已上线 API，支持图像输入。</p>
          <p>详情请参考<a href="{canonical}">这篇公告</a>。</p>
          <h3 id="search-release">开放搜索 API</h3>
          <p>这是一个独立发布，开放时间将在后续公布。</p></article>'''
        self.documents[canonical] = '''<article><h1>DeepSeek Example 正式发布</h1>
          <p>DeepSeek Example 已上线 API，支持图像输入。</p></article>'''
        output = self.collect("--source", "deepseek-updates", "--deepseek-updates-url", self.base + "/updates")
        self.assertEqual(len(output["items"]), 2)
        releases = {item["title"]: item for item in output["items"]}
        model = releases["DeepSeek Example 正式发布"]
        self.assertEqual(model["original"], f"https://api-docs.deepseek.com{canonical}")
        self.assertEqual(model["published"], release_date.isoformat())
        self.assertEqual(model["availableAt"], release_date.isoformat())
        self.assertEqual(len(model["evidence"]), 2)
        self.assertIsNone(releases["开放搜索 API"].get("availableAt"))

    def test_verified_release_publishes_chinese_points_and_preserves_them_across_retries(self):
        today = datetime.now(SHANGHAI).date().isoformat()
        self.documents["/official"] = rss("OpenAI News", [
            ("Introducing Example Kit", "https://openai.com/index/example-kit", today, "Product",
             "Example Kit offers image editing for developers and works with the existing API."),
        ])
        points = ["新的开发工具向现有接口增加了图片编辑能力。", "开发者可以继续调用现有接口来使用新的工具。"]
        self.documents["/summary"] = json.dumps({"choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps({"points": points})}},
        ]})
        args = ("--source", "openai-news", "--openai-rss-url", self.base + "/official")
        first = self.collect(*args, allow_highlights=True)
        self.assertEqual(first["items"][0]["points"], points)
        del self.documents["/summary"]
        self.assertEqual(self.collect(*args, allow_highlights=True)["items"], first["items"])


if __name__ == "__main__":
    unittest.main()
