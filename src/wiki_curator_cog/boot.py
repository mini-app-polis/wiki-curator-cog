"""Bootstrap helpers for ephemeral deployments (Railway, etc.).

On Railway, every container start gets a fresh filesystem. The cog
can't rely on a pre-existing local clone of wcs-wiki; it has to clone
on boot, push back to GitHub when done, and tolerate a re-clone on
restart.

``ensure_wiki_clone(config)`` is the single entry point. It:

  1. Clones ``WIKI_REPO_URL`` to ``WIKI_REPO_PATH`` if absent. Injects
     ``GH_TOKEN`` into the HTTPS clone URL so authenticated push works.
  2. Or, if the path exists and is already a git repo, fetches origin
     and resets to the configured branch's remote tip — so re-runs
     pick up any out-of-band changes (merged PRs, manual edits).
  3. Checks out the configured ``WIKI_BRANCH``, creating it from the
     current default-branch HEAD if it doesn't exist yet.
  4. Configures the repo's local ``user.name`` / ``user.email`` so
     GitPython commits attribute to the curator identity.

GH_TOKEN is consumed only here. It is never logged, never persisted
beyond the cloned-in remote URL (which lives inside the ephemeral
``.git/config``), and is masked when the configured URL is echoed in
INFO logs.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from git import GitCommandError, Repo
from mini_app_polis import logger as log

from .config import Config

LOG = log.get_logger()


# ── URL handling ────────────────────────────────────────────────────────


def _inject_token(url: str, token: str) -> str:
    """Return ``url`` with the PAT injected as the user component.

    GitHub recommends the ``x-access-token`` username for fine-grained
    PATs over HTTPS. The token never appears in any returned value
    other than the URL itself, which is passed straight into the
    GitPython clone call. ``mask_url`` produces the log-safe form.
    """
    if not token:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def mask_url(url: str) -> str:
    """Redact userinfo from a URL for safe logging."""
    return re.sub(r"//[^@/]+@", "//***@", url)


# ── Clone / refresh ─────────────────────────────────────────────────────


def _is_existing_repo(path) -> bool:
    return path.exists() and (path / ".git").exists()


def _remote_branch_exists(repo: Repo, remote_name: str, branch: str) -> bool:
    """True if ``refs/heads/<branch>`` exists on the named remote."""
    try:
        out = repo.git.ls_remote("--heads", remote_name, branch)
    except GitCommandError:
        return False
    return bool(out and out.strip())


def _checkout_or_create(repo: Repo, branch: str, remote_name: str) -> None:
    """Land on ``branch``, creating it from current HEAD if needed.

    If the branch exists on the remote, hard-reset the local branch to
    match the remote tip — re-runs of the cog should pick up any
    changes merged into the branch out-of-band. If the branch only
    exists locally (or not at all), create it from HEAD and leave it
    untouched.
    """
    if _remote_branch_exists(repo, remote_name, branch):
        # Track the remote branch and hard-reset to it.
        repo.git.checkout("-B", branch, f"{remote_name}/{branch}")
        LOG.info(
            "wiki.checkout.tracking_remote",
            extra={"branch": branch, "remote": remote_name},
        )
    else:
        # Local-only branch (first run with a new WIKI_BRANCH value).
        # Create from current HEAD; push later will set upstream.
        repo.git.checkout("-B", branch)
        LOG.info(
            "wiki.checkout.new_local",
            extra={"branch": branch, "remote": remote_name},
        )


def ensure_wiki_clone(config: Config) -> Repo:
    """Idempotently clone-or-refresh the wcs-wiki repo at ``config.wiki_repo_path``.

    Returns the GitPython Repo handle for the caller's convenience.
    Safe to call on every boot: if the path already contains a clone,
    we fetch + reset rather than re-clone.
    """
    path = config.wiki_repo_path
    safe_url = mask_url(config.wiki_repo_url)

    if _is_existing_repo(path):
        LOG.info(
            "wiki.clone.refresh",
            extra={"path": str(path), "url": safe_url},
        )
        repo = Repo(path)
        # Re-point origin URL in case the token rotated between runs.
        authed_url = _inject_token(config.wiki_repo_url, config.gh_token)
        repo.git.remote("set-url", config.wiki_repo_remote, authed_url)
        repo.git.fetch(config.wiki_repo_remote, "--prune")
    else:
        LOG.info(
            "wiki.clone.fresh",
            extra={"path": str(path), "url": safe_url},
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        authed_url = _inject_token(config.wiki_repo_url, config.gh_token)
        repo = Repo.clone_from(authed_url, str(path))

    # Local identity for commits.
    with repo.config_writer() as cw:
        cw.set_value("user", "name", config.wiki_git_author_name)
        cw.set_value("user", "email", config.wiki_git_author_email)

        # ── Push reliability tuning ───────────────────────────────────
        # Force HTTP/1.1 and bump the post buffer so large pushes (the
        # backfill commits ~87 source updates plus ~700-1000 derived-
        # page files in a single push) don't trip the libcurl HTTP/2
        # stream-not-closed-cleanly error documented at
        # https://github.com/git/git/blob/master/Documentation/config/http.txt
        # and reproduced reliably in our 2026-05-18 backfill run.
        #
        # ``http.version = HTTP/1.1`` disables HTTP/2 multiplexing; the
        # PROTOCOL_ERROR ("curl 92 HTTP/2 stream N was not closed
        # cleanly") originates in libcurl's HTTP/2 frame handling under
        # network pressure and is a well-known workaround. Pushes are
        # one-shot (the curator never pulls or fetches over this
        # codepath), so the multiplexing benefit is moot.
        #
        # ``http.postBuffer = 500MB`` lifts git's default 1MB chunking
        # threshold above the typical full-corpus push size so the push
        # body is sent in a single transfer rather than being split
        # across multiple POSTs (which is what produces the stream
        # errors in the first place).
        cw.set_value("http", "version", "HTTP/1.1")
        cw.set_value("http", "postBuffer", "524288000")  # 500 MB

    _checkout_or_create(repo, config.wiki_branch, config.wiki_repo_remote)

    return repo
