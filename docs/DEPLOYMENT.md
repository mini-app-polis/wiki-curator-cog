# Deployment — wiki-curator-cog

`wiki-curator-cog` runs as the **wiki-jobs Lambda worker**
(`wiki_curator_cog.worker.lambda_handler`), like the other pipeline cogs: one
SQS message is one job, and each job names the jobs that follow it (ADR-006).
The Railway one-shot `export` process that cloned, rendered and pushed the
markdown wiki was retired with its service (MINI-95); see ADR-004 for what it
was and why it is gone.

## What runs where

| Piece | Where it is declared |
|---|---|
| `wiki-curator-jobs.fifo`, its dead-letter queue, the function (`wiki-curator-worker`), the event source mapping, reserved concurrency 1, the alarms | `module "wiki_curator"` in mini-app-polis/infra |
| The function's code | this repo, deployed by CI on each release (`deploy` in `.github/workflows/ci.yml`, the shared `lambda-deploy.yml`) |
| Its secrets | SSM, read by `load_secrets` at each invocation |
| Who enqueues | api-kaianolevine-com only: a `link` job when a source is composed (behind `flags.wcs.wiki_jobs_enabled`), and the follow-ons a job posts to `POST /v1/wcs/wiki/jobs` |

The deploy job needs three repository variables, set from `terraform output
cogs` in mini-app-polis/infra: `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and
`AWS_FUNCTION_NAME`. Its handler, architecture (arm64) and Python version must
match the function's block in infra; the deploy refuses a function whose
architecture differs.

## Starting and stopping the flow of jobs

`flags.wcs.wiki_jobs_enabled` (migration 055 in api-kaianolevine-com) decides
whether a composed source enqueues a `link` job. Turning it off stops new
work at the source; jobs already queued, and the follow-ons they name, still
run. Changing it takes `config.flags.write`, a human admin scope.

To run one job by hand, post it to `POST /v1/wcs/wiki/jobs`, or run it
locally with the `job` command (README), which uses the same code against
production without the queue.

## Observability

| Layer | Signal | Where |
| ----- | ------ | ----- |
| L1 | Structured logs, one timing line per job | the function's CloudWatch log group |
| L2 | The exception that fails a job | Sentry, shared cogs project, `service:wiki-curator-cog` |
| L3 | Each job's outcome | one run report per job: quiet when clean, WARN for work left undone, sent on a raise |
| L4 | A job that keeps failing | the dead-letter queue and its alarm |

An unreadable message is reported once as an ERROR finding and then
dead-lettered: it is a producer bug, and retrying it changes nothing.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| A job fails and comes back | the export, a state read or a model call failed | Retries are safe: every pass is watermarked, so the finished parts are no-ops. Read the error in Sentry or the log group |
| Messages in the dead-letter queue | a job failed on every receive, or the message was unreadable | Read the message and its last error; fix the cause, then re-send the job through `POST /v1/wcs/wiki/jobs` |
| `StateMismatchError` locally | a local cache file and the API disagree | Run `state-verify`; if the file is the one to keep, `state-import` (README) |
| Every call returns 401 | `WIKI_CURATOR_COG_API_KEY` unset, stale, or not matching the API's copy | Compare SSM (worker) or Doppler (local) against the API. There is no fallback credential |
| A new lesson changes no pages | `flags.wcs.wiki_jobs_enabled` is off, or the source was not composed | Check the flag, then the API's dispatch log for the source |
