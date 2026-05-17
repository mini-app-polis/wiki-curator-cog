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

from collections.abc import Iterable
from pathlib import Path

from git import Actor, Repo

from .config import Config


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
        """
        remote_name = self._config.wiki_repo_remote
        branch = self._config.wiki_branch
        remote = self._repo.remote(remote_name)
        # ``set_upstream=True`` is harmless on subsequent runs and
        # essential on the first push for a freshly-created branch.
        remote.push(refspec=f"{branch}:{branch}", set_upstream=True)
