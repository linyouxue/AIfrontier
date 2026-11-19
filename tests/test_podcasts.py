from datetime import datetime, time, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))


def archive(items):
    return '<script id="archive-props" type="application/json">' + json.dumps({
        "initial": {"section": "podcasts", "page": 1, "pages": 1, "items": items},
    }) + "</script>"


def rss(items=""):
    return f"""<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
    <channel><title>Official podcast feed</title>{items}</channel></rss>"""


class PodcastTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / "AIfrontier"
        self.documents = {"/latent-feed": rss(), "/latent-archive": archive([]), "/dwarkesh": rss()}
        documents = self.documents

        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                document = documents.get(self.path)
                if document is None:
                    self.send_error(503)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(document.encode("utf-8"))

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Source)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.upstream = f"http://127.0.0.1:{server.server_port}"
        self.day = datetime.now(SHANGHAI).date() - timedelta(days=1)
        self.release = datetime.combine(self.day, time(16, 25), timezone.utc) - timedelta(days=1)

    def collect(self, *arguments):
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts" / "collect_podcasts.py"),
            "--output", str(self.output),
            "--latent-space-feed-url", f"{self.upstream}/latent-feed",
            "--latent-space-archive-url", f"{self.upstream}/latent-archive",
            "--dwarkesh-feed-url", f"{self.upstream}/dwarkesh",
            *arguments,
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads((self.output / "podcasts.json").read_text(encoding="utf-8"))

    def test_merges_official_episode_page_feed_and_video(self):
        title = "Building Visual Intelligence — Andreas Blattmann & Dustin Podell, Black Forest Labs"
        audio = "https://publisher.example/episode-audio.mp3"
        self.documents["/latent-archive"] = archive([{
            "type": "podcast", "section": "podcasts", "audience": "everyone", "slug": "bfl",
            "title": title, "description": "Learning from images, motion and audio.",
            "date": self.release.isoformat(), "video": "https://youtu.be/8cGfJOU3AlQ?si=share",
            "episode": {"id": "pod_ep_123", "audio": audio},
        }])
        self.documents["/latent-feed"] = rss(f"""<item>
          <title><![CDATA[{title}]]></title><link>https://publisher.example/episode</link>
          <guid>audio-copy</guid><pubDate>{format_datetime(self.release)}</pubDate>
          <enclosure url="{audio}" type="audio/mpeg"/><itunes:duration>5903</itunes:duration>
          <itunes:episodeType>full</itunes:episodeType>
        </item><item>
          <title>Building visual intelligence</title><link>https://www.youtube.com/watch?v=8cGfJOU3AlQ</link>
          <guid>youtube:8cGfJOU3AlQ</guid><pubDate>{format_datetime(self.release)}</pubDate>
          <description>Watch the full show on YouTube: https://www.youtube.com/watch?v=8cGfJOU3AlQ</description>
          <itunes:episodeType>full</itunes:episodeType>
        </item>""")
        first = self.collect()
        self.assertEqual(len(first["items"]), 1)
        episode = first["items"][0]
        self.assertEqual(episode["title"], title)
        self.assertEqual(episode["summary"], "Learning from images, motion and audio.")
        self.assertEqual(episode["show"], "Latent Space")
        self.assertEqual(episode["guests"], ["Andreas Blattmann", "Dustin Podell"])
        self.assertEqual(episode["durationSeconds"], 5903)
        self.assertEqual(episode["topics"], ["Building Visual Intelligence"])
        self.assertEqual(episode["published"], self.day.isoformat())
        self.assertEqual(episode["original"], "https://www.youtube.com/watch?v=8cGfJOU3AlQ")
        self.assertEqual(episode["evidence"][0]["url"], "https://latent.space/p/bfl/")
        self.assertEqual(datetime.fromisoformat(episode["collected"]).utcoffset(), timedelta(0))
        source = next(source for source in first["sources"] if source["id"] == "latent-space")
        self.assertEqual((source["status"], source["discovered"], source["inserted"]), ("ok", 1, 1))
        replay = self.collect()
        self.assertEqual(replay["items"], first["items"])
        self.assertEqual(next(source for source in replay["sources"] if source["id"] == "latent-space")["inserted"], 0)


    def test_collects_ai_interviews_and_skips_clips_audio_and_non_ai(self):
        def dwarkesh_item(title, slug, intro, video="", marked=""):
            return f"""<item>
              <title><![CDATA[{title}]]></title><link>https://www.dwarkesh.com/p/{slug}</link>
              <guid>substack:post:{slug}</guid><pubDate>{format_datetime(self.release)}</pubDate>
              <enclosure type="audio/mpeg" url="https://cdn.example/{slug}.mp3"/>
              <description><![CDATA[{intro}{f'<p>Watch on <a href="https://youtu.be/{video}">YouTube</a>; read the transcript.</p>' if video else ''}
                <p>Timestamps</p><p>(00:00:00) – How AI models learn</p>]]></description>
              <itunes:duration>1:25:01</itunes:duration>{marked}
            </item>"""

        self.documents["/dwarkesh"] = rss("".join([
            dwarkesh_item("AI researchers debate how models learn", "roundtable",
                          '<p>New episode with <a href="https://example.com/john">John Schulman</a>'
                          ' and <a href="https://example.com/beren">Beren Millidge</a>. '
                          'They compare the effect of data and compute on AI systems.</p>', "PrSf7IOYu-I"),
            dwarkesh_item("AI models learn (clip)", "clip", "<p>New episode with John Schulman.</p>",
                          "8cGfJOU3AlQ", "<itunes:episodeType>trailer</itunes:episodeType>"),
            dwarkesh_item("Jane Doe — Audio on AI models", "audio",
                          '<p>New episode with Jane Doe. Related <a href="https://youtu.be/QmDLDy3ZSNE">YouTube video</a>.</p>'),
            dwarkesh_item("Jane Doe — History of the city", "history",
                          "<p>New episode with Jane Doe.</p><p>We talk about local history.</p>"
                          "<p>The story can also be an analogy for AI.</p>", "frYSOKvNiiE"),
            dwarkesh_item("The rise and fall of AI agents", "video-essay",
                          "<p>This is a video recording of a post I wrote last week.</p>", "u15N3l4RT80"),
        ]))
        data = self.collect()
        self.assertEqual([item["original"] for item in data["items"]], ["https://www.youtube.com/watch?v=PrSf7IOYu-I"])
        episode = data["items"][0]
        self.assertEqual(episode["show"], "Dwarkesh Podcast")
        self.assertEqual(episode["guests"], ["John Schulman", "Beren Millidge"])
        self.assertEqual(episode["durationSeconds"], 5101)
        self.assertEqual(episode["topics"], ["How AI models learn"])
        self.assertEqual(episode["evidence"][0]["url"], "https://www.dwarkesh.com/p/roundtable")
        self.assertIn("effect of data and compute", episode["summary"])
        self.assertEqual([source["status"] for source in data["sources"]], ["ok", "ok"])

    def test_keeps_first_collection_when_original_video_changes_and_sources_fail(self):
        entry = {
            "type": "podcast", "section": "podcasts", "audience": "everyone", "slug": "models",
            "title": "Learning With Models — Jane Doe", "description": "Training smaller models.",
            "date": self.release.isoformat(), "video": "https://youtu.be/8cGfJOU3AlQ",
            "episode": {"duration": 1000},
        }
        self.documents["/latent-archive"] = archive([entry])
        baseline = self.collect()
        original = baseline["items"][0]

        entry["video"] = "https://youtu.be/QmDLDy3ZSNE"
        self.documents["/latent-archive"] = archive([entry])
        replay = self.collect()
        self.assertEqual(replay["items"], [{**original, "original": "https://www.youtube.com/watch?v=QmDLDy3ZSNE"}])
        last_check = replay["sources"][0]
        self.assertEqual(last_check["inserted"], 0)

        expired = {**original, "id": "expired", "published": (self.day - timedelta(days=120)).isoformat()}
        (self.output / "podcasts.json").write_text(
            json.dumps({**replay, "items": [*replay["items"], expired]}), encoding="utf-8",
        )
        self.documents.clear()
        outage = self.collect()
        self.assertEqual(outage["items"], replay["items"])
        self.assertEqual([source["status"] for source in outage["sources"]], ["failed", "failed"])
        self.assertEqual(outage["sources"][0]["lastSuccessAt"], last_check["lastSuccessAt"])


if __name__ == "__main__":
    unittest.main()
