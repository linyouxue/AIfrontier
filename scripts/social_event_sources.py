"""Read an authorized single-account feed for each social platform."""

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from event_sources import EvidenceCandidate, canonical_url, clean, publication_date, read


ATOM = "{http://www.w3.org/2005/Atom}"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
RSS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
XHS_PROFILE = re.compile(r"/user/profile/([0-9a-f]{24})")
WEIBO_PROFILE = re.compile(r"/(?:u/)?([1-9][0-9]{4,19})")
XHS_NOTE = re.compile(r"/(?:(?:explore|discovery/item)|user/profile/([0-9a-f]{24}))/([0-9a-f]{24})")
WEIBO_POST = re.compile(r"/([1-9][0-9]{4,19})/([A-Za-z0-9]{5,20})")
WEIBO_MOBILE_POST = re.compile(r"/(?:status|detail)/([A-Za-z0-9]{5,20})")
TITLE_SUFFIXES = {"xiaohongshu": (" - 笔记 • 小红书 / RED", " - 小红书笔记"), "weibo": ("的微博",)}
PRIMARY_ANNOUNCEMENTS = {
    "openai.com": re.compile(r"/index/[a-z0-9][a-z0-9-]*"),
    "anthropic.com": re.compile(r"/news/[a-z0-9][a-z0-9-]*"),
    "api-docs.deepseek.com": re.compile(r"/zh-cn/(?:news/news[0-9]{6}|updates#[\w-]+)"),
    "platform.claude.com": re.compile(r"/docs/en/release-notes/overview\?entry=[a-z0-9-]+"),
}
TEXT_URL = re.compile(r"https://[^\s<>\"'，。；、）]+")
REFERENCE_LABEL = re.compile(r"发布(?:公告|原文)|(?:官网|官方)(?:公告|原文)|\bofficial (?:announcement|release notes)\b", re.I)
OWN_ANNOUNCEMENT = re.compile(
    r"(?:^|[。！？.!?]\s*)(?:今天[，,]\s*)?(?:我们|我司)(?:\s|今天|今日|正式|刚刚|已|现){0,5}(?:发布|上线|开放|开源)"
    r"|(?:^|[.!?]\s*)(?:today,\s*)?we (?:have )?(?:just |officially )?(?:launched|released|introduced|open-sourced)\b",
    re.I,
)
AI_SUBJECT = re.compile(r"模型|人工智能|智能体|推理|\b(?:AI|LLMs?|API|agents?|inference|models?)\b", re.I)
SECONDARY = re.compile(r"🔁|转发|转载|转述|据传|传闻|爆料|预告|\b(?:repost|retweet|rumou?r)\b", re.I)


class PostContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.links = []
        self.excluded = 0
        self.in_link = False

    def handle_starttag(self, tag, attrs):
        name = tag.rsplit(":", 1)[-1]
        if name in {"blockquote", "script", "style"}:
            self.excluded += 1
        elif name == "a" and not self.excluded:
            self.in_link = True
            if href := dict(attrs).get("href"):
                self.links.append(href)

    def handle_endtag(self, tag):
        name = tag.rsplit(":", 1)[-1]
        if name in {"blockquote", "script", "style"} and self.excluded:
            self.excluded -= 1
        elif name == "a" and not self.excluded:
            self.in_link = False

    def handle_data(self, data):
        if not self.excluded:
            self.text.append(data)
            if not self.in_link:
                self.links.extend(TEXT_URL.findall(data))


def profile_url(platform, value):
    if not value:
        return None
    parts = urlsplit(canonical_url(value))
    if platform == "xiaohongshu":
        profile = XHS_PROFILE.fullmatch(parts.path)
        if parts.netloc == "xiaohongshu.com" and profile:
            return f"https://www.xiaohongshu.com/user/profile/{profile[1]}"
    else:
        profile = WEIBO_PROFILE.fullmatch(parts.path)
        if parts.netloc in {"weibo.com", "m.weibo.cn"} and profile:
            return f"https://weibo.com/u/{profile[1]}"
    return None


def atom_links(node, relation):
    return [link.get("href", "").strip() for link in node.findall(f"{ATOM}link")
            if link.get("rel", "alternate") == relation]


def feed_entries(platform, url):
    root = ET.fromstring(read(url))
    if root.tag == "rss" and (channel := root.find("channel")) is not None:
        source = channel.findtext("link", "").strip()
        entries = channel.findall("item")
        title = channel.findtext("title", "")
    elif root.tag == f"{ATOM}feed":
        links = atom_links(root, "alternate")
        source = links[0] if len(links) == 1 else ""
        entries = root.findall(f"{ATOM}entry")
        title = root.findtext(f"{ATOM}title", "")
    else:
        raise ValueError("Expected a platform RSS or Atom feed")
    profile = profile_url(platform, source)
    if not profile or not title.strip():
        raise ValueError("Expected a named platform account feed")
    if (platform == "xiaohongshu" and "/xiaohongshu/user/" in urlsplit(url).path
            and not title.endswith(" - 笔记 • 小红书 / RED")):
        raise ValueError("This note listing lacks original publication times")
    name = clean(title)
    for suffix in TITLE_SUFFIXES[platform]:
        if name.endswith(suffix):
            name = name.removesuffix(suffix).strip()
            break
    return profile, name, entries


def post_url(platform, profile, value):
    parts = urlsplit(canonical_url(value))
    owner = profile.rsplit("/", 1)[1]
    if platform == "xiaohongshu":
        note = XHS_NOTE.fullmatch(parts.path)
        if parts.netloc == "xiaohongshu.com" and note and note[1] in {None, owner}:
            return f"https://www.xiaohongshu.com/explore/{note[2]}", note[1] == owner
    else:
        post = WEIBO_POST.fullmatch(parts.path)
        if parts.netloc == "weibo.com" and post and post[1] == owner:
            return f"https://weibo.com/{owner}/{post[2]}", True
        mobile_post = WEIBO_MOBILE_POST.fullmatch(parts.path)
        if parts.netloc == "m.weibo.cn" and mobile_post:
            return f"https://m.weibo.cn{parts.path}", False
    raise ValueError("Expected a platform original from the subscribed account")


def original_timestamp(value):
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value).isoformat()
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        instant = parsedate_to_datetime(value)
    if instant.tzinfo is None:
        raise ValueError("Expected the original publication timezone")
    return instant.isoformat()


def related_announcement(links):
    primary = set()
    for link in links:
        try:
            normalized = canonical_url(link.rstrip(".,;:!?)］]"))
        except ValueError:
            continue
        parts = urlsplit(normalized)
        pattern = PRIMARY_ANNOUNCEMENTS.get(parts.netloc)
        target = parts.path
        if parts.netloc == "api-docs.deepseek.com" and parts.fragment:
            target += f"#{parts.fragment}"
        elif parts.netloc == "platform.claude.com" and parts.query:
            target += f"?{parts.query}"
        if parts.query and parts.netloc != "platform.claude.com":
            continue
        if pattern and pattern.fullmatch(target):
            primary.add(normalized)
    return tuple(primary) if len(primary) == 1 else ()


def atom_body(entry):
    node = entry.find(f"{ATOM}content")
    if node is None:
        node = entry.find(f"{ATOM}summary")
    if node is None:
        return ""
    return (node.text or "") + "".join(ET.tostring(child, encoding="unicode") for child in node)


def social_candidate(platform, profile, name, official_name, entry):
    if entry.tag == f"{ATOM}entry":
        title = entry.findtext(f"{ATOM}title", "")
        links = atom_links(entry, "alternate")
        link = links[0] if len(links) == 1 else ""
        guid = entry.findtext(f"{ATOM}id", "")
        author = entry.findtext(f"{ATOM}author/{ATOM}name", "")
        author_uri = entry.findtext(f"{ATOM}author/{ATOM}uri", "").strip()
        timestamp = entry.findtext(f"{ATOM}published", "")
        body = atom_body(entry)
    else:
        title = entry.findtext("title", "")
        link = entry.findtext("link", "")
        guid = entry.findtext("guid", "")
        rss_author = entry.findtext("author", "").strip()
        author_uri = rss_author if rss_author.startswith(("http://", "https://")) else ""
        author = entry.findtext(DC_CREATOR) or (name if author_uri else rss_author)
        timestamp = entry.findtext("pubDate", "")
        body = entry.findtext(RSS_CONTENT) or entry.findtext("description", "")

    author = clean(author)
    title = clean(title)
    if not (title and author and link and (author_uri or author == name)):
        raise ValueError("Social evidence needs an original title, link and publisher")
    if author_uri and profile_url(platform, author_uri) != profile:
        raise ValueError("The entry author does not match the subscribed account")
    original, owned = post_url(platform, profile, link.strip())
    if guid.strip().startswith(("http://", "https://")):
        identifier, guid_owned = post_url(platform, profile, guid.strip())
        if identifier.rsplit("/", 1)[-1] != original.rsplit("/", 1)[-1]:
            raise ValueError("The entry link and identifier refer to different posts")
        if guid_owned and not owned:
            original, owned = identifier, True
    published = original_timestamp(timestamp.strip())
    content = PostContent()
    try:
        content.feed(body)
        content.close()
    except AssertionError as error:
        raise ValueError("Invalid social post markup") from error
    summary = clean(" ".join(content.text))
    announcement = OWN_ANNOUNCEMENT.search(summary) if official_name and (owned or author_uri) else None
    verified = bool(announcement
                    and AI_SUBJECT.search(f"{title} {summary}")
                    and not SECONDARY.search(f"{title} {summary}"))
    related = related_announcement(content.links) if REFERENCE_LABEL.search(summary) else ()
    announced = publication_date(published) if verified else None
    return EvidenceCandidate(original, title, official_name if verified else author, published, summary[:400],
                             verified=verified, related_urls=related,
                             announced_at=announced,
                             available_at=announced if verified and announcement[0].endswith(("上线", "开放")) else None)


def social_sources(EventSource, EventSourceBatch, *, xiaohongshu_url=None, weibo_url=None, official_profiles=None,
                   subscriptions=()):
    """Use separately confirmed profile URLs for first-party publication."""
    profiles = dict(official_profiles or {})

    def source(platform, url, expected=None):
        def load():
            if not url:
                return EventSourceBatch((), status="unconfigured")
            profile, name, entries = feed_entries(platform, url)
            if expected and profile != expected:
                raise ValueError("Feed belongs to a different subscribed account")
            candidates = {}
            invalid = 0
            for entry in entries:
                try:
                    candidate = social_candidate(platform, profile, name, profiles.get(profile), entry)
                except ValueError:
                    invalid += 1
                else:
                    candidates.setdefault(candidate.url, candidate)
            if invalid and not candidates:
                raise ValueError("The feed has no attributable posts with an original publication time")
            return EventSourceBatch(candidates.values(), status="partial" if invalid else "ok", checked=len(entries))
        identifier = platform + ":" + expected.rsplit("/", 1)[-1] if expected else platform
        return EventSource(identifier, platform, load)

    configured = []
    for platform, url, homepage, official in subscriptions:
        profile = profile_url(platform, homepage)
        if not profile:
            raise ValueError(f"Invalid {platform} account homepage")
        if official:
            profiles[profile] = official
        configured.append(source(platform, url, profile))
    for platform, url in (("xiaohongshu", xiaohongshu_url), ("weibo", weibo_url)):
        if url or not any(entry.platform == platform for entry in configured):
            configured.append(source(platform, url))
    return configured
