#!/usr/bin/env python3
"""Update pinned GitHub Action SHAs while retaining reviewed major-version tags."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PIN = re.compile(
    r"(?P<prefix>uses:\s+)(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@"
    r"(?P<sha>[0-9a-f]{40})(?P<suffix>\s+#\s+(?P<tag>v\d+)\s*)$",
    re.MULTILINE,
)


def github_json(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SocialNews action updater"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def resolve_tag(repo: str, tag: str) -> str:
    ref = github_json(f"https://api.github.com/repos/{repo}/git/ref/tags/{tag}")
    target = ref["object"]
    if not isinstance(target, dict):
        raise ValueError(f"Unexpected tag response for {repo}@{tag}")
    while target.get("type") == "tag":
        tag_object = github_json(f"https://api.github.com/repos/{repo}/git/tags/{target['sha']}")
        target = tag_object["object"]
        if not isinstance(target, dict):
            raise ValueError(f"Unexpected annotated tag response for {repo}@{tag}")
    sha = target.get("sha")
    if target.get("type") != "commit" or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"{repo}@{tag} did not resolve to a commit SHA")
    return sha


def update_text(text: str, resolver=resolve_tag) -> tuple[str, list[str]]:
    changes: list[str] = []
    resolved: dict[tuple[str, str], str] = {}

    def replace(match: re.Match[str]) -> str:
        key = (match["repo"], match["tag"])
        new_sha = resolved.setdefault(key, resolver(*key))
        if new_sha != match["sha"]:
            changes.append(f"{key[0]}@{key[1]}: {match['sha']} -> {new_sha}")
        return f"{match['prefix']}{key[0]}@{new_sha}{match['suffix']}"

    return PIN.sub(replace, text), changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Write verified pin updates")
    args = parser.parse_args()
    all_changes: list[str] = []
    pending: list[tuple[Path, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        original = path.read_text(encoding="utf-8")
        updated, changes = update_text(original)
        all_changes.extend(f"{path.relative_to(ROOT)}: {change}" for change in changes)
        pending.append((path, updated))
    for change in all_changes:
        print(change)
    if args.write:
        for path, updated in pending:
            path.write_text(updated, encoding="utf-8")
    elif all_changes:
        print("Updates available; rerun with --write.")
    else:
        print("Action pins are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
