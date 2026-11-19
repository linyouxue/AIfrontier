"""Read release evidence from public or authorized video feeds."""

import re
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

from event_sources import EvidenceCandidate, canonical_url, clean, publication_date, read


DEEPMIND_CHANNEL = "UCP7jMXSY2xbc3KCAE0MHQ-A"
YOUTUBE_DEEPMIND_ATOM = f"https://www.youtube.com/feeds/videos.xml?channel_id={DEEPMIND_CHANNEL}"
ATOM = "{http://www.w3.org/2005/Atom}"
YOUTUBE = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"
CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
CLIP = re.compile(r"\b(?:clips?|shorts|trailer|teaser|highlights|rumou?rs?|leaks?)\b|切片|预告|传闻|转载", re.I)
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
BILIBILI_PATH = re.compile(r"/video/(BV[A-Za-z0-9]{10})/?")
REFERENCE = re.compile(
    r"(?:official announcement|original release video|release notes|发布公告|官方公告|原始发布视频)"
    r"\s*[:：]\s*(https?://\S+)", re.I,
)


def offset(query, *, suffix=""):
    times = query.get("t", [])
    if len(times) != 1:
        return None
    match = re.fullmatch(rf"([0-9]{{1,9}}){suffix}", times[0])
    if match:
        return int(match[1])


def youtube_origin(url, identifier):
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if (parts.scheme not in {"http", "https"} or parts.netloc.lower() not in {"youtube.com", "www.youtube.com"}
            or not YOUTUBE_ID.fullmatch(identifier)):
        raise ValueError("Expected a YouTube watch URL")
    if parts.path == f"/shorts/{identifier}":
        return None, None
    if parts.path != "/watch" or query.get("v") != [identifier]:
        raise ValueError("Expected a YouTube watch URL")
    return f"https://www.youtube.com/watch?v={identifier}", offset(query, suffix="s?")


def announcement_reference(description, *, same_title_blog=None):
    # RSSHub wraps uploader-written notes in HTML. Keep explicit link labels and line breaks.
    description = re.sub(r"<a\b[^>]*\bhref=(['\"])(.*?)\1[^>]*>.*?</a>", r" \2 ", description, flags=re.I | re.S)
    description = re.sub(r"<(?:br\s*/?|/(?:p|div|li))>", "\n", description, flags=re.I)
    references = set()
    for raw in description.splitlines():
        line = clean(raw)
        link = REFERENCE.fullmatch(line)
        if link:
            target = link[1]
        elif same_title_blog and line.startswith("Learn more: "):
            target = line.removeprefix("Learn more: ")
        else:
            continue
        try:
            reference = canonical_url(target)
        except ValueError:
            continue
        if not same_title_blog or reference == same_title_blog:
            references.add(reference)
    # A video with several distinct release references is ambiguous at event level.
    return tuple(references) if len(references) == 1 else ()


def first_party_release(title, description):
    # The official channel also publishes tutorials and interviews. Require a named launch.
    product = re.sub(r"^introducing\s+", "", title.split(":", 1)[0], flags=re.I).strip()
    if not product:
        return None
    action = re.search(
        r"(?:^|\n)\s*(?:today,? )?we (?:(?:have started|are now|are|just|have) )?"
        r"(?:rolling out|roll(?:ed)? out|releasing|released|announcing|announced|launching|launched|introducing) "
        rf"{re.escape(product)}(?=\s|[,!?:]|[.](?:\s|$)|$)[^\n]*",
        description,
        re.I,
    )
    return action[0].strip() if action else None


def youtube_video(entry, channel=DEEPMIND_CHANNEL, official=True):
    identifier = entry.findtext(YOUTUBE + "videoId", "")
    author = entry.findtext(f"{ATOM}author/{ATOM}name", "").strip()
    account = entry.findtext(f"{ATOM}author/{ATOM}uri", "").strip().rstrip("/")
    title = entry.findtext(ATOM + "title", "").strip()
    link = entry.find(f"{ATOM}link[@rel='alternate']")
    if (not title or not author or account != f"https://www.youtube.com/channel/{channel}"
            or entry.findtext(YOUTUBE + "channelId") != channel or link is None):
        raise ValueError("Missing original video metadata")
    published = publication_date(entry.findtext(ATOM + "published", "").strip())
    original, seconds = youtube_origin(link.get("href", ""), identifier)
    description = entry.findtext(f"{MEDIA}group/{MEDIA}description", "").strip()
    if not original or CLIP.search(title):
        return None
    launch = first_party_release(title, description) if official else None
    blog = None
    if launch and channel == DEEPMIND_CHANNEL:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        blog = f"https://deepmind.google/blog/{slug}"
    related = announcement_reference(description, same_title_blog=blog)
    if not launch and not related:
        return None
    announcing = launch and (launch.lower().startswith("today") or "we are announcing " in launch.lower())
    return EvidenceCandidate(
        original, title, author, published, clean(launch or description), verified=bool(launch),
        related_urls=related, announced_at=published if announcing else None, offset_seconds=seconds,
    )


def bilibili_video(entry):
    title = entry.findtext("title", "").strip()
    author = clean(entry.findtext(CREATOR) or entry.findtext("author", ""))
    published = publication_date(entry.findtext("pubDate", "").strip())
    parts = urlsplit(entry.findtext("link", "").strip())
    path = BILIBILI_PATH.fullmatch(parts.path)
    if (not title or not author or parts.scheme not in {"http", "https"}
            or parts.netloc.lower() not in {"bilibili.com", "www.bilibili.com"} or not path):
        raise ValueError("Missing original Bilibili video metadata")
    description = entry.findtext("description", "")
    related = announcement_reference(description)
    if not related or CLIP.search(title):
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    # An offset on another part cannot address the canonical video link's first part.
    seconds = offset(query) if query.get("p", ["1"]) == ["1"] else None
    return EvidenceCandidate(
        f"https://www.bilibili.com/video/{path[1]}", title, author, published, clean(description),
        related_urls=related, offset_seconds=seconds,
    )


def collect_entries(entries, normalize, batch):
    candidates = []
    invalid = 0
    for entry in entries:
        try:
            candidate = normalize(entry)
        except (TypeError, ValueError):
            invalid += 1
        else:
            if candidate:
                candidates.append(candidate)
    if invalid and invalid == len(entries):
        raise ValueError("No video in the feed has verifiable original metadata")
    return batch(candidates, status="partial" if invalid else "ok", checked=len(entries))


def youtube_candidates(url, batch, channel=DEEPMIND_CHANNEL, official=True):
    root = ET.fromstring(read(url))
    if (root.tag != ATOM + "feed"
            or root.findtext(YOUTUBE + "channelId") not in {channel, channel.removeprefix("UC")}):
        raise ValueError("Expected the subscribed YouTube channel")
    return collect_entries(root.findall(ATOM + "entry"), lambda e: youtube_video(e, channel, official), batch)


def bilibili_candidates(url, batch, expected=None):
    if not url:
        return batch((), status="unconfigured", checked=0)
    root = ET.fromstring(read(url))
    channel = root.find("channel")
    if (root.tag != "rss" or channel is None
            or not re.fullmatch(r"https://space\.bilibili\.com/[0-9]+/?", channel.findtext("link", "").strip())):
        raise ValueError("Expected RSS for one Bilibili uploader")
    if expected and channel.findtext("link", "").strip().rstrip("/") != expected.rstrip("/"):
        raise ValueError("Bilibili feed belongs to a different uploader")
    return collect_entries(channel.findall("item"), bilibili_video, batch)


def video_sources(source, batch, *, youtube_url=YOUTUBE_DEEPMIND_ATOM, bilibili_url=None, youtube_accounts=None,
                  bilibili_accounts=()):
    """Discover first-party YouTube releases and same-event Bilibili video references."""
    result = []
    for row in youtube_accounts if youtube_accounts is not None else [{"channel": DEEPMIND_CHANNEL, "official": True}]:
        channel = row["channel"]
        if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel):
            raise ValueError("Invalid YouTube channel ID")
        url = youtube_url if channel == DEEPMIND_CHANNEL else "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel
        identifier = "youtube-deepmind" if channel == DEEPMIND_CHANNEL else "youtube:" + channel
        result.append(source(identifier, "youtube", lambda u=url, c=channel, o=row.get("official") is True:
                             youtube_candidates(u, batch, c, o)))
    for homepage, url in bilibili_accounts:
        if not re.fullmatch(r"https://space\.bilibili\.com/\d+/?", homepage):
            raise ValueError("Invalid Bilibili uploader homepage")
        result.append(source("bilibili:" + homepage.rstrip("/").split("/")[-1], "bilibili",
                             lambda h=homepage, u=url: bilibili_candidates(u, batch, h)))
    if bilibili_url or not bilibili_accounts:
        result.append(source("bilibili", "bilibili", lambda: bilibili_candidates(bilibili_url, batch)))
    return result
