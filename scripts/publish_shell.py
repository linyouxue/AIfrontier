"""Publish the shared AIfrontier interface and retain its older public URLs."""

import json
from pathlib import Path
from shutil import copyfile, copytree
from html import escape

from build_preview import ROOT
from paper_reader import publish_paper_reader
from search_index import build_search_index, read_documents, topic_labels


def redirect(site, directory, route):
    dest = site / directory / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = "../" * len(Path(directory).parts)
    url = prefix + "#/" + route
    # A previous paper bookmark can include a topic and a full-text query.
    script = ("const u=new URL(location.href),t=u.searchParams.get('topic')||'',q=u.searchParams.get('q');"
              "const path=t.split('/').filter(Boolean).map(encodeURIComponent).join('/');"
              "location.replace(" + json.dumps(url) + "+(path?'/'+path:'')+(q?'?q='+encodeURIComponent(q):''));"
              if route == "arxiv" else "location.replace(" + json.dumps(url) + ");")
    dest.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AIfrontier</title><script>'
                    +script+'</script><a href="'+escape(url)+'">进入 AIfrontier</a></html>', encoding="utf-8")


def publish_shell(site, snapshot, heat, first, last):
    for name in ("index.html", "app.js", "styles.css", "search.js", "paper-ranking.js"):
        copyfile(ROOT / "site" / name, site / name)
    copytree(ROOT / "site" / "assets", site / "assets", dirs_exist_ok=True)
    checks = {}
    for channel, name in [("wechat","wechat"),("blogs","blogs"),("x","social"),
                          ("events","events"),("podcasts","podcasts")]:
        data_file = site / (name + ".json")
        data = json.loads(data_file.read_bytes())
        sources = data["sources"]
        successful = sum(s["status"] == "ok" for s in sources)
        partial = any(s["status"] == "partial" for s in sources)
        checks[channel] = {"checkedAt": max(s["checkedAt"] for s in sources),
                           "status": "ok" if successful == len(sources) else "partial" if successful or partial else "failed"}
        if channel in ("events", "podcasts"):
            if all("inserted" in s for s in sources):
                checks[channel]["inserted"] = sum(s["inserted"] for s in sources)
            else:
                checks[channel] = {"archivedAt": checks[channel]["checkedAt"]}
        if channel == "x":
            # The search builder uses the reader's module id as the filename.
            registry = json.loads((ROOT / "config/social-accounts.json").read_bytes())
            data["accounts"] = registry["xiaohongshu"]
            data["baselineDate"] = registry["baselineDate"]
            (site / "x.json").write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    stamp = snapshot["checkedAt"]
    checks["arxiv"] = {"checkedAt":stamp, "lastSuccessAt":stamp, "status":"partial" if snapshot.get("retryAnnouncements") else "ok",
                       "coverageIncomplete": bool(snapshot.get("announcementGaps"))}
    (site/"collection-status.json").write_text(json.dumps({"modules":checks}),encoding="utf-8")
    if heat:
        (site/"paper-heat.json").write_text(json.dumps(heat,separators=(",",":")),encoding="utf-8")
    publish_paper_reader(site, snapshot)
    docs = read_documents(site)
    for p in snapshot["papers"]:
        meta = " ".join([p["id"],p["source"],*p["categories"],topic_labels(p,snapshot["taxonomy"])])
        docs.append(("arxiv:"+p["id"],p["title"],p["summary"],meta))
    build_search_index(site, docs)
    for directory, route in {"papers":"arxiv","wechat":"wechat","blogs":"blogs","blogs/author":"blogs/author",
                             "blogs/team":"blogs/team","social":"x","events":"events","podcasts":"podcasts"}.items():
        redirect(site,directory,route)
