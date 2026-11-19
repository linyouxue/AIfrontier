"""Read attributable announcements from publisher and Codex staff timelines."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from event_sources import EvidenceCandidate, publication_date, read
from social_event_sources import PostContent, related_announcement


LAUNCH = re.compile(r"\b(?:introducing |we(?:['’]re|['’]ve| are| just| have)? (?:launching|launched|released|open-sourced|introducing|priced our IPO|filed for) |(?:is|are) now available\b)", re.I)
RESET = re.compile(r"\b(?:banked|codex|usage|rate[- ]?limit) resets?\b.{0,55}\b(?:activated|available|live|applied)\b|\b(?:we(?:['’]ve| have| just)?|I(?:['’]ve| have| just)?) (?:just )?reset\b.{0,65}\b(?:limits?|usage|codex)\b", re.I)


def feed_posts(handle):
    channel = ET.fromstring(read(f"https://fxtwitter.com/{handle.lower()}/feed.xml?with_replies=0")).find("channel")
    if channel is None or urlsplit(channel.findtext("link", "")).path.strip("/").lower() != handle.lower():
        raise ValueError("Unexpected X feed owner")
    posts = []
    for item in channel.findall("item"):
        original = item.findtext("link", "")
        if original != item.findtext("guid") or not re.fullmatch(rf"https://x\.com/{re.escape(handle)}/status/\d+", original, re.I):
            continue
        paragraph = re.match(r"\s*<p>(.*?)</p>", item.findtext("description", ""), re.S)
        if not paragraph:
            continue
        body = PostContent()
        body.feed(paragraph[1])
        stamp = parsedate_to_datetime(item.findtext("pubDate", ""))
        message = " ".join(body.text).strip()
        if stamp.tzinfo and not message.startswith(("RT @", "@")):
            posts.append({"id": original.rsplit("/", 1)[1], "url": original, "type":"status",
                          "author":{"screen_name":handle}, "text":message, "created_timestamp":stamp.timestamp(),
                          "raw_text":{"facets":[{"type":"url","replacement":link} for link in body.links]}})
    return {"code":200, "results":posts}


def x_candidates(handle, batch):
    try:
        result = json.loads(read(f"https://api.fxtwitter.com/2/profile/{handle.lower()}/statuses?count=50"))
    except HTTPError as error:
        if error.code != 404:
            raise
        result = feed_posts(handle)
    if result.get("code") != 200 or not isinstance(result.get("results"), list):
        raise ValueError("X timeline unavailable")
    candidates = []
    for post in result["results"]:
        author = post.get("author") or {}
        original = f"https://x.com/{handle}/status/{post.get('id', '')}"
        if (post.get("type") != "status" or author.get("screen_name", "").lower() != handle.lower()
                or author.get("protected") or post.get("reposted_by") or post.get("replying_to")
                or not str(post.get("id", "")).isdigit() or post.get("url", "").lower() != original.lower()):
            continue
        body = post.get("text", "").strip()
        links = [f["replacement"] for f in (post.get("raw_text") or {}).get("facets", [])
                 if f.get("type") == "url" and f.get("replacement")]
        related = related_announcement(links)
        reset = handle == "thsottiaux" and bool(RESET.search(body))
        if not reset and not (related and LAUNCH.search(body)):
            continue
        when = datetime.fromtimestamp(post["created_timestamp"], timezone.utc).isoformat()
        excerpt = re.sub(r"https?://\S+", "", body).strip()
        title = next((line for line in excerpt.splitlines() if (RESET if reset else LAUNCH).search(line)), excerpt)
        primary_domains = {"OpenAI":"openai.com", "AnthropicAI":"anthropic.com", "GoogleDeepMind":"deepmind.google"}
        owner_release = related and urlsplit(related[0]).hostname == primary_domains.get(handle)
        candidates.append(EvidenceCandidate(original, title[:155], "@" + handle, when, excerpt[:450],
                                            verified=bool(reset or owner_release), related_urls=related,
                                            announced_at=publication_date(when) if reset or owner_release else None))
    return batch(candidates, checked=len(result["results"]))
