"""Build a static reading page from the collected public WeChat snapshot."""

from datetime import datetime
from html import escape
import json
from string import Template

from collect_public_wechat import ROOT, SHANGHAI, article_link


def linked_card(article, href):
    author = escape(article["source"])
    title = escape(article["title"])
    summary = escape(article["summary"][:140])
    day = escape(article["published"])
    return (
        f'<article class="card"><a href="{escape(href)}">'
        f'<div class="meta">{author}<time datetime="{day}">{day}</time></div>'
        f'<h2>{title}</h2><p>{summary}</p></a></article>'
    )


def article_card(article):
    original = article_link(article["original"])
    if original is None:
        raise ValueError("The snapshot contains a non-WeChat article link")
    return linked_card(article, original[1])


def listing(**values):
    template = Template((ROOT / "site" / "wechat-preview.html").read_text(encoding="utf-8"))
    return template.substitute(**values)


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
    return listing(
        title="微信公众号文章", description="查看 AIfrontier 收录的微信公众号文章，点开阅读原文。",
        coverage=f"{len({s['account'] for s in sources})} 个公众号 · {len(articles)} 篇",
        navigation='<a href="./" aria-current="page">微信公众号</a><a href="blogs/">技术博客</a>',
        filters="",
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
    # The published snapshot restores verified items if the Actions cache expires.
    public_sources = [{k: v for k, v in source.items() if k not in {"reviewId", "articleId"}} for source in snapshot["sources"]]
    page.with_name("wechat.json").write_text(json.dumps({"items": snapshot["items"], "sources": public_sources},
                                                        ensure_ascii=False), encoding="utf-8")
