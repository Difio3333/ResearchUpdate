#!/usr/bin/env python3
"""Collects AI/tech research items from arXiv, Hugging Face daily papers and GitHub.

Standard library only. Merges new items into docs/data/items.json,
deduplicating by id and dropping items older than RETENTION_DAYS.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "items.json"
CONFIG = json.loads((ROOT / "config.json").read_text())
RETENTION_DAYS = CONFIG.get("retention_days", 30)
UA = "ai-research-radar/0.1 (+https://github.com)"
NOW = datetime.now(timezone.utc)


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------- arXiv ----------
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def parse_arxiv(xml_bytes):
    items = []
    root = ET.fromstring(xml_bytes)
    for e in root.findall("a:entry", ATOM):
        url = e.findtext("a:id", "", ATOM).strip()
        arxiv_id = url.rsplit("/abs/", 1)[-1]
        if "v" in arxiv_id:  # drop version suffix so ids match Hugging Face's
            base, _, ver = arxiv_id.rpartition("v")
            if ver.isdigit():
                arxiv_id = base
        title = " ".join(e.findtext("a:title", "", ATOM).split())
        summary = " ".join(e.findtext("a:summary", "", ATOM).split())
        published = e.findtext("a:published", "", ATOM)
        authors = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        items.append({
            "id": f"arxiv:{arxiv_id}",
            "source": "arxiv",
            "title": title,
            "url": url,
            "summary": summary[:600],
            "authors": authors[:6],
            "published": published,
            "score": 0,
        })
    return items


def collect_arxiv():
    cfg = CONFIG["arxiv"]
    query = "+OR+".join(f"cat:{c}" for c in cfg["categories"])
    url = (
        "https://export.arxiv.org/api/query?search_query=" + query
        + f"&sortBy=submittedDate&sortOrder=descending&max_results={cfg['max_results']}"
    )
    return parse_arxiv(fetch(url))


# ---------- Hugging Face daily papers ----------
def parse_hf(data):
    items = []
    for row in data:
        p = row.get("paper", row)
        pid = p.get("id")
        if not pid:
            continue
        items.append({
            "id": f"arxiv:{pid}",  # same id as arXiv so they merge
            "source": "huggingface",
            "title": p.get("title", "").strip(),
            "url": f"https://huggingface.co/papers/{pid}",
            "summary": (p.get("summary") or "")[:600].replace("\n", " "),
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
def merge(existing, new):
    by_id = {i["id"]: i for i in existing}
    for item in new:
        old = by_id.get(item["id"])
        if old:
            sources = set(old.get("sources", [old["source"]])) | {item["source"]}
            merged = {**old, **{k: v for k, v in item.items() if v not in (None, "", [])}}
            merged["score"] = max(old.get("score", 0), item.get("score", 0))
            merged["sources"] = sorted(sources)
            merged["first_seen"] = old.get("first_seen", iso(NOW))
            by_id[item["id"]] = merged
        else:
            item["sources"] = [item["source"]]
            item["first_seen"] = iso(NOW)
            by_id[item["id"]] = item
    cutoff = NOW - timedelta(days=RETENTION_DAYS)
    kept = [i for i in by_id.values() if datetime.fromisoformat(i["first_seen"]) >= cutoff]
    kept.sort(key=lambda i: i["first_seen"], reverse=True)
    return kept


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
    OUT.write_text(json.dumps({"updated": iso(NOW), "items": items}, indent=1, ensure_ascii=False))
    print(f"Wrote {len(items)} items")


if __name__ == "__main__":
    main()
