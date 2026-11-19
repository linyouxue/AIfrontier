"""Collect complete AI video conversations from official podcast catalogs."""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from textwrap import shorten
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from build_preview import SHANGHAI, publication_window


ROOT = Path(__file__).resolve().parents[1]
LATENT_FEED = "https://podcast.latent.space/feed.xml"
LATENT_ARCHIVE = "https://latent.space/podcasts/"
DWARKESH_FEED = "https://api.substack.com/feed/podcast/69345.rss"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CLIP = re.compile(
    r"\b(?:trailer|teaser|shorts)\b|\b(?:clip|preview|excerpt|highlight)s?\s*[:|—)\]]"
    r"|(?:^|\|)\s*(?:clip|preview|excerpt|highlights?)\s*(?:from\b|$)",
    re.I,
)
PERSON = re.compile(r"[A-Z][\w.’'-]*(?: [A-Z][\w.’'-]*){1,4}")
SEPARATOR = re.compile(r"\s+[—–-]\s+")
ARCHIVE_DATA = re.compile(r'<script id="archive-props" type="application/json">(.*?)</script>', re.S)
WATCH_LINK = re.compile(
    r"\bWatch (?:the full (?:episode|video) )?on\s+(?:</?span[^>]*>\s*)*"
    r"""<a\b[^>]*?\bhref=["']([^"']+)["'][^>]*>\s*YouTube\s*</a>""",
    re.I,
)
LINK_LABEL = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
EPISODE_INTRO = re.compile(
    r"<p>\s*(?:New episode with|In this (?:episode|conversation),? I (?:speak|talk) with)\s+(.*?)</p>",
    re.I | re.S,
)
AI_TOPIC = re.compile(
    r"\b(?:AIs?|AGI|LLMs?|artificial intelligence|machine learning|superintelligence|"
    r"OpenAI|Anthropic|DeepMind|ChatGPT|neural networks?|pretraining)\b",
    re.I,
)
CHAPTER = re.compile(r"^\(?\d{1,2}:\d{2}(?::\d{2})?\)?\s*[–—-]\s*(.+)")
SOLO = re.compile(r"\b(?:video essay|video recording of a post|monologue|narration)\b", re.I)


def read(url):
    request = Request(url, headers={"User-Agent": "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)"})
    with urlopen(request, timeout=20) as response:
        return response.read()


def video_id(url):
    parts = urlsplit(url)
    host = parts.hostname
    if host == "youtu.be":
        identifier = parts.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}:
        if parts.path == "/watch":
            identifier = parse_qs(parts.query).get("v", [""])[0]
        else:
            prefix, _, identifier = parts.path.lstrip("/").partition("/")
            if prefix != "embed":
                return ""
    else:
        return ""
    return identifier if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier) else ""


class Notes(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.text = []
        self.feed(html)

    def handle_data(self, data):
        self.text.append(data)

    def plain(self):
        return " ".join("".join(self.text).split())

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "h2", "h3"}:
            self.text.append("\n")

    def lines(self):
        return [" ".join(line.split()) for line in "".join(self.text).splitlines() if line.strip()]


def text(html):
    return Notes(html).plain()


def rss_entries(url, first_day, last_day):
    channel = ET.fromstring(read(url)).find("channel")
    if channel is None:
        raise ValueError("Expected a podcast RSS channel")
    for item in channel.findall("item"):
        date_value = item.findtext("pubDate", "")
        instant = parsedate_to_datetime(date_value)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        published = instant.astimezone(SHANGHAI).date().isoformat()
        title = text(item.findtext("title", ""))
        if first_day <= published <= last_day and title and not CLIP.search(title):
            yield item, title, published


def duration(value):
    parts = str(value).split(":")
    if 1 <= len(parts) <= 3 and all(part.isascii() and part.isdigit() for part in parts):
        return sum(int(part) * 60 ** position for position, part in enumerate(reversed(parts)))


def title_guests(title, *, before_topic=False):
    parts = SEPARATOR.split(title, maxsplit=1)
    if len(parts) != 2:
        return []
    return guest_names(parts[0 if before_topic else 1].removeprefix("with "))


def guest_names(names):
    candidates = (part.split(",", 1)[0].strip() for part in re.split(r" & | and ", names))
    return [name for name in candidates if PERSON.fullmatch(name)]


def title_topic(title, *, after_guest=False):
    parts = SEPARATOR.split(title, maxsplit=1)
    return parts[-1] if after_guest else parts[0]


def make_item(identifier, title, summary, show, published, *, page="", guests=(), seconds=None, topics=()):
    item = {
        "id": f"youtube:{identifier}",
        "title": title,
        "summary": shorten(summary, width=300, placeholder="…") if summary else title,
        "source": show,
        "published": published,
        "original": f"https://www.youtube.com/watch?v={identifier}",
        "show": show,
    }
    if guests:
        item["guests"] = list(guests)
    if seconds:
        item["durationSeconds"] = seconds
    if topics:
        item["topics"] = list(topics)
    if page and not video_id(page):
        item["evidence"] = [{"url": page, "title": title, "source": show, "published": published}]
    return item


def latent_archive(url, first_day, last_day):
    page = 1
    while True:
        if page == 1:
            page_url = url
        else:
            parts = urlsplit(url)
            query = urlencode({**parse_qs(parts.query), "page": page}, doseq=True)
            page_url = urlunsplit(parts._replace(query=query))
        document = read(page_url).decode("utf-8")
        match = ARCHIVE_DATA.search(document)
        if not match:
            raise ValueError("Latent Space podcast archive data is missing")
        data = json.loads(match[1])["initial"]
        if data["section"] != "podcasts" or data["page"] != page:
            raise ValueError("Unexpected Latent Space podcast archive page")
        oldest = last_day
        for entry in data["items"]:
            published = datetime.fromisoformat(entry["date"]).astimezone(SHANGHAI).date().isoformat()
            oldest = min(oldest, published)
            if first_day <= published <= last_day:
                yield entry, published
        if oldest < first_day or page >= data["pages"]:
            break
        page += 1


def collect_latent(feed_url, archive_url, first_day, last_day, items, errors):
    show = "Latent Space"
    feed = {}
    try:
        for entry, title, published in rss_entries(feed_url, first_day, last_day):
            episode_type = entry.findtext(ITUNES + "episodeType", "").strip().lower()
            if episode_type == "trailer":
                continue
            identifier = video_id(entry.findtext("link", ""))
            for enclosure in entry.findall("enclosure"):
                if enclosure.get("type", "").startswith("audio/") and enclosure.get("url"):
                    feed[enclosure.get("url")] = entry
            if identifier and episode_type == "full":
                feed[identifier] = entry
                description = text(entry.findtext("description", ""))
                if description.startswith("Watch the full show on YouTube:"):
                    items[identifier] = make_item(
                        identifier, title, "", show, published,
                        guests=title_guests(title), seconds=duration(entry.findtext(ITUNES + "duration", "")),
                        topics=[title_topic(title)],
                    )
    except (OSError, ET.ParseError, ValueError) as error:
        errors.append(f"RSS: {error}")

    try:
        for entry, published in latent_archive(archive_url, first_day, last_day):
            identifier = video_id(entry.get("video", ""))
            title = text(entry["title"])
            if (not identifier or not title or CLIP.search(title) or entry.get("audience") != "everyone"
                    or entry.get("type") not in {"podcast", "video"}):
                continue
            episode = entry.get("episode") or {}
            if not episode:
                continue
            rss_item = feed.get(episode.get("audio"))
            if rss_item is None:
                rss_item = feed.get(identifier)
            seconds = duration(episode.get("duration"))
            if not seconds and rss_item is not None:
                seconds = duration(rss_item.findtext(ITUNES + "duration", ""))
            # The archive represents official full conversations. Individual clips have a separate feed.
            page = f"https://latent.space/p/{entry['slug']}/" if entry["type"] == "podcast" else ""
            summary = text(entry.get("description") or entry.get("subtitle") or "")
            if not summary and rss_item is not None:
                notes = text(rss_item.findtext("description", ""))
                if not notes.startswith("Watch the full show on YouTube:"):
                    summary = notes
            guests = title_guests(title)
            if not guests and " on " in summary:
                guests = guest_names(summary.split(" on ", 1)[0])
            items[identifier] = make_item(
                identifier, title, summary, show, published, page=page,
                guests=guests, seconds=seconds, topics=[title_topic(title)],
            )
    except (OSError, ValueError, KeyError) as error:
        errors.append(f"archive: {error}")


def collect_dwarkesh(feed_url, first_day, last_day, items, errors):
    show = "Dwarkesh Podcast"
    try:
        for entry, title, published in rss_entries(feed_url, first_day, last_day):
            if entry.findtext(ITUNES + "episodeType", "").strip().lower() == "trailer":
                continue
            description = entry.findtext("description", "")
            link = WATCH_LINK.search(description)
            if not link or not (identifier := video_id(unescape(link[1]))):
                continue
            intro = description[:link.start()]
            description_lines = Notes(intro).lines()
            overview = " ".join(description_lines[:2])
            if SOLO.search(overview) or not AI_TOPIC.search(title + " " + overview):
                continue
            guests = title_guests(title, before_topic=True)
            if guest_intro := EPISODE_INTRO.search(intro):
                named = [text(name) for name in LINK_LABEL.findall(guest_intro[1])]
                guests = [name for name in named if PERSON.fullmatch(name)] or guests
            if not guests and not re.search(r"\b(?:interview|roundtable|debate)\b", title + " " + overview, re.I):
                continue
            chapters = [match[1] for line in Notes(description).lines() if (match := CHAPTER.match(line))]
            topics = [chapter for chapter in chapters if chapter.casefold() not in {"intro", "introduction", "outro"}]
            items[identifier] = make_item(
                identifier, title, " ".join(description_lines), show, published, page=entry.findtext("link", ""),
                guests=guests, seconds=duration(entry.findtext(ITUNES + "duration", "")),
                topics=topics[:3] or [title_topic(title, after_guest=bool(guests))],
            )
    except (OSError, ET.ParseError, ValueError) as error:
        errors.append(str(error))


def episode_links(episode):
    yield episode["original"]
    for evidence in episode.get("evidence", []):
        yield evidence["url"]


def collect(dwarkesh_feed_url, latent_feed_url, latent_archive_url, output):
    now = datetime.now(timezone.utc)
    checked_at = now.isoformat()
    first_day, last_day = publication_window(now.astimezone(SHANGHAI).date())
    target = output / "podcasts.json"
    prior = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    retained = {
        item["id"]: item for item in prior.get("items", [])
        if first_day <= item["published"] <= last_day
    }
    keys_by_link = {link: key for key, item in retained.items() for link in episode_links(item)}
    prior_sources = {source["id"]: source for source in prior.get("sources", [])}
    sources = []

    collectors = (
        ("latent-space", collect_latent, (latent_feed_url, latent_archive_url)),
        ("dwarkesh-podcast", collect_dwarkesh, (dwarkesh_feed_url,)),
    )
    for source_id, collector, urls in collectors:
        discovered, failures = {}, []
        collector(*urls, first_day, last_day, discovered, failures)
        inserted = 0
        # Resolve alternate uploads against their official episode page before feed-only records.
        for episode in sorted(discovered.values(), key=lambda item: not item.get("evidence")):
            links = list(episode_links(episode))
            key = next((keys_by_link[link] for link in links if link in keys_by_link), episode["id"])
            previous = retained.get(key)
            if previous:
                # Feed-only evidence should not replace an established episode page and video.
                if not episode.get("evidence") and previous.get("evidence"):
                    continue
                retained[key] = {**previous, **episode, "id": key, "published": previous["published"]}
            else:
                retained[key] = {**episode, "collected": checked_at}
                inserted += 1
            keys_by_link.update((link, key) for link in links)
        status = "partial" if failures and discovered else "failed" if failures else "ok"
        source = {
            "id": source_id, "platform": "rss", "status": status, "checkedAt": checked_at,
            "discovered": len(discovered), "inserted": inserted,
        }
        if failures:
            source["error"] = "; ".join(failures)
        if status == "ok":
            source["lastSuccessAt"] = checked_at
        elif last_success := prior_sources.get(source_id, {}).get("lastSuccessAt"):
            source["lastSuccessAt"] = last_success
        sources.append(source)

    episodes = sorted(
        (episode for episode in retained.values() if first_day <= episode["published"] <= last_day),
        key=lambda episode: (episode["published"], episode["id"]), reverse=True,
    )
    output.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as file:
        temporary = Path(file.name)
        json.dump({"items": episodes, "sources": sources}, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(target)
    for source in sources:
        print(f"{source['id']}: {source['status']}, {source['discovered']} discovered, {source['inserted']} inserted")
        if source.get("error"):
            print(f"  {source['error']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "AIfrontier")
    parser.add_argument("--latent-space-feed-url", default=LATENT_FEED)
    parser.add_argument("--latent-space-archive-url", default=LATENT_ARCHIVE)
    parser.add_argument("--dwarkesh-feed-url", default=DWARKESH_FEED)
    arguments = parser.parse_args()
    collect(arguments.dwarkesh_feed_url, arguments.latent_space_feed_url, arguments.latent_space_archive_url, arguments.output)


if __name__ == "__main__":
    main()
