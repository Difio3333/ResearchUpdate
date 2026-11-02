#!/usr/bin/env python3
"""Collects AI/tech research items from arXiv, Hugging Face daily papers and GitHub.

Standard library only. Merges new items into docs/data/items.json,
deduplicating by id, dropping items older than retention_days and
keeping at most max_items.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "items.json"
CONFIG = json.loads((ROOT / "config.json").read_text())
RETENTION_DAYS = CONFIG.get("retention_days", 21)
MAX_ITEMS = CONFIG.get("max_items", 3000)
SUMMARY_CHARS = 1500  # long enough for nearly every arXiv abstract
UA = "ai-research-radar/0.2 (+https://github.com)"
NOW = datetime.now(timezone.utc)


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------- arXiv ----------
ATOM = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def strip_version(arxiv_id):
    """2610.01234v2 -> 2610.01234, so ids match Hugging Face's."""
    base, sep, ver = arxiv_id.rpartition("v")
    return base if sep and ver.isdigit() else arxiv_id


def matches(text, keywords):
    if not keywords:
        return True
    text = text.lower()
    return any(k.lower() in text for k in keywords)


def parse_arxiv(xml_bytes, topic="ai", keywords=None):
    items = []
    root = ET.fromstring(xml_bytes)
    for e in root.findall("a:entry", ATOM):
        url = e.findtext("a:id", "", ATOM).strip()
        arxiv_id = strip_version(url.rsplit("/abs/", 1)[-1])
        title = " ".join(e.findtext("a:title", "", ATOM).split())
        summary = " ".join(e.findtext("a:summary", "", ATOM).split())
        if not matches(title + " " + summary, keywords):  # filter on the full abstract
            continue
        primary = e.find(f"{ARXIV_NS}primary_category")
        cats = [c.get("term") for c in e.findall("a:category", ATOM)]
        authors = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        items.append({
            "id": f"arxiv:{arxiv_id}",
            "source": "arxiv",
            "topics": [topic],
            "category": primary.get("term") if primary is not None else (cats[0] if cats else ""),
            "title": title,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "summary": summary[:SUMMARY_CHARS],
            "authors": authors[:6],
            "published": e.findtext("a:published", "", ATOM),
            "score": 0,
        })
    return items


def collect_arxiv():
    items = []
    for i, feed in enumerate(CONFIG["arxiv"]["feeds"]):
        if i:
            time.sleep(3)  # arXiv API terms ask for a 3 s pause between requests
        query = "+OR+".join(f"cat:{c}" for c in feed["categories"])
        url = (
            "https://export.arxiv.org/api/query?search_query=" + query
            + f"&sortBy=submittedDate&sortOrder=descending&max_results={feed.get('max_results', 100)}"
        )
        try:
            got = parse_arxiv(fetch(url), feed["name"], feed.get("keywords"))
            print(f"  arXiv/{feed['name']}: {len(got)} items")
            items += got
        except Exception as ex:  # one feed failing should not kill the others
            print(f"  arXiv/{feed['name']} failed: {ex}", file=sys.stderr)
    return items


# ---------- Hugging Face daily papers ----------
def parse_hf(data):
    items = []
    for row in data:
        p = row.get("paper", row)
        pid = p.get("id")
        if not pid:
            continue
        items.append({
            "id": f"arxiv:{strip_version(pid)}",  # same id as arXiv so they merge
            "source": "huggingface",
            "topics": ["ai"],
            "title": p.get("title", "").strip(),
            "url": f"https://huggingface.co/papers/{pid}",
            "summary": " ".join((p.get("summary") or "").split())[:SUMMARY_CHARS],
            "authors": [a.get("name", "") for a in p.get("authors", [])][:6],
            "published": p.get("publishedAt") or row.get("publishedAt") or iso(NOW),
            "score": p.get("upvotes", 0),
        })
    return items


def collect_hf():
    items = []
    for days_back in range(CONFIG["huggingface"]["days_back"]):
        d = (NOW - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            data = json.loads(fetch(f"https://huggingface.co/api/daily_papers?date={d}"))
            items += parse_hf(data)
        except Exception as ex:  # one bad day should not kill the run
            print(f"  HF {d} failed: {ex}", file=sys.stderr)
    return items


# ---------- GitHub ----------
def parse_github(data):
    items = []
    for r in data.get("items", []):
        items.append({
            "id": f"github:{r['full_name']}",
            "source": "github",
            "topics": ["ai"],
            "title": r["full_name"],
            "url": r["html_url"],
            "summary": (r.get("description") or "")[:300],
            "authors": [r["owner"]["login"]],
            "published": r["created_at"],
            "score": r["stargazers_count"],
            "language": r.get("language"),
        })
    return items


def collect_github():
    cfg = CONFIG["github"]
    since = (NOW - timedelta(days=cfg["created_within_days"])).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    items = []
    for topic in cfg["topics"]:
        q = f"topic:{topic} created:>{since} stars:>={cfg['min_stars']}"
        url = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q)
               + "&sort=stars&order=desc&per_page=" + str(cfg["per_topic"]))
        try:
            items += parse_github(json.loads(fetch(url, headers)))
        except Exception as ex:
            print(f"  GitHub topic {topic} failed: {ex}", file=sys.stderr)
    return items


# ---------- merge ----------
def union(*lists):
    return sorted(set().union(*lists))


def merge(existing, new):
    by_id = {i["id"]: i for i in existing}
    for item in new:
        old = by_id.get(item["id"])
        if old:
            merged = {**old, **{k: v for k, v in item.items() if v not in (None, "", [])}}
            merged["score"] = max(old.get("score", 0), item.get("score", 0))
            merged["sources"] = union(old.get("sources", [old["source"]]), [item["source"]])
            merged["topics"] = union(old.get("topics", ["ai"]), item.get("topics", ["ai"]))
            merged["first_seen"] = old.get("first_seen", iso(NOW))
            by_id[item["id"]] = merged
        else:
            item["sources"] = [item["source"]]
            item.setdefault("topics", ["ai"])
            item["first_seen"] = iso(NOW)
            by_id[item["id"]] = item
    cutoff = NOW - timedelta(days=RETENTION_DAYS)
    kept = [i for i in by_id.values() if datetime.fromisoformat(i["first_seen"]) >= cutoff]
    kept.sort(key=lambda i: i["first_seen"], reverse=True)
    return kept[:MAX_ITEMS]


def main():
    existing = json.loads(OUT.read_text())["items"] if OUT.exists() else []
    new = []
    for name, fn in (("arXiv", collect_arxiv), ("Hugging Face", collect_hf), ("GitHub", collect_github)):
        try:
            got = fn()
            print(f"{name}: {len(got)} items")
            new += got
        except Exception as ex:
            print(f"{name} failed: {ex}", file=sys.stderr)
    items = merge(existing, new)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": iso(NOW), "items": items}, ensure_ascii=False, separators=(",", ":")))
    print(f"Wrote {len(items)} items")


if __name__ == "__main__":
    main()
