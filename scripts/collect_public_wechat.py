"""Collect and verify AIfrontier WeChat articles from RSS and WeRead."""

import argparse
from base64 import b64encode
from calendar import monthrange
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from unicodedata import normalize
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request
import urllib.request
import xml.etree.ElementTree as ET

from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
WECHAT = "https://mp.weixin.qq.com"
SOURCES = (
    # Public feed ID and the publisher identifier from its WeChat original links.
    ("jiqizhixin", "机器之心", "51e92aad2728acdd1fda7314be32b16639353001", "MzA3MzI4MjgzMw=="),
    ("qbitai", "量子位", "7131b577c61365cb47e81000738c10d872685908", "MzIzNjc1NzUzMw=="),
    ("aiera", "新智元", "ede30346413ea70dbef5d485ea5cbb95cca446e7", "MzI3MTA0MTk1MA=="),
    ("paperweekly", "PaperWeekly", "3be891c2f4e526629ab055a297cc2cd6c1f0a563", "MzIwMTc4ODE0Mw=="),
    ("datawhale", "Datawhale", "4d620d988cb21cfeefd2263207221f0dc70df9ff", "MzIyNjM2MzQyNg=="),
    ("xiaoya", "夕小瑶科技说", "a1cd365aa14ed7d64cabfc8aa086da40ecaba34d", "MzIwNzc2NTk0NQ=="),
    ("meituantech", "美团技术团队", "eb4d04149424a874693a51c6fdda0dba8673f5e4", "MjM5NjQ5MTI5OA=="),
    ("tencenttech", "腾讯技术工程", "9685937b45fe9c7a526dbc32e4f24ba879a65b9a", "MjM5ODYwMjI2MA=="),
)
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
PUBLICATION_EPOCH = re.compile(r"\bvar\s+ct\s*=\s*['\"]?(\d{10})(?!\d)")
CREATE_TIME = re.compile(r"\bcreate_time\s*[:=]\s*(?:JsDecode\(\s*)?['\"]?(\d{13}|\d{10})(?!\d)")
ACCOUNT_CLASSES = {"wx_follow_nickname", "profile_nickname", "rich_media_meta_nickname"}
TECHNICAL_TOPIC = re.compile(
    r"算法|注意力|数据集|基准测试|消融|架构|预训练|微调|推理|量化|蒸馏|并行|部署|"
    r"复现|工程实践|上下文|多模态|强化学习|模型权重|神经网络|\bbenchmark\b", re.I,
)
TECHNICAL_DETAIL = re.compile(
    r"提出|实现|设计|比较|对比|实验|吞吐|延迟|准确率|显存|复杂度|评测|推导|源代码|训练数据|"
    r"配置|复现|教程|技术报告|论文解读|\barxiv\b|\d+(?:\.\d+)?\s*(?:tokens?|tok)/s\b", re.I,
)
NEWS_OR_PROMOTION = re.compile(r"融资|估值|招聘|会议信息|报名|直播预告|限时")
EXPLICIT_REPOST = re.compile(r"^[【\[（(]?\s*(?:转载|转发)\s*[】\]）)｜|:：]")
EVENT_PROMO = re.compile(r"嘉宾(?:阵容|官宣|揭晓|强势来袭)|(?:学术|技术|产业)论坛(?:举办|邀约)|校招沙龙|大会门票|早鸟票")
RESULT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|倍|ms\b|GB\b|tok/s\b)", re.I)


def clean(value):
    return " ".join(unescape(value).split())


def comparable(value):
    return normalize("NFKC", clean(value))


def reader_publisher_id(book):
    return b64encode(book.removeprefix("MP_WXS_").encode("ascii")).decode("ascii")


def read(url):
    request = Request(url, headers={"User-Agent": "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.geturl(), response.read()


def article_link(url):
    try:
        link = urlsplit(url)
        if (link.scheme, link.hostname) != ("https", "mp.weixin.qq.com"):
            return None
        if link.username is not None or link.password is not None or link.port not in (None, 443):
            return None
    except ValueError:
        return None
    query = parse_qs(link.query, keep_blank_values=True)
    names = {key.casefold() for key in query}
    if names & {"signature", "timestamp"} or {"src", "ver", "new"} <= names:
        return None
    if match := re.fullmatch(r"/s/([A-Za-z0-9_~-]+)", link.path):
        return f"wechat:{match[1]}", WECHAT + link.path
    if link.path != "/s":
        return None
    fields = ["__biz", "mid", "idx"]
    if "sn" in query:
        fields.append("sn")
    if any(len(query.get(field, [])) != 1 or not query[field][0] for field in fields):
        return None
    biz, mid, index = (query[field][0] for field in fields[:3])
    if not (re.fullmatch(r"[A-Za-z0-9+/=_-]+", biz) and re.fullmatch(r"[0-9]+", mid)
            and re.fullmatch(r"[0-9]+", index)):
        return None
    return f"wechat:{biz}:{mid}:{index}", f"{WECHAT}/s?" + urlencode([(key, query[key][0]) for key in fields])


class OriginalPage(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.meta = {}
        self.fields = {name: [] for name in ("activity-name", "js_text_title", "js_name", "account", "js_content",
                                            "publish_time")}
        self.active = None
        self.hidden = 0
        self.depth = 0
        self.feed(document)

    def handle_starttag(self, tag, attributes):
        if tag in {"script", "style"}:
            self.hidden += 1
            return
        if self.hidden:
            return
        attrs = dict(attributes)
        if tag == "meta" and attrs.get("content"):
            self.meta[attrs.get("property") or attrs.get("name")] = attrs["content"]
        if tag == "link" and attrs.get("rel") == "canonical" and attrs.get("href"):
            self.meta["canonical"] = attrs["href"]
        if tag not in VOID_TAGS:
            if self.active:
                self.depth += 1
            else:
                field = attrs.get("id")
                if field not in self.fields and not self.fields["account"]:
                    if ACCOUNT_CLASSES.intersection((attrs.get("class") or "").split()):
                        field = "account"
                if field in self.fields:
                    self.active = field
                    self.depth = 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if self.active and tag not in VOID_TAGS:
            if tag in {"p", "div", "section", "li"}:
                self.fields[self.active].append(" ")
            self.depth -= 1
            if not self.depth:
                self.active = None

    def handle_data(self, data):
        if self.active and not self.hidden:
            self.fields[self.active].append(data)

    def text(self, field):
        return clean("".join(self.fields[field]))


def original_time(page, document):
    if match := PUBLICATION_EPOCH.search(document) or CREATE_TIME.search(document):
        seconds = int(match[1][:10])
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    for value in (page.meta.get("og:article:published_time"), page.meta.get("article:published_time"),
                  page.text("publish_time")):
        if value and ("T" in value or ":" in value):
            try:
                instant = datetime.fromisoformat(value)
            except ValueError:
                continue
            return instant if instant.tzinfo else instant.replace(tzinfo=SHANGHAI)
    raise ValueError("missing original time")


def technical_article(title, body):
    if EXPLICIT_REPOST.search(title) or EVENT_PROMO.search(title + " " + body[:120]):
        return False
    # Topic and method/result evidence must be present in the verified article.
    if not (TECHNICAL_TOPIC.search(body) and TECHNICAL_DETAIL.search(body)):
        return False
    return not NEWS_OR_PROMOTION.search(title) or bool(RESULT.search(body))


def verify(link, candidate_title, candidate_time, account):
    if (requested := article_link(link)) is None:
        raise ValueError("unstable original link")
    response_url, raw = read(requested[1])
    if urlsplit(response_url).path == "/mp/wappoc_appmsgcaptcha":
        raise ValueError("WeChat requires interactive verification")
    if (verified := article_link(response_url)) is None:
        raise ValueError("redirected away from the original")
    return parse_original(raw, requested, verified, candidate_title, candidate_time, account)


def parse_original(raw, requested, verified, candidate_title, candidate_time, account, publisher_id=None):
    document = raw.decode("utf-8")
    page = OriginalPage(document)
    title = page.text("activity-name") or page.text("js_text_title") or clean(page.meta.get("og:title", ""))
    body = page.text("js_content")
    if not body or not title:
        raise ValueError("missing original article")
    if comparable(page.text("js_name") or page.text("account")) != comparable(account):
        raise ValueError("original account mismatch")
    if comparable(title) != comparable(candidate_title):
        raise ValueError("original title mismatch")
    published = original_time(page, document)
    actual = (published.replace(second=0, microsecond=0) if isinstance(candidate_time, datetime)
              else published.astimezone(SHANGHAI).date())
    expected = candidate_time.replace(second=0, microsecond=0) if isinstance(candidate_time, datetime) else candidate_time
    if candidate_time is not None and actual != expected:
        raise ValueError("original publication time mismatch")
    aliases = {requested[0], verified[0]}
    for field in ("canonical", "og:url"):
        if canonical := article_link(page.meta.get(field, "")):
            aliases.add(canonical[0])
    long_ids = {key for key in aliases if key.count(":") == 3}
    if len(long_ids) > 1:
        raise ValueError("original article identity mismatch")
    link = requested[1]
    if publisher_id is not None:
        canonical = article_link(page.meta.get("og:url", ""))
        if not canonical or parse_qs(urlsplit(canonical[1]).query).get("__biz") != [publisher_id]:
            raise ValueError("reader publisher identity mismatch")
        link = canonical[1]
    if not technical_article(title, body):
        return None, aliases
    return {
        "id": next(iter(long_ids), verified[0]), "title": title, "source": account, "original": link,
        "summary": clean(page.meta.get("og:description", "") or body)[:280],
        "published": published.astimezone(SHANGHAI).date().isoformat(),
        "publishedAt": published.astimezone(timezone.utc).isoformat(),
    }, aliases


def feed_items(account, feed):
    url = feed if feed.startswith("https://") else f"https://wechat2rss.xlab.app/feed/{feed}.xml"
    _, xml = read(url)
    channel = ET.fromstring(xml).find("channel")
    if channel is None or comparable(channel.findtext("title", "")) != comparable(account):
        raise ValueError("RSS account mismatch")
    return channel.findall("item")


def publication_window(today):
    month_index = today.year * 12 + today.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    first_day = today.replace(year=year, month=month, day=min(today.day, monthrange(year, month)[1]))
    return first_day.isoformat(), today.isoformat()


def feed_article(row, account, published, *, public_biz, deferred=""):
    title = clean(row.findtext("title", ""))
    link = row.findtext("link", "").replace("http://mp.weixin.qq.com/", "https://mp.weixin.qq.com/", 1)
    original = article_link(link)
    if not original or not title:
        raise ValueError("unstable original link" if not original else "missing article title")
    markup = row.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "").strip()
    markup = markup.removeprefix("<![CDATA[").removesuffix("]]>")
    if public_biz and markup:
        # Wechat2RSS embeds the author's publication time in the opening byline.
        # Short URLs and feeds without corroborating metadata need the original page.
        byline = re.match(r"\s*<p\b[^>]*>(.*?)</p>", markup, re.S | re.I)
        stamp = published.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
        biz = parse_qs(urlsplit(original[1]).query).get("__biz")
        header = unescape(re.sub(r"<[^>]+>", " ", byline[1])) if byline else ""
        if byline and biz == [public_biz] and re.search(r"(?<!\d)" + re.escape(stamp) + r"(?!\d)", header):
            markup = markup[byline.end():]
        else:
            markup = ""
    content = OriginalPage(markup).text("js_content") or OriginalPage('<div id="js_content">' + markup + "</div>").text("js_content")
    if not content:
        if deferred:
            raise ValueError(deferred)
        return verify(original[1], title, published, account)
    if not technical_article(title, content):
        return None, {original[0]}
    return {"id": original[0], "original": original[1], "source": account, "title": title,
            "summary": content[:280], "published": published.astimezone(SHANGHAI).date().isoformat(),
            "publishedAt": published.astimezone(timezone.utc).isoformat()}, {original[0]}


class NoReaderRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReaderSessionError(ValueError):
    pass


def reader_request(opener, cookie, resource, params):
    url = "https://weread.qq.com" + resource + "?" + urlencode(params)
    request = Request(url, headers={"Cookie": cookie, "Accept": "application/json" if resource == "/api/mp/cover" else "text/html",
                                    "User-Agent": "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)"})
    with opener.open(request, timeout=20) as response:
        return response.read()


def reader_candidate(book, account, previous, items, keys_by_alias, cookie, opener, first, last, source):
    cover = json.loads(reader_request(opener, cookie, "/api/mp/cover", {"bookId": book}))
    if not isinstance(cover, dict):
        raise ValueError("invalid reader response")
    code = cover.get("errCode") or cover.get("errcode")
    if code and str(code) != "0":
        if str(code) in {"-2012", "-2010"}:
            raise ReaderSessionError("reader authorization required")
        raise ValueError("reader cover unavailable")
    review = cover.get("reviewId", "")
    title = clean(cover.get("title") or "")
    if not isinstance(review, str) or not re.fullmatch(re.escape(book) + r"_[A-Za-z0-9_~-]+", review) or not title:
        raise ValueError("missing reader cover")
    source["discovered"] = 1
    short = article_link(WECHAT + "/s/" + review[len(book) + 1:])
    saved_id = keys_by_alias.get(short[0])
    if not saved_id and previous.get("reviewId") == review:
        saved_id = previous.get("articleId")
    if (saved_id in items and items[saved_id]["source"] == account
            and comparable(items[saved_id]["title"]) == comparable(title)):
        source.update(matched=1, articleId=saved_id, reviewId=review)
        return
    article_page = reader_request(opener, cookie, "/web/mp/content", {"reviewId": review})
    article, aliases = parse_original(article_page, short, short, title, None, account, reader_publisher_id(book))
    if article is None or not first <= article["published"] <= last:
        source["filtered"] = 1
        return
    key = article["id"]
    if key not in items:
        key = next((keys_by_alias[alias] for alias in sorted(aliases) if alias in keys_by_alias), key)
    if key in items:
        source["matched"] = 1
    else:
        items[key] = article | {"collected": source["checkedAt"]}
        source["inserted"] = 1
    keys_by_alias.update((alias, key) for alias in aliases)
    source.update(articleId=key, reviewId=review)


def collect_reader(accounts, cookie, items, prior_sources, checked, first, last, *, keys_by_alias=None):
    opener = urllib.request.build_opener(NoReaderRedirect())
    if keys_by_alias is None:
        keys_by_alias = {key: key for key in items}
    blocked = "" if cookie else "reader authorization missing"
    for account, book in accounts.items():
        if not re.fullmatch(r"MP_WXS_\d+", book):
            raise ValueError("invalid publisher in account catalog")
        key = "weread:" + book
        previous = prior_sources.get(key, {})
        source = {"id": key, "account": account, "platform": "weread", "status": "ok", "checkedAt": checked,
                  "discovered": 0, "inserted": 0, "matched": 0, "filtered": 0, "unverified": 0}
        if previous.get("lastSuccessAt"):
            source["lastSuccessAt"] = previous["lastSuccessAt"]
        try:
            if blocked:
                raise ReaderSessionError(blocked)
            reader_candidate(book, account, previous, items, keys_by_alias, cookie, opener, first, last, source)
        except (OSError, HTTPException, ValueError) as error:
            label = error_label(error)
            if isinstance(error, ReaderSessionError) or isinstance(error, HTTPError) and error.code in {401, 403, 429}:
                blocked = label
            source.update(status="partial" if source["discovered"] else "failed", error=label, unverified=source["discovered"])
        else:
            source["lastSuccessAt"] = checked
        print(json.dumps({k: v for k, v in source.items() if k not in {"reviewId", "articleId"}}, ensure_ascii=True))
        yield source


def collect(output, reader_cookie=None):
    now = datetime.now(timezone.utc)
    checked = now.isoformat()
    first_day, today = publication_window(now.astimezone(SHANGHAI).date())
    target = output / "wechat.json"
    prior = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    items = {item["id"]: item for item in prior.get("items", [])
             if first_day <= item["published"] <= today and not EVENT_PROMO.search(item["title"] + " " + item["summary"][:120])}
    keys_by_alias = {key: key for key in items}
    for key, item in items.items():
        if original := article_link(item["original"]):
            keys_by_alias[original[0]] = key
    prior_sources = {source["id"]: source for source in prior.get("sources", [])}
    sources = []
    consecutive_network_errors = 0
    deferred = ""

    accounts = json.loads((ROOT / "config" / "wechat-accounts.json").read_text("utf-8"))
    public_feeds = json.loads((ROOT / "config" / "wechat-public-feeds.json").read_text("utf-8"))
    added_sources = tuple(("rss:" + accounts[account], account, feed, reader_publisher_id(accounts[account]))
                          for account, feed in public_feeds.items())
    for source_id, account, feed, publisher_biz in SOURCES + added_sources:
        source = {"id": source_id, "account": account, "platform": "rss", "status": "ok", "checkedAt": checked,
                  "discovered": 0, "inserted": 0, "matched": 0, "unverified": 0, "filtered": 0}
        if last_success := prior_sources.get(source_id, {}).get("lastSuccessAt"):
            source["lastSuccessAt"] = last_success
        try:
            candidates = feed_items(account, feed)
        except (OSError, HTTPException, ValueError, ET.ParseError) as error:
            source.update(status="failed", error=error_label(error))
        else:
            reasons = Counter()
            source["feedItems"] = len(candidates)
            for candidate in candidates:
                try:
                    published = parsedate_to_datetime(candidate.findtext("pubDate", ""))
                    if published.tzinfo is None:
                        raise ValueError("missing RSS timezone")
                except (TypeError, ValueError):
                    reasons["invalid RSS publication time"] += 1
                    source["discovered"] += 1
                    continue
                day = published.astimezone(SHANGHAI).date().isoformat()
                if not first_day <= day <= today:
                    continue
                published_at = published.astimezone(timezone.utc).isoformat()
                source["newestCandidateAt"] = max(published_at, source.get("newestCandidateAt", published_at))
                source["oldestCandidateAt"] = min(published_at, source.get("oldestCandidateAt", published_at))
                source["discovered"] += 1
                link = candidate.findtext("link", "").replace("http://mp.weixin.qq.com/", "https://mp.weixin.qq.com/", 1)
                original = article_link(link)
                previous = items.get(keys_by_alias.get(original[0])) if original else None
                if (previous and previous["source"] == account
                        and comparable(previous["title"]) == comparable(candidate.findtext("title", ""))):
                    source["matched"] += 1
                    continue
                try:
                    article, aliases = feed_article(candidate, account, published,
                                                    public_biz=publisher_biz, deferred=deferred)
                except (OSError, HTTPException, ValueError) as error:
                    reasons[error_label(error)] += 1
                    if isinstance(error, HTTPError) and error.code == 429:
                        deferred = "verification deferred after HTTP 429"
                    elif str(error) == "WeChat requires interactive verification":
                        deferred = "WeChat requires interactive verification"
                    elif isinstance(error, (URLError, ConnectionError, TimeoutError)) and not isinstance(error, HTTPError):
                        consecutive_network_errors += 1
                        if consecutive_network_errors >= 2:
                            deferred = "verification deferred after repeated network errors"
                    else:
                        consecutive_network_errors = 0
                    continue
                consecutive_network_errors = 0
                if article is None or not first_day <= article["published"] <= today:
                    source["filtered"] += 1
                    continue
                article_id = article["id"]
                if article_id not in items:
                    article_id = next((keys_by_alias[key] for key in sorted(aliases) if key in keys_by_alias), article_id)
                if article_id in items:
                    source["matched"] += 1
                else:
                    items[article_id] = article | {"collected": checked}
                    source["inserted"] += 1
                keys_by_alias.update((key, article_id) for key in aliases)
            if reasons:
                source.update(status="partial", unverified=sum(reasons.values()), verificationFailures=dict(reasons))
            else:
                source["lastSuccessAt"] = checked
        sources.append(source)
        print(json.dumps(source, ensure_ascii=True))

    if reader_cookie is not None:
        # WeRead can see a newer post before it reaches RSS. Check its cover,
        # then fetch the article only when the review still needs to be matched.
        sources.extend(collect_reader(accounts, reader_cookie, items, prior_sources, checked, first_day, today,
                                      keys_by_alias=keys_by_alias))
    published_items = sorted(items.values(), key=lambda item: (item["published"], item["id"]), reverse=True)
    output.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as file:
        pending = Path(file.name)
        json.dump({"items": published_items, "sources": sources}, file, ensure_ascii=False, indent=2)
        file.write("\n")
    pending.replace(target)


def error_label(error):
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, (OSError, HTTPException)):
        return type(getattr(error, "reason", error)).__name__
    return str(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "wechat-preview")
    parser.add_argument("--with-reader", action="store_true", help="Include the cloud WeRead account catalog")
    args = parser.parse_args()
    collect(args.output, os.environ.pop("AIFRONTIER_WEREAD_COOKIE", "") if args.with_reader else None)


if __name__ == "__main__":
    main()
