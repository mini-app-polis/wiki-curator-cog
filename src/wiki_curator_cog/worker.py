"""The Lambda entrypoint: one wiki job in, its result and follow-ons out (MINI-95).

The curator runs on Lambda behind its own FIFO queue,
``wiki-curator-jobs.fifo`` (ADR-006). The API is the only producer: it
enqueues a ``link`` job when a source is composed, and the jobs a job names
as its follow-ons, which this worker posts back to it. Every message is

    {"type": "wiki.job", "version": 1,
     "payload": {"job": "link", "source_id": "<uuid>"}}

with a payload :func:`wiki_curator_cog.units.validate` accepts.

**Side by side only where it is safe.** The queue is FIFO and the API picks
each job's message group by what it writes (its ``wiki_dispatch.group_for``).
``consolidate`` and ``generate`` share one group, so two of them never pay for
one entity. ``link`` writes one source's statements and runs a group per
source; ``materialise`` only writes page documents and spreads over a few
groups. A job writes back only the state entries it changed, so jobs in
different groups do not overwrite each other.

**State in the API, checked on every invocation.** Each invocation reads the
export and the state documents from the API and writes back what it changed.
Its cache folder is a fresh temporary directory. The one thing a warm
container keeps is the shared client's last export body, sent back by ETag:
the API answers 304 when the corpus is unchanged, so nothing is used without
the API confirming it current. The export is the expensive read (about 30 MB,
built once per corpus revision in the API); the incident of 2026-10-18 was
five jobs building it at once.

**Nothing is deleted until the work is done.** The event source mapping
deletes every record the handler does not name in ``batchItemFailures``, so
every failure below names its record. A job that fails is retried whole;
that is safe, because every pass is watermarked and skips what an earlier
attempt finished.

**An unreadable message is a producer bug.** It is reported once, exhausts
its receives and lands in the dead-letter queue, where someone can see what
produced it.

**One run report per job, as every cog sends one per run.** It counts the
model calls and their cost, and is posted whenever the job did something;
only a job that found nothing to do stays a log line. Work
the job paid for and left undone — a failed link or consolidation batch, an
entity whose prose failed — is an issue, so the report is a WARN: nothing
retries it on its own, because the job succeeded. A job that raises is
reported on its way out. The worker itself reports only the message it could
not read, which never reached a job.

**Follow-ons are sent before the job counts as done.** If the API cannot
take them, the job is retried: its own work is a no-op the second time, and
the follow-ons are asked for again.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import WcsWikiJob, WcsWikiJobsRequest
from mini_app_polis.pipeline_status import RunReport, post_run_finding, run_report
from mini_app_polis.sentry import init_sentry

from . import link, state_backend, units, usage
from ._deadline import deadline
from .flow import MACHINE_NAME, REPO, _fetch_export, _require_corpus, _state_client

log = logger_mod.get_logger()

#: Must match api-kaianolevine-com's wiki_dispatch.
MESSAGE_VERSION = 1

#: The most jobs one enqueue request may carry, read from the contract.
_ENQUEUE_PER_REQUEST: int = next(
    m.max_length
    for m in WcsWikiJobsRequest.model_fields["jobs"].metadata
    if getattr(m, "max_length", None) is not None
)
TYPE_JOB = "wiki.job"

# At import, once per cold start (see transcription-cog's worker).
# An empty DSN in production logs an ERROR rather than passing silently
# (CD-038); the service tag tells this cog's events apart in the shared project.
init_sentry(MACHINE_NAME)


class UnprocessableMessage(RuntimeError):
    """The message cannot be handled by this consumer, ever."""


def job_of(body: str) -> dict[str, Any]:
    """The job a message asks for. Raises UnprocessableMessage otherwise."""
    try:
        message = json.loads(body)
    except ValueError as exc:
        raise UnprocessableMessage(f"body is not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise UnprocessableMessage("body is not an object")
    if message.get("version") != MESSAGE_VERSION:
        raise UnprocessableMessage(
            f"message version {message.get('version')!r}, this consumer speaks "
            f"{MESSAGE_VERSION}"
        )
    if message.get("type") != TYPE_JOB:
        raise UnprocessableMessage(f"unknown message type {message.get('type')!r}")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise UnprocessableMessage("message carries no payload object")
    try:
        units.validate(payload)
    except units.JobError as exc:
        raise UnprocessableMessage(str(exc)) from exc
    return payload


def run(
    job: dict[str, Any], *, client: Any, state_dir: Path, run_id: str | None = None
) -> dict[str, Any]:
    """Run one job against the API and send the follow-ons it names.

    ``run_id`` — the queue message id — is stored on the pages the job
    writes, with the source that started its chain (MINI-101).
    """
    export = _fetch_export()
    _require_corpus(export)
    llm, model = link.llm_from_env()
    ctx = units.build_context(
        export,
        state_backend.ApiState(client),
        units.StatePaths.under(state_dir),
        llm=llm,
        model=model,
        job_id=run_id,
    )
    result = units.handle(job, ctx)
    result["usage"] = usage.usage_of(llm)
    follow = [WcsWikiJob.model_validate(j) for j in result.get("next") or []]
    if follow:
        # A rebuild of every page names more follow-ons than one request
        # carries, so they go in requests of the contract's maximum.
        enqueued: list[str] = []
        for i in range(0, len(follow), _ENQUEUE_PER_REQUEST):
            accepted = client.enqueue_wcs_wiki_jobs(
                WcsWikiJobsRequest(jobs=follow[i : i + _ENQUEUE_PER_REQUEST])
            )
            enqueued += accepted.message_ids
        result["enqueued"] = enqueued
    return result


def record(report: RunReport, job: dict[str, Any], result: dict[str, Any]) -> bool:
    """Translate one job's result onto its run report; return whether it is notable.

    Counts are counts. What changed is an outcome, named: the references a
    link found, the folds a consolidation moved, the pages whose prose was
    generated, and the pages a materialise created, changed or removed —
    a page rewritten identically is only counted. A consolidation's refresh
    of the thin list is counted too: how many are thin, and how many were
    written and removed. What a person should look
    at is an issue: work the job paid for and could not finish — a batch the
    model answered badly, an entity whose prose failed twice — which nothing
    retries on its own, because the job itself succeeded and its message is
    gone.

    A job that did something — linked, decided, generated, wrote or removed
    pages, paid for a model call, or queued a follow-on — is notable, so its
    report goes to Discord even when it is clean, as every other queue-driven
    cog's does (transcription-cog marks each asked-for run notable). A job
    that found nothing left to do, a duplicate delivery meeting its
    watermark, stays a log line.
    """
    kind = str(job.get("job"))
    spent = result.get("usage") or {}
    calls = int(spent.get("calls") or 0)
    enqueued = len(result.get("enqueued") or [])
    report.count("model_calls", calls)
    report.count("cost_usd_cents", round(float(spent.get("cost_usd") or 0) * 100))
    report.count("jobs_enqueued", enqueued)
    done = 0
    if kind == "link":
        done = int(result.get("linked") or 0)
        report.ok(done)
        report.count("statements", int(result.get("statements") or 0))
        for ref in result.get("references") or []:
            report.created("reference", str(ref))
        if result.get("failed_batches"):
            report.issue(
                "link_batch_failed",
                str(result.get("source_id")),
                detail=f"{result['failed_batches']} batch(es) left unlinked; "
                "re-send this link job to retry them",
            )
    elif kind == "consolidate":
        done = int(result.get("decided") or 0)
        report.ok(done)
        for fold in result.get("folds") or []:
            report.updated("fold", str(fold))
        thin = result.get("thin") or {}
        if thin:
            report.count("thin_entities", int(thin.get("total") or 0))
            report.count("thin_written", int(thin.get("written") or 0))
            report.count("thin_removed", int(thin.get("removed") or 0))
        if result.get("failed_batches"):
            report.issue(
                "consolidate_batch_failed",
                ", ".join(result.get("entities") or []),
                detail=f"{result['failed_batches']} batch(es) left undecided",
            )
    elif kind == "generate":
        done = int(result.get("generated") or 0)
        report.ok(done)
        for slug in result.get("generated_slugs") or []:
            report.updated("prose", str(slug))
        for slug in result.get("failed_slugs") or []:
            report.issue("generate_failed", str(slug))
    elif kind == "materialise":
        removed = int(result.get("deleted") or 0)
        written = int(result.get("written") or 0)
        created = list(result.get("created") or [])
        changed = list(result.get("changed") or [])
        done = written + removed
        report.ok(written)
        report.count("pages_removed", removed)
        report.count("pages_unchanged", max(0, written - len(created) - len(changed)))
        for slug in created:
            report.created("page", str(slug))
        for slug in changed:
            report.updated("page", str(slug))
        for slug in result.get("removed_slugs") or []:
            report.removed("page", str(slug))
    return bool(done or calls or enqueued)


def process_message(body: str, *, budget_s: int, run_id: str) -> dict[str, Any]:
    """Run the job one message asks for. Raises if it must be redelivered.

    One run report per job, as every cog sends one per run: posted when the
    job did something (see :func:`record`), a WARN when it finished with work
    left undone, a log line when it found nothing to do, and on an exception
    the report goes out on the way (``run_report``) before the message
    returns to the queue.
    """
    job = job_of(body)
    log.info("worker: run %s job=%s", run_id, job.get("job"))
    with (
        run_report(f"wiki-{job.get('job')}", repo=REPO, run_id=run_id) as report,
        tempfile.TemporaryDirectory(prefix="wiki-state-") as tmp,
        deadline(budget_s),
    ):
        result = run(job, client=_state_client(), state_dir=Path(tmp), run_id=run_id)
        report.send(notable=record(report, job, result))
    log.info(
        "worker: done %s job=%s next=%s usage=%s",
        run_id,
        job.get("job"),
        len(result.get("next") or []),
        result.get("usage"),
    )
    return result


def _report_unprocessable(exc: BaseException, run_id: str) -> None:
    """Say a message could not be read. Only this case: nothing else ran."""
    try:
        post_run_finding(
            "wiki_job",
            "ERROR",
            text=f"an unprocessable message failed: {type(exc).__name__}: {exc}",
            repo=REPO,
            source="queue_consumer",
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def _job_label(body: str) -> str:
    """The message's job, for the timing line. Never raises."""
    try:
        return str(job_of(body).get("job"))
    except Exception:  # noqa: BLE001 — a label must not fail the run
        return "unreadable"


def _budget(context: Any) -> int:
    """Lambda's remaining seconds. :func:`._deadline.deadline` keeps the margin,
    so a job that runs long stops itself and its record is returned, rather
    than being killed mid-write. Without a context (a local call), no guard.
    """
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    return int(remaining() / 1000) if remaining is not None else 0


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises: reporting per record is correct at every batch size.
    """
    records = event.get("Records", []) if isinstance(event, dict) else []
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return {
            "batchItemFailures": [
                {"itemIdentifier": str(r.get("messageId") or "")} for r in records
            ]
        }

    failures: list[dict[str, str]] = []
    for record in records:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        attempt = (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")
        with timing.invocation(
            cog="wiki-curator", mode=_job_label(body), attempt=attempt
        ) as timed:
            try:
                process_message(body, budget_s=_budget(context), run_id=message_id)
            except UnprocessableMessage as exc:
                timed.label(outcome="unprocessable")
                log.error(
                    "worker: unprocessable message (attempt %s): %s", attempt, exc
                )
                if attempt in ("1", "?"):
                    _report_unprocessable(exc, message_id)
                failures.append({"itemIdentifier": message_id})
            except Exception:  # noqa: BLE001 — every failure is a retry
                timed.label(outcome="failed")
                # Already reported by the job's run report on its way out.
                log.exception("worker: job failed (attempt %s)", attempt)
                failures.append({"itemIdentifier": message_id})
            else:
                timed.label(outcome="ok")

    if failures:
        log.warning(
            "worker: %d of %d record(s) returned to the queue",
            len(failures),
            len(records),
        )
    return {"batchItemFailures": failures}
