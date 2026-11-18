"""Read versioned records from arXiv's official incremental metadata harvest."""

from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode


BASE = "https://oaipmh.arxiv.org/oai"


def paper_record(raw):
    def field(name):
        value = " ".join((raw.findtext("{*}" + name) or "").split())
        if not value:
            raise ValueError(f"Missing arXivRaw {name}")
        return value
    versions = raw.findall("{*}version")
    if not versions or versions[0].get("version") != "v1":
        raise ValueError("Missing arXiv version history")
    first = parsedate_to_datetime(versions[0].findtext("{*}date"))
    last = parsedate_to_datetime(versions[-1].findtext("{*}date"))
    identifier = field("id")
    cats = field("categories").split()
    return {"id": identifier, "title":field("title"), "summary":field("abstract"),
            "source":"arXiv · " + field("authors"), "version":versions[-1].attrib["version"],
            "original":"https://arxiv.org/abs/" + identifier, "publishedAt":first.isoformat(),
            "published":first.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            "updatedAt":last.isoformat(), "primaryCategory":cats[0], "categories":sorted(set(cats))}


def harvest(read_xml, identifiers, since):
    wanted = set(identifiers)
    found = {}
    query = {"verb":"ListRecords", "metadataPrefix":"arXivRaw", "from":since}
    seen = set()
    while wanted:
        root = read_xml(BASE + "?" + urlencode(query))
        error = root.find("{*}error")
        if error is not None:
            if error.get("code") == "noRecordsMatch":
                break
            raise ValueError("OAI-PMH " + str(error.get("code")))
        listing = root.find("{*}ListRecords")
        if listing is None:
            raise ValueError("Missing OAI-PMH listing")
        for raw in listing.findall("{*}record/{*}metadata/{*}arXivRaw"):
            identifier = raw.findtext("{*}id")
            if identifier in wanted:
                found[identifier] = paper_record(raw)
                wanted.remove(identifier)
        token = listing.findtext("{*}resumptionToken")
        if not token:
            break
        if token in seen:
            raise ValueError("Repeated OAI-PMH cursor")
        seen.add(token)
        query = {"verb":"ListRecords", "resumptionToken":token}
    for identifier in sorted(wanted):
        query = {"verb":"GetRecord", "metadataPrefix":"arXivRaw", "identifier":"oai:arXiv.org:"+identifier}
        raw = read_xml(BASE+"?"+urlencode(query)).find("{*}GetRecord/{*}record/{*}metadata/{*}arXivRaw")
        if raw is None or raw.findtext("{*}id") != identifier:
            raise ValueError("Missing OAI-PMH record " + identifier)
        found[identifier] = paper_record(raw)
    print(f"OAI-PMH: verified metadata for {len(found)} announced papers", flush=True)
    return found
