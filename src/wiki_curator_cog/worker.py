"""The Lambda entrypoint: one wiki job in, its result and follow-ons out.

The curator runs on Lambda behind its own FIFO queue, declared in
mini-app-polis/infra (ADR-0006). The API is the only producer: it
enqueues a ``link`` job when a source is composed, and the jobs a job names
as its follow-ons, which this worker posts back to it. Every message is

    {"type": "wiki.job", "version": 1,
     "payload": {"job": "link", "source_id": "<uuid>"}}

with a payload :func:`wiki_curator_cog.units.validate` accepts.

**Side by side only where it is safe.** The queue is FIFO and the API picks
each job's message group by what it writes, when it enqueues.
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

import os
import tempfile
from pathlib import Path
from typing import Any

from mini_app_polis import environment, load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import (
    WcsWikiJob,
    WcsWikiJobsRequest,
    WikiJobMessage,
)
from mini_app_polis.lambda_worker import (
    UnprocessableMessage,
    all_failed,
    deadline,
    parse_message,
    process_records,
)
from mini_app_polis.pipeline_status import RunReport, post_run_finding, run_report
from mini_app_polis.sentry import init_sentry

from . import link, state_backend, units, usage
from ._version import __version__
from .flow import MACHINE_NAME, REPO, _fetch_export, _require_corpus, _state_client

__all__ = ["UnprocessableMessage", "job_of", "lambda_handler", "process_message"]

log = logger_mod.get_logger()

#: How long before the function's timeout a job is stopped, so that its
#: run report can still be sent and the handler can still return.
DEADLINE_MARGIN_SECONDS = 30

#: The most jobs one enqueue request may carry, read from the contract.
_ENQUEUE_PER_REQUEST: int = next(
    m.max_length
    for m in WcsWikiJobsRequest.model_fields["jobs"].metadata
    if getattr(m, "max_length", None) is not None
)

#: Whether this container has logged its effective settings yet.
_settings_logged = False

# At import, once per cold start (see transcription-cog's worker).
# An empty DSN in production logs an ERROR rather than passing silently
# (CD-038); the service tag tells this cog's events apart in the shared project.
init_sentry(MACHINE_NAME)


def effective_settings() -> dict[str, Any]:
    """Every name this worker reads, with the value in effect, defaults applied.

    The API base URL is keyed by the variable actually read in this
    environment. Only names on ``SETTINGS_ALLOWLIST`` are logged with their
    value (:func:`mini_app_polis.environment.log_settings`); the rest are
    logged as set or unset.
    """
    provider, model = link.llm_settings()

    def raw(name: str) -> str:
        return (os.environ.get(name) or "").strip()

    return {
        "ENVIRONMENT": environment.current_environment().value,
        environment.env_var_name("KAIANO_API_BASE_URL"): environment.api_base_url(),
        "WIKI_CURATOR_COG_API_KEY": raw("WIKI_CURATOR_COG_API_KEY"),
        "LLM_PROVIDER": provider,
        "LLM_MODEL": model,
        "ANTHROPIC_API_KEY": raw("ANTHROPIC_API_KEY"),
        "OPENAI_API_KEY": raw("OPENAI_API_KEY"),
        "LLM_PRICE_INPUT_PER_MTOK": raw("LLM_PRICE_INPUT_PER_MTOK"),
        "LLM_PRICE_OUTPUT_PER_MTOK": raw("LLM_PRICE_OUTPUT_PER_MTOK"),
        "LOGGING_LEVEL": raw("LOGGING_LEVEL").upper() or "INFO",
        "SENTRY_DSN_COGS": raw("SENTRY_DSN_COGS"),
    }


def _log_settings_once() -> None:
    """Log the effective settings on this container's first invocation.

    After the first load from SSM, so the values are the ones in effect.
    """
    global _settings_logged
    if _settings_logged:
        return
    _settings_logged = True
    environment.log_settings(effective_settings())


def job_of(body: str) -> dict[str, Any]:
    """The job a message asks for. Raises UnprocessableMessage otherwise.

    The envelope and the payload's shape are read against the shared
    message model; what each kind of job needs is checked by
    :func:`wiki_curator_cog.units.validate`. The job comes back as the
    fields the message set, so a job carries what its producer sent and
    nothing the model filled in.
    """
    message = parse_message(body, WikiJobMessage)
    job = message.payload.model_dump(mode="json", exclude_unset=True)
    try:
        units.validate(job)
    except units.JobError as exc:
        raise UnprocessableMessage(str(exc)) from exc
    return job


def run(
    job: dict[str, Any], *, client: Any, state_dir: Path, run_id: str | None = None
) -> dict[str, Any]:
    """Run one job against the API and send the follow-ons it names.

    ``run_id`` — the queue message id — is stored on the pages the job
    writes, with the source that started its chain.
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
    a page rewritten identically is only counted. A refresh of the thin list
    (consolidate, link, the every-page rebuild) is counted too: how many are
    thin, and how many were written and removed. What a person should look
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
    thin = result.get("thin") or {}
    if thin:
        report.count("thin_entities", int(thin.get("total") or 0))
        report.count("thin_written", int(thin.get("written") or 0))
        report.count("thin_removed", int(thin.get("removed") or 0))
    return bool(done or calls or enqueued)


def process_message(body: str, *, run_id: str, context: Any = None) -> dict[str, Any]:
    """Run the job one message asks for. Raises if it must be redelivered.

    One run report per job, as every cog sends one per run: posted when the
    job did something (see :func:`record`), a WARN when it finished with work
    left undone, a log line when it found nothing to do, and on an exception
    the report goes out on the way (``run_report``) before the message
    returns to the queue.

    ``context`` is Lambda's: the job is stopped
    :data:`DEADLINE_MARGIN_SECONDS` before the function's timeout, inside
    the report, so a job that runs long fails the ordinary way and says so
    rather than being killed mid-write. Without one (a local call), no
    guard.
    """
    job = job_of(body)
    log.info("worker: run %s job=%s", run_id, job.get("job"))
    with (
        run_report(
            f"wiki-{job.get('job')}", repo=REPO, run_id=run_id, version=__version__
        ) as report,
        tempfile.TemporaryDirectory(prefix="wiki-state-") as tmp,
        deadline(context, margin_seconds=DEADLINE_MARGIN_SECONDS),
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
            version=__version__,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def _job_label(body: str) -> str:
    """The message's job, for the timing line. Never raises."""
    try:
        return str(job_of(body).get("job"))
    except Exception:  # noqa: BLE001 — a label must not fail the run
        return "unreadable"


def _attempt(record: dict[str, Any]) -> str:
    """Which receive of this message this is, or "?" when unknown."""
    return str((record.get("attributes") or {}).get("ApproximateReceiveCount", "?"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises an ordinary failure: reporting per record is correct at
    every batch size, and
    :func:`~mini_app_polis.lambda_worker.process_records` names every record
    whose job raised.
    """
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return all_failed(event)
    _log_settings_once()

    def handle(record: dict[str, Any]) -> None:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        attempt = _attempt(record)
        with timing.invocation(
            cog="wiki-curator", mode=_job_label(body), attempt=attempt
        ) as timed:
            try:
                process_message(body, run_id=message_id, context=context)
            except UnprocessableMessage:
                timed.label(outcome="unprocessable")
                raise
            except BaseException:
                timed.label(outcome="failed")
                # Already reported by the job's run report on its way out.
                log.exception("worker: job failed (attempt %s)", attempt)
                raise
            timed.label(outcome="ok")

    def on_unprocessable(exc: UnprocessableMessage, record: dict[str, Any]) -> None:
        # Once, on the first receive: every later receive fails the same way,
        # and the dead-letter queue it ends up in has an alarm of its own.
        if _attempt(record) in ("1", "?"):
            _report_unprocessable(exc, str(record.get("messageId") or ""))

    result = process_records(event, handle, on_unprocessable=on_unprocessable)
    failures = result["batchItemFailures"]
    if failures:
        records = event.get("Records", []) if isinstance(event, dict) else []
        log.warning(
            "worker: %d of %d record(s) returned to the queue",
            len(failures),
            len(records),
        )
    return result
