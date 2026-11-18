"""Publish the blog archive beside the WeChat page in the same Pages artifact."""

from collections import Counter
from datetime import datetime
from html import escape
import json
from shutil import copyfile

from collect_blogs import public_host
from collect_public_wechat import ROOT, SHANGHAI
from render_wechat_preview import linked_card, listing


SECTIONS = {"": "全部", "author": "个人博客", "team": "团队博客"}


def publish(output):
    snapshot = json.loads((output / "blogs.json").read_text(encoding="utf-8"))
    articles, sources = snapshot["items"], snapshot["sources"]
    cards = {key: [] for key in SECTIONS}
    for article in articles:
        if not public_host(article["original"]):
            raise ValueError("Invalid original blog URL")
        card = linked_card(article, article["original"])
        cards[""].append(card)
        cards[article["kind"]].append(card)

    checked = datetime.fromisoformat(max(s["checkedAt"] for s in sources)).astimezone(SHANGHAI)
    counts = Counter(s["kind"] for s in sources)
    statuses = []
    for source in sources:
        state = "已检查" if source["status"] == "ok" else "读取失败 · " + source["error"]
        statuses.append(f'<li>{escape(source["id"])}<span>{escape(state)}</span></li>')

    site = output / "site"
    for kind, label in SECTIONS.items():
        root = "../" if kind else "./"
        links = []
        for key, name in SECTIONS.items():
            href = root + (key + "/" if key else "")
            current = ' aria-current="page"' if key == kind else ""
            links.append(f'<a href="{href}"{current}>{name}（{len(cards[key])}）</a>')
        title = "技术博客" if not kind else label
        page = listing(
            title=title, description="最近三个月 AI 作者与研究团队发表的技术文章。",
            navigation=f'<a href="{root}../">微信公众号</a><a href="{root}" aria-current="page">技术博客</a>'
                       f'<a href="{root}../social/">X 与小红书</a>',
            coverage=f"{counts['author']} 位作者 · {counts['team']} 个团队 · 近三个月收录 {len(cards[''])} 篇",
            filters='<nav class="filters" aria-label="博客类型">' + "".join(links) + "</nav>",
            checked=checked.strftime("%Y-%m-%d %H:%M"), statuses="\n".join(statuses),
            articles="\n".join(cards[kind]) or "<p>近三个月暂无已收录文章。</p>",
        )
        target = site / "blogs" / kind / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
    copyfile(output / "blogs.json", site / "blogs.json")


if __name__ == "__main__":
    publish(ROOT / "dist" / "wechat-preview")
