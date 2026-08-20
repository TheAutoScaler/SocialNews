# SocialNews

SocialNews is an account-free social-discovery pipeline designed to run entirely in GitHub Actions. It searches configured topics on a daily cron, writes a durable Markdown report into this repository, and keeps compact history so later runs show only newly discovered links.

The second half is a native ChatGPT scheduled task. GitHub Actions performs network collection; ChatGPT reads `reports/latest.md` after the Action finishes and presents it alongside the rest of your scheduled items. Nothing depends on a Mac remaining powered on.

## Status at a glance

- Workflow: `.github/workflows/social-news.yml`
- Collector: `src/socialnews.py`
- Configuration: `config/searches.json`
- Deduplication state: `data/seen.json`
- Stable ChatGPT input: `reports/latest.md`
- Historical reports: `reports/YYYY-MM-DD.md`
- Tests: `tests/test_socialnews.py`
- Default collection schedule: `05:00 UTC` daily
- ChatGPT briefing schedule: `06:00 Europe/London` daily

The collector uses only Python's standard library. This keeps runs inexpensive, auditable, and resistant to dependency abandonment.

GitHub Actions are pinned to immutable commit SHAs. A separate weekly workflow checks the reviewed major-version tags, updates those SHAs in place, runs the complete test suite, and commits only verified changes. This deliberately avoids Dependabot and its pull-request notifications. Major-version upgrades remain manual because they can contain breaking changes and should not be accepted without review.

## Architecture

```text
GitHub Actions cron (05:00 UTC)
        |
        v
Python collector
  |-- Reddit: PullPush public endpoint
  |-- X: anonymous Bing RSS discovery for site:x.com
  |-- Instagram: anonymous Bing RSS discovery for site:instagram.com
        |
        v
Normalize + deduplicate against data/seen.json
        |
        +--> reports/YYYY-MM-DD.md
        +--> reports/latest.md
        +--> data/seen.json
        |
        v
Commit generated files to main
        |
        v
Native ChatGPT scheduled task reads reports/latest.md
and displays a digest in the ChatGPT/Codex app
```

### Why it is split in two

GitHub Actions is the collection runner because it provides a durable repository, cron execution, logs, and no requirement for a personal computer to be online. A native ChatGPT scheduled task is the presentation layer because it appears in the app's Scheduled section and can maintain conversational context.

The ChatGPT task does not execute this local checkout. Web scheduled tasks cannot access a Mac folder. Once this repository is public, the task reads the pushed report from `https://raw.githubusercontent.com/TheAutoScaler/SocialNews/main/reports/latest.md` using ordinary web access.

### Authentication boundary

There is no direct authenticated connection from the GitHub Action to ChatGPT, and neither side sends a credential to the other.

- **GitHub Actions → repository:** GitHub injects a short-lived `GITHUB_TOKEN` into each workflow run. The workflow's `contents: write` permission lets that run commit `reports/` and `data/seen.json` back to this repository. No personal access token or OpenAI secret is stored in the repository.
- **ChatGPT → report:** the native ChatGPT task uses ordinary web access to read the public raw URL. It has no GitHub plugin, OAuth connection, personal access token, or repository write access.
- **The handoff:** `reports/latest.md` on the default branch is the interface between the two systems. GitHub Actions writes it; the later ChatGPT task reads it.

The briefing cannot read the raw URL while the repository is private. Disabling Actions or removing `contents: write` stops report updates but does not affect public read access to already committed reports.

## Data sources and honest limitations

This project deliberately avoids social-network accounts and official social-network API credentials.

### Reddit

Reddit discovery uses the public PullPush endpoint. It normally provides structured post data without authentication. PullPush is an independent archival service, not Reddit itself, so it may lag, rate-limit, or become unavailable. A failed source is recorded in the report rather than treated as an empty result.

### X and Instagram

X and Instagram aggressively restrict anonymous automated access. SocialNews therefore uses Bing RSS search output with a `site:` restriction. This can find indexed public pages, but it is not complete, real-time, or guaranteed. It cannot see private posts, login-gated material, or content the search engine has not indexed.

The collector does not bypass login walls, CAPTCHAs, access controls, or anti-bot protections. If comprehensive X or Instagram coverage becomes important, replace the discovery adapter with an approved provider or official API and document its credentials and costs.

## Initial setup

### 1. Configure searches

Edit `config/searches.json`. The repository ships with no topics so it does not invent your interests. Add one object per topic:

```json
{
  "queries": [
    {
      "name": "Example product mentions",
      "query": "\"Example Product\" OR exampleproduct",
      "platforms": ["reddit", "x", "instagram"]
    },
    {
      "name": "A focused Reddit topic",
      "query": "agentic coding",
      "platforms": ["reddit"]
    }
  ],
  "max_results_per_source": 20,
  "lookback_days": 7,
  "request_timeout_seconds": 20,
  "seen_retention_days": 180,
  "user_agent": "SocialNews/1.0 (+https://github.com/TheAutoScaler/SocialNews)"
}
```

Supported platforms are `reddit`, `x`, and `instagram`. Query names become report headings. Keep queries focused; anonymous search-engine discovery deteriorates with large Boolean expressions.

`lookback_days` limits dated results to a recent window and is also sent to PullPush as its `after` boundary. Results without a parseable publication time are retained because dropping them would silently hide potentially current posts. X and Instagram discovery additionally rejects any search result whose final hostname is not the requested platform; this prevents search engines that ignore `site:` from contaminating the report with ordinary web pages.

### 2. Enable GitHub Actions write access

The workflow declares `contents: write` because it commits reports and state. Repository or organization policy can override that permission.

In GitHub, open **Settings** → **Actions** → **General** → **Workflow permissions**, then select **Read and write permissions** if required.

No repository secrets are required by the account-free implementation or the action-pin updater.

### Branch-protection trade-off

Both scheduled workflows commit generated or maintenance changes directly to `main` with the repository-scoped `GITHUB_TOKEN`. A rule that blocks all direct pushes to `main` will therefore break report generation and self-updates. Do not enable such a rule unless generated output is first moved to a separate branch or the workflows are redesigned to use reviewed pull requests.

### 3. Run it manually once

Open **Actions** → **Social News** → **Run workflow**. Inspect the log and `reports/latest.md`. A manual run uses the same path as cron.

The Action only commits when generated files change. Its commit message includes `[skip ci]` to avoid a generated-commit workflow loop.

### 4. Create the native ChatGPT scheduled task

After making the repository public, create a web scheduled task using this prompt. No GitHub connection is required:

```text
Every day after the SocialNews GitHub Action has completed, read
https://raw.githubusercontent.com/TheAutoScaler/SocialNews/main/reports/latest.md
using ordinary web access. Do not use or request a GitHub account connection.

Present it as a concise daily social-news briefing. Lead with the most
important new findings, preserve source links, group findings by configured
topic and platform, and explicitly mention failures or partial coverage. If
the report says no searches are configured, tell me to edit
config/searches.json. If generated_at is not from today, warn that the
GitHub Action may be delayed or failing. Do not claim that an empty source
means there were no posts; distinguish zero findings from collection failure.
```

Schedule it for `06:00 Europe/London`. The native task and GitHub cron are independent; if you change one, review the other.

## Schedule and time zones

The workflow contains:

```yaml
schedule:
  - cron: "0 5 * * *"
```

GitHub cron uses UTC. `05:00 UTC` is `06:00` in the UK during British Summer Time and `05:00` during Greenwich Mean Time. The ChatGPT task is fixed at `06:00 Europe/London`. Consequently, the schedules coincide during British Summer Time and have a one-hour gap during Greenwich Mean Time. GitHub may also start scheduled workflows late during high load, so the ChatGPT task's stale-report warning remains important.

GitHub may start scheduled workflows late during high load. The workflow also supports `workflow_dispatch` for manual runs.

## Local development

Python 3.11 or newer is recommended.

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Run without changing real state:

```bash
tmp_dir="$(mktemp -d)"
python3 src/socialnews.py \
  --config config/searches.json \
  --state "$tmp_dir/seen.json" \
  --output-dir "$tmp_dir/reports"
```

Run against repository files:

```bash
python3 src/socialnews.py
```

Individual source failures do not fail the entire command because partial reports remain useful. Configuration and filesystem errors exit non-zero. Source failures appear under **Collection health**.

## Generated report contract

`reports/latest.md` is the stable interface consumed by ChatGPT. It contains:

- an ISO-8601 `generated_at` timestamp;
- counts for topics, checks, candidates, failures, and new items;
- health for every attempted topic/platform pair;
- results grouped by topic and platform;
- explicit interpretation limits.

Historical files use the UTC date. A second run on the same date replaces that day's report. `data/seen.json` prevents previously seen links from being presented as new.

The state file stores fingerprints, first-seen timestamps, topics, platforms, and canonical URLs. Entries older than `seen_retention_days` are pruned so the repository remains small.

## Operations and troubleshooting

### The workflow runs but finds nothing

First check `config/searches.json`. Then read **Collection health**. A successful zero-result check differs from a failed upstream request, and neither proves the platform contains no matching content.

### Git push is rejected

Confirm Actions has `contents: write`. Branch protection may prohibit pushes from `github-actions[bot]`. Allow the bot, change the workflow to open a pull request, or store reports on an unprotected branch.

### A source reports an HTTP error

Anonymous discovery fails occasionally. The collector catches per-source failures and continues. Repeated failures usually mean the upstream endpoint changed or blocks GitHub-hosted runners. Update the relevant adapter; do not convert failures into empty-success responses.

### The ChatGPT briefing is stale

Compare `generated_at` with today's date, inspect the latest Actions run, and confirm that the repository and raw report URL are public.

### Duplicate links appear

URLs are canonicalized before hashing: fragments and common tracking parameters are removed. Search-engine redirect URLs can still change. Improve `canonicalize_url` or add a platform-specific ID rather than deleting all state.

### Reset history

Replace `data/seen.json` with:

```json
{
  "version": 1,
  "items": {}
}
```

Commit it and run the workflow manually. Git history makes the reset reversible.

## Security and maintenance

- Treat fetched titles/descriptions as untrusted content. They are quoted into Markdown, never executed.
- The collector only makes HTTP `GET` requests.
- No cookies, browser profiles, or social credentials are used.
- Never add API keys to configuration, workflow YAML, reports, or logs. Use Actions secrets if authenticated adapters are added.
- Third-party actions are pinned to immutable SHAs. `scripts/update_actions.py` follows only the explicitly reviewed major tags recorded in workflow comments.
- The updater runs weekly at `04:00 UTC` on Sunday, tests before committing, and creates no Dependabot pull requests.
- Review generated links before acting on claims; social posts and snippets are not authoritative evidence.
- Respect platform terms, robots policies, rate limits, and applicable law.
- Periodically review and pin third-party Actions more tightly if stronger supply-chain controls are required.

## Design history

### 2026-08-20 — Initial implementation

- Added a dependency-free Python collector.
- Added PullPush Reddit discovery.
- Added Bing RSS discovery for public X and Instagram pages.
- Added normalized reports, per-source health, URL canonicalization, deduplication, and retention pruning.
- Added a daily GitHub Actions cron and manual dispatch.
- Chose committed reports/state so GitHub Actions and ChatGPT share durable results without a local machine.
- Chose a separate native ChatGPT scheduled task as the app-visible presentation layer.
- Updated GitHub's official checkout and Python setup Actions to their Node 24-compatible v7 majors after the first hosted run exposed deprecation warnings.

## References

- [ChatGPT scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [Codex GitHub Action](https://learn.chatgpt.com/docs/github-action)
- [GitHub scheduled events](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#schedule)
- [Makerskills social-fetch](https://github.com/coreyhaines31/makerskills/blob/main/skills/social-fetch/SKILL.md)
