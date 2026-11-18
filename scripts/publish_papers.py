"""Refresh the official arXiv catalog and its public reading index."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
import gzip
import json
import os
from pathlib import Path
from shutil import copyfile
import sys
from zoneinfo import ZoneInfo

from arxiv_lists import CORE, SUPPLEMENTARY, LIST_URL
from build_preview import (ArxivSource, METADATA_URL, ROOT, SHANGHAI, build_preview,
                           catalog_backfill_usable, publication_window)
from paper_heat import FILENAME, refresh_heat
from render_wechat_preview import listing
from search_index import build_search_index, topic_labels


PAGE_SIZE = 24
CHUNK_SIZE = 120


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def expected_announcement(now):
    eastern = now.astimezone(ZoneInfo("America/New_York")) - timedelta(hours=1, minutes=23)
    day = eastern.date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def ranked(papers, votes, today):
    first, last = publication_window(today)
    valid = [p for p in papers if first <= p["published"] <= last]
    def order(p):
        day = date.fromisoformat(p["published"]).toordinal()
        batch = (today.toordinal() - day) // 10
        return (batch, -votes.get(p["id"], -1), -day, p["id"])
    return sorted(valid, key=order)


def group_title(batch):
    return "最近 10 天" if batch == 0 else f"{batch*10}–{batch*10+9} 天前"


def card(paper, votes):
    identifier = escape(paper["id"])
    count = votes.get(paper["id"])
    signal = f"HF 推荐 {count} 票" if count is not None else "暂无公开票数"
    authors = paper["source"].removeprefix("arXiv · ").split(", ")
    author = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
    summary = " ".join(paper["summary"].split())
    excerpt = escape(summary[:240]) + ("…" if len(summary) > 240 else "")
    revision = f' · 修订 {escape(paper["version"])}' if paper.get("revision") else ""
    points = (paper.get("highlights") or {}).get("points", [])
    highlights = "<ul>" + "".join(f"<li>{escape(s)}</li>" for s in points) + "</ul>" if points else ""
    return (f'<article class="card" data-id="{identifier}"><a href="https://arxiv.org/abs/{identifier}">'
            f'<div class="meta"><time>{escape(paper["published"])}</time> · {signal}{revision}</div>'
            f'<h2>{escape(paper["title"])}</h2><p>{excerpt}</p>{highlights}'
            f'<small class="meta">{escape(author)}</small></a></article>')


def publish(work, output, state, heat, now):
    today = now.astimezone(SHANGHAI).date()
    heat_time = datetime.fromisoformat(heat["checkedAt"]) if heat else None
    fresh = heat_time and timedelta(0) <= now - heat_time <= timedelta(hours=48)
    votes = heat["votes"] if fresh else {}
    papers = ranked(state["papers"], votes, today)
    if not papers:
        raise ValueError("No in-window papers to publish")
    taxonomy = state["taxonomy"]
    groups = Counter()
    paths = {}
    records, cards, docs, first_page = [], [], [], []
    previous = None
    chunks = output / "entries"
    chunks.mkdir(parents=True, exist_ok=True)
    for i, paper in enumerate(papers):
        day = date.fromisoformat(paper["published"])
        batch = (today - day).days // 10
        groups[batch] += 1
        codes = []
        for path in paper["paths"]:
            key = "/".join(path)
            codes.append(paths.setdefault(key, len(paths)))
        records.append([paper["id"], codes, batch, votes.get(paper["id"])])
        html = card(paper, votes)
        cards.append(html)
        if i < PAGE_SIZE:
            if batch != previous:
                first_page.append(f'<h3>{group_title(batch)}</h3>')
            first_page.append(html)
            previous = batch
        if len(cards) == CHUNK_SIZE or i == len(papers)-1:
            dump(chunks / f"{i // CHUNK_SIZE}.json", cards)
            cards = []
        metadata = " ".join([paper["id"], paper["source"], *paper["categories"], topic_labels(paper, taxonomy)])
        docs.append(("arxiv:" + paper["id"], paper["title"], paper["summary"], metadata))
    build_search_index(output, docs)
    dump(output / "index-data.json", {"asOf": str(today), "taxonomy": taxonomy, "paths": list(paths),
         "rows": records, "chunkSize": CHUNK_SIZE, "pageSize": PAGE_SIZE})
    for name in ("papers.js", "search.js"):
        copyfile(ROOT / "site" / name, output / name)
    options = "".join(f'<option value="{k}">{escape(v["label"])}</option>' for k,v in taxonomy.items())
    filters = (f'<form id="paper-controls"><label>研究方向<select id="level1"><option value="">全部方向</option>{options}</select></label>'
        '<label>细分领域<select id="level2" disabled><option value="">全部领域</option></select></label>'
        '<label>研究主题<select id="level3" disabled><option value="">全部主题</option></select></label>'
        '<label class="search">搜索论文<input type="search" id="query" placeholder="搜索标题、摘要、作者或主题"></label>'
        '<button type="submit">搜索</button></form>')
    signal = ("Hugging Face Daily Papers 推荐票，更新于 " + heat_time.astimezone(SHANGHAI).strftime("%m-%d %H:%M")
              if fresh else "暂无近期公开票数，批内按发表日期排列")
    statuses = [f"<li>arXiv 公告<span>{escape(state['announcementDate'])}</span></li>",
                f"<li>全收分类<span>{', '.join(CORE)}</span></li>", f"<li>筛选补充<span>{', '.join(SUPPLEMENTARY)}</span></li>"]
    controls = ('<p id="paper-count" class="status" role="status">共 ' + f'{len(papers):,} 篇</p><div id="paper-rows">'
                + "".join(first_page) + '</div><nav aria-label="论文翻页"><button id="prev" disabled>上一页</button>'
                f'<span id="page">1 / {(len(papers)+PAGE_SIZE-1)//PAGE_SIZE}</span><button id="next">下一页</button></nav>'
                '<script defer src="search.js"></script><script defer src="papers.js"></script>')
    html = listing(title="arXiv 论文", description="按研究方向浏览和检索最近三个月的 AI 论文。", filters=filters,
        coverage=f"每 10 天分一批 · 批内按热度排序 · {signal}", statuses="".join(statuses), articles=controls,
        checked=datetime.fromisoformat(state["checkedAt"]).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M"),
        navigation='<a href="../">微信公众号</a><a href="../blogs/">技术博客</a><a href="../social/">X 与小红书</a>'
                   '<a href="./" aria-current="page">arXiv 论文</a>')
    (output / "index.html").write_text(html, encoding="utf-8")
    state["papers"] = papers
    with gzip.open(output / "archive.json.gz", "wt", encoding="utf-8", compresslevel=3) as archive:
        json.dump(state, archive, ensure_ascii=False, separators=(",", ":"))
    if heat:
        dump(output / FILENAME, heat)
    report = f"arXiv · {len(papers):,} 篇，最近 10 天 {groups[0]:,} 篇，有公开推荐票 {sum(p['id'] in votes for p in papers):,} 篇。"
    print(report, flush=True)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as file:
            print(report, file=file)


def main():
    root = ROOT / "dist" / "wechat-preview"
    work, output = root / "paper-work", root / "site" / "papers"
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "archive.json.gz"
    state = {}
    state_file = work / "papers.json"
    if archive.exists():
        with gzip.open(archive, "rt", encoding="utf-8") as file:
            state = json.load(file)
        if not state["papers"] or not state["taxonomy"]:
            raise ValueError("Invalid published paper archive")
        dump(state_file, state)
    now = datetime.now(timezone.utc)
    today = now.astimezone(SHANGHAI).date()
    late = not state or (today - date.fromisoformat(state["announcementDate"])).days > 6
    backfill = late or not catalog_backfill_usable(state.get("catalogBackfill"), today)
    failed = False
    if backfill or state["announcementDate"] < expected_announcement(now) or os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        try:
            build_preview(work, ArxivSource(None, METADATA_URL, LIST_URL), backfill=backfill)
        except (OSError, ValueError, SyntaxError) as error:
            if not state:
                raise
            print(f"::warning::arXiv update failed, retaining verified records: {error}", flush=True)
            failed = True
        else:
            state = json.loads(state_file.read_bytes())
    ok = refresh_heat(work)
    heat = json.loads((work / FILENAME).read_bytes()) if (work / FILENAME).exists() else {}
    publish(work, output, state, heat, datetime.now(timezone.utc))
    if path := os.getenv("GITHUB_OUTPUT"):
        with open(path, "a") as file:
            print("fresh=" + str(not failed and ok).lower(), file=file)


if __name__ == "__main__":
    main()
