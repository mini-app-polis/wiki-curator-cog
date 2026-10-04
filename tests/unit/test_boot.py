"""Tests for boot.py URL handling.

The git clone/refresh path exercises external state (subprocess git
commands against real or local-file remotes) and is covered by a
separate integration test pattern; these unit tests just lock in the
URL-manipulation helpers so PAT injection and log masking can never
silently regress.
"""

from __future__ import annotations

import pytest

from wiki_curator_cog.boot import _inject_token, mask_url

# ── _inject_token ───────────────────────────────────────────────────────


def test_inject_token_into_https_url() -> None:
    out = _inject_token(
        "https://github.com/mini-app-polis/wcs-wiki.git", "github_pat_abc"
    )
    assert out == (
        "https://x-access-token:github_pat_abc@github.com/mini-app-polis/wcs-wiki.git"
    )


def test_inject_token_preserves_port() -> None:
    out = _inject_token("https://example.com:8443/foo/bar.git", "tok")
    assert out == "https://x-access-token:tok@example.com:8443/foo/bar.git"


def test_inject_token_no_op_on_ssh_url() -> None:
    url = "git@github.com:mini-app-polis/wcs-wiki.git"
    assert _inject_token(url, "github_pat_abc") == url


def test_inject_token_no_op_when_token_empty() -> None:
    url = "https://github.com/mini-app-polis/wcs-wiki.git"
    assert _inject_token(url, "") == url


# ── mask_url ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://x-access-token:github_pat_abc@github.com/x/y.git",
            "https://***@github.com/x/y.git",
        ),
        (
            "https://github.com/x/y.git",
            "https://github.com/x/y.git",
        ),
        (
            "git@github.com:x/y.git",
            "git@github.com:x/y.git",
        ),
        (
            "https://user:pass@example.com:8080/path?q=1",
            "https://***@example.com:8080/path?q=1",
        ),
    ],
)
def test_mask_url(raw: str, expected: str) -> None:
    assert mask_url(raw) == expected


def test_mask_url_never_leaks_token() -> None:
    token = "github_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    authed = _inject_token("https://github.com/foo.git", token)
    masked = mask_url(authed)
    assert token not in masked
    assert "***" in masked
