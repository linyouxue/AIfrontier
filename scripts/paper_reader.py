"""Publish a compact paper index and loadable batches of complete abstracts."""

import argparse
import gzip
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


INDEX_FIELDS = ("id", "title", "source", "published", "publishedAt", "collected",
                "original", "paths", "version", "revision", "highlights")
PAPERS_PER_ABSTRACT_FILE = 128
EXCERPT_LENGTH = 180


def publish_paper_reader(output, snapshot=None):
    if snapshot is None:
        source = output / "papers.json"
        if not source.is_file():
            return
        snapshot = json.loads(source.read_text(encoding="utf-8"))
    papers = snapshot["papers"]
    destination = output / "paper-abstracts"
    destination.mkdir(exist_ok=True)
    index = []
    active_files = set()

    for start in range(0, len(papers), PAPERS_PER_ABSTRACT_FILE):
        filename = f"{start // PAPERS_PER_ABSTRACT_FILE:04d}.json"
        active_files.add(filename)
        batch = papers[start:start + PAPERS_PER_ABSTRACT_FILE]
        abstracts = {paper["id"]: paper["summary"] for paper in batch}
        (destination / filename).write_text(json.dumps(abstracts, ensure_ascii=False), encoding="utf-8")
        for paper in batch:
            entry = {field: paper[field] for field in INDEX_FIELDS if field in paper}
            summary = paper["summary"]
            entry["summary"] = summary if len(summary) <= EXCERPT_LENGTH else summary[:EXCERPT_LENGTH].rstrip() + "…"
            entry["abstractFile"] = f"paper-abstracts/{filename}"
            index.append(entry)

    manifest = {"papers": index, "taxonomy": snapshot.get("taxonomy")}
    body = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (output / "papers-reader.json").write_bytes(body)
    with NamedTemporaryFile(dir=output, delete=False) as pending:
        temporary_path = Path(pending.name)
        with gzip.GzipFile(fileobj=pending, mode="wb", compresslevel=4) as encoded:
            encoded.write(body)
    temporary_path.replace(output / "papers-reader.json.gz")
    for stale in destination.glob("*.json"):
        if stale.stem.isascii() and stale.stem.isdigit() and stale.name not in active_files:
            stale.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    publish_paper_reader(parser.parse_args().output)
