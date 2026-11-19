"""Discover public video references by speaker, as in VCIQ's person_video_discovery."""

from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlencode

from event_sources import EvidenceCandidate, clean, publication_date, read
from video_event_sources import CLIP, YOUTUBE_ID, announcement_reference


class VideoSearchError(ValueError):
    """Fixed public source status suitable for the collection report."""


def embedded_json(page, name):
    match = re.search(r"(?:\b" + name + r"\s*=|\x22" + name + r"\x22\s*:)\s*", page)
    if not match:
        raise VideoSearchError("视频页面缺少公开元数据")
    return json.JSONDecoder().raw_decode(page[match.end():])[0]


def objects(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects(value)


def youtube_results(person, batch):
    url = "https://www.youtube.com/results?" + urlencode({"search_query": person + " interview talk keynote conversation"})
    listing = embedded_json(read(url).decode(), "ytInitialData")
    videos = {}
    for node in objects(listing):
        video = node.get("videoRenderer") or {}
        identifier = video.get("videoId", "")
        title = "".join(run.get("text", "") for run in video.get("title", {}).get("runs", []))
        if YOUTUBE_ID.fullmatch(identifier) and not CLIP.search(title) and person.casefold() in title.casefold():
            videos.setdefault(identifier, title)
    results = []
    failed = 0
    for index, (identifier, title) in enumerate(list(videos.items())[:3]):
        original = "https://www.youtube.com/watch?v=" + identifier
        try:
            player = embedded_json(read(original).decode(), "ytInitialPlayerResponse")
            if player.get("playabilityStatus", {}).get("status") == "LOGIN_REQUIRED":
                break
            details = player.get("videoDetails", {})
            if details.get("videoId") != identifier or not details.get("author"):
                raise ValueError("Missing original video")
            description = details.get("shortDescription", "")
            primary = announcement_reference(description)
            if primary:
                micro = player["microformat"]["playerMicroformatRenderer"]
                day = publication_date(micro["publishDate"])
                results.append(EvidenceCandidate(original, details.get("title") or title, details["author"], day,
                                                 clean(description)[:400], related_urls=primary))
        except (OSError, ValueError, KeyError):
            failed += 1
    else:
        if videos and failed == min(3, len(videos)):
            raise VideoSearchError("未能核验 YouTube 原视频详情")
        return batch(results, status="partial" if failed else "ok", checked=len(videos))
    if index == failed:
        raise VideoSearchError("视频详情要求登录")
    return batch(results, status="partial", checked=len(videos))


def bilibili_results(person, batch):
    terms = person + " 演讲 采访 对话"
    params = urlencode({"search_type": "video", "keyword": terms, "page": 1, "page_size": 12})
    try:
        data = json.loads(read("https://api.bilibili.com/x/web-interface/search/type?" + params))
        if data.get("code") in {-101, -352, -412}:
            raise VideoSearchError("哔哩哔哩要求登录或验证")
        if data.get("code") != 0 or not isinstance(data.get("data", {}).get("result"), list):
            raise ValueError("Public Bilibili search unavailable")
        rows = data["data"]["result"]
    except (OSError, ValueError) as error:
        if isinstance(error, VideoSearchError) or isinstance(error, HTTPError) and error.code in {401, 403, 412, 429}:
            raise
        page = read("https://search.bilibili.com/all?" + urlencode({"keyword": terms})).decode()
        listing = embedded_json(page, "__INITIAL_STATE__")
        rows = [node for node in objects(listing) if node.get("bvid") and node.get("title")]
        if not rows:
            raise ValueError("Missing Bilibili search results")
    results = {}
    for row in rows:
        identifier = row.get("bvid", "")
        title = clean(row.get("title", ""))
        description = row.get("description") or ""
        primary = announcement_reference(description)
        if (not re.fullmatch(r"BV[A-Za-z0-9]{10}", identifier) or CLIP.search(title) or not row.get("author")
                or not primary or not row.get("pubdate")):
            continue
        day = datetime.fromtimestamp(int(row["pubdate"]), timezone.utc).isoformat()
        results[identifier] = EvidenceCandidate("https://www.bilibili.com/video/" + identifier, title, row["author"],
                                               day, clean(description)[:400], related_urls=primary)
    return batch(results.values(), checked=len(rows))
