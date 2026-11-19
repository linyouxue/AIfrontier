from datetime import datetime, timedelta
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collect_events import EventSource, EventSourceBatch, collect_events
from build_preview import SHANGHAI
from event_sources import EvidenceCandidate
from video_event_sources import DEEPMIND_CHANNEL, video_sources


def atom_entry(identifier, title, description, published, *, updated="", link="", channel=DEEPMIND_CHANNEL):
    return f"""<entry>
      <id>yt:video:{identifier}</id>
      <yt:videoId>{identifier}</yt:videoId><yt:channelId>{channel}</yt:channelId>
      <title>{escape(title)}</title>
      <link rel="alternate" href="{escape(link or f'https://www.youtube.com/watch?v={identifier}', {'"': '&quot;'})}"/>
      <author><name>Google DeepMind</name><uri>https://www.youtube.com/channel/{channel}</uri></author>
      <published>{published}</published><updated>{updated or published}</updated>
      <media:group><media:description>{escape(description)}</media:description></media:group>
    </entry>"""


def atom(*entries):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
      <yt:channelId>{DEEPMIND_CHANNEL.removeprefix("UC")}</yt:channelId>
      <author><name>Google DeepMind</name><uri>https://www.youtube.com/channel/{DEEPMIND_CHANNEL}</uri></author>
      {"".join(entries)}
    </feed>""".encode()


class VideoSourceTest(unittest.TestCase):
    def setUp(self):
        output = tempfile.TemporaryDirectory()
        self.addCleanup(output.cleanup)
        self.output = Path(output.name)
        self.published = datetime.now(SHANGHAI) - timedelta(days=2)

    def collect(self, youtube, *official_sources, bilibili=None):
        urls = {"https://feeds.example/youtube": youtube, "https://feeds.example/bilibili": bilibili}
        with patch("video_event_sources.read", side_effect=urls.__getitem__):
            collect_events(
                self.output, official_sources,
                extra_sources=video_sources(
                    EventSource, EventSourceBatch, youtube_url="https://feeds.example/youtube",
                    bilibili_url="https://feeds.example/bilibili" if bilibili is not None else None,
                ),
            )
        return json.loads((self.output / "events.json").read_text(encoding="utf-8"))

    def test_publisher_video_discloses_a_release_and_metadata_update_keeps_one_event(self):
        published = self.published.isoformat()
        identifier = "AbCdEfGh123"
        title = "Genie 4: A new generation of world simulation"
        description = "Meet Genie 4, our new world model.\n\nWe have started rolling out Genie 4 as a research preview."
        original = f"https://www.youtube.com/watch?v={identifier}"
        first = self.collect(atom(atom_entry(identifier, title, description, published)))
        self.assertEqual(len(first["items"]), 1)
        event = first["items"][0]
        evidence = event["evidence"][0]
        self.assertEqual((evidence["url"], evidence["title"], evidence["source"], evidence["published"]),
                         (original, title, "Google DeepMind", self.published.date().isoformat()))
        # A calendar date for general availability is absent from the announcement.
        self.assertNotIn("availableAt", event)

        update = atom_entry(identifier, "Genie 4: An interactive world model", description, published,
                            updated=datetime.now(SHANGHAI).isoformat())
        second = self.collect(atom(update))
        self.assertEqual([item["id"] for item in second["items"]], [event["id"]])
        self.assertEqual(second["items"][0]["collected"], event["collected"])
        self.assertEqual(len(second["items"][0]["evidence"]), 1)

    def test_video_references_link_one_release_and_keep_only_known_offsets(self):
        published = self.published.date().isoformat()
        original = "https://openai.com/index/introducing-example-kit/"
        official = EventSource("official-fixture", "official", lambda: [EvidenceCandidate(
            original, "Introducing Example Kit", "OpenAI", published, "Example Kit is available today.",
            verified=True, announced_at=published,
        )])
        episode = atom_entry(
            "AbCdEfGh124", "Working with Example Kit | Full interview",
            f"A full conversation about the launch.\nOfficial announcement: {original}",
            self.published.isoformat(), link="https://www.youtube.com/watch?v=AbCdEfGh124&t=90s&si=tracking",
        )
        bilibili = f"""<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
          <title>发布团队 的 bilibili 空间</title><link>https://space.bilibili.com/508452265</link>
          <item><title>Example Kit 发布演示</title><dc:creator>发布团队</dc:creator>
            <link>https://www.bilibili.com/video/BV1HXTs6bEzH/?spm_id_from=tracking&amp;t=72</link>
            <pubDate>{self.published:%a, %d %b %Y %H:%M:%S %z}</pubDate>
            <description><![CDATA[<p>发布公告：<a href="{original}">原文</a></p>]]></description></item>
          <item><title>Example Kit 发布演示第二场</title><dc:creator>发布团队</dc:creator>
            <link>https://www.bilibili.com/video/BV1AXTs6bEzH/?t=-25</link>
            <pubDate>{self.published:%a, %d %b %Y %H:%M:%S %z}</pubDate>
            <description>发布公告：{original}</description></item>
          <item><title>传闻：Example Kit 将支持其他产品</title><dc:creator>发布团队</dc:creator>
            <link>https://www.bilibili.com/video/BV1BXTs6bEzH/</link>
            <pubDate>{self.published:%a, %d %b %Y %H:%M:%S %z}</pubDate>
            <description>发布公告：{original}</description></item>
          <item><title>另一项产品的演示</title><dc:creator>发布团队</dc:creator>
            <link>https://www.bilibili.com/video/BV1CXTs6bEzH/</link>
            <pubDate>{self.published:%a, %d %b %Y %H:%M:%S %z}</pubDate>
            <description>发布公告：https://openai.com/index/no-confirmed-release/</description></item>
        </channel></rss>""".encode()

        result = self.collect(atom(episode), official, bilibili=bilibili)
        self.assertEqual(len(result["items"]), 1)
        event = result["items"][0]
        self.assertEqual((event["original"], event["title"], event["announcedAt"]),
                         (original, "Introducing Example Kit", published))
        evidence = {entry["url"]: entry for entry in event["evidence"]}
        youtube = evidence["https://www.youtube.com/watch?v=AbCdEfGh124"]
        self.assertEqual((youtube["source"], youtube["title"], youtube["offsetSeconds"]),
                         ("Google DeepMind", "Working with Example Kit | Full interview", 90))
        bili = evidence["https://www.bilibili.com/video/BV1HXTs6bEzH"]
        self.assertEqual((bili["title"], bili["source"], bili["published"], bili["offsetSeconds"]),
                         ("Example Kit 发布演示", "发布团队", published, 72))
        self.assertNotIn("offsetSeconds", evidence["https://www.bilibili.com/video/BV1AXTs6bEzH"])
        self.assertEqual(len(evidence), 4)
        statuses = {source["id"]: source for source in result["sources"]}
        self.assertEqual((statuses["bilibili"]["status"], statuses["bilibili"]["checked"],
                          statuses["bilibili"]["matched"]), ("ok", 4, 2))

        again = self.collect(atom(episode), official, bilibili=bilibili)
        self.assertEqual(again["items"], [event])

    def test_source_states_preserve_checked_records_and_prior_evidence(self):
        published = self.published.isoformat()
        release = atom_entry("AbCdEfGh123", "Genie 4: World model",
                             "Meet Genie 4, our new model.\nToday, we are launching Genie 4.", published)
        short = atom_entry("AbCdEfGh124", "Genie 4: World model",
                           "Meet Genie 4, our new model.\nToday, we are launching Genie 4.", published,
                           link="https://www.youtube.com/shorts/AbCdEfGh124")
        bad_link = atom_entry("AbCdEfGh125", "Genie 4: World model", "", published,
                              link="https://www.youtube.com.example/watch?v=AbCdEfGh125")
        tutorial = atom_entry("AbCdEfGh126", "How to use Genie 4", "An existing product tutorial.", published)
        first = self.collect(atom(release, short, bad_link, tutorial))
        self.assertEqual(len(first["items"]), 1)
        states = {source["id"]: source for source in first["sources"]}
        self.assertEqual((states["youtube-deepmind"]["status"], states["youtube-deepmind"]["checked"]), ("partial", 4))
        self.assertEqual((states["bilibili"]["status"], states["bilibili"]["checked"]), ("unconfigured", 0))

        failure = self.collect(b"<html>Service unavailable</html>")
        states = {source["id"]: source for source in failure["sources"]}
        self.assertEqual(states["youtube-deepmind"]["status"], "failed")
        self.assertEqual(failure["items"], first["items"])

        recovery = self.collect(atom(short, tutorial))
        states = {source["id"]: source for source in recovery["sources"]}
        self.assertEqual((states["youtube-deepmind"]["status"], states["youtube-deepmind"]["checked"],
                          states["youtube-deepmind"]["discovered"]), ("ok", 2, 0))
        self.assertEqual(recovery["items"], first["items"])


if __name__ == "__main__":
    unittest.main()
