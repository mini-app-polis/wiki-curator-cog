"""The Lambda entrypoint: one wiki job in, its result and follow-ons out (MINI-95).

The curator runs on Lambda behind its own FIFO queue,
``wiki-curator-jobs.fifo`` (ADR-006). The API is the only producer: it
enqueues a ``link`` job when a source is composed, and the jobs a job names
as its follow-ons, which this worker posts back to it. Every message is

    {"type": "wiki.job", "version": 1,
     "payload": {"job": "link", "source_id": "<uuid>"}}

with a payload :func:`wiki_curator_cog.units.validate` accepts.

**One job at a time.** The queue has one message group, so jobs never run
side by side: two of them cannot race on one state document, or pay twice
for one entity.

**State in the API, no cache between invocations.** Each invocation reads the
export and the state documents from the API and writes back what it changed.
Its cache folder is a fresh temporary directory, so a warm container never
compares the API with what it saw an invocation ago.

**Nothing is deleted until the work is done.** The event source mapping
deletes every record the handler does not name in ``batchItemFailures``, so
every failure below names its record. A job that fails is retried whole;
that is safe, because every pass is watermarked and skips what an earlier
attempt finished.

**An unreadable message is a producer bug.** It is reported once, exhausts
its receives and lands in the dead-letter queue, where someone can see what
produced it.

**Follow-ons are sent before the job counts as done.** If the API cannot
take them, the job is retried: its own work is a no-op the second time, and
the follow-ons are asked for again.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import sentry_sdk
from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import WcsWikiJob, WcsWikiJobsRequest
from mini_app_polis.environment import current_environment
from mini_app_polis.pipeline_status import post_run_finding

from . import link, state_backend, units, usage
from ._deadline import deadline
from .flow import MACHINE_NAME, REPO, _fetch_export, _require_corpus, _state_client

log = logger_mod.get_logger()

#: Must match api-kaianolevine-com's wiki_dispatch.
MESSAGE_VERSION = 1
TYPE_JOB = "wiki.job"

# At import, once per cold start (see transcription-cog's worker).
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN_COGS"),
    environment=current_environment().value,
)
sentry_sdk.get_global_scope().set_tag("service", MACHINE_NAME)


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


def run(job: dict[str, Any], *, client: Any, state_dir: Path) -> dict[str, Any]:
    """Run one job against the API and send the follow-ons it names."""
    export = _fetch_export()
    _require_corpus(export)
    llm, model = link.llm_from_env()
    ctx = units.build_context(
        export,
        state_backend.ApiState(client),
        units.StatePaths.under(state_dir),
        llm=llm,
        model=model,
    )
    result = units.handle(job, ctx)
    result["usage"] = usage.usage_of(llm)
    follow = result.get("next") or []
    if follow:
        accepted = client.enqueue_wcs_wiki_jobs(
            WcsWikiJobsRequest(jobs=[WcsWikiJob.model_validate(j) for j in follow])
        )
        result["enqueued"] = accepted.message_ids
    return result


def process_message(body: str, *, budget_s: int, run_id: str) -> dict[str, Any]:
    """Run the job one message asks for. Raises if it must be redelivered."""
    job = job_of(body)
    log.info("worker: run %s job=%s", run_id, job.get("job"))
    with tempfile.TemporaryDirectory(prefix="wiki-state-") as tmp, deadline(budget_s):
        result = run(job, client=_state_client(), state_dir=Path(tmp))
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
