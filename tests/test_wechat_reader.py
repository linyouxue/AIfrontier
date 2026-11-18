import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_public_wechat as collector


class ReaderCollectionTest(unittest.TestCase):
    def test_verified_original_across_checks(self):
        accounts = {"测试公众号": "MP_WXS_123"}
        cover = json.dumps({"title": "推理实测", "reviewId": "MP_WXS_123_a~b"}, ensure_ascii=False).encode()
        document = """<meta property="og:url" content="https://mp.weixin.qq.com/s?__biz=MTIz&amp;mid=12&amp;idx=1">
        <h1 id="activity-name">推理实测</h1><a id="js_name">测试公众号</a>
        <script>var ct = "1767225600";</script><div id="js_content">研究提出注意力算法，推理吞吐提高了两倍。</div>""".encode()
        items = {}
        def run(previous):
            return list(collector.collect_reader(accounts, "test-session", items, previous, "2026-01-10T00:00:00+00:00",
                                                 "2025-10-10", "2026-01-10"))[0]
        with patch.object(collector, "reader_request", side_effect=[cover, document]) as fetch:
            first = run({})
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(fetch.call_args_list[-1].args[-1], {"reviewId": "MP_WXS_123_a~b"})
        article = next(iter(items.values()))
        self.assertEqual(article["published"], "2026-01-01")
        self.assertTrue(article["original"].endswith("__biz=MTIz&mid=12&idx=1"))
        with patch.object(collector, "reader_request", return_value=cover) as fetch:
            second = run({first["id"]: first})
        self.assertEqual(second["matched"], 1)
        self.assertEqual(fetch.call_count, 1)
        with patch.object(collector, "reader_request", side_effect=[cover, document.replace(b"MTIz", b"MTI0")]):
            mismatch = run({})
        self.assertEqual(mismatch["status"], "partial")
        self.assertEqual(len(items), 1)

    def test_one_rate_limit_stops_the_remaining_session_requests(self):
        accounts = {"甲": "MP_WXS_123", "乙": "MP_WXS_124"}
        with patch.object(collector, "reader_request", side_effect=HTTPError("", 429, "", None, None)) as fetch:
            sources = list(collector.collect_reader(accounts, "test-session", {}, {}, "2026-01-01", "2025-10-01", "2026-01-01"))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([row["error"] for row in sources], ["HTTP 429", "HTTP 429"])
