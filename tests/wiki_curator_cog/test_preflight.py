"""The credential is checked before the clone, not after the render.

On 2026-09-23 a fine-grained PAT issued with ``Contents: Read`` cloned
happily, rendered the whole corpus, committed, and was refused at
``git-receive-pack``. Everything before the push was thrown away, and the
failure read as a push problem rather than a permissions one.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from wiki_curator_cog.boot import assert_push_access

_REFS = "https://github.com/mini-app-polis/wcs-wiki.git/info/refs"


def _config(**overrides: object) -> SimpleNamespace:
    base = {
        "wiki_repo_is_https": True,
        "wiki_repo_url": "https://github.com/mini-app-polis/wcs-wiki.git",
        "gh_token": "ghp_notreal",
        "wiki_branch": "main",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@respx.mock
def test_a_writable_credential_passes() -> None:
    route = respx.get(_REFS).mock(return_value=httpx.Response(200))
    assert_push_access(_config())
    assert route.called
    # The probe must be the push endpoint, not the clone one.
    assert route.calls[0].request.url.params["service"] == "git-receive-pack"


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_a_read_only_credential_fails_before_anything_is_cloned(status: int) -> None:
    respx.get(_REFS).mock(return_value=httpx.Response(status))
    with pytest.raises(RuntimeError, match="Contents: Read and write"):
        assert_push_access(_config())


@respx.mock
def test_the_token_never_reaches_the_error_message() -> None:
    respx.get(_REFS).mock(return_value=httpx.Response(403))
    with pytest.raises(RuntimeError) as exc:
        assert_push_access(_config(gh_token="ghp_supersecretvalue"))
    assert "ghp_supersecretvalue" not in str(exc.value)


@respx.mock
def test_an_unreachable_probe_does_not_stop_the_run() -> None:
    """The clone is about to test the network anyway."""
    respx.get(_REFS).mock(side_effect=httpx.ConnectError("no route"))
    assert_push_access(_config())


@respx.mock
def test_an_unexpected_status_is_a_warning_not_a_failure() -> None:
    respx.get(_REFS).mock(return_value=httpx.Response(500))
    assert_push_access(_config())


@respx.mock
def test_an_ssh_remote_is_skipped() -> None:
    """A deploy key has no equivalent cheap probe."""
    route = respx.get(_REFS).mock(return_value=httpx.Response(403))
    assert_push_access(
        _config(
            wiki_repo_is_https=False,
            wiki_repo_url="git@github.com:mini-app-polis/wcs-wiki.git",
        )
    )
    assert not route.called
