"""Read and reconcile the complete dated sections of arXiv's official lists."""

from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlencode


LIST_URL = "https://export.arxiv.org/list"
CORE = ("cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML", "cs.RO", "cs.MA")
SUPPLEMENTARY = ("cs.IR", "cs.HC", "cs.NE", "cs.SD")
ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?")
COUNTS = re.compile(r"(.+?) \((?:continued, )?showing (?:first )?([\d,]+) of ([\d,]+) entries\s*\)")
NEW_TYPES = {"New submissions": "new", "Cross submissions": "cross", "Replacement submissions": "replace"}
NEW_DATE = "Showing new listings for "
PAGE_SIZE = 2000
CACHE_SLOT = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")


class ListPage(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.totals = set()
        self.date = None
        self.sections = []
        self.capture = None
        self.parts = []
        self.feed(content)
        self.close()
        if len(self.totals) != 1:
            raise ValueError("The arXiv list does not have a consistent entry total")
        self.total = self.totals.pop()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"h2", "h3"} or (tag == "div" and attributes.get("class") == "paging"):
            self.capture = tag
            self.parts = []
        if tag == "a" and attributes.get("title") == "Abstract":
            link = attributes.get("href", "")
            match = ID.fullmatch(link.removeprefix("/abs/")) if link.startswith("/abs/") else None
            if not self.sections or not match:
                raise ValueError(f"Unexpected abstract link in arXiv list: {link}")
            self.sections[-1][3].append(match.groups())

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.capture != tag:
            return
        self.capture = None
        text = " ".join("".join(self.parts).split())
        if tag == "div":
            if match := re.match(r"Total of ([\d,]+) entries\b", text, re.IGNORECASE):
                self.totals.add(number(match[1]))
        elif text.startswith(NEW_DATE):
            self.date = datetime.strptime(text[len(NEW_DATE):], "%A, %d %B %Y").date().isoformat()
        elif match := COUNTS.fullmatch(text):
            self.sections.append((match[1], number(match[2]), number(match[3]), []))


def number(value):
    return int(value.replace(",", ""))


def read_list(read, base, category, view):
    url = f"{base}/{category}/{view}"
    total = offset = 0
    batch_date = None
    groups = defaultdict(dict)
    declared = {}
    last_section = None
    while True:
        parameters = {"skip": offset, "show": PAGE_SIZE}
        if base == LIST_URL:
            # This mirror can serve different, days-old snapshots for distinct list URLs.
            parameters["aifrontier"] = CACHE_SLOT
        page = ListPage(read(f"{url}?{urlencode(parameters)}"))
        if not offset:
            total, batch_date = page.total, page.date
        if page.total != total or batch_date != page.date or (view == "new" and not page.date):
            raise ValueError(f"The arXiv list changed during pagination: {url}")
        rows = 0
        for title, shown, expected, identifiers in page.sections:
            if view == "new" and title in NEW_TYPES:
                key = (batch_date, NEW_TYPES[title])
            elif view == "recent":
                key = (datetime.strptime(title, "%a, %d %b %Y").date().isoformat(), "new-or-cross")
            else:
                raise ValueError(f"Unrecognized section of {url}: {title}")
            if last_section and key != last_section and key in declared:
                raise ValueError(f"Non-contiguous section of {url}: {title}")
            last_section = key
            if shown != len(identifiers) or declared.setdefault(key, expected) != expected:
                raise ValueError(f"Incomplete section of {url}: {title}")
            for identifier, version in identifiers:
                if identifier in groups[key]:
                    raise ValueError(f"Duplicate paper in {url}: {identifier}")
                groups[key][identifier] = version
            rows += shown
        offset += rows
        if offset == total:
            break
        if not rows or offset > total:
            raise ValueError(f"Incomplete arXiv list: {url} ({offset}/{total})")
    if any(len(groups[key]) != expected for key, expected in declared.items()):
        raise ValueError(f"Missing arXiv entries across the pages of {url}")
    return groups, batch_date


def read_announcements(read, base, category):
    recent, _ = read_list(read, base, category, "recent")
    current, batch_date = read_list(read, base, category, "new")
    dates = [day for day, _ in recent]
    verified = [(batch_date, batch_date)]
    if dates:
        verified.append((min(dates), max(dates)))
    recent_key = (batch_date, "new-or-cross")
    if recent_key in recent:
        dated_ids = current.get((batch_date, "new"), {}) | current.get((batch_date, "cross"), {})
        if recent.pop(recent_key).keys() != dated_ids.keys():
            raise ValueError(f"The official new and recent lists disagree for {category} on {batch_date}")
    return recent | current, batch_date, verified
