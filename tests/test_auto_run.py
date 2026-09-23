"""Starting the container is not, by itself, a request to rebuild the wiki.

In production it is: the deploy is the trigger until the API owns the wake.
In development a deploy is someone shipping code, and a run there would
clone the real wcs-wiki, re-render the whole corpus and push to a branch of
it — there is no second wiki to push to.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import wiki_curator_cog.main as main


class _Env:
    def __init__(self, value: str) -> None:
        self.value = value


def _in(environment: str):
    """Pin current_environment(), and its identity check against PRODUCTION."""
    production = _Env("production")
    here = production if environment == "production" else _Env(environment)
    return (
        patch.object(main, "current_environment", return_value=here),
        patch.object(main, "Environment", type("E", (), {"PRODUCTION": production})),
    )


@pytest.mark.parametrize("environment", ["production", "development", "local"])
def test_run_on_start_is_obeyed_in_both_directions(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_patch, enum_patch = _in(environment)
    with env_patch, enum_patch:
        monkeypatch.setenv("RUN_ON_START", "true")
        assert main._auto_run_enabled() is True
        monkeypatch.setenv("RUN_ON_START", "false")
        assert main._auto_run_enabled() is False


def test_a_deploy_runs_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_ON_START", raising=False)
    env_patch, enum_patch = _in("production")
    with env_patch, enum_patch:
        assert main._auto_run_enabled() is True


@pytest.mark.parametrize("environment", ["development", "local"])
def test_a_deploy_does_not_run_anywhere_else(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RUN_ON_START", raising=False)
    env_patch, enum_patch = _in(environment)
    with env_patch, enum_patch:
        assert main._auto_run_enabled() is False


def test_a_gated_start_exits_cleanly_without_loading_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0, and before load_config — a container doing nothing needs no secrets."""
    monkeypatch.delenv("RUN_ON_START", raising=False)
    env_patch, enum_patch = _in("development")
    with (
        env_patch,
        enum_patch,
        patch.object(main, "load_config", side_effect=AssertionError("too early")),
        patch.object(main, "export_run") as export,
    ):
        assert main.main([]) == 0
    export.assert_not_called()


def test_naming_the_mode_runs_regardless_of_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`export` is an explicit request; the gate is only about bare starts."""
    monkeypatch.delenv("RUN_ON_START", raising=False)
    env_patch, enum_patch = _in("development")
    with (
        env_patch,
        enum_patch,
        patch.object(main, "load_config"),
        patch.object(main, "_init_observability"),
        patch.object(main, "_ping_healthchecks"),
        patch.object(main, "export_run", return_value={"paths_written": 0}) as export,
    ):
        assert main.main(["export"]) == 0
    export.assert_called_once()
