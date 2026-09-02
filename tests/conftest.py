"""Shared pytest fixtures for wiki-curator-cog."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
import types
from pathlib import Path

import pytest

# The project targets Python 3.11+ and uses ``datetime.UTC`` throughout
# (per ruff's UP017). When tests are executed against an older
# interpreter (e.g., a sandbox where 3.11 isn't installable), polyfill
# the attribute so test files can use ``dt.UTC`` uniformly. No-op on
# 3.11+ where the attribute already exists.
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]  # noqa: UP017

# Polyfill enum.StrEnum (3.11+) for older interpreters so the curator
# module can be imported. No-op on 3.11+.
import enum as _enum  # noqa: E402

if not hasattr(_enum, "StrEnum"):

    class _StrEnumPolyfill(str, _enum.Enum):  # noqa: UP042
        pass

    _enum.StrEnum = _StrEnumPolyfill  # type: ignore[attr-defined]


def _install_mini_app_polis_stub() -> None:
    """Provide a stub for mini_app_polis when the real package isn't installed.

    The real common-python-utils package lives in a private GitHub
    repo (see pyproject.toml [tool.uv.sources]). In environments
    without git access it can't be fetched, so we install a minimal
    stub that satisfies the imports the curator actually uses:

        from mini_app_polis import logger as log
        log.get_logger() -> logging.Logger
        from mini_app_polis.api import KaianoApiClient
        KaianoApiClient.from_env() -> stub instance

    Tests that exercise the curator never call the API stub's methods;
    they pass a fake api directly to ingest_one_source. The stub
    exists purely so module imports succeed.
    """
    if "mini_app_polis" in sys.modules:
        return  # real package or earlier stub already present

    pkg = types.ModuleType("mini_app_polis")
    pkg.__path__ = []  # mark as a package

    logger_module = types.ModuleType("mini_app_polis.logger")

    def get_logger(name: str = "wiki-curator-cog") -> logging.Logger:
        return logging.getLogger(name)

    logger_module.get_logger = get_logger  # type: ignore[attr-defined]

    api_module = types.ModuleType("mini_app_polis.api")

    class _StubKaianoApiClient:
        @classmethod
        def from_env(cls, machine_name: str | None = None) -> _StubKaianoApiClient:  # noqa: ARG003 - matches the real signature
            return cls()

        def get(self, path: str, params: dict | None = None) -> dict:
            raise RuntimeError(
                "KaianoApiClient stub.get() called in tests — "
                "tests must not hit the network."
            )

        def post(self, path: str, payload: dict) -> dict:
            raise RuntimeError(
                "KaianoApiClient stub.post() called in tests — "
                "tests must not hit the network."
            )

    api_module.KaianoApiClient = _StubKaianoApiClient  # type: ignore[attr-defined]

    pkg.logger = logger_module  # type: ignore[attr-defined]
    pkg.api = api_module  # type: ignore[attr-defined]

    sys.modules["mini_app_polis"] = pkg
    sys.modules["mini_app_polis.logger"] = logger_module
    sys.modules["mini_app_polis.api"] = api_module


_install_mini_app_polis_stub()


@pytest.fixture
def wiki_repo_path(tmp_path: Path) -> Path:
    """A throwaway directory shaped like the wcs-wiki repo skeleton."""
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n")
    for sub in (
        "sources",
        "concepts",
        "techniques",
        "patterns",
        "drills",
        "instructors",
        "views",
    ):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would otherwise leak from the host shell."""
    for key in list(os.environ):
        if key.startswith(("KAIANO_", "WIKI_", "LLM_", "SENTRY_", "HEALTHCHECKS_")):
            monkeypatch.delenv(key, raising=False)
