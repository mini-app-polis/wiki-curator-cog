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
        from mini_app_polis.pipeline_status import (
            RunReport, make_failure_hook
        )
        from mini_app_polis.environment import (
            Effect, current_environment, effect_enabled, env_var
        )

    Tests that exercise the curator never call the API stub's methods;
    they pass a fake api directly to ingest_one_source. The stub
    exists purely so module imports succeed.
    """
    try:
        # Prefer the real package whenever it is installed. The previous
        # guard only checked sys.modules, which at conftest-import time is
        # always empty for this package — so the stub won every run, and
        # these tests never touched the library they claim to depend on.
        import mini_app_polis.api  # noqa: F401
        import mini_app_polis.environment  # noqa: F401
        import mini_app_polis.logger  # noqa: F401
        import mini_app_polis.pipeline_status  # noqa: F401
    except ImportError:
        pass
    else:
        return

    if "mini_app_polis" in sys.modules:
        return  # an earlier stub is already present

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

    status_module = types.ModuleType("mini_app_polis.pipeline_status")

    class _StubDeliveryReport:
        """Mirrors the real report's shape so call sites can read it."""

        def __init__(self) -> None:
            self.sent = 0
            self.suppressed = 1
            self.failed = 0
            self.skipped = 0

        @property
        def ok(self) -> bool:
            return True

    class _StubRunReport:
        """Minimal RunReport so main can accumulate and send without the real package."""

        def __init__(self, flow_name: str, *, repo: str, **_kwargs: object) -> None:
            self.flow_name = flow_name
            self.repo = repo
            self.processed = 0
            self.issues: dict[str, int] = {}
            self.counters: dict[str, object] = {}
            self._notable: bool | None = None

        def ok(self, n: int = 1) -> None:
            self.processed += n

        def issue(
            self, reason: str, item: str | None = None, **_kwargs: object
        ) -> None:
            self.issues[reason] = self.issues.get(reason, 0) + 1

        def count(self, key: str, value: object) -> None:
            self.counters[key] = value

        @property
        def severity(self) -> str:
            return "WARN" if self.issues else "SUCCESS"

        def send(
            self, *, notable: bool = False, **_kwargs: object
        ) -> _StubDeliveryReport:
            self._notable = notable
            return _StubDeliveryReport()

    def post_run_finding(*args: object, **kwargs: object) -> _StubDeliveryReport:  # noqa: ARG001 - matches the real signature
        return _StubDeliveryReport()

    def make_failure_hook(*args: object, **kwargs: object):  # noqa: ANN202, ARG001 - matches the real signature
        def _hook(flow: object, flow_run: object, state: object) -> None:
            return None

        return _hook

    status_module.post_run_finding = post_run_finding  # type: ignore[attr-defined]
    status_module.make_failure_hook = make_failure_hook  # type: ignore[attr-defined]
    status_module.DeliveryReport = _StubDeliveryReport  # type: ignore[attr-defined]
    status_module.RunReport = _StubRunReport  # type: ignore[attr-defined]

    env_module = types.ModuleType("mini_app_polis.environment")

    class _StubEnvironment:
        PRODUCTION = type("E", (), {"value": "production"})()
        DEVELOPMENT = type("E", (), {"value": "development"})()
        LOCAL = type("E", (), {"value": "local"})()

    class _StubEffect:
        HEALTHCHECKS = type(
            "E", (), {"name": "HEALTHCHECKS", "value": "healthchecks"}
        )()

    def _current_environment():
        return _StubEnvironment.PRODUCTION

    def _effect_enabled(_effect: object) -> bool:
        return True

    def _env_var(name: str) -> str:
        return (os.environ.get(name) or "").strip()

    env_module.Environment = _StubEnvironment  # type: ignore[attr-defined]
    env_module.Effect = _StubEffect  # type: ignore[attr-defined]
    env_module.current_environment = _current_environment  # type: ignore[attr-defined]
    env_module.effect_enabled = _effect_enabled  # type: ignore[attr-defined]
    env_module.env_var = _env_var  # type: ignore[attr-defined]

    pkg.logger = logger_module  # type: ignore[attr-defined]
    pkg.api = api_module  # type: ignore[attr-defined]
    pkg.pipeline_status = status_module  # type: ignore[attr-defined]
    pkg.environment = env_module  # type: ignore[attr-defined]

    sys.modules["mini_app_polis"] = pkg
    sys.modules["mini_app_polis.logger"] = logger_module
    sys.modules["mini_app_polis.api"] = api_module
    sys.modules["mini_app_polis.pipeline_status"] = status_module
    sys.modules["mini_app_polis.environment"] = env_module


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
