"""Git operations for the wiki repo.

The curator commits one-per-source to the wcs-wiki repo and pushes when
each ingest succeeds. Concurrency is gated to a single Prefect slot, so
no locking is needed here.

Uses GitPython rather than shelling out so errors are typed and tests
can mock cleanly.

Branching model (Phase 1):
  - WIKI_BRANCH selects the branch the curator commits and pushes to.
  - For Phase 1 backfill we use a feature branch (e.g.
    ``phase-1-backfill``) so the entire backfill lands as a reviewable
    PR. Subsequent incremental runs target ``main`` directly.
  - The branch is checked out (and, if it exists on the remote, reset
    to the remote tip) by ``boot.ensure_wiki_clone`` BEFORE WikiRepo is
    instantiated. WikiRepo only commits and pushes.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from git import Actor, GitCommandError, Repo
from mini_app_polis import logger as log

from .config import Config

LOG = log.get_logger()

# Push retries — large backfill pushes (~1000 file changes, ~90 commits)
# occasionally trip transient network errors on the way to GitHub:
# libcurl HTTP/2 stream resets, RST_STREAM, "remote end hung up
# unexpectedly". The HTTP/1.1 + bumped postBuffer settings configured
# in boot.ensure_wiki_clone reduce the likelihood, but we still retry
# at the GitPython layer to ride out any residual blips.
#
# Three attempts is enough for transient network issues without
# masking a real ref-rejection (those fail consistently across
# attempts and surface on the third). Backoff is exponential so we
# don't hammer the remote.
_PUSH_MAX_ATTEMPTS: int = 3
_PUSH_BACKOFF_BASE_SECONDS: float = 2.0


class WikiRepo:
    """Thin wrapper for the curator's git operations."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._repo = Repo(config.wiki_repo_path)
        self._author = Actor(config.wiki_git_author_name, config.wiki_git_author_email)

    @property
    def path(self) -> Path:
        """The working tree this wrapper operates on."""
        return self._config.wiki_repo_path

    @property
    def branch(self) -> str:
        """The branch the curator commits and pushes to."""
        return self._config.wiki_branch

    def current_branch_name(self) -> str:
        """The name of the branch currently checked out.

        Read from the repository rather than from config, so a caller can
        tell whether the checkout matches :attr:`branch`.
        """
        return self._repo.active_branch.name

    def has_changes(self) -> bool:
        """Return True if there are staged or unstaged changes."""
        return self._repo.is_dirty(untracked_files=True)

    def stage(self, paths: Iterable[Path]) -> None:
        """Stage the given paths. Paths must be inside the wiki repo."""
        rel = [str(p.relative_to(self.path)) for p in paths]
        if rel:
            self._repo.index.add(rel)

    def stage_all(self) -> None:
        """Stage every change in the working tree."""
        self._repo.git.add(A=True)

    def stage_removal(self, paths: Iterable[Path]) -> None:
        """Stage already-deleted files for removal in the next commit.

        Callers must have already removed the files from the working
        tree (curator.ingest_one_source does this when a source moves
        buckets or slugs). This method only updates the git index to
        match — it does NOT attempt to remove files itself.
        """
        rel = [str(p.relative_to(self.path)) for p in paths]
        if rel:
            # working_tree=False because the file is already gone from
            # disk; we just need git to record the deletion.
            self._repo.index.remove(rel, working_tree=False)

    #: ``git diff --name-status`` codes, mapped onto what happened.
    _STATUS: dict[str, str] = {"A": "added", "M": "modified", "D": "removed"}

    def staged_changes(self) -> dict[str, list[str]]:
        """What the next commit would contain, grouped by what happened.

        Asked of git rather than inferred from what the render wrote, and
        that distinction is the whole point. ``_write_bundle`` rewrites
        every page on every run whether or not a byte differs, so "pages
        written" is a constant — 2505 of them at the time of writing — and
        says nothing about whether the wiki changed. A run report built on
        it announces 2505 created pages every time, including for a
        rebuild that produced a byte-identical tree.

        ``git diff --cached --name-status HEAD`` rather than GitPython's
        ``index.diff("HEAD")``: that compares the index to HEAD in that
        order, so additions are reported as deletions and vice versa, and
        a report that swaps those is worse than no report.

        A rename is recorded as both halves — a page gone and a page
        arrived — because for a wiki that is what a slug change is, and
        naming only the new one hides a dead link.
        """
        raw = str(self._repo.git.diff("--cached", "--name-status", "HEAD"))
        out: dict[str, list[str]] = {"added": [], "modified": [], "removed": []}
        for line in raw.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            code = fields[0][:1]
            if code in ("R", "C") and len(fields) >= 3:
                out["removed"].append(fields[1])
                out["added"].append(fields[2])
                continue
            key = self._STATUS.get(code)
            if key:
                out[key].append(fields[-1])
        return out

    def commit(self, message: str) -> str:
        """Create a commit. Returns the new commit hex."""
        commit = self._repo.index.commit(
            message, author=self._author, committer=self._author
        )
        return commit.hexsha

    def push(self) -> None:
        """Push the configured branch to the configured remote.

        Always uses an explicit refspec (``<branch>:<branch>``) so the
        push target is unambiguous regardless of the local branch's
        tracking state. Sets upstream on first push so any subsequent
        manual git operations from the same working tree behave
        normally.

        Retries up to ``_PUSH_MAX_ATTEMPTS`` times with exponential
        backoff to absorb transient network errors (HTTP/2 stream
        resets, RST_STREAM, momentary GitHub flakiness). The retry
        is at this layer rather than higher because the entire local
        commit graph is already prepared; replaying the backfill from
        scratch would be wasteful when the actual failure was a
        single failed HTTPS request.
        """
        remote_name = self._config.wiki_repo_remote
        branch = self._config.wiki_branch
        remote = self._repo.remote(remote_name)

        last_exc: GitCommandError | None = None
        for attempt in range(1, _PUSH_MAX_ATTEMPTS + 1):
            try:
                # ``set_upstream=True`` is harmless on subsequent runs
                # and essential on the first push for a freshly-created
                # branch.
                remote.push(refspec=f"{branch}:{branch}", set_upstream=True)
                if attempt > 1:
                    LOG.info(
                        "wiki.push.recovered",
                        extra={"branch": branch, "attempts": attempt},
                    )
                return
            except GitCommandError as exc:
                last_exc = exc
                if attempt < _PUSH_MAX_ATTEMPTS:
                    delay = _PUSH_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    LOG.warning(
                        "wiki.push.transient_failure",
                        extra={
                            "branch": branch,
                            "attempt": attempt,
                            "max_attempts": _PUSH_MAX_ATTEMPTS,
                            "next_delay_seconds": delay,
                            "stderr": exc.stderr,
                        },
                    )
                    time.sleep(delay)
                    continue
                LOG.error(
                    "wiki.push.exhausted_retries",
                    extra={
                        "branch": branch,
                        "attempts": _PUSH_MAX_ATTEMPTS,
                        "stderr": exc.stderr,
                    },
                )
        assert last_exc is not None  # for type-checkers; loop guarantees this
        raise last_exc
