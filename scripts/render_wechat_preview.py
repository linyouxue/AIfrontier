"""Build a static reading page from the collected public WeChat snapshot."""

from datetime import datetime
from html import escape
import json
from string import Template

from collect_public_wechat import ROOT, SHANGHAI, SOURCES, article_link


def article_card(article):
    original = article_link(article["original"])
    if original is None:
        raise ValueError("The snapshot contains a non-WeChat article link")
    author = escape(article["source"])
    title = escape(article["title"])
    summary = escape(article["summary"][:140])
    day = escape(article["published"])
    return (
        f'<article class="card"><a href="{escape(original[1])}">'
        f'<div class="meta">{author}<time datetime="{day}">{day}</time></div>'
        f'<h2>{title}</h2><p>{summary}</p></a></article>'
    )


def render(snapshot):
    articles = snapshot["items"]
    sources = snapshot["sources"]
    if not articles and all(source["status"] != "ok" for source in sources):
        raise ValueError("No verified articles or successful source checks. Keeping the last deployed page.")
    last_check = max(source["checkedAt"] for source in sources)
    checked = datetime.fromisoformat(last_check).astimezone(SHANGHAI)
    labels = {"ok": "已检查", "partial": "部分条目待核验", "failed": "订阅读取失败"}
    status_rows = [
        f'<li>{escape(source["account"])}<span>{labels[source["status"]]}</span></li>'
        for source in sources
    ]
    return Template((ROOT / "site" / "wechat-preview.html").read_text(encoding="utf-8")).substitute(
        source_count=len(SOURCES),
        article_count=len(articles),
        checked=checked.strftime("%Y-%m-%d %H:%M"),
        statuses="\n".join(status_rows),
        articles="\n".join(article_card(article) for article in articles) or "<p>本次暂无符合收录条件的文章。</p>",
    )


if __name__ == "__main__":
    output = ROOT / "dist" / "wechat-preview"
    snapshot = json.loads((output / "wechat.json").read_text(encoding="utf-8"))
    page = output / "site" / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render(snapshot), encoding="utf-8")
