"""Collect public Hugging Face Daily Papers upvotes by arXiv ID."""

import argparse
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from build_preview import SHANGHAI, publication_window


FILENAME = "paper-heat.json"
DAILY = "https://huggingface.co/api/daily_papers"


def monthly_page(month, page):
    query = urlencode({"month": month, "limit": 100, "p": page})
    request = Request(f"{DAILY}?{query}", headers={"User-Agent": "AIfrontier/0.1"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Daily Papers response exceeded 8 MB")
    return json.loads(raw)


def refresh_heat(output, *, now=None, fetch=monthly_page):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(SHANGHAI).date()
    start, end = publication_window(today)
    votes = {}
    try:
        month = today.replace(day=1)
        while month >= date.fromisoformat(start).replace(day=1):
            page = 0
            seen = set()
            while True:
                rows = fetch(month.strftime("%Y-%m"), page)
                if not isinstance(rows, list):
                    raise ValueError("Invalid Daily Papers response")
                previous_count = len(seen)
                for row in rows:
                    paper = row.get("paper") if isinstance(row, dict) else None
                    if not isinstance(paper, dict):
                        raise ValueError("Invalid Daily Papers entry")
                    match = re.fullmatch(r"(\d{4}\.\d{4,5})(?:v\d+)?", str(paper.get("id", "")))
                    if not match:
                        continue
                    count = paper.get("upvotes")
                    if type(count) is not int or count < 0:
                        raise ValueError("Invalid Daily Papers upvote count")
                    identifier = match[1]
                    seen.add(identifier)
                    # Editions can include much older submissions. Match the public retention window.
                    published_at = datetime.fromisoformat(str(paper.get("publishedAt", "")).replace("Z", "+00:00"))
                    if published_at.tzinfo is None:
                        raise ValueError("Missing timezone in Daily Papers publication time")
                    published = published_at.astimezone(SHANGHAI).date().isoformat()
                    if start <= published <= end:
                        votes.setdefault(identifier, count)
                if len(rows) < 100:
                    break
                if len(seen) == previous_count:
                    raise ValueError("Repeated Daily Papers page")
                page += 1
            month = (month - timedelta(days=1)).replace(day=1)
    except (OSError, ValueError, HTTPException) as error:
        print(f"Daily Papers unavailable, retaining previous votes: {error}", file=sys.stderr)
        return False
    output.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=output, encoding="utf-8", delete=False) as pending:
        json.dump({"checkedAt": now.isoformat(), "votes": votes}, pending, separators=(",", ":"))
    Path(pending.name).replace(output / FILENAME)
    print(f"Daily Papers: refreshed community votes for {len(votes)} papers")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "dist" / "AIfrontier")
    raise SystemExit(0 if refresh_heat(parser.parse_args().output) else 1)
