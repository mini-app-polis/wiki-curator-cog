# ADR-004 — One shot on Railway, no Prefect, no queue

**Date:** 2026-09-23
**Status:** Accepted
**Supersedes:** ADR-001's "Three operating modes" and its `wiki_curator_router`
dispatcher; ADR-002's "The Prefect deployment shape and concurrency slot"

## Context

Through 2026-09 the fleet moved off Prefect one cog at a time, onto SQS
queues with Lambda workers. evaluator-cog, deejay-cog and transcription-cog
all made that move, and ecosystem-standards ADR-009 wrote the destination
into the taxonomy: `pipeline-cog` now *means* a queue-driven Lambda worker.
This cog was the fourth and last, and ADR-009 names it as failing PIPE-016
and PIPE-017 until it moved.

A fit check against Lambda was run first, before any edits, and it failed.

**GitPython cannot import without a `git` executable.** Measured directly:
importing `git` with an empty `PATH` raises `ImportError: Failed to
initialize: Bad git executable` from `git/__init__.py`, at *import* time —
not at first call. `boot.py` and `git_ops.py` both import it at module
level, so the failure would land during the cold-start module load, before
any handler code ran. The managed python3.11 runtime ships no `git`; this
repo already knows that, which is why `railpack.json` declares `git` as a
runtime apt package and why `DEPLOYMENT.md`'s troubleshooting table lists
this exact `ImportError`. Railway had a package hook to fix it with. The
Lambda managed runtime has none.

The remedies all cost more than they save here: a layer carrying a git
binary (an artifact the shared `lambda-deploy.yml@v3` knows nothing about),
a container image (ECR, and the "which version is deployed" property the zip
deploy exists to preserve), replacing GitPython with dulwich or the GitHub
Git Data API (a rewrite of the two modules that do the one thing this cog
must not get wrong — and this cog's push already needed `http.version=HTTP/1.1`
and `postBuffer=500MB` to survive HTTP/2 stream resets, which are git-config
knobs no replacement would honour), or Fargate.

Four things about this cog make that bill not worth paying:

1. It is still in development. Rewriting its git layer settles a shape that
   is not settled.
2. It runs very rarely and nothing about it is time-sensitive.
3. **It needs no queue.** A run takes no arguments: the export is a full,
   idempotent re-render of the whole corpus. Every message would be
   identical and a consumer would collapse N of them into one run. In the
   other three cogs each message names a distinct unit of work — a
   `drive_file_id`, a repo — and PIPE-017's apparatus exists for those
   units. Here there are none.
4. It is already on Railway. It is the one cog with no platform to migrate.

## Decision

**wiki-curator-cog stays on Railway, as a one-shot process, with no queue.
Prefect goes anyway.** Railway versus Lambda and Prefect versus SQS are
independent questions, and only the second one is what this migration is
about.

One wake is one run. The process renders the wiki once, reports, and exits.
Nothing stays resident between runs.

What each Prefect facility did, and what does it now:

| Prefect | Replacement |
|---|---|
| `serve()` and the `wiki_curator_router` dispatcher in `main.py` | Nothing. `main` calls `flow.export_run` once and exits. One mode, so nothing to dispatch. |
| `concurrency("wiki-curator-cog", occupy=1)` | Removed, not replaced. The slot guarded one shared working tree; a one-shot process clones into its own container's `/tmp` and shares nothing. Two overlapping runs could still race on the push, and git arbitrates that — the loser is rejected non-fast-forward and fails visibly. |
| `on_failure` / `on_crashed` (`make_failure_hook`) | The explicit failure path in `flow.export_run`: one ERROR finding carrying what the run managed to do, then re-raise. `run_report` was used first and was wrong — its severity is derived, and a report has no verb for ERROR, so a run that died on a rejected push reported WARN. The report is now sent on exactly one of the two paths. |
| `get_run_id()` | A uuid4 minted in `main` and threaded through. The library's fallback resolves Prefect's ids and answers `"local-run"` without them, which would make every report unattributable. |
| `@task(retries=...)` | There were none in this flow. The push retries itself three times with backoff; the export GET retries inside `KaianoApiClient`. Nothing else is retried, and with no queue there is no redelivery to fall back on — a failed run is re-triggered by hand. |

**A declared run budget, and a non-zero exit.** Railway will not start this
service again while a previous start is still `Active`. A run that hangs
therefore does not merely run long — it takes every later run with it, and
nothing says so. `_deadline.py` raises inside the run a margin before the
budget (`RUN_TIMEOUT_SECONDS`, default 1800s), so the failure travels the
ordinary path: the report is sent, `main` exits non-zero, and the next start
is free to happen. This is transcription-cog's `_deadline.py` with a
declared budget in place of Lambda's `get_remaining_time_in_millis`, which
there is no runtime clock to read here.

**Healthchecks.io per run, not per boot.** The old ping fired once at
startup, which for a one-shot is the least informative moment in the run: it
went green whether or not the export then hung for an hour. It is now
`/start` followed by a success or `/fail` ping, so the check's grace period
catches the run that never came back — the one failure a process cannot
report on its own behalf.

**Dead LLM dependencies dropped.** Nothing in `src/` calls an LLM; the only
match for anthropic/openai across the package is the string literal
`"conformance_llm"`. `anthropic`, the common-utils `[llm]` extra, and the
unused `llm_provider` / `llm_model` / `_DEFAULT_MODELS` config went with
Prefect. The lock fell from 173 packages to 87.

## Consequences

- Prefect is retired from this cog, which is what the fleet-wide migration
  needed. The Prefect Cloud teardown is now unblocked.
- **The taxonomy question is left open, deliberately.** ADR-009 says
  `pipeline-cog` means a queue-driven Lambda worker, and this cog is now
  none of those things. It therefore fails PIPE-016, PIPE-017, PIPE-018 and
  CD-024, which read an `infra/` that does not exist. Those are recorded as
  deferrals in `evaluator.yaml`, not exemptions: an exemption says the rule
  should not apply, and that claim cannot be made until the taxonomy
  decision is taken. ADR-002's own test — the same exemption carried for the
  same structural reason — points at a missing type rather than at this cog,
  and the Layer 2 polish pass described in ADR-001 will be the second thing
  of this shape. Revisit when Layer 2 is built.
- A failed run is not retried. `railway.json` sets `restartPolicyType: NEVER`
  rather than `ON_FAILURE` with three retries: each restart would be a fresh
  clone, a full render and another push attempt, and — since the flow now
  reports on its way out — three reports for one triggered run. A failure
  should be seen and re-triggered, not multiplied.
- The trigger is manual for now: starting the container is the run. The
  direction of travel is a run per source change, asked for by
  api-kaianolevine-com; nothing is built for that yet.
- **Starting the container is a run only in production.** A development deploy
  is someone shipping code, and there is no development wiki — a run there
  would clone the real `wcs-wiki`, re-render everything and push to a branch of
  it. `RUN_ON_START` overrides in both directions; naming the `export` argument
  bypasses the gate outright.
- **The credential is checked before the clone.** A fine-grained PAT issued
  with `Contents: Read` clones happily and is refused only at
  `git-receive-pack` — which is what happened on the first run, after the whole
  corpus had been rendered and committed. `boot.assert_push_access` makes that
  same request first. It probes the git transport rather than the REST API on
  purpose: `GET /repos/{owner}/{repo}` reports the *owner's* role on the
  repository, not the token's grants, so it answers `push: true` for a
  read-only token.

## Alternatives considered

**Move to Lambda behind a queue anyway**, paying for git with a layer or a
container image. Rejected above: the four properties that make this cog
different all point the other way, and the queue would carry no information.

**Railway cron on a schedule** instead of a wake. Rejected for now because
nothing triggers this cog today and a schedule would render a corpus that
had not changed; the wake is what the API will eventually own. Worth
revisiting as a backstop — a periodic idempotent rebuild is self-healing in
a way a single wake is not.

**Keep Prefect and only stop the container running all the time.** Rejected:
Prefect's retirement is the point of the fleet migration, and this cog is the
last thing holding the Prefect Cloud workspace open.
