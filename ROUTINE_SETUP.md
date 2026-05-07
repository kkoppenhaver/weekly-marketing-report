# Setting up the weekly Routine

This document describes how to wire up a Claude Code Routine that runs
`scripts/run_pipeline.py` every Sunday morning, end-to-end.

## Prerequisites

Everything below assumes:

- This repo is pushed to GitHub (✅ `kkoppenhaver/weekly-marketing-report`)
- The site repo (`kkoppenhaver/claude-code-for-marketers`) is reachable to the Routine
- All API credentials are working locally — the pipeline runs cleanly via `python scripts/run_pipeline.py`

## 1. Connect the repositories

In the Claude Code Routines UI, connect both repos:

- `kkoppenhaver/weekly-marketing-report` (this repo — defines the skills + orchestrator)
- `kkoppenhaver/claude-code-for-marketers` (site repo — `fetch-post-manifest` reads frontmatter from `src/content/blog/`)

The Routine mounts each connected repo at a known path inside its container.
Note the path of the site repo and use it for `SITE_REPO_LOCAL` in step 3.

## 2. Set the schedule

Sunday morning, your timezone. Suggested: **Sunday 08:00 America/Chicago**.

Cron expression: `0 8 * * 0` (with timezone configured separately in the Routine UI).

GSC has a 2-day data lag, so by Sunday morning it has Mon–Fri data for the
just-completed ISO week — enough for a clean week-over-week comparison.

## 3. Configure environment variables (vault)

Every value from your local `.env` needs to land in the Routine's vault.
Three of them are JSON content rather than simple strings — the dump helper
makes those easy to copy/paste:

```bash
python scripts/dump_creds_for_routine.py
```

That prints:

- `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` (the SA key)
- `GSC_OAUTH_CLIENT_JSON` (OAuth client secret)
- `GSC_OAUTH_TOKEN_JSON` (OAuth refresh token)

…each as a single-line JSON string. Paste each into the corresponding env var
in the Routine's vault.

The full env var list:

| Variable | Purpose | Source |
|---|---|---|
| `STORAGE` | Set to `r2` | static |
| `SITE_URL` | `https://claudecodeformarketers.com` | static |
| `SITE_REPO` | `kkoppenhaver/claude-code-for-marketers` | static |
| `SITE_REPO_LOCAL` | Path where the Routine mounts the site repo | from step 1 |
| `CONTENT_PATH` | `src/content/blog` | static |
| `R2_ACCESS_KEY` / `R2_SECRET_KEY` / `R2_ENDPOINT` / `R2_BUCKET` | Cloudflare R2 | dashboard |
| `GSC_PROPERTY` | `sc-domain:claudecodeformarketers.com` | static |
| `GSC_OAUTH_CLIENT_JSON` | Inlined JSON | dump helper |
| `GSC_OAUTH_TOKEN_JSON` | Inlined JSON | dump helper |
| `FATHOM_API_KEY` / `FATHOM_SITE_ID` | Fathom | dashboard |
| `KIT_API_KEY` / `KIT_PRIMARY_TAG_ID` | Kit | dashboard |
| `PSI_API_KEY` | PageSpeed Insights | Google Cloud |
| `DFS_LOGIN` / `DFS_PASSWORD` | DataForSEO | dashboard |
| `GOOGLE_DRIVE_FOLDER_ID` | Shared Drive ID | Drive URL |
| `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` | Inlined JSON | dump helper |
| `RESEND_API_KEY` / `REPORT_TO_EMAIL` / `REPORT_FROM_EMAIL` | Resend | dashboard |

## 4. Define the Routine prompt

The Routine prompt instructs Claude what to do at each tick. Keep it tight:

```
Run the weekly site report. Execute:

    python scripts/run_pipeline.py

If the pipeline exits with a non-zero status, report which step(s) failed
and surface the error output. Do not retry — the cron will run again next
Sunday and a notification of failure is more useful than a partial recovery.

If the pipeline succeeds, confirm that the email was sent and link the
Google Doc URL written to reports/{week}/doc-url.txt in the snapshot store.
```

## 5. Trigger a manual run

Before enabling the schedule, run the Routine manually:

1. Watch the output. PSI takes 2–3 min; the rest finishes in seconds.
2. Confirm an email arrives at `REPORT_TO_EMAIL`.
3. Confirm a new Google Doc appears in your Drive folder.

## 6. Enable the schedule

Once the manual run succeeds, enable the cron. Watch the next 1–2 weekly
runs to confirm the report keeps arriving.

## Failure modes to watch

- **GSC OAuth token expires.** Production-mode tokens are long-lived but
  not infinite. If a Sunday run reports `invalid_grant`, run the local
  consent flow again (`python .claude/skills/fetch-search-console/scripts/fetch.py --reauth`),
  re-dump credentials, and update the Routine's `GSC_OAUTH_TOKEN_JSON`.
- **DataForSEO 403 / 5xx.** Transient, usually clears within minutes. The
  pipeline continues without DFS data; the report will note it as missing.
- **PSI flakiness.** Lighthouse occasionally returns 500 for individual URLs.
  4 of 56 calls failed in our test run; report still composes cleanly.
