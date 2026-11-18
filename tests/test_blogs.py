from datetime import date
import gzip
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_blogs
from render_blogs import publish


class BlogCollectionTest(unittest.TestCase):
    def test_compressed_atom_keeps_the_dated_original_through_a_source_failure(self):
        source = {"name": "作者", "feed": "https://example.org/feed", "kind": "author", "filterAI": True}
        day = date.today().isoformat()
        feed = f"""<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>A new design</title><published>{day}</published><link href="/posts/design"/>
          <summary>Implementation notes.</summary><content>A new inference algorithm for a transformer.</content></entry>
          <entry><title>Edited old LLM article</title><updated>{day}</updated><link href="/posts/old"/>
          <summary>Recently edited.</summary></entry>
          <entry><title>Podcast: LLMs</title><published>{day}</published><link href="/audio"/>
          <summary>An audio episode.</summary></entry></feed>"""
        with TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(collect_blogs, "read", return_value=gzip.compress(feed.encode())):
                collect_blogs.collect(output, [source])
            snapshot = json.loads((output / "blogs.json").read_text("utf-8"))
            self.assertEqual([i["original"] for i in snapshot["items"]], ["https://example.org/posts/design"])
            self.assertEqual(snapshot["items"][0]["kind"], "author")
            with patch.object(collect_blogs, "read", side_effect=TimeoutError):
                collect_blogs.collect(output, [source])
            later = json.loads((output / "blogs.json").read_text("utf-8"))
            self.assertEqual(later["items"], snapshot["items"])
            self.assertEqual(later["sources"][0]["status"], "failed")
            publish(output)
            site = output / "site"
            self.assertEqual(json.loads((site / "blogs.json").read_text("utf-8")), later)
            for page in ("blogs/index.html", "blogs/author/index.html"):
                html = (site / page).read_text("utf-8")
                self.assertIn('href="https://example.org/posts/design"', html)
                self.assertIn("读取失败", html)
            self.assertNotIn("example.org/posts/design", (site / "blogs/team/index.html").read_text("utf-8"))
