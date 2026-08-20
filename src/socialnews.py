#!/usr/bin/env python3
"""Account-free social discovery with durable Markdown reports."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SUPPORTED_PLATFORMS = {
    "arxiv", "bluesky", "github", "github_new", "github_trending", "hackernews", "huggingface", "instagram",
    "linkedin", "mastodon", "news", "reddit", "stackexchange", "threads",
    "tiktok", "x", "youtube",
}
INDEXED_DOMAINS = {
    "x": "x.com", "instagram": "instagram.com", "threads": "threads.net",
    "tiktok": "tiktok.com", "linkedin": "linkedin.com", "youtube": "youtube.com",
}
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}


@dataclasses.dataclass(frozen=True)
class Item:
    topic: str
    platform: str
    title: str
    url: str
    published_at: str | None = None
    author: str | None = None
    summary: str | None = None
    source: str | None = None


@dataclasses.dataclass(frozen=True)
class Health:
    topic: str
    platform: str
    ok: bool
    found: int = 0
    error: str | None = None


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_config(config: dict[str, Any]) -> None:
    queries = config.get("queries")
    if not isinstance(queries, list):
        raise ValueError("config.queries must be an array")
    for index, entry in enumerate(queries):
        if not isinstance(entry, dict):
            raise ValueError(f"queries[{index}] must be an object")
        for field in ("name", "query", "platforms"):
            if field not in entry:
                raise ValueError(f"queries[{index}] is missing {field!r}")
        if not isinstance(entry["name"], str) or not entry["name"].strip():
            raise ValueError(f"queries[{index}].name must be non-empty")
        if not isinstance(entry["query"], str) or not entry["query"].strip():
            raise ValueError(f"queries[{index}].query must be non-empty")
        if not isinstance(entry["platforms"], list) or not entry["platforms"]:
            raise ValueError(f"queries[{index}].platforms must be a non-empty array")
        unknown = set(entry["platforms"]) - SUPPORTED_PLATFORMS
        if unknown:
            raise ValueError(f"queries[{index}] has unsupported platforms: {sorted(unknown)}")
    for field in ("max_results_per_source", "lookback_days", "request_timeout_seconds", "seen_retention_days"):
        value = config.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"config.{field} must be an integer >= 1")
    if not isinstance(config.get("max_report_items", 100), int) or config.get("max_report_items", 100) < 1:
        raise ValueError("config.max_report_items must be an integer >= 1")
    feeds = config.get("feeds", [])
    if not isinstance(feeds, list):
        raise ValueError("config.feeds must be an array")
    for index, feed in enumerate(feeds):
        if not isinstance(feed, dict) or not all(isinstance(feed.get(key), str) and feed[key].strip() for key in ("name", "url", "category")):
            raise ValueError(f"feeds[{index}] must contain non-empty name, url, and category strings")
        if urllib.parse.urlsplit(feed["url"]).scheme not in {"http", "https"}:
            raise ValueError(f"feeds[{index}].url must be HTTP(S)")
    sites = config.get("sites", [])
    if not isinstance(sites, list):
        raise ValueError("config.sites must be an array")
    for index, site in enumerate(sites):
        if not isinstance(site, dict) or not all(isinstance(site.get(key), str) and site[key].strip() for key in ("name", "domain", "category")):
            raise ValueError(f"sites[{index}] must contain non-empty name, domain, and category strings")


def fetch_bytes(url: str, *, timeout: int, user_agent: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        },
        method="GET",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = min(int(retry_after), 10) if retry_after and retry_after.isdigit() else 2 ** attempt
            time.sleep(delay)
    raise RuntimeError("unreachable")


def fetch_json(url: str, *, timeout: int, user_agent: str) -> Any:
    return json.loads(fetch_bytes(url, timeout=timeout, user_agent=user_agent))


def canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    scheme = parsed.scheme.lower() or "https"
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = sorted((key, value) for key, value in query if key.lower() not in TRACKING_PARAMS)
    return urllib.parse.urlunsplit((scheme, hostname + port, path, urllib.parse.urlencode(query), ""))


def fingerprint(item: Item) -> str:
    basis = f"{item.platform}\n{canonicalize_url(item.url)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(value))
    cleaned = re.sub(r"\s+", " ", without_tags).strip()
    return cleaned or None


def iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).isoformat()
        parsed = email.utils.parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def search_reddit(
    topic: str, query: str, *, limit: int, after: int, timeout: int, user_agent: str
) -> list[Item]:
    params = urllib.parse.urlencode(
        {"q": query, "size": limit, "after": after, "sort": "desc", "sort_type": "created_utc"}
    )
    url = f"https://api.pullpush.io/reddit/search/submission/?{params}"
    try:
        payload = json.loads(fetch_bytes(url, timeout=timeout, user_agent=user_agent))
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 429}:
            raise
        return search_site_rss(topic, query, "reddit.com", "reddit", limit=limit, timeout=timeout, user_agent=user_agent, source="Bing RSS (Reddit fallback)")
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise ValueError("PullPush response did not contain a data array")
    results: list[Item] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        post_id = str(row.get("id", "")).strip()
        permalink = str(row.get("permalink", "")).strip()
        if permalink.startswith("/"):
            post_url = "https://www.reddit.com" + permalink
        elif permalink.startswith("http"):
            post_url = permalink
        elif post_id:
            post_url = f"https://www.reddit.com/comments/{post_id}"
        else:
            continue
        results.append(
            Item(
                topic=topic,
                platform="reddit",
                title=clean_text(str(row.get("title", ""))) or "Untitled Reddit post",
                url=post_url,
                published_at=iso_timestamp(row.get("created_utc")),
                author=clean_text(str(row.get("author", ""))),
                summary=clean_text(str(row.get("selftext", ""))),
                source="PullPush",
            )
        )
    return results


def xml_text(node: ET.Element, *names: str) -> str | None:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text
    return None


def parse_rss(data: bytes, *, topic: str, platform: str, source: str) -> list[Item]:
    root = ET.fromstring(data)
    results: list[Item] = []
    for node in root.findall(".//item"):
        url = (node.findtext("link") or "").strip()
        if not url:
            continue
        results.append(
            Item(
                topic=topic,
                platform=platform,
                title=clean_text(node.findtext("title")) or "Untitled result",
                url=url,
                published_at=iso_timestamp(node.findtext("pubDate")),
                author=clean_text(node.findtext("author")),
                summary=clean_text(node.findtext("description")),
                source=source,
            )
        )
    atom_ns = "{http://www.w3.org/2005/Atom}"
    for node in root.findall(f".//{atom_ns}entry"):
        link = node.find(f"{atom_ns}link")
        url = (link.get("href", "") if link is not None else "").strip()
        if not url:
            continue
        author_node = node.find(f"{atom_ns}author/{atom_ns}name")
        results.append(Item(
            topic=topic, platform=platform,
            title=clean_text(xml_text(node, f"{atom_ns}title")) or "Untitled result",
            url=url,
            published_at=iso_timestamp(xml_text(node, f"{atom_ns}published", f"{atom_ns}updated")) or xml_text(node, f"{atom_ns}published", f"{atom_ns}updated"),
            author=clean_text(author_node.text if author_node is not None else None),
            summary=clean_text(xml_text(node, f"{atom_ns}summary", f"{atom_ns}content")),
            source=source,
        ))
    return results


def search_bing_rss(
    topic: str, query: str, platform: str, *, limit: int, timeout: int, user_agent: str
) -> list[Item]:
    domain = INDEXED_DOMAINS[platform]
    params = urllib.parse.urlencode({"q": f"({query}) site:{domain}", "format": "rss", "count": limit})
    url = f"https://www.bing.com/search?{params}"
    results = parse_rss(
        fetch_bytes(url, timeout=timeout, user_agent=user_agent),
        topic=topic,
        platform=platform,
        source="Bing RSS",
    )
    allowed_hosts = {domain, f"www.{domain}", f"mobile.{domain}"}
    return [
        item for item in results
        if (urllib.parse.urlsplit(item.url).hostname or "").lower() in allowed_hosts
    ][:limit]


def search_news(topic: str, query: str, *, limit: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"q": query, "format": "rss", "count": limit})
    return parse_rss(fetch_bytes(f"https://www.bing.com/news/search?{params}", timeout=timeout, user_agent=user_agent), topic=topic, platform="news", source="Bing News RSS")[:limit]


def search_site_rss(topic: str, query: str, domain: str, platform: str, *, limit: int, timeout: int, user_agent: str, source: str = "Bing RSS") -> list[Item]:
    params = urllib.parse.urlencode({"q": f"({query}) site:{domain}", "format": "rss", "count": limit})
    results = parse_rss(fetch_bytes(f"https://www.bing.com/search?{params}", timeout=timeout, user_agent=user_agent), topic=topic, platform=platform, source=source)
    expected = domain.lower().removeprefix("www.")
    return [
        item for item in results
        if (lambda host: host == expected or host.endswith("." + expected))(
            (urllib.parse.urlsplit(item.url).hostname or "").lower().removeprefix("www.")
        )
    ][:limit]


def search_bluesky(topic: str, query: str, *, limit: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"q": query, "limit": min(limit, 100), "sort": "latest"})
    try:
        payload = fetch_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{params}", timeout=timeout, user_agent=user_agent)
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        return search_site_rss(topic, query, "bsky.app", "bluesky", limit=limit, timeout=timeout, user_agent=user_agent, source="Bing RSS (Bluesky fallback)")
    results = []
    for row in payload.get("posts", []):
        record, author = row.get("record", {}), row.get("author", {})
        uri = str(row.get("uri", ""))
        rkey = uri.rsplit("/", 1)[-1]
        handle = str(author.get("handle", ""))
        if not handle or not rkey:
            continue
        text = clean_text(str(record.get("text", ""))) or "Bluesky post"
        results.append(Item(topic, "bluesky", text[:160], f"https://bsky.app/profile/{handle}/post/{rkey}", record.get("createdAt"), f"@{handle}", text, "Bluesky public AppView"))
    return results


def search_hackernews(topic: str, query: str, *, limit: int, after: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"query": query, "tags": "story", "numericFilters": f"created_at_i>{after}", "hitsPerPage": limit})
    payload = fetch_json(f"https://hn.algolia.com/api/v1/search_by_date?{params}", timeout=timeout, user_agent=user_agent)
    return [Item(topic, "hackernews", clean_text(row.get("title")) or "Hacker News story", f"https://news.ycombinator.com/item?id={row['objectID']}", iso_timestamp(row.get("created_at_i")), row.get("author"), clean_text(row.get("story_text")), "HN Algolia") for row in payload.get("hits", []) if row.get("objectID")]


def search_github(topic: str, query: str, *, limit: int, cutoff: dt.datetime, timeout: int, user_agent: str, newly_created: bool = False, trending: bool = False) -> list[Item]:
    since = cutoff.date().isoformat()
    qualifier = "created" if newly_created else "pushed"
    sort = "stars" if newly_created or trending else "updated"
    stars = " stars:>=25" if trending else ""
    params = urllib.parse.urlencode({"q": f"{query} {qualifier}:>={since}{stars}", "sort": sort, "order": "desc", "per_page": min(limit, 100)})
    payload = fetch_json(f"https://api.github.com/search/repositories?{params}", timeout=timeout, user_agent=user_agent)
    platform = "github_new" if newly_created else "github_trending" if trending else "github"
    return [Item(topic, platform, row.get("full_name", "GitHub repository"), row.get("html_url", ""), row.get("created_at") if newly_created else row.get("pushed_at"), row.get("owner", {}).get("login"), clean_text(row.get("description")), "GitHub public search") for row in payload.get("items", []) if row.get("html_url")]


def search_huggingface(topic: str, query: str, *, limit: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"search": query, "sort": "lastModified", "direction": "-1", "limit": limit})
    rows = fetch_json(f"https://huggingface.co/api/models?{params}", timeout=timeout, user_agent=user_agent)
    return [Item(topic, "huggingface", row.get("id", "Hugging Face model"), f"https://huggingface.co/{row['id']}", row.get("lastModified"), row.get("author"), f"downloads: {row.get('downloads', 0)} · likes: {row.get('likes', 0)}", "Hugging Face Hub") for row in rows if row.get("id")]


def search_arxiv(topic: str, query: str, *, limit: int, timeout: int, user_agent: str) -> list[Item]:
    simple = " ".join(re.findall(r"[A-Za-z0-9-]+", query)[:12])
    params = urllib.parse.urlencode({"search_query": f'all:"{simple}"', "start": 0, "max_results": limit, "sortBy": "submittedDate", "sortOrder": "descending"})
    return parse_rss(fetch_bytes(f"https://export.arxiv.org/api/query?{params}", timeout=timeout, user_agent=user_agent), topic=topic, platform="arxiv", source="arXiv")


def search_stackexchange(topic: str, query: str, *, limit: int, after: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"site": "stackoverflow", "q": query, "fromdate": after, "pagesize": min(limit, 100), "order": "desc", "sort": "creation", "filter": "default"})
    payload = fetch_json(f"https://api.stackexchange.com/2.3/search/advanced?{params}", timeout=timeout, user_agent=user_agent)
    return [Item(topic, "stackexchange", clean_text(row.get("title")) or "Stack Overflow question", row.get("link", ""), iso_timestamp(row.get("creation_date")), row.get("owner", {}).get("display_name"), f"score: {row.get('score', 0)} · answers: {row.get('answer_count', 0)}", "Stack Exchange") for row in payload.get("items", []) if row.get("link")]


def search_mastodon(topic: str, query: str, *, limit: int, timeout: int, user_agent: str) -> list[Item]:
    params = urllib.parse.urlencode({"q": f"({query}) (site:mastodon.social OR site:hachyderm.io OR site:fosstodon.org)", "format": "rss", "count": limit})
    return parse_rss(fetch_bytes(f"https://www.bing.com/search?{params}", timeout=timeout, user_agent=user_agent), topic=topic, platform="mastodon", source="Bing RSS (public Mastodon)")[:limit]


def collect_feeds(config: dict[str, Any], *, cutoff: dt.datetime) -> tuple[list[Item], list[Health]]:
    timeout, user_agent = config["request_timeout_seconds"], config.get("user_agent", "SocialNews/1.0")
    limit = config["max_results_per_source"]
    items, health = [], []
    for feed in config.get("feeds", []):
        try:
            found = parse_rss(fetch_bytes(feed["url"], timeout=timeout, user_agent=user_agent), topic=feed["name"], platform=feed["category"], source=feed["name"])
            recent = [item for item in found if not item.published_at or parsed_time(item.published_at) is None or parsed_time(item.published_at) >= cutoff][:limit]
            items.extend(recent)
            health.append(Health(feed["name"], feed["category"], True, len(recent)))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as exc:
            health.append(Health(feed["name"], feed["category"], False, error=f"{type(exc).__name__}: {exc}"))
    return items, health


def collect_sites(config: dict[str, Any]) -> tuple[list[Item], list[Health]]:
    timeout, user_agent = config["request_timeout_seconds"], config.get("user_agent", "SocialNews/1.0")
    limit, items, health = config["max_results_per_source"], [], []
    for site in config.get("sites", []):
        try:
            found = search_site_rss(site["name"], site.get("query", "AI"), site["domain"], site["category"], limit=limit, timeout=timeout, user_agent=user_agent, source=f"Bing RSS ({site['domain']})")
            items.extend(found)
            health.append(Health(site["name"], site["category"], True, len(found)))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as exc:
            health.append(Health(site["name"], site["category"], False, error=f"{type(exc).__name__}: {exc}"))
    return items, health


def parsed_time(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def collect(config: dict[str, Any]) -> tuple[list[Item], list[Health]]:
    limit = config["max_results_per_source"]
    timeout = config["request_timeout_seconds"]
    user_agent = config.get("user_agent", "SocialNews/1.0")
    cutoff = utc_now() - dt.timedelta(days=config["lookback_days"])
    items, health = collect_feeds(config, cutoff=cutoff)
    site_items, site_health = collect_sites(config)
    items.extend(site_items)
    health.extend(site_health)
    for entry in config["queries"]:
        topic = entry["name"].strip()
        query = entry["query"].strip()
        for platform in entry["platforms"]:
            try:
                if platform == "reddit":
                    found = search_reddit(
                        topic, query, limit=limit, after=int(cutoff.timestamp()),
                        timeout=timeout, user_agent=user_agent,
                    )
                elif platform in INDEXED_DOMAINS:
                    found = search_bing_rss(
                        topic, query, platform, limit=limit, timeout=timeout, user_agent=user_agent
                    )
                elif platform == "news": found = search_news(topic, query, limit=limit, timeout=timeout, user_agent=user_agent)
                elif platform == "bluesky": found = search_bluesky(topic, query, limit=limit, timeout=timeout, user_agent=user_agent)
                elif platform == "hackernews": found = search_hackernews(topic, query, limit=limit, after=int(cutoff.timestamp()), timeout=timeout, user_agent=user_agent)
                elif platform in {"github", "github_new", "github_trending"}: found = search_github(topic, query, limit=limit, cutoff=cutoff, timeout=timeout, user_agent=user_agent, newly_created=platform == "github_new", trending=platform == "github_trending")
                elif platform == "huggingface": found = search_huggingface(topic, query, limit=limit, timeout=timeout, user_agent=user_agent)
                elif platform == "arxiv": found = search_arxiv(topic, query, limit=limit, timeout=timeout, user_agent=user_agent)
                elif platform == "stackexchange": found = search_stackexchange(topic, query, limit=limit, after=int(cutoff.timestamp()), timeout=timeout, user_agent=user_agent)
                elif platform == "mastodon": found = search_mastodon(topic, query, limit=limit, timeout=timeout, user_agent=user_agent)
                else: raise ValueError(f"No adapter for {platform}")
                recent = []
                for item in found:
                    if not item.published_at:
                        recent.append(item)
                        continue
                    try:
                        parsed = parsed_time(item.published_at)
                        if parsed is None or parsed >= cutoff:
                            recent.append(item)
                    except ValueError:
                        recent.append(item)
                found = recent
                items.extend(found)
                health.append(Health(topic=topic, platform=platform, ok=True, found=len(found)))
            except (OSError, ValueError, json.JSONDecodeError, ET.ParseError, urllib.error.URLError) as exc:
                health.append(
                    Health(topic=topic, platform=platform, ok=False, error=f"{type(exc).__name__}: {exc}")
                )
    return items, health


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": {}}
    state = load_json(path)
    if state.get("version") != 1 or not isinstance(state.get("items"), dict):
        raise ValueError(f"Unsupported or malformed state file: {path}")
    return state


def update_state(
    state: dict[str, Any], items: list[Item], *, now: dt.datetime, retention_days: int
) -> tuple[list[Item], dict[str, Any]]:
    cutoff = now - dt.timedelta(days=retention_days)
    retained: dict[str, Any] = {}
    for key, value in state["items"].items():
        try:
            first_seen = dt.datetime.fromisoformat(value["first_seen"])
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=dt.timezone.utc)
            if first_seen >= cutoff:
                retained[key] = value
        except (KeyError, TypeError, ValueError):
            continue

    new_items: list[Item] = []
    seen_this_run: set[str] = set()
    for item in items:
        key = fingerprint(item)
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        if key not in retained:
            new_items.append(item)
            retained[key] = {
                "first_seen": now.isoformat(),
                "platform": item.platform,
                "topic": item.topic,
                "url": canonicalize_url(item.url),
            }
    return new_items, {"version": 1, "items": dict(sorted(retained.items()))}


def md_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def rank_items(items: list[Item], limit: int) -> list[Item]:
    priority = {
        "official": 100, "policy": 90, "news": 80, "hackernews": 75,
        "github": 70, "github_new": 80, "github_trending": 85, "huggingface": 70, "arxiv": 65, "bluesky": 60,
        "reddit": 60, "stackexchange": 55, "mastodon": 50, "youtube": 50,
        "x": 45, "linkedin": 40, "threads": 35, "instagram": 35, "tiktok": 35,
    }
    def key(item: Item) -> tuple[int, float, str]:
        published = parsed_time(item.published_at) if item.published_at else None
        return (priority.get(item.platform, 0), published.timestamp() if published else 0, item.title.lower())
    return sorted(items, key=key, reverse=True)[:limit]


def render_report(
    *, now: dt.datetime, configured_queries: int, collected_count: int,
    new_items: list[Item], health: list[Health], max_report_items: int = 100
) -> str:
    report_items = rank_items(new_items, max_report_items)
    successful = sum(1 for entry in health if entry.ok)
    failed = len(health) - successful
    lines = [
        "# SocialNews daily report", "", f"- generated_at: `{now.isoformat()}`",
        f"- configured_topics: `{configured_queries}`", f"- source_checks: `{len(health)}`",
        f"- successful_checks: `{successful}`", f"- failed_checks: `{failed}`",
        f"- collected_candidates: `{collected_count}`", f"- new_items: `{len(new_items)}`",
        f"- reported_items: `{len(report_items)}`", "",
    ]
    if configured_queries == 0:
        lines.extend([
            "> No searches are configured. Add topics to `config/searches.json`, commit the change, and run the workflow again.",
            "",
        ])
    lines.extend(["## Collection health", ""])
    if not health:
        lines.extend(["No sources were attempted.", ""])
    else:
        lines.extend(["| Topic | Platform | Status | Candidates |", "|---|---|---:|---:|"])
        for entry in health:
            status = "ok" if entry.ok else f"failed: {entry.error}"
            lines.append(f"| {md_escape(entry.topic)} | {entry.platform} | {md_escape(status)} | {entry.found} |")
        lines.append("")
    lines.extend(["## New findings", ""])
    if not report_items:
        lines.extend(["No new links were discovered during this run.", ""])
    else:
        groups: dict[tuple[str, str], list[Item]] = {}
        for item in report_items:
            groups.setdefault((item.topic, item.platform), []).append(item)
        for (topic, platform), grouped_items in sorted(groups.items()):
            lines.extend([f"### {topic} — {platform}", ""])
            for item in grouped_items:
                metadata = [part for part in (item.author, item.published_at, item.source) if part]
                lines.append(f"- [{md_escape(item.title)}]({item.url})")
                if metadata:
                    lines.append(f"  - {' · '.join(md_escape(part) for part in metadata)}")
                if item.summary:
                    excerpt = item.summary[:500] + ("…" if len(item.summary) > 500 else "")
                    lines.append(f"  - {md_escape(excerpt)}")
            lines.append("")
    lines.extend([
        "## Interpretation limits", "",
        f"- The report ranks and caps new findings at {len(report_items)} items; collection and deduplication still process every candidate.",
        "- A successful check with zero candidates means the upstream discovery source returned no matches; it does not prove that the social platform had no relevant posts.",
        "- X, Instagram, Threads, TikTok, LinkedIn, YouTube keyword, and cross-instance Mastodon results use anonymous search-engine indexes and are necessarily incomplete.",
        "- Reddit first uses an independent public archive and falls back to indexed discovery when it is blocked or rate-limited; Hacker News search uses its public Algolia index.",
        "- A public endpoint may expose only a platform-defined subset even when the request succeeds.",
        "- Private, deleted, login-gated, and non-indexed content is unavailable.", "",
    ])
    return "\n".join(lines)


def write_outputs(
    *, state_path: Path, output_dir: Path, state: dict[str, Any], report: str, now: dt.datetime
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "latest.md").write_text(report, encoding="utf-8")
    (output_dir / f"{now.date().isoformat()}.md").write_text(report, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/searches.json"))
    parser.add_argument("--state", type=Path, default=Path("data/seen.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        config = load_json(args.config)
        validate_config(config)
        state = load_state(args.state)
        now = utc_now()
        items, health = collect(config)
        new_items, updated_state = update_state(
            state, items, now=now, retention_days=config["seen_retention_days"]
        )
        report = render_report(
            now=now, configured_queries=len(config["queries"]), collected_count=len(items),
            new_items=new_items, health=health, max_report_items=config.get("max_report_items", 100),
        )
        write_outputs(
            state_path=args.state, output_dir=args.output_dir, state=updated_state, report=report, now=now
        )
        print(
            f"SocialNews: {len(items)} candidates, {len(new_items)} new, "
            f"{sum(not entry.ok for entry in health)} source failures"
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SocialNews configuration/output error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
