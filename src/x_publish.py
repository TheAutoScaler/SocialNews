#!/usr/bin/env python3
"""Queue an approved X draft and publish due drafts exactly once."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = ROOT / "data" / "x_queue.json"
X_CREATE_POST_URL = "https://api.x.com/2/tweets"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("publish_at must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "posts": []}
    with path.open(encoding="utf-8") as handle:
        queue = json.load(handle)
    if not isinstance(queue, dict) or queue.get("version") != 1 or not isinstance(queue.get("posts"), list):
        raise ValueError(f"{path} is not a version 1 X queue")
    return queue


def save_queue(path: Path, queue: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def queue_post(
    queue: dict[str, Any], post_id: str, text: str, publish_at: dt.datetime, approved_at: dt.datetime
) -> dict[str, Any]:
    post_id = post_id.strip()
    text = text.strip()
    if not post_id:
        raise ValueError("post_id must not be empty")
    if not text:
        raise ValueError("approved text must not be empty")
    if any(post.get("id") == post_id for post in queue["posts"]):
        raise ValueError(f"post id {post_id!r} already exists; approved posts are immutable")
    post = {
        "id": post_id,
        "status": "approved",
        "text": text,
        "content_hash": content_hash(text),
        "approved_at": isoformat(approved_at),
        "publish_at": isoformat(publish_at),
        "published_at": None,
        "x_post_id": None,
        "x_post_url": None,
        "last_error": None,
    }
    queue["posts"].append(post)
    return post


def oauth_quote(value: str) -> str:
    return urllib.parse.quote(value, safe="~-._")


def oauth1_header(method: str, url: str, credentials: dict[str, str], nonce: str | None = None,
                  timestamp: str | None = None) -> str:
    parameters = {
        "oauth_consumer_key": credentials["consumer_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": credentials["access_token"],
        "oauth_version": "1.0",
    }
    normalized = "&".join(f"{oauth_quote(key)}={oauth_quote(value)}" for key, value in sorted(parameters.items()))
    base = "&".join((method.upper(), oauth_quote(url), oauth_quote(normalized)))
    signing_key = f"{oauth_quote(credentials['consumer_secret'])}&{oauth_quote(credentials['access_token_secret'])}"
    signature = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    parameters["oauth_signature"] = signature
    values = ", ".join(f'{oauth_quote(key)}="{oauth_quote(value)}"' for key, value in sorted(parameters.items()))
    return f"OAuth {values}"


def authorization_header(environment: dict[str, str]) -> str:
    bearer = environment.get("X_USER_ACCESS_TOKEN", "").strip()
    if bearer:
        return f"Bearer {bearer}"
    names = {
        "consumer_key": "X_CONSUMER_KEY",
        "consumer_secret": "X_CONSUMER_SECRET",
        "access_token": "X_ACCESS_TOKEN",
        "access_token_secret": "X_ACCESS_TOKEN_SECRET",
    }
    missing = [name for name in names.values() if not environment.get(name, "").strip()]
    if missing:
        raise ValueError("missing X credentials: provide X_USER_ACCESS_TOKEN or " + ", ".join(missing))
    credentials = {key: environment[name].strip() for key, name in names.items()}
    return oauth1_header("POST", X_CREATE_POST_URL, credentials)


def create_x_post(text: str, environment: dict[str, str] | None = None) -> dict[str, str]:
    environment = environment or os.environ
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        X_CREATE_POST_URL,
        data=payload,
        method="POST",
        headers={"Authorization": authorization_header(environment), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"X API returned HTTP {error.code}: {detail}") from error
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise RuntimeError(f"X API returned an unexpected response: {result!r}")
    return {"id": data["id"], "text": str(data.get("text", text))}


def publish_due(queue: dict[str, Any], now: dt.datetime, live: bool,
                publisher=create_x_post) -> list[str]:
    messages: list[str] = []
    for post in queue["posts"]:
        if post.get("status") != "approved" or parse_datetime(post["publish_at"]) > now:
            continue
        if content_hash(post.get("text", "")) != post.get("content_hash"):
            raise ValueError(f"approved text for {post.get('id')!r} changed after approval")
        if not live:
            messages.append(f"DRY RUN: {post['id']} is due ({len(post['text'])} characters)")
            continue
        try:
            result = publisher(post["text"])
        except Exception as error:
            post["last_error"] = str(error)[:2000]
            messages.append(f"FAILED: {post['id']}: {post['last_error']}")
            continue
        post["status"] = "published"
        post["published_at"] = isoformat(now)
        post["x_post_id"] = result["id"]
        post["x_post_url"] = f"https://x.com/i/web/status/{result['id']}"
        post["last_error"] = None
        messages.append(f"PUBLISHED: {post['id']}: {post['x_post_url']}")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    queue_parser = subparsers.add_parser("queue")
    queue_parser.add_argument("--id", required=True)
    queue_parser.add_argument("--text", required=True)
    queue_parser.add_argument("--publish-at", required=True, help="ISO-8601 time, normally Monday 17:00Z")
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--live", action="store_true", help="Actually call X; default is a dry run")
    args = parser.parse_args()
    queue = load_queue(args.queue)
    if args.command == "queue":
        post = queue_post(queue, args.id, args.text, parse_datetime(args.publish_at), utc_now())
        save_queue(args.queue, queue)
        print(f"QUEUED: {post['id']} for {post['publish_at']} ({len(post['text'])} characters)")
        return 0
    messages = publish_due(queue, utc_now(), args.live)
    save_queue(args.queue, queue)
    for message in messages:
        print(message)
    if not messages:
        print("No approved X posts are due.")
    return 1 if any(message.startswith("FAILED:") for message in messages) else 0


if __name__ == "__main__":
    raise SystemExit(main())
