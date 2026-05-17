# Deployment — wiki-curator-cog

This document walks through the one-time setup to run `wiki-curator-cog` on Railway and trigger the Phase 1 backfill of `wcs-wiki`.

## What the cog needs

`wiki-curator-cog` is a stateless Python process that:

1. Clones `wcs-wiki` from GitHub at startup.
2. Reads structured WCS notes from `api-kaianolevine-com` over HTTPS.
3. Writes rendered source pages into the local clone.
4. Commits and pushes to a configured branch on `wcs-wiki`.

For the Phase 1 backfill, no Prefect Cloud trigger is required — the cog is invoked once as a one-off Railway job (`python -m wiki_curator_cog.main backfill`) and exits after pushing. Subsequent steady-state operation uses the default `python -m wiki_curator_cog.main` start command, which registers a Prefect deployment and serves it.

## One-time setup

### 1. GitHub fine-grained PAT for `wcs-wiki`

Create a fine-grained personal access token scoped to `wcs-wiki` only.

Go to https://github.com/settings/personal-access-tokens/new and configure:

- **Token name**: `wiki-curator-cog`
- **Resource owner**: `mini-app-polis`
- **Repository access**: Only select repositories → `mini-app-polis/wcs-wiki`
- **Repository permissions**: `Contents: Read and write`
- **Expiration**: 90 days (or whatever your rotation cadence allows)

Copy the token (starts with `github_pat_…`) — you'll paste it into Railway as `GH_TOKEN`.

### 2. Railway service

Mirror the transcription-cog deployment shape: a Railway service backed by this repo, Nixpacks builder, start command from `railway.json`.

In the Railway dashboard:

1. **New project** → Deploy from GitHub repo → `mini-app-polis/wiki-curator-cog`.
2. The existing `railway.json` declares `python -m wiki_curator_cog.main` as the default start command. Leave it alone — that's the steady-state command.
3. Add the environment variables below.

### 3. Environment variables

Set these in Railway (either directly in the dashboard or via Doppler → Railway sync if that's how transcription-cog is configured).

Required:

```
KAIANO_API_BASE_URL=https://api.kaianolevine.com
KAIANO_API_CLERK_MACHINE_SECRET=ak_…              # same Clerk machine as transcription-cog (wcs_admin scope)
GH_TOKEN=github_pat_…                              # from step 1
ANTHROPIC_API_KEY=sk-ant-…                         # not used in Phase 1 but config validates the LLM provider
LLM_PROVIDER=anthropic
WIKI_REPO_URL=https://github.com/mini-app-polis/wcs-wiki.git
WIKI_BRANCH=phase-1-backfill                       # the backfill lands here; merge via PR when satisfied
WIKI_REPO_PATH=/tmp/wcs-wiki                       # ephemeral filesystem on Railway
WIKI_GIT_AUTHOR_NAME=wiki-curator-cog
WIKI_GIT_AUTHOR_EMAIL=wiki-curator@kaianolevine.com
WIKI_CURATOR_VERSION=4
```

Optional but recommended (mirror transcription-cog):

```
SENTRY_DSN_WIKI_CURATOR_COG=https://…@sentry.io/…
HEALTHCHECKS_URL_WIKI_CURATOR_COG=https://hc-ping.com/…
LOGGING_LEVEL=INFO
```

Not needed for Phase 1 backfill (only required once incremental_flow is triggered via Prefect Cloud):

```
PREFECT_API_KEY=
PREFECT_API_URL=
```

## Running the Phase 1 backfill

Once env vars are set:

1. **Override the start command for one run.** In Railway dashboard → service → Settings → Deploy → custom start command, set:

   ```
   python -m wiki_curator_cog.main backfill
   ```

2. **Trigger a redeploy.** The service will boot, clone `wcs-wiki` to `/tmp/wcs-wiki`, checkout (or create) `phase-1-backfill`, iterate every note from the API in chronological order, commit one source page per note, push to `origin/phase-1-backfill`, and exit with `status: 0` and a JSON summary in stdout:

   ```json
   {
     "total": 42,
     "ingested": 42,
     "skipped": 0,
     "curator_version": 1
   }
   ```

3. **Review the PR.** Open https://github.com/mini-app-polis/wcs-wiki/compare/main…phase-1-backfill and scroll through the 42+ new source pages. Each lands as its own commit (`ingest: <slug>`) so individual sources are reviewable in isolation.

4. **Revert the start command.** Once the backfill is satisfactory, restore the default `python -m wiki_curator_cog.main` start command so the service goes back to serving the Prefect deployment (in preparation for Phase 2 incremental runs).

## Re-running the backfill

The curator is idempotent: re-running at the same `WIKI_CURATOR_VERSION` skips every source already at that version. If you want to force a re-render of all sources (e.g., after a `rendering.py` change):

1. Bump `WIKI_CURATOR_VERSION` in Railway env vars (e.g., 1 → 2).
2. Re-trigger the backfill run.

Every source page's `curator_version` frontmatter gets updated and the page content is re-emitted. Existing source pages on the branch are overwritten; index and log entries are updated in place / appended.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `WIKI_REPO_URL is HTTPS but GH_TOKEN is not set` at boot | PAT not configured | Set `GH_TOKEN` in Railway env vars |
| `Permission denied` on git push | PAT lacks `contents:write` or wrong repo scope | Recreate PAT with the scope from step 1 |
| Backfill writes pages but doesn't push | Branch doesn't exist on remote, push succeeds anyway via `--set-upstream` | Should not occur; if it does, check Railway logs for `wiki.checkout.new_local` and confirm push refspec |
| Sentry shows `Missing required environment variable: KAIANO_API_BASE_URL` | Doppler sync hasn't propagated to Railway | Force a Doppler → Railway sync, restart the service |
| Cog can't find any notes | Clerk M2M token missing `wcs_admin` scope | Confirm the machine secret has that scope at https://dashboard.clerk.com |

## What this doesn't cover (yet)

- **Concept / technique / instructor / terminology page creation.** Phase 1 only writes source pages. The LLM-driven page-updating pass is Phase 1.5 work.
- **Prefect Cloud trigger for incremental runs.** Phase 2.
- **Watcher integration (auto-run after transcription-cog completes).** Phase 2.
- **View regeneration (`views/*.md`).** Phase 1.5.
