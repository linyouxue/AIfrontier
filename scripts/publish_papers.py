"""Refresh the official arXiv catalog and its public reading index."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
import json
import os
from zoneinfo import ZoneInfo

from arxiv_lists import CORE, SUPPLEMENTARY, LIST_URL
from build_preview import (ArxivSource, METADATA_URL, ROOT, SHANGHAI, build_preview,
                           catalog_backfill_usable, publication_window)
from paper_heat import FILENAME, refresh_heat
from paper_highlights import generate_highlights, highlight_connection
from publish_shell import publish_shell




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


def publish(work, output, state, heat, now):
    today = now.astimezone(SHANGHAI).date()
    stamp = datetime.fromisoformat(heat["checkedAt"]) if heat else None
    votes = heat["votes"] if stamp and timedelta(0) <= now-stamp <= timedelta(hours=48) else {}
    papers = ranked(state["papers"], votes, today)
    if not papers:
        raise ValueError("No in-window papers to publish")
    output.mkdir(parents=True, exist_ok=True)
    groups = Counter()
    paths = {}
    records = []
    for paper in papers:
        batch = (today-date.fromisoformat(paper["published"])).days // 10
        groups[batch] += 1
        codes = []
        for path in paper["paths"]:
            codes.append(paths.setdefault("/".join(path), len(paths)))
        records.append([paper["id"], codes, batch, votes.get(paper["id"])])
    dump(output / "index-data.json", {"asOf":str(today), "taxonomy":state["taxonomy"],
                                     "paths":list(paths), "rows":records})
    state["papers"] = papers
    with gzip.open(output / "archive.json.gz", "wt", encoding="utf-8", compresslevel=3) as archive:
        json.dump(state, archive, ensure_ascii=False, separators=(",", ":"))
    if heat:
        dump(output / FILENAME, heat)
    publish_shell(output.parent, state, heat, *publication_window(today))
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
    if os.getenv("FULL_UPDATE") == "false":
        if not state:
            raise ValueError("Hourly event publication requires the existing paper catalog")
        heat = json.loads((work / FILENAME).read_bytes()) if (work / FILENAME).exists() else {}
        publish(work, output, state, heat, datetime.now(timezone.utc))
        return
    now = datetime.now(timezone.utc)
    today = now.astimezone(SHANGHAI).date()
    late = not state or (today - date.fromisoformat(state["announcementDate"])).days > 6
    backfill = late or not catalog_backfill_usable(state.get("catalogBackfill"), today)
    failed = False
    if (backfill or state.get("retryAnnouncements") or state["announcementDate"] < expected_announcement(now)
            or os.getenv("FORCE_ARXIV") == "true"):
        try:
            build_preview(work, ArxivSource(None, METADATA_URL, LIST_URL), backfill=backfill, with_highlights=False)
        except (OSError, ValueError, SyntaxError) as error:
            if not state:
                raise
            print(f"::warning::arXiv update failed, retaining verified records: {error}", flush=True)
            failed = True
        else:
            state = json.loads(state_file.read_bytes())
    ok = refresh_heat(work)
    heat = json.loads((work / FILENAME).read_bytes()) if (work / FILENAME).exists() else {}
    state["retryAnnouncements"] = failed
    generate_highlights(state["papers"], *highlight_connection())
    publish(work, output, state, heat, datetime.now(timezone.utc))
    if path := os.getenv("GITHUB_OUTPUT"):
        with open(path, "a") as file:
            print("fresh=" + str(not failed and ok).lower(), file=file)


if __name__ == "__main__":
    main()
