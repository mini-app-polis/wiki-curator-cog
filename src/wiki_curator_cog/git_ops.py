"""Git operations for the wiki repo.

The curator commits one-per-source to the wcs-wiki repo and pushes when
each ingest succeeds. Concurrency is gated to a single Prefect slot, so
no locking is needed here.

Uses GitPython rather than shelling out so errors are typed and tests
can mock cleanly.
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

    def commit(self, message: str) -> str:
        """Create a commit. Returns the new commit hex."""
        commit = self._repo.index.commit(
            message, author=self._author, committer=self._author
        )
        return commit.hexsha

    def push(self) -> None:
        """Push to the configured remote (default: origin)."""
        remote = self._repo.remote(self._config.wiki_repo_remote)
        remote.push()
