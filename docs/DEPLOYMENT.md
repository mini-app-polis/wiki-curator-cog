# Deployment — wiki-curator-cog

This document walks through the one-time setup to run `wiki-curator-cog` on Railway and trigger a backfill of `wcs-wiki`.

## What the cog needs

`wiki-curator-cog` is a stateless Python process that:

1. Clones `wcs-wiki` from GitHub at startup (re-clones on every run; Railway's filesystem is ephemeral).
2. Reads structured WCS notes from `api-kaianolevine-com` over HTTPS.
3. Renders source pages, concept / technique / instructor / terminology pages, views, and the index from those notes.
4. Commits one per ingest plus a residual commit, then pushes to the configured branch of `wcs-wiki`.

The cog runs no LLM at runtime — the upstream `notes-ingest-cog` has already extracted the structured `notes_json`. The cog's job is deterministic routing of those structured fields onto pages.

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

Mirror the transcription-cog deployment shape: a Railway service backed by this repo, Railpack builder, start command from `railway.json`.

In the Railway dashboard:

1. **New project** → Deploy from GitHub repo → `mini-app-polis/wiki-curator-cog`.
2. The existing `railway.json` declares `python -m wiki_curator_cog.main` as the default start command. Leave it — that's the steady-state command (it registers a Prefect deployment and serves it). For one-off backfill runs you'll override below.
3. The existing `railpack.json` declares `git` as a runtime apt package. The cog's `boot.py` uses GitPython, which shells out to `git`; without this entry the runtime image lacks the binary and the cog crashes on import.
4. Add the environment variables below.

### 3. Environment variables

Set these in Railway (either directly in the dashboard or via Doppler → Railway sync if that's how transcription-cog is configured).

Required:

```
KAIANO_API_BASE_URL=https://api.kaianolevine.com
WIKI_CURATOR_COG_API_KEY=ak_…              # same Clerk machine as transcription-cog (wcs_admin scope)
GH_TOKEN=github_pat_…                              # from step 1
WIKI_REPO_URL=https://github.com/mini-app-polis/wcs-wiki.git
WIKI_BRANCH=phase-1-backfill                       # backfill lands here; merge to main via PR when satisfied
WIKI_REPO_PATH=/tmp/wcs-wiki                       # ephemeral filesystem on Railway
WIKI_GIT_AUTHOR_NAME=wiki-curator-cog
WIKI_GIT_AUTHOR_EMAIL=wiki-curator@kaianolevine.com
```

The curator version is auto-derived from `pyproject.toml` at runtime via `importlib.metadata.version("wiki-curator-cog")` — semantic-release bumps it on every push to main based on conventional commits (`feat:` → minor, `fix:` → patch). No `WIKI_CURATOR_VERSION` env var is required; it exists only as a local-dev escape hatch to force a specific version string.

Optional but recommended (mirror transcription-cog):

```
SENTRY_DSN_WIKI_CURATOR_COG=https://…@sentry.io/…
HEALTHCHECKS_URL_WIKI_CURATOR_COG=https://hc-ping.com/…
LOGGING_LEVEL=INFO
```

Required for the steady-state Prefect-served mode (not needed for one-off backfill jobs):

```
PREFECT_API_KEY=
PREFECT_API_URL=
```

## Running a backfill

Once env vars are set:

1. **Override the start command for one run.** In Railway dashboard → service → Settings → Deploy → custom start command, set:

   ```
   python -m wiki_curator_cog.main backfill
   ```

2. **Trigger a redeploy.** The service will boot, clone `wcs-wiki` to `/tmp/wcs-wiki`, checkout (or create) `phase-1-backfill`, wipe the derived layer (`concepts/`, `techniques/`, `instructors/`, `terminology/` — preserving the `_aliases.yaml` files), iterate every note from the API in chronological order, ingest each at the running curator version, regenerate views and the index's derived-page sections, push to `origin/phase-1-backfill`, and exit with `status: 0` and a JSON summary in stdout:

   ```json
   {
     "total": 87,
     "ingested": 87,
     "skipped": 0,
     "curator_version": "1.2.5"
   }
   ```

3. **Review the PR.** Open https://github.com/mini-app-polis/wcs-wiki/compare/main…phase-1-backfill. Each source ingest lands as its own commit (`ingest: <slug>`); a final `backfill: alias map, views, and residual updates` commit captures view regeneration, alias-map auto-additions, and the index rebuild.

4. **Revert the start command.** Once the backfill is satisfactory, restore the default `python -m wiki_curator_cog.main` start command so the service goes back to serving the Prefect deployment (Phase 2 incremental runs).

## Re-running a backfill

Backfill mode is the rebuild-from-scratch mode. It wipes the derived layer at start of run and bypasses the curator-version-equality skip — every source re-ingests regardless of whether the source page on disk already matches the deployed version. Use it whenever the curator ships a behavior change that should reshape the derived layer (alias-map seed, filter tightening, render output change).

The wipe preserves the three `_aliases.yaml` files (instructors / concepts / techniques) so Kaiano's manual canonicalization decisions survive. Everything else under those directories regenerates from scratch.

Incremental mode (used by `wiki_curator_cog.main incremental`, triggered downstream of new note arrivals) is the idempotent mode: it honors the version-equality skip and only re-processes sources whose existing `curator_version` doesn't match the running version. Bumping the curator version via a `feat:` / `fix:` commit triggers a release; the next incremental run sees the version mismatch and re-renders affected sources.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `WIKI_REPO_URL is HTTPS but GH_TOKEN is not set` at boot | PAT not configured | Set `GH_TOKEN` in Railway env vars |
| `Permission denied` on git push | PAT lacks `contents:write` or wrong repo scope | Recreate PAT with the scope from step 1 |
| `ImportError: Bad git executable` on boot | runtime image lacks the `git` binary | Confirm `railpack.json` declares `deploy.aptPackages: ["git"]` |
| `error: RPC failed; curl 92 HTTP/2 stream … was not closed cleanly` on push | large backfill push trips libcurl HTTP/2 stream error | Already mitigated: `boot.ensure_wiki_clone` sets `http.version=HTTP/1.1` and `http.postBuffer=500MB`, and `WikiRepo.push` retries up to 3 times with exponential backoff |
| Backfill finishes but derived/ stays empty | source pages already at current curator version + backfill incorrectly honors the skip | Should not occur as of curator 1.2.4 — backfill mode now explicitly bypasses the version-equality skip |
| Sentry shows `Missing required environment variable: KAIANO_API_BASE_URL` | Doppler sync hasn't propagated to Railway | Force a Doppler → Railway sync, restart the service |
| Cog can't find any notes | Clerk M2M token missing `wcs_admin` scope | Confirm the machine secret has that scope at https://dashboard.clerk.com |
