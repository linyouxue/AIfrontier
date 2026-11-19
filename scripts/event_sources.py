"""Read public evidence for AI releases from the configured publishers."""

from dataclasses import KW_ONLY, dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
import ssl
import urllib.request
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request
import xml.etree.ElementTree as ET

from build_preview import SHANGHAI, publication_window


OPENAI_RSS = "https://openai.com/news/rss.xml"
ANTHROPIC_NEWS = "https://www.anthropic.com/news"
CLAUDE_RELEASE_NOTES = "https://platform.claude.com/docs/en/release-notes/overview"
DEEPSEEK_UPDATES = "https://api-docs.deepseek.com/zh-cn/updates/"
DIGEST_RSS = "https://lijunliu-gh.github.io/ai-news-digest/feed.xml"
DIGEST_NAME = "AI News Digest"
USER_AGENT = "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)"
TRACKING = {"trk", "_bhlid", "fbclid", "gclid"}
AVAILABLE = re.compile(r"\b(?:now (?:generally )?(?:available|live)|is rolling out (?:today|now)|available (?:starting )?today)\b", re.I)
RELEASE = re.compile(r"\b(?:introduc(?:e[sd]?|ing)|launch(?:e[sd]?|ing)?|releas(?:e[sd]?|ing)|now (?:available|live|support|can)|"
                     r"rolling out|(?:is|are) expanding|expands? (?:to|across)|files S-1)\b", re.I)
CLAUDE_MODEL = re.compile(r"\bClaude (Fable|Mythos|Opus|Sonnet|Haiku) (\d+[.]\d+)\b", re.I)
CLAUDE_LAUNCH = re.compile(r"^(?:We[’']ve launched |We (?:released|launched) |Released )\*\*Claude ", re.I)
ANTHROPIC_ROWS = re.compile(r'<li\b[^>]*\bdata-listing-row="[^"]+"[^>]*>(.*?)</li>', re.S)
DEEPSEEK_SECTIONS = re.compile(r'<h([23])\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>', re.S)
DEEPSEEK_LIVE = re.compile(r"已(?:同步)?上线|今天.{0,12}开放|已在.{0,55}支持|已同步更新至|已升级至")
DEEPSEEK_ANNOUNCEMENT = re.compile(r'/zh-cn/news/news\d{6}/?')


@dataclass(frozen=True)
class EvidenceCandidate:
    url: str
    title: str
    source: str
    published: str
    summary: str
    _: KW_ONLY
    verified: bool = False
    related_urls: tuple = ()
    announced_at: str | None = None
    available_at: str | None = None
    offset_seconds: int | None = None
    release_key: str | None = None
    provenance: str | None = None
    points: tuple = ()


def open_public(request, timeout):
    # On some hosts the configured outbound proxy drops TLS to these public origins.
    # Retry only a broken connection; an HTTP response is authoritative.
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except URLError as error:
        if (urlsplit(request.full_url).hostname not in {"mp.weixin.qq.com", "api.bilibili.com", "search.bilibili.com"}
                or not isinstance(error.reason, (ssl.SSLEOFError, ConnectionResetError))
                or not urllib.request.getproxies()):
            raise
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        headers = {key: value for key, value in request.header_items() if key.lower() in {"user-agent", "cache-control"}}
        return direct.open(Request(request.full_url, headers=headers), timeout=timeout)


def read(url):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    timeout = 95 if urlsplit(url).hostname == "127.0.0.1" else 20
    with open_public(request, timeout=timeout) as response:
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise ValueError("Upstream response exceeded 4 MB")
    return body


def clean(text):
    return " ".join(unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", text)).replace("\u200b", "").split())


def public_host(value):
    if not isinstance(value, str) or not value or re.search(r"[\s\x00-\x20\x7f\\]", value):
        return None
    try:
        link = urlsplit(value)
        if link.scheme == "https" and link.username is None and link.hostname and (
            link.port is None or link.port > 0
        ):
            return link.hostname
    except ValueError:
        pass
    return None


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected a public evidence URL")
    if any(char.isspace() for char in url):
        raise ValueError("Expected a public evidence URL")
    query = sorted((name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not (name.lower().startswith("utm_") or name.lower() in TRACKING))
    host = parts.hostname.removeprefix("www.")
    if parts.port is not None and parts.port not in {80, 443}:
        host = parts.netloc.lower()
    return urlunsplit(("https", host, parts.path.rstrip("/") or "/", urlencode(query), parts.fragment))


def public_event_urls(items):
    return {
        canonical_url(url)
        for item in items
        for url in (item["original"], *(entry["url"] for entry in item.get("evidence", [])))
        if public_host(url)
    }


def publication_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    try:
        published = datetime.fromisoformat(value)
    except ValueError:
        published = parsedate_to_datetime(value)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.astimezone(SHANGHAI).date().isoformat()


def rss_items(url, expected_title):
    root = ET.fromstring(read(url))
    channel = root.find("channel")
    if root.tag != "rss" or channel is None or channel.findtext("title", "").strip() != expected_title:
        raise ValueError("Expected the configured RSS feed")
    for item in channel.findall("item"):
        title = clean(item.findtext("title", ""))
        original = item.findtext("link", "").strip()
        published = item.findtext("pubDate", "").strip()
        if not (title and original and published):
            raise ValueError("RSS item missing required evidence")
        yield (title, original, publication_date(published), item.findtext("category", "").strip(),
               clean(item.findtext("description", "")))


def openai_news(url):
    for title, original, published, category, summary in rss_items(url, "OpenAI News"):
        target = urlsplit(canonical_url(original))
        if target.hostname != "openai.com" or not target.path.startswith("/index/"):
            continue
        if category not in {"Product", "Release", "API"} or not summary or not RELEASE.search(f"{title} {summary}"):
            continue
        yield EvidenceCandidate(original, title, "OpenAI News", published, summary, verified=True,
                                announced_at=published, available_at=published if AVAILABLE.search(title + " " + summary) else None)


def news_digest(url):
    # Feed prose can supplement primary evidence. It cannot establish publication on its own.
    for title, original, published, _, summary in rss_items(url, DIGEST_NAME):
        yield EvidenceCandidate(original, title, DIGEST_NAME, published, summary, provenance=DIGEST_NAME)


def claude_release_key(names):
    # A named model family and exact version are explicit identifiers in both first-party sources.
    identifiers = {f"{family.lower()}-{version}" for name in names for family, version in CLAUDE_MODEL.findall(name)}
    return "anthropic:model:" + "+".join(sorted(identifiers)) if identifiers else None


def anthropic_news(url):
    rows = ANTHROPIC_ROWS.findall(read(url).decode("utf-8"))
    if not rows:
        raise ValueError("Anthropic Newsroom is missing its dated article listing")
    for row in rows:
        link = re.search(r'<a\b[^>]*class="[^"]*_title-link"[^>]*href="([^"]+)"[^>]*>.*?<h3\b[^>]*>(.*?)</h3>', row, re.S)
        when = re.search(r"<time\b[^>]*>(.*?)</time>", row, re.S)
        description = re.search(r'<a\b[^>]*class="[^"]*_details-description"[^>]*>\s*<p\b[^>]*>(.*?)</p>', row, re.S)
        if not link or not when:
            raise ValueError("Anthropic article missing title, link, or date")
        title = clean(link[2])
        summary = clean(description[1]) if description else ""
        original = urljoin(ANTHROPIC_NEWS, unescape(link[1]))
        if not summary or not RELEASE.search(f"{title} {summary}") or urlsplit(original).hostname != "www.anthropic.com":
            continue
        published = datetime.strptime(clean(when[1]), "%b %d, %Y").date().isoformat()
        key = claude_release_key([title]) if title.startswith("Introducing Claude ") else None
        yield EvidenceCandidate(original, title, "Anthropic Newsroom", published, summary, verified=True,
                                release_key=key, announced_at=published,
                                available_at=published if AVAILABLE.search(title + " " + summary) else None)


def markdown_text(text):
    return clean(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text).replace("**", "").replace("`", ""))


def anthropic_notes(url):
    text = read(url).decode("utf-8")
    days = list(re.finditer(r"^### ([A-Z][a-z]+ \d{1,2}, \d{4})\s*$", text, re.M))
    if not text.startswith("---\ntitle: Claude Platform release notes") or not days:
        raise ValueError("Expected dated Claude Platform release notes")
    for index, day in enumerate(days):
        date_value = datetime.strptime(day[1], "%B %d, %Y").date()
        published = date_value.isoformat()
        anchor = f"{date_value:%B}-{date_value.day}-{date_value.year}".lower()
        section_end = days[index + 1].start() if index + 1 < len(days) else len(text)
        for bullet in re.findall(r"^\* (.+)$", text[day.end():section_end], re.M):
            if not CLAUDE_LAUNCH.match(bullet):
                continue
            names = [name for name in re.findall(r"\*\*(.*?)\*\*", bullet) if CLAUDE_MODEL.fullmatch(name)]
            key = claude_release_key(names)
            if not key:
                continue
            summary = markdown_text(bullet)
            notes_page = f"{CLAUDE_RELEASE_NOTES}?entry={anchor}"
            yield EvidenceCandidate(notes_page, " and ".join(names), "Claude Platform Release Notes",
                                    published, summary[:700], verified=True, release_key=key,
                                    available_at=published if re.search(r"\bis available (?:on|in|to)\b", summary) else None)
            # The publisher addresses each day's section as one evidence document.
            break


def paragraphs(html):
    return [paragraph for segment in re.findall(r"<p\b[^>]*>(.*?)</p>", html, re.S) if (paragraph := clean(segment))]


def deepseek_announcement(source_url, path, published, note_url, available):
    article = read(urljoin(source_url, path)).decode("utf-8")
    main = re.search(r"<article\b[^>]*>(.*?)</article>", article, re.S)
    title = re.search(r"<h1\b[^>]*>(.*?)</h1>", main[1], re.S) if main else None
    body = paragraphs(main[1]) if main else []
    if not title or not body:
        raise ValueError("DeepSeek announcement missing title or body")
    lead = next((line for line in body[:3] if re.search(r"正式发布|正式开放|已(?:同步)?上线", line)), body[0])
    return EvidenceCandidate(urljoin(DEEPSEEK_UPDATES, path), clean(title[1]), "DeepSeek API Docs",
                             published, lead[:260], verified=True, related_urls=(note_url,),
                             announced_at=published, available_at=available, points=(lead[:260],))


def deepseek_updates(url):
    html = read(url).decode("utf-8")
    sections = list(DEEPSEEK_SECTIONS.finditer(html))
    if not sections or not any(section[1] == "2" and section[2].startswith("时间-") for section in sections):
        raise ValueError("Expected the dated Chinese DeepSeek changelog")
    first, today = publication_window(datetime.now(SHANGHAI).date())
    published = ""
    for index, heading in enumerate(sections):
        level, fragment, label = heading.groups()
        title = clean(label.split("<a ", 1)[0])
        if level == "2":
            day = re.fullmatch(r"时间:\s*(\d{4}-\d{2}-\d{2})", title)
            published = publication_date(day[1]) if day else ""
            continue
        if not first <= published <= today:
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(html)
        section = html[heading.end():end].split("</article>", 1)[0]
        summary = paragraphs(section)
        if not summary:
            raise ValueError("DeepSeek release note missing its description")
        note_url = f"{DEEPSEEK_UPDATES}#{fragment}"
        available = published if DEEPSEEK_LIVE.search(" ".join(summary)) else None
        for href in re.findall(r'\bhref="([^"]+)"', section):
            path = urlsplit(unescape(href)).path
            if not DEEPSEEK_ANNOUNCEMENT.fullmatch(path) or not path.rstrip("/").endswith(published[2:].replace("-", "")):
                continue
            try:
                yield deepseek_announcement(url, path, published, note_url, available)
            except (OSError, ValueError):
                # The independent changelog is still usable if its linked article is temporarily unavailable.
                print("AI events deepseek-updates: linked announcement unavailable")
            break
        yield EvidenceCandidate(note_url, title, "DeepSeek 更新日志", published, summary[0][:260],
                                verified=True, available_at=available, points=(summary[0][:260],))
