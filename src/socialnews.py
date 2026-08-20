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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SUPPORTED_PLATFORMS = {"reddit", "x", "instagram"}
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


def fetch_bytes(url: str, *, timeout: int, user_agent: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


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
    payload = json.loads(fetch_bytes(url, timeout=timeout, user_agent=user_agent))
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
    return results


def search_bing_rss(
    topic: str, query: str, platform: str, *, limit: int, timeout: int, user_agent: str
) -> list[Item]:
    domain = {"x": "x.com", "instagram": "instagram.com"}[platform]
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


def collect(config: dict[str, Any]) -> tuple[list[Item], list[Health]]:
    limit = config["max_results_per_source"]
    timeout = config["request_timeout_seconds"]
    user_agent = config.get("user_agent", "SocialNews/1.0")
    cutoff = utc_now() - dt.timedelta(days=config["lookback_days"])
    items: list[Item] = []
    health: list[Health] = []
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
                else:
                    found = search_bing_rss(
                        topic, query, platform, limit=limit, timeout=timeout, user_agent=user_agent
                    )
                recent = []
                for item in found:
                    if not item.published_at:
                        recent.append(item)
                        continue
                    try:
                        if dt.datetime.fromisoformat(item.published_at) >= cutoff:
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


def render_report(
    *, now: dt.datetime, configured_queries: int, collected_count: int,
    new_items: list[Item], health: list[Health]
) -> str:
    successful = sum(1 for entry in health if entry.ok)
    failed = len(health) - successful
    lines = [
        "# SocialNews daily report", "", f"- generated_at: `{now.isoformat()}`",
        f"- configured_topics: `{configured_queries}`", f"- source_checks: `{len(health)}`",
        f"- successful_checks: `{successful}`", f"- failed_checks: `{failed}`",
        f"- collected_candidates: `{collected_count}`", f"- new_items: `{len(new_items)}`", "",
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
    if not new_items:
        lines.extend(["No new links were discovered during this run.", ""])
    else:
        groups: dict[tuple[str, str], list[Item]] = {}
        for item in new_items:
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
        "- A successful check with zero candidates means the upstream discovery source returned no matches; it does not prove that the social platform had no relevant posts.",
        "- X and Instagram results come from anonymously accessible search-engine indexes and are necessarily incomplete.",
        "- Reddit results come from an independent public archive and may lag Reddit.",
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
            new_items=new_items, health=health,
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
