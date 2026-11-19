"""Collect public account updates into the Pages archive."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from collect_blogs import AI_TOPIC, clean, read
from collect_public_wechat import ROOT, SHANGHAI, publication_window


REGISTRY = ROOT / "config" / "social-accounts.json"
OUTPUT = ROOT / "dist" / "wechat-preview"
XHS = "https://www.xiaohongshu.com"
NOTE = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{24})/?")
TECHNICAL = re.compile(r"\b(?:benchmarks?|latency|throughput|quantization|neural|training|tokenizers?|decoding)\b|量化|神经网络|吞吐|评测", re.I)


def x_item(handle, url, body, stamp, minimum):
    text = " ".join(re.sub(r"https?://\S+", "", body).split())
    if len(text) < minimum or text.startswith(("RT @", "@")) or not (AI_TOPIC.search(text) or TECHNICAL.search(text)):
        return None
    return {"id": "x:" + url.rsplit("/", 1)[1], "original": url, "source": "@" + handle,
            "published": stamp.astimezone(SHANGHAI).date().isoformat(),
            "title": text.split(". ", 1)[0][:110], "summary": text[:200]}


def x_feed(handle):
    channel = ET.fromstring(read(f"https://fxtwitter.com/{handle.lower()}/feed.xml?with_replies=0")).find("channel")
    if channel is None or urlsplit(channel.findtext("link", "")).path.strip("/").lower() != handle.lower():
        raise ValueError("Mismatched X feed")
    for entry in channel.findall("item"):
        url = entry.findtext("link", "")
        if not re.fullmatch(rf"https://x\.com/{re.escape(handle)}/status/[0-9]{{1,20}}", url, re.I):
            continue
        if entry.findtext("guid") != url:
            continue
        # FxEmbed places the author's text first and adds quoted posts and media afterwards.
        paragraph = re.match(r"\s*<p>(.*?)</p>", entry.findtext("description", ""), re.S)
        if paragraph:
            stamp = parsedate_to_datetime(entry.findtext("pubDate", ""))
            if stamp.tzinfo is None:
                raise ValueError("Missing X publication timezone")
            if item := x_item(handle, url, clean(paragraph[1]), stamp, 180):
                yield item


def x_posts(handle):
    try:
        body = read(f"https://api.fxtwitter.com/2/profile/{handle.lower()}/statuses?count=50")
        page = json.loads(body)
    except HTTPError as error:
        if error.code != 404:
            raise
        yield from x_feed(handle)
        return
    if page.get("code") != 200 or not isinstance(page.get("results"), list):
        raise ValueError("Invalid FxEmbed timeline")
    for post in page["results"]:
        owner = post.get("author") or {}
        if (post.get("type") != "status" or owner.get("screen_name", "").lower() != handle.lower()
                or owner.get("protected") or post.get("reposted_by") or post.get("replying_to")):
            continue
        identifier = str(post["id"])
        url = f"https://x.com/{handle}/status/{identifier}"
        if not re.fullmatch(r"[0-9]{1,20}", identifier) or post["url"].lower() != url.lower():
            raise ValueError("Mismatched X original")
        has_link = any(f.get("type") == "url" and f.get("replacement")
                       for f in (post.get("raw_text") or {}).get("facets", []))
        minimum = 180 if post.get("quote") else 45 if has_link else 150
        stamp = datetime.fromtimestamp(post["created_timestamp"], timezone.utc)
        if item := x_item(handle, url, post.get("text", ""), stamp, minimum):
            yield item


def xhs_posts(base, account):
    owner = account["id"]
    channel = ET.fromstring(read(f"{base.rstrip('/')}/xiaohongshu/user/{owner}/notes", timeout=90)).find("channel")
    profile = f"{XHS}/user/profile/{owner}"
    if channel is None or channel.findtext("link", "").rstrip("/") != profile:
        raise ValueError("Feed belongs to a different profile")
    # RSSHub's full-note route sets pubDate from note.time. Its list fallback has no original date.
    suffix = " - 笔记 • 小红书 / RED"
    title = channel.findtext("title", "")
    if not title.endswith(suffix):
        raise ValueError("Original publication times unavailable")
    name = title.removesuffix(suffix).strip()
    if not name:
        raise ValueError("Missing author")
    skipped = 0
    for entry in channel.findall("item"):
        link = urlsplit(entry.findtext("link", ""))
        note = NOTE.fullmatch(link.path)
        author = entry.findtext("{http://purl.org/dc/elements/1.1/}creator") or entry.findtext("author")
        heading = clean(entry.findtext("title", ""))
        when = entry.findtext("pubDate", "")
        if not (link.scheme == "https" and link.netloc == "www.xiaohongshu.com" and note
                and author == name and heading and when):
            skipped += 1
            continue
        guid = entry.findtext("guid")
        if guid and urlsplit(guid).path.rstrip("/") != link.path.rstrip("/"):
            skipped += 1
            continue
        try:
            stamp = parsedate_to_datetime(when)
            if stamp.tzinfo is None:
                raise ValueError("Missing publication timezone")
        except (TypeError, ValueError, OverflowError):
            skipped += 1
            continue
        text = clean(entry.findtext("description", ""))
        yield {"id": "xiaohongshu:" + note[1], "original": f"{XHS}/explore/{note[1]}", "source": name,
               "published": stamp.astimezone(SHANGHAI).date().isoformat(), "title": heading,
               "summary": re.sub(r"https?://\S+", "", text).strip()[:900]}
    if skipped:
        raise ValueError("Some notes lack attributable titles or original dates")


def collect(registry, previous, base):
    now = datetime.now(timezone.utc)
    checked = now.isoformat()
    first, last = publication_window(now.astimezone(SHANGHAI).date())
    items = {i["id"]: i for i in previous.get("items", []) if first <= i["published"] <= last}
    prior_sources = {s["id"]: s for s in previous.get("sources", [])}
    sources = []
    for platform in ("x", "xiaohongshu"):
        for account in registry[platform]:
            is_x = platform == "x"
            owner = account if is_x else account["id"]
            source_id = f"{platform}:{owner}"
            baseline = set() if is_x else set(account["baseline"])
            status = {"id": source_id, "platform": platform, "name": owner if is_x else account["name"],
                      "checkedAt": checked, "status": "ok", "inserted": 0, "discovered": 0}
            sources.append(status)
            if not is_x:
                status["verifiedNotes"] = 0
            if last_ok := prior_sources.get(source_id, {}).get("lastSuccessAt"):
                status["lastSuccessAt"] = last_ok
            if not is_x and not base:
                status["status"] = "unconfigured"
                continue
            try:
                rows = x_posts(owner) if is_x else xhs_posts(base, account)
                for row in rows:
                    if not is_x:
                        status["verifiedNotes"] += 1
                    if not first <= row["published"] <= last:
                        continue
                    status["discovered"] += 1
                    key = row["id"]
                    old = items.get(key)
                    if not old and key.split(":", 1)[1] not in baseline:
                        status["inserted"] += 1
                    items[key] = {**row, "platform": platform, "collected": old["collected"] if old else checked}
            except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError, ET.ParseError) as error:
                status["status"] = "partial" if status["discovered"] else "failed"
                status["error"] = f"HTTP {error.code}" if isinstance(error, HTTPError) else "来源未返回可核验的数据"
            else:
                status["lastSuccessAt"] = checked
    return {"items": sorted(items.values(), key=lambda i: (i["published"], i["id"]), reverse=True),
            "sources": sources}


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / "social.json"
    previous = json.loads(destination.read_text("utf-8")) if destination.exists() else {}
    # A first cloud run can miss accounts that were already verified during enrollment.
    baseline = json.loads(REGISTRY.with_name("x-baseline.json").read_text("utf-8"))
    previous["items"] = baseline + previous.get("items", [])
    snapshot = collect(json.loads(REGISTRY.read_text("utf-8")), previous, os.getenv("AIFRONTIER_RSSHUB_URL"))
    temp = destination.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    temp.replace(destination)
    for source in snapshot["sources"]:
        print(f"{source['id']} {source['status']}, found={source['discovered']} new={source['inserted']}")
