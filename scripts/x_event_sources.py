"""Read attributable announcements from publisher and Codex staff timelines."""

from datetime import datetime, timezone
import json
import re

from event_sources import EvidenceCandidate, read
from social_event_sources import related_announcement


LAUNCH = re.compile(r"\b(?:introducing |we(?:['’]re| are| just| have)? (?:launching|launched|released|open-sourced|introducing) |(?:is|are) now available\b)", re.I)
RESET = re.compile(r"\b(?:banked|codex|usage|rate[- ]?limit) resets?\b.{0,55}\b(?:activated|available|live|applied)\b|\b(?:we(?:['’]ve| have| just)?|I(?:['’]ve| have| just)?) (?:just )?reset\b.{0,65}\b(?:limits?|usage|codex)\b", re.I)


def x_candidates(handle, batch):
    result = json.loads(read(f"https://api.fxtwitter.com/2/profile/{handle.lower()}/statuses?count=50"))
    if result.get("code") != 200 or not isinstance(result.get("results"), list):
        raise ValueError("X timeline unavailable")
    candidates = []
    for post in result["results"]:
        author = post.get("author") or {}
        original = f"https://x.com/{handle}/status/{post.get('id', '')}"
        if (post.get("type") != "status" or author.get("screen_name", "").lower() != handle.lower()
                or author.get("protected") or post.get("reposted_by") or post.get("replying_to")
                or not str(post.get("id", "")).isdigit() or post.get("url", "").lower() != original.lower()):
            continue
        body = post.get("text", "").strip()
        links = [f["replacement"] for f in (post.get("raw_text") or {}).get("facets", [])
                 if f.get("type") == "url" and f.get("replacement")]
        related = related_announcement(links)
        reset = handle == "thsottiaux" and bool(RESET.search(body))
        if not reset and not (related and LAUNCH.search(body)):
            continue
        when = datetime.fromtimestamp(post["created_timestamp"], timezone.utc).isoformat()
        excerpt = re.sub(r"https?://\S+", "", body).strip()
        title = next((line for line in excerpt.splitlines() if (RESET if reset else LAUNCH).search(line)), excerpt)
        candidates.append(EvidenceCandidate(original, title[:155], "@" + handle, when, excerpt[:450],
                                            verified=reset, related_urls=related))
    return batch(candidates, checked=len(result["results"]))
