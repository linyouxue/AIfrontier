"""Publish the account reader alongside WeChat and blogs."""

from datetime import datetime
from html import escape
import json
from shutil import copyfile

from collect_public_wechat import SHANGHAI
from collect_social import OUTPUT, REGISTRY, XHS
from render_wechat_preview import linked_card, listing


LABELS = {"ok": "已检查", "failed": "读取失败", "partial": "部分条目待核验", "unconfigured": "等待配置小红书登录"}


def publish(output):
    snapshot = json.loads((output / "social.json").read_text("utf-8"))
    registry = json.loads(REGISTRY.read_text("utf-8"))
    sources, items = snapshot["sources"], snapshot["items"]
    statuses, sections = [], []
    for source in sources:
        label = LABELS[source["status"]]
        statuses.append(f'<li>{escape(source["name"])}<span>{label}</span></li>')
    for platform, name in (("x", "X"), ("xiaohongshu", "小红书")):
        cards = [linked_card(row, row["original"]) for row in items if row["platform"] == platform]
        sections.append(f'<section id="{platform}"><h2>{name}</h2>')
        sections.extend(cards or ['<p>近三个月暂无已核验原发日期的条目。</p>'])
        if platform == "xiaohongshu":
            sections.append(f'<details><summary>{escape(registry["baselineDate"])} 建立的账号基线</summary><ul>')
            published = {row["id"] for row in items}
            for account in registry[platform]:
                profile = f'{XHS}/user/profile/{account["id"]}'
                sections.append(f'<li><a href="{profile}">{escape(account["name"])}</a> · '
                                f'已记录 {len(account["baseline"])} 篇笔记<ul>')
                for sample in account["samples"]:
                    if "xiaohongshu:" + sample["id"] not in published:
                        sections.append(f'<li><a href="{XHS}/explore/{sample["id"]}">{escape(sample["title"])}</a>'
                                        f' · {escape(sample["published"] or "原发日期待核验")}</li>')
                sections.append("</ul></li>")
            sections.append("</ul></details>")
        sections.append("</section>")
    checked = datetime.fromisoformat(max(s["checkedAt"] for s in sources)).astimezone(SHANGHAI)
    page = listing(title="X 与小红书", description="关注账号近三个月的技术帖与公开笔记。点开标题可阅读原文。",
                   navigation='<a href="../">微信公众号</a><a href="../blogs/">技术博客</a>'
                              '<a href="./" aria-current="page">X 与小红书</a>',
                   filters='<nav class="filters"><a href="#x">X</a><a href="#xiaohongshu">小红书</a></nav>',
                   checked=checked.strftime("%Y-%m-%d %H:%M"), statuses="".join(statuses),
                   coverage=f'{len(sources)} 个账号 · {len(items)} 条收录', articles="".join(sections))
    site = output / "site"
    target = site / "social" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    copyfile(output / "social.json", site / "social.json")


if __name__ == "__main__":
    publish(OUTPUT)
