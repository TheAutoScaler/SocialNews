# SocialNews

SocialNews is a vibe-coded AI news aggregator that pulls signals from across the web. It runs as a scheduled GitHub Action, writes the latest collection to [`reports/latest.md`](reports/latest.md), and is designed to hand that report to an AI such as ChatGPT on a separate schedule for deduplication and summarisation. I built it for personal use, but you are welcome to fork it and make it your own.

The second half is a native ChatGPT scheduled task. GitHub Actions performs network collection; ChatGPT reads `reports/latest.md` after the Action finishes and presents it alongside the rest of your scheduled items. Nothing depends on a Mac remaining powered on.

## Status at a glance

- Workflow: `.github/workflows/social-news.yml`
- Collector: `src/socialnews.py`
- Configuration: `config/searches.json`
- Deduplication state: `data/seen.json`
- Stable ChatGPT input: `reports/latest.md`
- Historical reports: `reports/YYYY-MM-DD.md`
- Tests: `tests/test_socialnews.py`
- License: Apache License 2.0
- Default collection schedule: Monday at `05:00 UTC`
- ChatGPT briefing schedule: Monday at `06:00 Europe/London`
- Approved X publication schedule: Monday at `17:00 UTC` (17:00 GMT / 18:00 BST)

The collector uses only Python's standard library. This keeps runs inexpensive, auditable, and resistant to dependency abandonment.

GitHub Actions are pinned to immutable commit SHAs. A separate weekly workflow checks the reviewed major-version tags, updates those SHAs in place, runs the complete test suite, and commits only verified changes. This deliberately avoids Dependabot and its pull-request notifications. Major-version upgrades remain manual because they can contain breaking changes and should not be accepted without review.

## Architecture

```text
GitHub Actions cron (Monday 05:00 UTC)
        |
        v
Python collector
  |-- Direct public search: GitHub, Hugging Face, arXiv, Stack Exchange
  |-- Public social search: Bluesky, Hacker News, Reddit
  |-- RSS/Atom: official labs, blogs, channels, podcasts, forums
  |-- News discovery: Bing News RSS
  |-- Official-site discovery: validated Google News RSS sources
  |-- Indexed social discovery: X, Instagram, Threads, TikTok, LinkedIn, YouTube, Mastodon
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
and displays a digest plus one long-form X draft
        |
        v
Explicit approval queues immutable copy in data/x_queue.json
        |
        v
Monday 17:00 UTC Action publishes through the X API
```

### Why it is split in two

GitHub Actions is the collection runner because it provides a durable repository, cron execution, logs, and no requirement for a personal computer to be online. A native ChatGPT scheduled task is the presentation layer because it appears in the app's Scheduled section and can maintain conversational context.

The ChatGPT task does not execute this local checkout. Web scheduled tasks cannot access a Mac folder. Once this repository is public, the task reads the pushed report from `https://raw.githubusercontent.com/TheAutoScaler/SocialNews/main/reports/latest.md` using ordinary web access.

### Authentication boundary

There is no direct authenticated connection from the GitHub Action to ChatGPT, and neither side sends a credential to the other.

- **GitHub Actions → repository:** GitHub injects a short-lived `GITHUB_TOKEN` into each workflow run. The workflow's `contents: write` permission lets that run commit `reports/` and `data/seen.json` back to this repository. No personal access token or OpenAI secret is stored in the repository.
- **GitHub Actions → Bluesky:** the workflow injects the encrypted `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` secrets only into the collector step. The dedicated app password cannot access direct messages and is exchanged for a short-lived Bluesky session token.
- **ChatGPT → report:** the native ChatGPT task uses ordinary web access to read the public raw URL. It has no GitHub plugin, OAuth connection, personal access token, or repository write access.
- **The handoff:** `reports/latest.md` on the default branch is the interface between the two systems. GitHub Actions writes it; the later ChatGPT task reads it.

The briefing cannot read the raw URL while the repository is private. Disabling Actions or removing `contents: write` stops report updates but does not affect public read access to already committed reports.

## Data sources and honest limitations

Most sources remain account-free. Bluesky is the exception: a dedicated, non-personal account is used because its anonymous search endpoint rejects GitHub-hosted runners.

### Direct and public endpoints

GitHub, Hugging Face, arXiv and Stack Exchange use public read endpoints. Hacker News uses its public Algolia index. Bluesky uses authenticated AppView search when both Actions secrets are present, otherwise it tries the public AppView endpoint and public index discovery. Reddit first uses Reddit's official search RSS feed, then PullPush (an independent public archive), and finally public index discovery. Every unrecovered failure is recorded rather than converted into a false zero-result success.

### Feeds and official sites

The generic RSS/Atom adapter covers official lab blogs, newsletters, podcasts, YouTube channel feeds and public forums. The shipped AI configuration includes live feeds from OpenAI, Google AI, Google DeepMind, NVIDIA, AWS Machine Learning and Hugging Face. Vendors without stable feeds are monitored through Google News RSS domain discovery validated against each item's declared source, including Anthropic, Meta AI, Microsoft AI, Apple ML, xAI, Mistral, Cohere, Stability AI and Runway. The same mechanism monitors NIST, the UK AI Security Institute and the European Commission. These checks are labelled `public index discovery`: a neutral description of the collection method, with the coverage limitation explained here rather than presented as a failure.

### Restricted social networks

X, Instagram, Threads, TikTok and LinkedIn aggressively restrict anonymous automated access. YouTube keyword discovery also lacks a complete anonymous endpoint. SocialNews uses Google News RSS and validates its `<source>` domain for these sources. Links pass through Google News and coverage remains incomplete. Mastodon uses native public hashtag timelines. Results may be delayed and may legitimately be empty.

The collector does not bypass login walls, CAPTCHAs, access controls, or anti-bot protections. If comprehensive X or Instagram coverage becomes important, replace the discovery adapter with an approved provider or official API and document its credentials and costs.

## Initial setup

### 1. Configure searches

Edit `config/searches.json`. The repository ships with a seven-day AI radar covering top news, newly launched AI companies, newly created and trending repositories, new models and tools, innovations and research, agents, policy, safety and funding. Each query selects only the adapters relevant to that signal. `feeds` contains stable RSS/Atom sources; `sites` contains official domains that lack stable feeds.

```json
{
  "queries": [
    {
      "name": "AI agents and coding",
      "query": "AI agent",
      "platforms": ["bluesky", "github", "hackernews", "huggingface", "arxiv", "stackexchange", "mastodon", "news", "x", "youtube"]
    }
  ],
  "feeds": [
    {"name": "OpenAI News", "category": "official", "url": "https://openai.com/news/rss.xml"}
  ],
  "sites": [
    {"name": "Anthropic", "category": "official", "domain": "anthropic.com", "query": "AI OR model OR research"}
  ],
  "max_results_per_source": 15,
  "max_report_items": 100,
  "lookback_days": 7,
  "request_timeout_seconds": 20,
  "seen_retention_days": 180,
  "user_agent": "SocialNews/1.0 (+https://github.com/TheAutoScaler/SocialNews)"
}
```

Supported query platforms are `arxiv`, `bluesky`, `github`, `github_new`, `github_trending`, `hackernews`, `huggingface`, `instagram`, `linkedin`, `mastodon`, `news`, `reddit`, `stackexchange`, `threads`, `tiktok`, `x`, and `youtube`. `github_new` restricts results to repositories created inside the lookback window and sorts them by stars. `github_trending` is an explicit approximation—repositories with at least 25 stars that were active during the lookback window, ranked by stars—because GitHub exposes no official Trending API. `github` finds recently active repositories. Query names become report headings. Keep queries short because syntax differs between upstream services.

`lookback_days` limits dated results and is sent to sources that support a lower time boundary. Results without a parseable publication time are retained because silently dropping them could hide current material. `max_report_items` caps the ranked briefing while collection and deduplication still process every candidate. Indexed platform discovery rejects results outside the requested hostname.

### 2. Enable GitHub Actions write access

The workflow declares `contents: write` because it commits reports and state. Repository or organization policy can override that permission.

In GitHub, open **Settings** → **Actions** → **General** → **Workflow permissions**, then select **Read and write permissions** if required.

### 3. Bluesky search credentials

The repository is configured with a dedicated account, `socialnews-ai.bsky.social`, created solely for read-only SocialNews discovery. Its `SocialNews-GHA` app password does **not** have direct-message access. The workflow receives these encrypted Actions secrets:

- `BLUESKY_HANDLE`
- `BLUESKY_APP_PASSWORD`

The values are never committed, printed in reports, or passed to ChatGPT. GitHub masks secret values in workflow logs. The collector exchanges the app password for a short-lived Bluesky session token at runtime and uses that token only for search. If either secret is absent, it reverts to anonymous search and public index discovery.

To rotate the credential, open Bluesky **Settings → Privacy and security → App passwords**, delete `SocialNews-GHA`, create a replacement without direct-message access, and update `BLUESKY_APP_PASSWORD` under GitHub **Settings → Secrets and variables → Actions**. Deleting the app password revokes pipeline access without changing the main account password.

No other source credential, personal access token, or OpenAI secret is stored in the repository. The action-pin updater requires no secrets.

### Branch-protection trade-off

Both scheduled workflows commit generated or maintenance changes directly to `main` with the repository-scoped `GITHUB_TOKEN`. A rule that blocks all direct pushes to `main` will therefore break report generation and self-updates. Do not enable such a rule unless generated output is first moved to a separate branch or the workflows are redesigned to use reviewed pull requests.

### 4. Run it manually once

Open **Actions** → **Social News** → **Run workflow**. Inspect the log and `reports/latest.md`. A manual run uses the same path as cron.

The Action only commits when generated files change. Its commit message includes `[skip ci]` to avoid a generated-commit workflow loop.

### 5. Create the native ChatGPT scheduled task

After making the repository public, create a web scheduled task using this prompt. No GitHub connection is required:

```text
Every Monday after the SocialNews GitHub Action has completed, read
https://raw.githubusercontent.com/TheAutoScaler/SocialNews/main/reports/latest.md
using ordinary web access. Do not use or request a GitHub account connection.

Create a concise weekly AI intelligence briefing focused on: top AI news,
new AI companies, newly created repositories, new models and tools, and
meaningful innovations or research.

Semantically deduplicate the findings across every topic, platform, and news
source. Treat different headlines or URLs about the same underlying event,
company, release, repository, paper, funding round, or announcement as one
story. Prefer the original company, repository, paper, or regulator as the
primary link, then include up to three useful corroborating source links.
Do not repeat a story in multiple sections. Mention where independent sources
disagree. Rank by significance and novelty, not by the number of duplicate
articles. Omit low-information reposts, SEO pages, and superficial rewrites.

Use sections: Top stories; New companies; New repositories and models;
Innovations and research; Policy, safety and funding; Collection health.
For each item explain what happened, why it matters, and link the evidence.
Explicitly mention failures and partial coverage. If generated_at is not from
today, warn that the GitHub Action may be delayed or failing. Do not claim
that an empty source means there were no posts; distinguish zero findings
from collection failure.
```

Schedule it for Monday at `06:00 Europe/London`. The native task and GitHub cron are independent; if you change one, review the other.

Append these instructions to make the task prepare the weekly X draft:

```text
Also write one long-form X post covering the developments from the previous
seven days that genuinely mattered in AI. Select roughly five to eight items
by lasting significance, technical importance and novelty rather than social
engagement. If fewer items truly mattered, include fewer.

Open with one sentence capturing the week's central theme. For every selected
development, state what happened, why it matters and what could follow. Join
related developments into a coherent narrative rather than a headline list.
Prefer primary sources, distinguish fact from interpretation, and omit rumours,
routine product tweaks, repetitive funding coverage, weak repositories, hype,
engagement bait, hashtag clutter and generic commentary. End with a short
assessment of what the week collectively indicates about the direction of AI.

Produce one recommended draft for review, followed by a compact source list and
a short note naming material stories you deliberately omitted. Never publish,
schedule or describe the post as approved unless I explicitly reply APPROVE.
When I request a revision, return the complete revised draft. After approval,
preserve the approved wording exactly.
```

## Approved X publishing

Publishing is deliberately separated from drafting. ChatGPT proposes the copy;
the repository records explicit approval and GitHub Actions owns credentials,
timing, retry behaviour and duplicate prevention.

### Configure X

In the X developer portal, give the app permission to read and write Posts and
generate user-context credentials for the account that will publish. In GitHub,
create an environment named `x-production` and add either:

- `X_USER_ACCESS_TOKEN` for an OAuth 2.0 user access token; or
- all four long-lived OAuth 1.0a secrets: `X_CONSUMER_KEY`,
  `X_CONSUMER_SECRET`, `X_ACCESS_TOKEN`, and `X_ACCESS_TOKEN_SECRET`.

Do not use an app-only bearer token: creating a Post requires user context. The
publisher calls `POST https://api.x.com/2/tweets` and sends only the approved
text. X account subscription and X API access are separate; the developer app
must have working write access.

Keep the environment variable `X_PUBLISHING_ENABLED` absent or set to `false`
while testing. Set it to `true` only after a successful manual dry run. Optional
environment protection rules can require approval before GitHub releases the
production secrets.

### Approve and queue a draft

After replying `APPROVE` in ChatGPT, open **Actions → Queue approved X post →
Run workflow** on `main` and enter:

- a unique ID such as `2026-W34`;
- the following Monday at `17:00:00Z` (for example
  `2026-08-24T17:00:00Z`); and
- the exact approved text.

The workflow tests the code and writes an immutable, hashed approval record to
`data/x_queue.json`. This repository is public, so queued copy is publicly
visible before publication. Move the queue to private storage before using it
for embargoed material.

The same ID cannot be reused or edited. If approved copy needs to change, queue
a new ID and remove or cancel the old entry through a reviewed repository
change. Silence and draft generation never count as approval.

### Dry run and enable publication

Open **Actions → Publish approved X post → Run workflow**, leave `live` off, and
inspect the log. A due item is reported as `DRY RUN`; no network request is made
and its status remains `approved`.

For the first real test, run the workflow manually with `live` enabled. After a
successful response, the workflow stores the X Post ID and URL and marks the
entry `published`. Published entries are ignored on every later run, preventing
duplicate Posts. Once verified, set the `x-production` environment variable
`X_PUBLISHING_ENABLED=true`; scheduled Monday runs will then publish due copy.

The schedule is `17:00 UTC`, which is 17:00 in the UK during GMT and 18:00
during BST. GitHub scheduled jobs may start late. A failed API request records a
bounded error message and leaves the entry approved so it can be retried; it
never substitutes or regenerates text.

## Schedule and time zones

The workflow contains:

```yaml
schedule:
  - cron: "0 5 * * 1"
```

GitHub cron uses UTC. The collector runs every Monday at `05:00 UTC`, which is `06:00` in the UK during British Summer Time and `05:00` during Greenwich Mean Time. The ChatGPT task runs every Monday at `06:00 Europe/London`. Consequently, the schedules coincide during British Summer Time and have a one-hour gap during Greenwich Mean Time. GitHub may also start scheduled workflows late during high load, so the ChatGPT task's stale-report warning remains important.

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
- the number of ranked findings included after the report cap;
- health and collection method for every attempted topic/platform pair (`direct`, `feed`, `public index discovery`, `public index fallback`, or `failed`);
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
- The collector primarily makes HTTP `GET` requests; the sole credentialed `POST` creates a short-lived Bluesky session.
- No cookies or browser profiles are used by GitHub Actions. The dedicated Bluesky app password is supplied only through encrypted Actions secrets.
- Never add API keys or passwords to configuration, workflow YAML, reports, state, or logs. Use Actions secrets and least-privilege app passwords.
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

### 2026-08-20 — Weekly AI radar expansion

- Added account-free adapters for Bluesky, Hacker News, GitHub, Hugging Face, arXiv and Stack Exchange.
- Added generic RSS/Atom parsing for labs, blogs, YouTube channels, podcasts and public forums.
- Added official-domain, policy, news, Mastodon and restricted-social indexed discovery.
- Added transient HTTP retry handling, Bluesky fallback, per-source health and ranked report caps.
- Shipped focused seven-day searches for AI models, agents, research, policy, business and Reddit discussion.

### 2026-08-20 — Personal AI intelligence configuration

- Added explicit searches for top news, new AI companies, newly created repositories, new models/tools, innovations/research, agents, policy, safety and funding.
- Added `github_new`, which searches repositories created during the seven-day window rather than merely updated repositories.
- Added `github_trending`, a documented seven-day activity-plus-stars approximation to GitHub Trending.
- Assigned semantic cross-source deduplication to the ChatGPT presentation layer, while the collector retains deterministic canonical-URL deduplication.

### 2026-08-20 — Source reliability and authenticated Bluesky search

- Replaced misleading Bing `site:` checks with validated Google News source discovery and renamed the report method to the neutral `public index discovery`.
- Added Reddit's official search RSS feed as the primary Reddit adapter, with PullPush and public index discovery retained as fallbacks.
- Created the dedicated `socialnews-ai.bsky.social` automation account and selected only Tech, Science, News and Software Dev interests; no accounts were followed and no posts were made.
- Created the `SocialNews-GHA` Bluesky app password with direct-message access disabled and stored only its handle and app password in encrypted GitHub Actions secrets.
- Added authenticated Bluesky session/search support. The main account password, recovery email and date of birth are not stored in the repository or GitHub Actions.

### 2026-08-20 — Weekly delivery schedule

- Changed the SocialNews collector from daily to every Monday at `05:00 UTC`.
- Changed the native ChatGPT briefing from daily to every Monday at `06:00 Europe/London`.
- Preserved `workflow_dispatch` so the collector can still be run manually between scheduled runs.

## References

- [ChatGPT scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [Codex GitHub Action](https://learn.chatgpt.com/docs/github-action)
- [GitHub scheduled events](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#schedule)
- [Makerskills social-fetch](https://github.com/coreyhaines31/makerskills/blob/main/skills/social-fetch/SKILL.md)

## License

Licensed under the [Apache License 2.0](LICENSE).
