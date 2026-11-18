from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FEEDS = {
    "机器之心": "https://wechat2rss.xlab.app/feed/51e92aad2728acdd1fda7314be32b16639353001.xml",
    "量子位": "https://wechat2rss.xlab.app/feed/7131b577c61365cb47e81000738c10d872685908.xml",
    "新智元": "https://wechat2rss.xlab.app/feed/ede30346413ea70dbef5d485ea5cbb95cca446e7.xml",
}
ORIGINAL = "https://mp.weixin.qq.com/s?__biz=MzA3MzI4MjgzMw%3D%3D&mid=123&idx=1&sn=123abc"
TECH_TITLE = "稀疏注意力的实现与测试"
TECH_BODY = "作者提出分块稀疏注意力算法，在长序列推理的基准测试中吞吐提高了22倍。"


def rss(account, *articles):
    items = "".join(f"""<item><title>{escape(title)}</title><link>{escape(link)}</link>
      <pubDate>{format_datetime(published)}</pubDate><description>只有索引的简介</description></item>"""
                    for title, link, published in articles)
    return f'<rss version="2.0"><channel><title>{account}</title>{items}</channel></rss>'


def original(account, title, published, body=TECH_BODY, *, canonical=ORIGINAL):
    return f"""<html><head><meta property="og:url" content="{escape(canonical)}">
      <meta property="og:title" content="{escape(title)}">
      <script nonce="sample">var ct = "{int(published.timestamp())}";</script></head>
      <h1 id="activity-name">{escape(title)}</h1><a id="js_name"><span>{account}</span></a>
      <div id="js_content"><p><span>{escape(body)}</span></p></div></html>"""


class WeChatTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.documents = {url: rss(account) for account, url in FEEDS.items()}
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        self.published = datetime.combine(yesterday, datetime.min.time(), timezone.utc).replace(hour=17, minute=28)

    def collect(self):
        def open_public_page(request, **_):
            url = request.full_url
            document = self.documents.get(url)
            if document is None or isinstance(document, int):
                raise HTTPError(url, document or 404, "fixture error", None, None)
            response = io.BytesIO(document.encode("utf-8"))
            response.geturl = lambda: url
            return response

        command = [str(SCRIPTS / "collect_public_wechat.py"), "--output", str(self.output)]
        with (patch("urllib.request.urlopen", side_effect=open_public_page),
              patch.object(sys, "argv", command), patch.object(sys, "path", [str(SCRIPTS), *sys.path]),
              redirect_stdout(io.StringIO())):
            runpy.run_path(command[0], run_name="__main__")
        return json.loads((self.output / "wechat.json").read_text(encoding="utf-8"))

    def test_publishes_article_verified_on_wechat_and_uses_shanghai_day(self):
        self.documents[FEEDS["机器之心"]] = rss("机器之心", (TECH_TITLE, ORIGINAL + "&scene=27", self.published))
        self.documents[ORIGINAL] = original("机器之心", TECH_TITLE, self.published + timedelta(seconds=37))

        snapshot = self.collect()

        self.assertEqual(len(snapshot["items"]), 1)
        article = snapshot["items"][0]
        self.assertEqual(article["original"], ORIGINAL)
        self.assertEqual(article["title"], TECH_TITLE)
        self.assertEqual(article["summary"], TECH_BODY)
        self.assertEqual(article["source"], "机器之心")
        self.assertEqual(article["published"], (self.published.date() + timedelta(days=1)).isoformat())
        self.assertEqual(article["publishedAt"], (self.published + timedelta(seconds=37)).isoformat())
        self.assertEqual(datetime.fromisoformat(article["collected"]).utcoffset(), timedelta(0))
        status = snapshot["sources"][0]
        self.assertEqual((status["status"], status["discovered"], status["inserted"]), ("ok", 1, 1))

    def test_public_full_text_checks_publisher_and_embedded_local_time(self):
        account = "机器之心"
        stamp = (self.published + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        other_account = ORIGINAL.replace("MzA3MzI4MjgzMw", "MzIzNjc1NzUzMw")
        wrong_time = ORIGINAL.replace("mid=123", "mid=456")
        other = ORIGINAL.replace("mid=123", "mid=789")
        page = rss(account, (TECH_TITLE, ORIGINAL, self.published), (TECH_TITLE, other_account, self.published),
                   (TECH_TITLE, wrong_time, self.published), (TECH_TITLE, other, self.published))
        page = page.replace("<rss ", '<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" ')

        def with_body(stamp):
            markup = f"<p><span>{stamp}</span></p><p>{TECH_BODY}</p>"
            return f"<content:encoded>{escape(markup)}</content:encoded></item>"

        parts = page.split("</item>")
        stamps = [stamp, stamp, "2000-01-01 00:00", stamp]
        self.documents[FEEDS[account]] = "".join(p + with_body(s) for p, s in zip(parts, stamps)) + parts[-1]
        self.documents[other_account] = 429
        data = self.collect()
        self.assertEqual({a["original"] for a in data["items"]}, {ORIGINAL, other})
        self.assertTrue(all(a["summary"] == TECH_BODY for a in data["items"]))
        self.assertEqual(data["sources"][0]["verificationFailures"],
                         {"HTTP 429": 1, "verification deferred after HTTP 429": 1})

    def test_conference_announcement_is_filtered_even_when_it_mentions_a_method(self):
        account = "机器之心"
        title = "AI Infra 大会第二批嘉宾强势来袭"
        self.documents[FEEDS[account]] = rss(account, (title, ORIGINAL, self.published))
        self.documents[ORIGINAL] = original(account, title, self.published)
        snapshot = self.collect()
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(snapshot["sources"][0]["filtered"], 1)

    def test_matches_short_alias_and_retains_initial_collection_during_source_failure(self):
        short = "https://mp.weixin.qq.com/s/official-short-id"
        self.documents[FEEDS["机器之心"]] = rss("机器之心", (TECH_TITLE, short, self.published))
        self.documents[short] = original("机器之心", TECH_TITLE, self.published)
        first = self.collect()

        self.documents[FEEDS["机器之心"]] = rss("机器之心", (TECH_TITLE, ORIGINAL + "#rd", self.published))
        self.documents[ORIGINAL] = original("机器之心", TECH_TITLE, self.published)
        repeated = self.collect()

        self.assertEqual(repeated["items"], first["items"])
        self.assertEqual(repeated["sources"][0]["matched"], 1)
        self.assertEqual(repeated["sources"][0]["inserted"], 0)

        self.documents[FEEDS["机器之心"]] = 503
        failed = self.collect()

        self.assertEqual(failed["items"], first["items"])
        self.assertEqual(failed["sources"][0]["status"], "failed")
        self.assertEqual(failed["sources"][0]["error"], "HTTP 503")
        self.assertEqual(failed["sources"][0]["lastSuccessAt"], repeated["sources"][0]["lastSuccessAt"])

    def test_reports_unverified_candidates_separately_from_articles_filtered_by_scope(self):
        temporary = ORIGINAL + "&signature=temporary&timestamp=123"
        blocked, mismatch, news, old, wrong_time, wrong_title = (ORIGINAL.replace("idx=1", f"idx={index}")
                                                                 for index in range(2, 8))
        financing = "AI公司完成新一轮融资"
        self.documents[FEEDS["机器之心"]] = rss(
            "机器之心",
            (TECH_TITLE, temporary, self.published),
            (TECH_TITLE, blocked, self.published),
            (TECH_TITLE, mismatch, self.published),
            (financing, news, self.published),
            (TECH_TITLE, old, self.published - timedelta(days=110)),
            (TECH_TITLE, wrong_time, self.published),
            (TECH_TITLE, wrong_title, self.published),
        )
        self.documents[blocked] = 403
        self.documents[mismatch] = original("另一个账号", TECH_TITLE, self.published, canonical=mismatch)
        self.documents[news] = original("机器之心", financing, self.published,
                                        "这家人工智能公司获得投资，介绍了其模型和研发实力。", canonical=news)
        self.documents[wrong_time] = original("机器之心", TECH_TITLE, self.published - timedelta(minutes=1),
                                              canonical=wrong_time)
        self.documents[wrong_title] = original("机器之心", "不同的标题", self.published, canonical=wrong_title)

        snapshot = self.collect()

        self.assertEqual(snapshot["items"], [])
        source, empty_source = snapshot["sources"][:2]
        self.assertEqual((source["status"], source["discovered"], source["filtered"], source["unverified"]),
                         ("partial", 6, 1, 5))
        self.assertEqual(source["verificationFailures"], {
            "unstable original link": 1, "HTTP 403": 1, "original account mismatch": 1,
            "original publication time mismatch": 1, "original title mismatch": 1,
        })
        self.assertEqual((empty_source["status"], empty_source["discovered"], empty_source["inserted"]), ("ok", 0, 0))

    def test_reads_current_original_template_and_expires_previous_article(self):
        expired = {
            "id": "wechat:expired-article", "published": (self.published - timedelta(days=110)).date().isoformat(),
            "title": "旧文章", "summary": "已过期", "source": "机器之心", "collected": self.published.isoformat(),
            "original": "https://mp.weixin.qq.com/s/expired-article",
        }
        (self.output / "wechat.json").write_text(json.dumps({"items": [expired]}), encoding="utf-8")
        self.documents[FEEDS["机器之心"]] = rss("机器之心", (TECH_TITLE, ORIGINAL, self.published))
        self.documents[ORIGINAL] = f"""<html><head>
          <script nonce="sample">window.cgiData = {{create_time: JsDecode('{int(self.published.timestamp())}')}};</script>
          </head><h1 id="js_text_title">{TECH_TITLE}</h1><span class="wx_follow_nickname">机器之心</span>
          <div id="js_content"><p>{TECH_BODY}</p></div></html>"""

        snapshot = self.collect()

        self.assertEqual([article["title"] for article in snapshot["items"]], [TECH_TITLE])
        self.assertEqual(snapshot["sources"][0]["status"], "ok")
