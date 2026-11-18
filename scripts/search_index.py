"""Build a sharded full-text index for the public feeds."""

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4


DIRECTORY = "search-index"
SHARDS = 128
FEEDS = {"arxiv": "papers", "x": "x", "wechat": "wechat", "events": "events", "podcasts": "podcasts", "blogs": "blogs"}
MODULE_LABELS = {"arxiv": "arXiv 论文", "x": "X 技术内容", "wechat": "微信公众号文章",
                 "events": "AI 事件", "podcasts": "播客", "blogs": "技术博客"}
HAN = re.compile(r"([\u3400-\u9fff]+)")
WORDS = re.compile(r"[^\W_]+", re.UNICODE)
SHORT_FORM = re.compile(r"\(([A-Za-z][A-Za-z0-9-]{1,10})\)")
STOP = set("a an and are as at be by for from in is of on or that the to with".split())


@lru_cache(maxsize=80_000)
def stem(word):
    if not word.isascii() or not word.isalpha():
        return word
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("sses", "ches", "shes", "xes", "zes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    if len(word) > 3 and word.endswith("e") and not word.endswith("ee"):
        word = word[:-1]
    return word


def tokens(text):
    folded = unicodedata.normalize("NFKD", text).lower()
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    result = []
    for part in HAN.split(folded):
        if part and "\u3400" <= part[0] <= "\u9fff":
            result.extend(part)
            result.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            result.extend(stem(word) for word in WORDS.findall(part) if word not in STOP)
    return result


def definitions(text):
    # Align abbreviation letters backwards, requiring the first to start a word.
    # This recognizes both initialisms and mixed-case forms such as LoRA.
    for mark in SHORT_FORM.finditer(text):
        short = mark[1].removesuffix("s").replace("-", "")
        if len(short) < 2 or sum(char.isupper() for char in short) < 2:
            continue
        before = re.search(r"[a-zA-Z0-9][a-zA-Z0-9\s\-–‑]*$", text[max(0, mark.start() - 140):mark.start()].rstrip())
        if not before:
            continue
        phrase = before[0].lower()
        pos = len(phrase)
        for i in range(len(short) - 1, -1, -1):
            pos = phrase.rfind(short[i].lower(), 0, pos)
            while i == 0 and pos > 0 and phrase[pos - 1].isalnum():
                pos = phrase.rfind(short[0].lower(), 0, pos)
            if pos < 0:
                break
        if pos >= 0:
            words = tokens(phrase[pos:])
            if 2 <= len(words) <= min(len(short) * 2, len(short) + 4):
                yield stem(short.lower()), tuple(words)


def topic_labels(paper, taxonomy):
    labels = set()
    for path in paper.get("paths", []):
        children = taxonomy
        for key in path:
            node = children.get(key, {})
            if node.get("label"):
                labels.add(node["label"])
            children = node.get("children", {})
    return " ".join(sorted(labels))


def read_documents(output):
    docs = []
    for module, filename in FEEDS.items():
        path = output / (filename + ".json")
        if not path.is_file():
            continue
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        for row in snapshot.get("papers" if module == "arxiv" else "items", []):
            fields = [row["id"], MODULE_LABELS[module], row.get("source", ""), row.get("show", ""),
                      topic_labels(row, snapshot.get("taxonomy", {}))]
            for field in ("guests", "topics", "categories", "points"):
                fields.extend(row.get(field) or [])
            for link in row.get("evidence") or []:
                fields.extend([link.get("title"), link.get("source"), *(link.get("sources") or [])])
            fields.extend((row.get("highlights") or {}).get("points", []))
            docs.append((module + ":" + row["id"], row.get("title", ""), row.get("summary", ""),
                         " ".join(value for value in fields if isinstance(value, str))))
    return docs


def shard_for(term):
    value = 0
    for char in term:
        value = (value * 31 + ord(char)) % SHARDS
    return value


def build_search_index(output, docs=None):
    if docs is None:
        docs = read_documents(output)
    observed = defaultdict(Counter)
    for _, title, summary, _ in docs:
        for abbr, form in set(definitions(title + ". " + summary)):
            observed[abbr][form] += 1
    aliases = {}
    for abbr, counts in observed.items():
        form, count = counts.most_common(1)[0]
        if count >= 2 and count * 2 > counts.total():
            aliases[form] = abbr
    by_first = defaultdict(list)
    for form, abbr in aliases.items():
        by_first[form[0]].append((list(form), abbr))

    def frequencies(text):
        words = tokens(text)
        counts = Counter(words)
        for i, first in enumerate(words):
            for form, abbr in by_first.get(first, ()):
                if words[i:i + len(form)] == form:
                    counts[abbr] += 1
        return counts

    shards = [{} for _ in range(SHARDS)]
    last_doc = {}
    for doc, (_, title, abstract, metadata) in enumerate(docs):
        body, names, headings = frequencies(abstract), frequencies(metadata), frequencies(title)
        for term in body.keys() | names.keys() | headings.keys():
            # A substantive discussion in the abstract can outrank a passing title mention.
            weight = 2 * min(6, body[term]) + 4 * min(2, names[term]) + 5 * min(2, headings[term])
            postings = shards[shard_for(term)].setdefault(term, [])
            postings.extend((doc - last_doc.get(term, 0), weight))
            last_doc[term] = doc

    directory = output / DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    build = uuid4().hex

    def publish(name, data):
        temporary = directory / (name + "." + build + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(directory / name)

    for i, terms in enumerate(shards):
        publish(f"{i:02x}.json", {"build": build, "terms": terms})
    publish("manifest.json", {"build": build, "ids": [doc[0] for doc in docs],
                              "aliases": {" ".join(form): abbr for form, abbr in aliases.items()}})
    print(f"Search index: {len(docs)} documents, {len(last_doc)} terms, {len(aliases)} corpus-derived abbreviations")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "dist" / "AIfrontier")
    build_search_index(parser.parse_args().output)
