# Deployment — wiki-curator-cog

`wiki-curator-cog` runs on Railway as a **one-shot process**: one wake is one
run. It clones `wcs-wiki`, re-renders the whole corpus, commits, pushes,
reports, and exits. Nothing stays resident between runs, and there is no queue.

See `docs/decisions/ADR-004-one-shot-on-railway-no-prefect.md` for why this cog
stayed on Railway when the rest of the fleet moved to SQS + Lambda, and for
what replaced each Prefect facility.

## What the cog needs

1. A clone of `wcs-wiki` from GitHub, made fresh on every run — Railway's
   filesystem is ephemeral, which is exactly what this wants.
2. The canonical WCS corpus from `api-kaianolevine-com` over HTTPS, in one
   call (`GET /v1/wcs/wiki/export`).
3. Write access to push the rendered bundle back to the configured branch.

The cog runs no LLM. The upstream has already extracted the structured data;
this is deterministic routing of those fields onto pages. `anthropic` and the
common-utils `[llm]` extra were declared but never called, and are gone.

## One-time setup

### 1. GitHub fine-grained PAT for `wcs-wiki`

Create a fine-grained personal access token scoped to `wcs-wiki` only, at
https://github.com/settings/personal-access-tokens/new:

- **Token name**: `wiki-curator-cog`
- **Resource owner**: `mini-app-polis`
- **Repository access**: Only select repositories → `mini-app-polis/wcs-wiki`
- **Repository permissions**: `Contents: Read and write`
- **Expiration**: 90 days, or whatever your rotation cadence allows

It reaches the cog as `GH_TOKEN`, through Doppler like every other secret. It
is consumed only in `boot.py`, never logged, and masked wherever the repo URL
is echoed.

### 2. Railway service

A Railway service backed by this repo, Railpack builder, start command from
`railway.json`.

- `railway.json` declares `python -m wiki_curator_cog.main` — which now renders
  once and exits rather than serving a Prefect deployment forever.
- `restartPolicyType` is **`NEVER`**, deliberately. Each restart would be a
  fresh clone, a full render and another push attempt, and — since the run
  reports on its way out — another report for the same triggered run. A
  failure should be seen and re-triggered, not multiplied.
- `railpack.json` declares `git` as a runtime apt package. `boot.py` uses
  GitPython, which shells out to `git`; without this entry the image lacks the
  binary and the cog crashes **on import**. This is load-bearing and is the
  single reason the cog did not move to Lambda — see ADR-004.
- `nixpacks.toml` is vestigial: the builder is Railpack. Left in place rather
  than deleted as part of this change.

### 3. Environment variables

Required:

```
KAIANO_API_BASE_URL=https://api.kaianolevine.com
WIKI_CURATOR_COG_API_KEY=ak_…                      # this cog's own named key
GH_TOKEN=github_pat_…                              # from step 1
WIKI_REPO_URL=https://github.com/mini-app-polis/wcs-wiki.git
WIKI_BRANCH=main
WIKI_REPO_PATH=/tmp/wcs-wiki
WIKI_GIT_AUTHOR_NAME=wiki-curator-cog
WIKI_GIT_AUTHOR_EMAIL=wiki-curator@kaianolevine.com
```

Optional:

```
SENTRY_DSN_WIKI_CURATOR_COG=https://…@sentry.io/…  # suffixed on purpose — see below
HEALTHCHECKS_URL_WIKI_CURATOR_COG=https://hc-ping.com/…
RUN_TIMEOUT_SECONDS=1800                           # the run budget; see below
LOGGING_LEVEL=INFO
```

**`PREFECT_API_KEY` and `PREFECT_API_URL` are no longer read.** Remove them
from Doppler with the rest of the Prefect teardown.

**The Sentry variable keeps its `_WIKI_CURATOR_COG` suffix**, unlike the cogs
that moved to Lambda. Each of those got a function with an environment of its
own, so a bare `SENTRY_DSN` there addresses one cog. This one is still a
Railway service sharing a secrets store with its neighbours, and an
unsuffixed name is the same name they read — events would land in another
cog's Sentry project while this one looked healthy.

The curator version is derived at runtime from `pyproject.toml` via
`importlib.metadata.version("wiki-curator-cog")`; semantic-release bumps it on
push to main.

## Triggering a run

The trigger is **manual** for now. Redeploy the service, or start it from the
Railway dashboard; it renders once and exits with `status: 0` and a JSON
summary on stdout:

```json
{
  "entities": 1723,
  "sources": 90,
  "instructors": 16,
  "paths_written": 812,
  "paths_removed": 3
}
```

The direction of travel is a run per source change, asked for by
api-kaianolevine-com. Nothing is built for that yet.

Locally: `uv run python -m wiki_curator_cog.main export` (the `export`
argument is an explicit spelling of the default and does the same thing).

## The run budget, and why it exists

Railway will not start this service again while a previous start is still
`Active`. A run that hangs therefore does not merely run long — it takes every
later run with it, silently, with the service showing green.

`RUN_TIMEOUT_SECONDS` (default 1800) is the budget. `_deadline.py` raises
`RunOutOfTime` inside the run 30 seconds before it, so the failure travels the
ordinary path: the run report is sent, `main` exits non-zero, and the next
start is free to happen.

Healthchecks.io is the other half. The cog pings `/start` at the beginning of
a run and then either success or `/fail` at the end, so the check's grace
period catches a run that never came back at all — the one failure a process
cannot report on its own behalf. Set the grace period above the budget.

## Observability

| Layer | Signal               | Where                                             |
| ----- | -------------------- | ------------------------------------------------- |
| L1    | Liveness             | Healthchecks.io `/start` → success or `/fail`     |
| L2    | Structured logs      | `mini_app_polis` JSON logger                      |
| L3    | Unhandled exceptions | Sentry                                            |
| L4    | Run outcome          | One `RunReport` per run, sent however it ends     |
| L5    | Quality signals      | `pipeline_evaluations` via `POST /v1/evaluations` |

L4 is new in shape, not in kind: the report used to cover only the success
path, with a crash reported by a Prefect state hook. With Prefect gone,
`run_report` records the exception as an issue, sends, and re-raises — one run,
one report.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `WIKI_REPO_URL is HTTPS but GH_TOKEN is not set` at boot | PAT not configured | Set `GH_TOKEN` in Doppler |
| `Permission denied` on git push | PAT lacks `contents:write` or wrong repo scope | Recreate the PAT with the scope from step 1 |
| `ImportError: Bad git executable` on boot | runtime image lacks the `git` binary | Confirm `railpack.json` declares `deploy.aptPackages: ["git"]` |
| `error: RPC failed; curl 92 HTTP/2 stream … was not closed cleanly` on push | large push trips libcurl's HTTP/2 stream error | Already mitigated: `boot.ensure_wiki_clone` sets `http.version=HTTP/1.1` and `http.postBuffer=500MB`, and `WikiRepo.push` retries three times with backoff |
| `RunOutOfTime` in the logs, run reported as failed | the render plus clone and push exceeded `RUN_TIMEOUT_SECONDS` | Working as intended — it failed instead of hanging. Raise the budget once a real run has been timed, or find what got slow |
| A new run never starts and the service shows `Active` | a previous run neither finished nor failed | This is what the budget exists to prevent; if it happens the budget is unset or too high. Stop the service, then check why the run hung |
| Healthchecks shows the check late but Railway shows no failure | the process died without pinging `/fail` | Check Sentry — a hard kill (OOM) leaves no report |
| Every call returns 401 | `WIKI_CURATOR_COG_API_KEY` unset, stale, or not matching the API's copy | Compare Doppler against the API's environment. There is no fallback credential — rotation must land on both sides |
| Sentry is silent on a failed run | `SENTRY_DSN_WIKI_CURATOR_COG` not set | Set it in Doppler. Note the suffix: a bare `SENTRY_DSN` is read by the neighbouring Railway services too, and events would land in another cog's project while this one looked healthy |
