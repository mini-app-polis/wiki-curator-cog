"""Tests for git_ops.WikiRepo.

Focused on the push-retry behavior added to absorb transient HTTP/2 /
RST_STREAM failures observed in production backfills. The clone, stage,
and commit paths are exercised by integration tests against real local
repos elsewhere; here we mock the remote so we can assert on retry
counts and backoff without spinning up an actual git daemon.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git import GitCommandError

from wiki_curator_cog import git_ops
from wiki_curator_cog.git_ops import WikiRepo


def _make_config(tmp_path: Path) -> MagicMock:
    """Build a Config double sufficient for WikiRepo construction."""
    cfg = MagicMock()
    cfg.wiki_repo_path = tmp_path
    cfg.wiki_repo_remote = "origin"
    cfg.wiki_branch = "phase-1-backfill"
    cfg.wiki_git_author_name = "Wiki Curator"
    cfg.wiki_git_author_email = "curator@example.com"
    return cfg


def _build_wiki_repo(tmp_path: Path) -> tuple[WikiRepo, MagicMock]:
    """Instantiate WikiRepo with a stubbed ``Repo`` so no real git runs.

    Returns ``(wiki_repo, mock_remote)`` so tests can configure the
    remote's ``.push`` side-effects.
    """
    mock_remote = MagicMock()
    mock_repo = MagicMock()
    mock_repo.remote.return_value = mock_remote

    cfg = _make_config(tmp_path)
    with patch.object(git_ops, "Repo", return_value=mock_repo):
        wiki_repo = WikiRepo(cfg)
    return wiki_repo, mock_remote


def _git_error(stderr: str) -> GitCommandError:
    """Build a GitCommandError shaped like the production failure."""
    return GitCommandError(
        ["git", "push", "--porcelain", "--set-upstream", "--", "origin"],
        1,
        stderr.encode(),
    )


def test_push_succeeds_on_first_attempt(tmp_path: Path) -> None:
    wiki_repo, remote = _build_wiki_repo(tmp_path)

    with patch.object(git_ops.time, "sleep") as mock_sleep:
        wiki_repo.push()

    # One successful push, no retry, no sleep.
    assert remote.push.call_count == 1
    mock_sleep.assert_not_called()


def test_push_recovers_after_transient_failure(tmp_path: Path) -> None:
    """A transient HTTP/2 stream error on attempt 1 should not surface
    to the caller if attempt 2 succeeds."""
    wiki_repo, remote = _build_wiki_repo(tmp_path)
    remote.push.side_effect = [
        _git_error(
            "error: RPC failed; curl 92 HTTP/2 stream 5 was not closed cleanly: "
            "PROTOCOL_ERROR (err 1)\nfatal: the remote end hung up unexpectedly"
        ),
        None,  # second call succeeds
    ]

    with patch.object(git_ops.time, "sleep") as mock_sleep:
        wiki_repo.push()

    assert remote.push.call_count == 2
    # First retry waits the base backoff (2.0s).
    mock_sleep.assert_called_once_with(2.0)


def test_push_retries_up_to_max_attempts(tmp_path: Path) -> None:
    """If every attempt fails, the final exception is propagated and
    the retry count matches the configured ceiling."""
    wiki_repo, remote = _build_wiki_repo(tmp_path)
    remote.push.side_effect = _git_error("fatal: the remote end hung up unexpectedly")

    with patch.object(git_ops.time, "sleep") as mock_sleep:
        with pytest.raises(GitCommandError):
            wiki_repo.push()

    assert remote.push.call_count == git_ops._PUSH_MAX_ATTEMPTS
    # Backoff between attempts: base, base*2 — no sleep after the
    # final failed attempt.
    assert mock_sleep.call_count == git_ops._PUSH_MAX_ATTEMPTS - 1
    delays = [call.args[0] for call in mock_sleep.call_args_list]
    # Exponential pattern: 2, 4, ...
    assert delays[0] == git_ops._PUSH_BACKOFF_BASE_SECONDS
    assert delays[1] == git_ops._PUSH_BACKOFF_BASE_SECONDS * 2


def test_push_uses_explicit_refspec_and_sets_upstream(tmp_path: Path) -> None:
    """Confirm the push call shape — explicit refspec, set_upstream=True."""
    wiki_repo, remote = _build_wiki_repo(tmp_path)
    wiki_repo.push()

    remote.push.assert_called_once_with(
        refspec="phase-1-backfill:phase-1-backfill",
        set_upstream=True,
    )


# ── staged_changes: what the next commit would contain ──────────────────


def _repo_reporting(tmp_path: Path, name_status: str) -> WikiRepo:
    """A WikiRepo whose `git diff --cached --name-status HEAD` is canned."""
    mock_repo = MagicMock()
    mock_repo.git.diff.return_value = name_status
    with patch.object(git_ops, "Repo", return_value=mock_repo):
        return WikiRepo(_make_config(tmp_path))


def test_staged_changes_groups_by_what_happened(tmp_path: Path) -> None:
    repo = _repo_reporting(
        tmp_path,
        "M\tconcepts/anchor.md\nA\tconcepts/new.md\nD\tconcepts/gone.md\n",
    )
    assert repo.staged_changes() == {
        "added": ["concepts/new.md"],
        "modified": ["concepts/anchor.md"],
        "removed": ["concepts/gone.md"],
    }


def test_staged_changes_is_empty_when_the_rebuild_was_identical(
    tmp_path: Path,
) -> None:
    """The case the whole method exists for."""
    repo = _repo_reporting(tmp_path, "")
    assert repo.staged_changes() == {"added": [], "modified": [], "removed": []}


def test_staged_changes_splits_a_rename_into_both_halves(tmp_path: Path) -> None:
    """A slug change is a page gone and a page arrived; say both."""
    repo = _repo_reporting(tmp_path, "R100\tsources/old-slug.md\tsources/new-slug.md\n")
    changes = repo.staged_changes()
    assert changes["removed"] == ["sources/old-slug.md"]
    assert changes["added"] == ["sources/new-slug.md"]


def test_staged_changes_asks_git_rather_than_the_index_diff(tmp_path: Path) -> None:
    """--name-status against HEAD; GitPython's index.diff reverses A and D."""
    repo = _repo_reporting(tmp_path, "")
    repo.staged_changes()
    repo._repo.git.diff.assert_called_once_with("--cached", "--name-status", "HEAD")
