"""Build the Pages preview from arXiv's official new and recent lists."""

import argparse
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from arxiv_lists import CORE, ID, LIST_URL, SUPPLEMENTARY, read_announcements
from arxiv_harvest import BASE as OAI_URL, harvest
from paper_highlights import HIGHLIGHTS_URL, generate_highlights
from paper_topics import TAXONOMY, research_paths

ROOT = Path(__file__).resolve().parents[1]
RSS_URL = "https://rss.arxiv.org/rss/cs.AI"
METADATA_URL = "https://export.arxiv.org/api/query"
ARXIV = "{http://arxiv.org/schemas/atom}"
ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
SHANGHAI = timezone(timedelta(hours=8))
ARXIV_URL = re.compile(r"https?://arxiv\.org/abs/(.+)")
# Keep id_list requests comfortably below the upstream HTTP request-line limit.
METADATA_BATCH_SIZE = 100
CATALOG_PAGE_SIZE = 500


class ArxivSource:
    def __init__(self, rss_url, metadata_url, listing_url):
        self.rss_url = rss_url
        self.metadata_url = metadata_url
        self.listing_url = listing_url
        self.next_request = 0
        self.harvest_since = None

    def read(self, url):
        # Use one request at a time and respect the separate HTML list crawl delay.
        official_list = url.startswith(LIST_URL + "/")
        official = official_list or url == RSS_URL or url.startswith((METADATA_URL + "?", OAI_URL + "?"))
        delay = 15 if official_list else 3
        request = Request(url, headers={
            "User-Agent": "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)",
            "Cache-Control": "no-cache",
        })
        attempts = 3 if official else 1
        for attempt in range(attempts):
            if official:
                sleep(max(0, self.next_request - monotonic()))
                self.next_request = monotonic() + delay
            try:
                with urlopen(request, timeout=45) as response:
                    return response.read()
            except HTTPError as error:
                if attempt + 1 == attempts or (error.code != 429 and error.code < 500):
                    raise
                retry = retry_delay(error.headers.get("Retry-After"))
                if retry > 90:
                    raise
                self.next_request = max(self.next_request, monotonic() + max(retry, 3 * (2 ** attempt)))
            except OSError:
                if attempt + 1 == attempts:
                    raise

    def read_xml(self, url):
        return ET.fromstring(self.read(url))

    def read_official(self):
        announcements = defaultdict(list)
        batches = {}
        verified_ranges = {}
        dates = []
        for category in (*CORE, *SUPPLEMENTARY):
            groups, current_date, verified_ranges[category] = read_announcements(
                lambda url: self.read(url).decode("utf-8"), self.listing_url, category,
            )
            counted = batches[category] = {current_date: {}}
            dates.append(current_date)
            for (date, kind), papers in groups.items():
                dates.append(date)
                counted.setdefault(date, {})[kind] = len(papers)
                for identifier, version in papers.items():
                    event = {"date": date, "category": category, "type": kind}
                    if version:
                        event["version"] = version
                    announcements[identifier].append(event)
            verified = sum(count for batch in counted.values() for kind, count in batch.items() if kind != "replace")
            edge = max(counted, default=current_date)
            print(f"{category}: {verified} new/cross list entries across {len(counted)} dated batches through {edge}", flush=True)
        self.harvest_since = (datetime.fromisoformat(min(dates)).date() - timedelta(days=1)).isoformat()
        return max(dates), announcements, batches, verified_ranges

    def read_legacy_feed(self):
        channel = self.read_xml(self.rss_url).find("channel")
        if channel is None or channel.findtext("title") != "cs.AI updates on arXiv.org":
            raise ValueError("Expected the official cs.AI announcement")
        announcement_date = parsedate_to_datetime(required_text(channel, "pubDate")).astimezone(timezone.utc)
        announced_ids = {
            paper_id(required_text(item, "link"))[0]
            for item in channel.findall("item")
            if item.findtext(ARXIV + "announce_type") in {"new", "cross"}
        }
        if not announced_ids:
            raise ValueError("The cs.AI feed has no new or cross submissions to preview")
        return announcement_date.isoformat(), sorted(announced_ids)

    def read_papers(self, ids):
        if ids and self.harvest_since and self.metadata_url == METADATA_URL:
            return harvest(self.read_xml, ids, self.harvest_since)
        papers = {}
        for start in range(0, len(ids), METADATA_BATCH_SIZE):
            requested_ids = ids[start:start + METADATA_BATCH_SIZE]
            query = urlencode({"id_list": ",".join(requested_ids), "max_results": len(requested_ids)}, safe=",/")
            try:
                feed = self.read_xml(f"{self.metadata_url}?{query}")
            except OSError as error:
                raise ValueError(f"arXiv metadata at {requested_ids[0]} ({start}/{len(ids)}): {error}") from error
            batch = {}
            for entry in feed.findall(ATOM + "entry"):
                paper = metadata(entry)
                identifier = paper["id"]
                if identifier in batch:
                    raise ValueError(f"Duplicate arXiv metadata for {identifier}")
                batch[identifier] = paper
            if batch.keys() != set(requested_ids):
                missing = ", ".join(sorted(set(requested_ids) - batch.keys()))
                raise ValueError(f"The arXiv metadata response did not match the requested IDs (missing: {missing or 'none'})")
            papers.update(batch)
            if len(ids) > METADATA_BATCH_SIZE and (len(papers) == len(ids) or len(papers) % 1000 == 0):
                print(f"Metadata: {len(papers)}/{len(ids)} announcement IDs verified", flush=True)
        return papers

    def read_catalog(self, first_day, last_day):
        beginning = datetime.combine(date.fromisoformat(first_day), time.min, SHANGHAI).astimezone(timezone.utc)
        ending = datetime.combine(date.fromisoformat(last_day) + timedelta(days=1), time.min, SHANGHAI)
        ending = ending.astimezone(timezone.utc)
        categories = " OR ".join(f"cat:{category}" for category in (*CORE, *SUPPLEMENTARY))
        while beginning < ending:
            next_week = min(beginning + timedelta(days=7), ending)
            last_minute = next_week - timedelta(minutes=1)
            query = f"({categories}) AND submittedDate:[{beginning:%Y%m%d%H%M} TO {last_minute:%Y%m%d%H%M}]"
            yield from self.read_catalog_period(query, beginning, next_week)
            beginning = next_week

    def read_catalog_period(self, query, beginning, ending):
        offset = 0
        total = None
        seen = set()
        while True:
            parameters = urlencode({
                "search_query": query, "sortBy": "submittedDate", "sortOrder": "ascending",
                "start": offset, "max_results": CATALOG_PAGE_SIZE,
            })
            feed = self.read_xml(f"{self.metadata_url}?{parameters}")
            reported_total = int(required_text(feed, OPENSEARCH + "totalResults"))
            if total is None:
                total = reported_total
            if reported_total != total or int(required_text(feed, OPENSEARCH + "startIndex")) != offset:
                raise ValueError(f"The arXiv catalog changed during pagination: {query}")
            entries = feed.findall(ATOM + "entry")
            if offset + len(entries) > total or (not entries and offset < total):
                raise ValueError(f"Incomplete catalog page: {query} ({offset}/{total})")
            for item in entries:
                paper = metadata(item)
                published = datetime.fromisoformat(paper["publishedAt"])
                if paper["id"] in seen or not beginning <= published < ending:
                    raise ValueError(f"Unexpected arXiv catalog record: {paper['id']}")
                seen.add(paper["id"])
                yield paper
            offset += len(entries)
            if offset == total:
                break
        print(f"Catalog {beginning.date()} to {ending.date()} UTC: {total} records")


def paper_id(link):
    url = ARXIV_URL.fullmatch(link)
    match = ID.fullmatch(url[1]) if url else None
    if not match:
        raise ValueError(f"Invalid arXiv abstract URL: {link}")
    return match.groups()


def retry_delay(header):
    if not header:
        return 0
    if header.isdecimal():
        return int(header)
    try:
        return (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return 0


def metadata(entry):
    identifier, version = paper_id(required_text(entry, ATOM + "id"))
    published_at = required_text(entry, ATOM + "published")
    published = datetime.fromisoformat(published_at).astimezone(SHANGHAI).date().isoformat()
    authors = [required_text(author, ATOM + "name") for author in entry.findall(ATOM + "author")]
    paper = {
        "id": identifier,
        "version": version,
        "title": required_text(entry, ATOM + "title"),
        "summary": required_text(entry, ATOM + "summary"),
        "source": "arXiv" + (f" · {', '.join(authors)}" if authors else ""),
        "published": published,
        "publishedAt": published_at,
        "original": f"https://arxiv.org/abs/{identifier}",
    }
    if updated := entry.findtext(ATOM + "updated"):
        paper["updatedAt"] = updated
    if categories := entry.findall(ATOM + "category"):
        paper["categories"] = sorted({node.attrib["term"] for node in categories if node.get("term")})
    primary = entry.find(ARXIV + "primary_category")
    if primary is not None and primary.get("term"):
        paper["primaryCategory"] = primary.attrib["term"]
    return paper


def required_text(element, tag):
    text = " ".join(element.findtext(tag, "").split())
    if not text:
        raise ValueError(f"Missing arXiv field {tag}")
    return text


def publication_window(today):
    month_index = today.year * 12 + today.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    first_day = today.replace(year=year, month=month, day=min(today.day, monthrange(year, month)[1]))
    return first_day.isoformat(), today.isoformat()


def catalog_backfill_usable(marker, today):
    if not isinstance(marker, dict):
        return False
    try:
        through = date.fromisoformat(marker["through"])
    except (KeyError, TypeError, ValueError):
        return False
    first, last = publication_window(today)
    return (marker["through"] == through.isoformat() and first <= marker["through"] <= last
            and marker.get("from") == publication_window(through)[0])


def active_announcement_gaps(gaps, today):
    first, last = publication_window(today)
    active = {}
    if not isinstance(gaps, dict):
        raise ValueError("Expected a map of unverified arXiv announcement dates")
    for category, dates in gaps.items():
        if category not in (*CORE, *SUPPLEMENTARY) or not isinstance(dates, list) or any(
            not isinstance(day, str) or date.fromisoformat(day).isoformat() != day for day in dates
        ):
            raise ValueError("Invalid unverified arXiv announcement dates")
        if retained := sorted({day for day in dates if first <= day <= last}):
            active[category] = retained
    return active


def build_preview(output, source, *, legacy_feed=False, highlights_url=HIGHLIGHTS_URL, backfill=False):
    now = datetime.now(timezone.utc)
    first_day, last_day = publication_window(now.astimezone(SHANGHAI).date())

    data_path = output / "papers.json"
    prior = json.loads(data_path.read_text(encoding="utf-8")) if data_path.is_file() else {}
    papers = {paper["id"]: paper for paper in prior.get("papers", [])}
    catalog = prior.get("catalogBackfill")

    if legacy_feed:
        announcement_date, announced_ids = source.read_legacy_feed()
        announcements = {}
    else:
        announcement_date, announcements, batches, verified_ranges = source.read_official()
        for category, dates in prior.get("announcementBatches", {}).items():
            latest = max(batches.get(category, {}), default="")
            if dates and latest < max(dates):
                raise ValueError(f"The arXiv {category} list regressed from {max(dates)} to {latest or 'an empty list'}")
        announced_ids = sorted(
            identifier for identifier, events in announcements.items()
            if identifier in papers or any(event["type"] != "replace" for event in events)
        )
    new_papers = {}
    if backfill:
        for paper in source.read_catalog(first_day, last_day):
            new_papers[paper["id"]] = paper
        catalog = {"from": first_day, "through": last_day}
    metadata_ids = set(announced_ids) - new_papers.keys()
    if not legacy_feed:
        # Modern arXiv identifiers encode the first submission month in YYMM.
        first_month = first_day[2:4] + first_day[5:7]
        metadata_ids = {i for i in metadata_ids if i[:4].isdigit() and i[:4] >= first_month}
        # arXiv's recent list repeats previously verified announcements for several days.
        # An unseen announcement (including a replacement) still needs fresh Atom metadata.
        metadata_ids = {identifier for identifier in metadata_ids
                        if not known_announcements(papers.get(identifier), announcements[identifier])}
        # The earlier cs.AI preview has no Atom classification fields.
        metadata_ids.update(
            identifier for identifier, paper in papers.items()
            if first_day <= paper["published"] <= last_day and not paper.get("categories") and identifier not in new_papers
        )
    new_papers.update(source.read_papers(sorted(metadata_ids)))
    collection_time = datetime.now(timezone.utc).isoformat()
    for identifier, paper in new_papers.items():
        previous = papers.get(identifier)
        old_version = version_number(previous)
        new_version = version_number(paper)
        if previous and old_version > new_version:
            paper = previous.copy()
        paper["collected"] = previous["collected"] if previous else collection_time
        if previous:
            if "publishedAt" in previous:
                paper.update(published=previous["published"], publishedAt=previous["publishedAt"])
            if previous.get("revision"):
                paper["revision"] = previous["revision"]
            if previous.get("highlights"):
                paper["highlights"] = previous["highlights"]
            if old_version and new_version > old_version:
                paper["revision"] = {"version": paper["version"], "updatedAt": paper.get("updatedAt")}
        if not legacy_feed:
            if not all(paper.get(field) for field in ("version", "updatedAt", "primaryCategory", "categories")):
                raise ValueError(f"Incomplete arXiv metadata for {identifier}")
            events = announcements.get(identifier, [])
            paper["announcements"] = merge_announcements(
                previous.get("announcements", []) if previous else [], events, first_day,
            )
            core = (
                (previous and previous.get("coverage", "core") == "core")
                or any(event["category"] in CORE for event in events if event["type"] != "replace")
                or any(category in CORE for category in paper.get("categories", []))
            )
            paper["coverage"] = "core" if core else "supplementary"
            paper["paths"] = research_paths(paper, core)
            if not paper["paths"]:
                papers.pop(identifier, None)
                continue
        papers[identifier] = paper
    retained_papers = [paper for paper in papers.values() if first_day <= paper["published"] <= last_day]
    retained_papers.sort(key=lambda paper: (paper["published"], paper["collected"], paper["id"]), reverse=True)
    if not legacy_feed:
        generate_highlights(retained_papers, highlights_url, os.environ.get("OPENROUTER_API_KEY"))

    output.mkdir(parents=True, exist_ok=True)
    payload = {"announcementDate": announcement_date, "checkedAt": collection_time, "papers": retained_papers}
    if not legacy_feed:
        payload["taxonomy"] = TAXONOMY
        old_batches = prior.get("announcementBatches", {})
        old_gaps = active_announcement_gaps(prior.get("announcementGaps", {}), now.astimezone(SHANGHAI).date())
        payload["announcementBatches"] = merge_batches(old_batches, batches, first_day)
        payload["announcementGaps"] = unresolved_announcement_dates(old_batches, old_gaps, verified_ranges, first_day)
        if catalog:
            payload["catalogBackfill"] = catalog
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as published:
        temporary_path = Path(published.name)
        json.dump(payload, published, ensure_ascii=False, indent=2)
        published.write("\n")
    temporary_path.replace(data_path)
    print(f"arXiv {announcement_date}: {len(announced_ids)} announcement IDs, {len(retained_papers)} retained papers")
    print(f"Preview: {output.resolve()}")


def known_announcements(paper, events):
    if not paper or not all(paper.get(k) for k in ("version", "categories", "primaryCategory", "updatedAt")):
        return False
    old = {(e["date"], e["category"], e["type"]): e.get("version")
           for e in paper.get("announcements", [])}
    for e in events:
        kinds = ("new", "cross", "new-or-cross") if e["type"] == "new-or-cross" else (e["type"],)
        keys = [(e["date"], e["category"], kind) for kind in kinds]
        if not any(key in old and (not e.get("version") or e["version"] == old[key]) for key in keys):
            return False
    return bool(events)


def merge_announcements(previous, current, first_day):
    dated = {}
    for event in [*previous, *current]:
        if event["date"] < first_day:
            continue
        key = (event["date"], event["category"], event["type"] == "replace")
        existing = dated.get(key)
        if not existing or event["type"] != "new-or-cross":
            if existing and existing["type"] == event["type"] and existing.get("version") and not event.get("version"):
                event = {**event, "version": existing["version"]}
            dated[key] = event
    return [dated[key] for key in sorted(dated)]


def merge_batches(previous, current, first_day):
    coverage = {}
    for category, dates in current.items():
        old = previous.get(category, {})
        combined = old.copy()
        for date, counts in dates.items():
            merged = combined.get(date, {}) | counts
            if "new" in merged or "cross" in merged:
                merged.pop("new-or-cross", None)
            combined[date] = merged
        coverage[category] = {date: combined[date] for date in sorted(combined) if date >= first_day}
    return coverage


def unresolved_announcement_dates(previous, gaps, verified_ranges, first_day):
    incomplete = {}
    for category, ranges in verified_ranges.items():
        pending = set(gaps.get(category, []))
        if old := previous.get(category):
            start = max(date.fromisoformat(max(old)) + timedelta(days=1), date.fromisoformat(first_day))
            end = date.fromisoformat(max(through for _, through in ranges))
            pending.update((start + timedelta(days=offset)).isoformat() for offset in range((end - start).days))
        # The complete recent list verifies its own dated span. A separate new page
        # can verify its day without proving the interval between the two lists.
        outstanding = sorted(day for day in pending if not any(start <= day <= end for start, end in ranges))
        if outstanding:
            incomplete[category] = outstanding
            print(f"{category}: {len(outstanding)} unverified announcement dates from {outstanding[0]}")
    return incomplete


def version_number(paper):
    version = paper.get("version") if paper else None
    return int(version[1:]) if version else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "AIfrontier")
    parser.add_argument("--rss-url", help="Use the original single-feed preview for a supplied cs.AI feed")
    parser.add_argument("--listing-url", default=LIST_URL)
    parser.add_argument("--metadata-url", default=METADATA_URL)
    parser.add_argument("--highlights-url", default=HIGHLIGHTS_URL)
    parser.add_argument("--backfill", action="store_true", help="Fill the publication window from the official arXiv catalog")
    arguments = parser.parse_args()
    if arguments.backfill and arguments.rss_url:
        parser.error("--backfill requires the official lists")
    try:
        build_preview(
            arguments.output, ArxivSource(arguments.rss_url, arguments.metadata_url, arguments.listing_url),
            legacy_feed=bool(arguments.rss_url), highlights_url=arguments.highlights_url, backfill=arguments.backfill,
        )
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"Preview build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
