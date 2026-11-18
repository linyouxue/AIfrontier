"""Read dated articles from AI authors' and research teams' own RSS and Atom feeds."""

import argparse
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import gzip
from http.client import HTTPException
from html import unescape
import json
from io import BytesIO
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from collect_public_wechat import ROOT, SHANGHAI, publication_window


AI_TOPIC = re.compile(
    r"\b(?:AI|ML|AGI|LLMs?|GPT\w*|Claude|agents?|inference|transformers?|embeddings?|"
    r"model training|machine learning|neural|deep learning|generative|multimodal|diffusion|"
    r"reinforcement learning|fine.tuning|large language model|robotics|CUDA|PyTorch)\b|"
    r"大模型|机器学习|多模态|深度学习|智能体|神经网络|强化学习|推理模型|预训练", re.I,
)


def read(url):
    request = Request(url, headers={"User-Agent": "AIfrontier/0.1 (+https://github.com/linyouxue/AIfrontier)"})
    with urlopen(request, timeout=20) as response:
        body = response.read(4_000_001)
    if len(body) > 4_000_000:
        raise ValueError("Blog response exceeded 4 MB")
    return body


def clean(value):
    return " ".join(unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", value)).replace("\u200b", "").split())


def public_host(url):
    if not url or re.search(r"[\s\x00-\x20\x7f\\]", url):
        return None
    try:
        link = urlsplit(url)
        if link.scheme == "https" and link.username is None and link.port in (None, 443):
            return link.hostname
    except ValueError:
        pass
    return None


def canonical_url(url):
    link = urlsplit(url)
    tracking = {"trk", "_bhlid", "fbclid", "gclid"}
    query = sorted((k, v) for k, v in parse_qsl(link.query, keep_blank_values=True)
                   if not (k.lower().startswith("utm_") or k.lower() in tracking))
    return urlunsplit(("https", link.hostname.removeprefix("www."), link.path.rstrip("/") or "/", urlencode(query), ""))


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


def written_article(title, summary):
    return (not re.match(r"^(?:podcast|播客)\s*[:：]", title, re.I)
            and not re.search(r"输入密码后阅读|password[- ]protected post", summary, re.I))


def source_bytes(url):
    raw = read(url)
    if raw.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
            raw = stream.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Expanded blog feed exceeded 4 MB")
    return raw


def engineering_articles(source, first, last):
    page = source_bytes(source["feed"]).decode("utf-8")
    cards = re.findall(r"<article\b[^>]*>(.*?)</article>", page, re.S)
    if not cards:
        raise ValueError("Missing engineering article list")
    for card in cards:
        href = re.search(r'href="(/(?:research|engineering)/[\w-]+)"', card)
        title = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.S)
        stamp = re.search(r"<time\b[^>]*>([^<]+)</time>", card)
        if not (href and title and stamp):
            continue
        day = datetime.strptime(stamp[1].strip(), "%b %d, %Y").date().isoformat()
        if not first <= day <= last:
            continue
        url = urljoin(source["feed"], href[1])
        article = source_bytes(url).decode("utf-8")
        summary = re.search(r'<meta\b(?=[^>]*(?:name="description"|property="og:description"))[^>]*?content="([^"]+)"', article)
        if summary:
            yield {"id": "blog:" + canonical_url(url), "original": url, "title": clean(title[1]), "source": source["name"],
                   "kind": source["kind"], "summary": clean(summary[1])[:320], "published": day}


def articles(source, first, last):
    if source.get("format") == "anthropic-engineering":
        yield from engineering_articles(source, first, last)
        return
    root = ET.fromstring(source_bytes(source["feed"]))
    channel = root.find("channel")
    if channel is not None:
        rows = channel.findall("item")
    elif root.tag == "{http://www.w3.org/2005/Atom}feed":
        rows = root.findall("{*}entry")
    else:
        raise ValueError("Expected RSS or Atom")
    publisher = public_host(source["feed"]).removeprefix("www.")
    for row in rows:
        title = " ".join(unescape(row.findtext("{*}title", "")).split())
        date = row.findtext("pubDate") or row.findtext("{*}published")
        if not date or not title:
            continue
        day = publication_date(date)
        if not first <= day <= last:
            continue
        link = (row.findtext("link") or "").strip() or next(
            (node.get("href") for node in row.findall("{*}link") if node.get("rel", "alternate") == "alternate"), "")
        if not link:
            continue
        url = urljoin(source["feed"], link)
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        if (public_host(url) or "").removeprefix("www.") != publisher or "/podcast/" in urlsplit(url).path:
            continue
        categories = [node.get("term") or node.text or "" for node in row.findall("{*}category")]
        if source.get("categories") and not set(categories).intersection(source["categories"]):
            continue
        summary = ""
        for tag in ["description", "{*}summary", "{*}encoded", "{*}content"]:
            node = row.find(tag)
            if node is not None:
                summary = clean(" ".join(node.itertext()))
                if summary and summary != title:
                    break
        if not summary or summary == title or not written_article(title, summary):
            continue
        body = row.findtext("{*}content") or row.findtext("{*}encoded") or ""
        if len(summary) < 48 and body:
            preview = clean(body[:6000])
            if len(preview) > len(summary):
                summary = preview
        if source.get("filterAI"):
            if not AI_TOPIC.search(" ".join([title, *categories, summary[:600], clean(body[:6000])])):
                continue
        yield {"id": "blog:" + canonical_url(url), "original": url, "title": title, "source": source["name"],
               "kind": source["kind"], "summary": summary[:320], "published": day}


def collect(output, sources):
    now = datetime.now(timezone.utc)
    first, last = publication_window(now.astimezone(SHANGHAI).date())
    checked = now.isoformat()
    path = output / "blogs.json"
    previous = json.loads(path.read_text("utf-8")) if path.exists() else {}
    items = {item["id"]: item for item in previous.get("items", [])
             if first <= item["published"] <= last and written_article(item["title"], item["summary"])}
    reports = []
    for source in sources:
        report = {"id": source["name"], "platform": "blog", "kind": source["kind"], "checkedAt": checked,
                  "inserted": 0, "matched": 0}
        try:
            found = list(articles(source, first, last))
        except (OSError, HTTPException, ValueError, ET.ParseError) as error:
            report.update(status="failed", error=f"HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__)
        else:
            report.update(status="ok", discovered=len(found), lastSuccessAt=checked)
            for item in found:
                old = items.get(item["id"])
                items[item["id"]] = item | {"collected": old["collected"] if old else checked}
                report["matched" if old else "inserted"] += 1
        reports.append(report)
    output.mkdir(parents=True, exist_ok=True)
    ordered = sorted(items.values(), key=lambda i: (i["published"], i["id"]), reverse=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as file:
        json.dump({"items": ordered, "sources": reports}, file, ensure_ascii=False)
    Path(file.name).replace(path)
    print(f"{len(items)} articles from {sum(r['status'] == 'ok' for r in reports)}/{len(reports)} responding feeds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "wechat-preview")
    collect(parser.parse_args().output, json.loads((ROOT / "config" / "blogs.json").read_text("utf-8")))
