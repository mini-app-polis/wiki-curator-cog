"""Shared pytest fixtures for wiki-curator-cog."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def wiki_repo_path(tmp_path: Path) -> Path:
    """A throwaway directory shaped like the wcs-wiki repo skeleton.

    Provides just enough structure for inventory.build_inventory() and
    AliasMap.load() to function without raising.
    """
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n")
    for sub in (
        "sources/kate",
        "sources/kaiano",
        "sources/robert",
        "sources/external",
        "concepts",
        "techniques",
        "instructors",
        "terminology",
        "views",
    ):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "instructors" / "_aliases.yaml").write_text(
        "# alias map\n\n"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would otherwise leak from the host shell."""
    for key in list(os.environ):
        if key.startswith(("KAIANO_", "WIKI_", "LLM_", "SENTRY_", "HEALTHCHECKS_")):
            monkeypatch.delenv(key, raising=False)
