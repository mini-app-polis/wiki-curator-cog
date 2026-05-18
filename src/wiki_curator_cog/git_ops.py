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
        return self._config.wiki_repo_path

    @property
    def branch(self) -> str:
        return self._config.wiki_branch

    def current_branch_name(self) -> str:
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
