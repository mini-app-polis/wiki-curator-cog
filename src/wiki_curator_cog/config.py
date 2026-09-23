"""Configuration for wiki-curator-cog.

Environment variables are read once at startup via load_config() and
exposed as an immutable Config dataclass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mini_app_polis.environment import env_var

_DEFAULT_WIKI_REPO_URL = "https://github.com/mini-app-polis/wcs-wiki.git"
_DEFAULT_WIKI_REPO_PATH = "/tmp/wcs-wiki"

#: How long one export may take before :mod:`._deadline` stops it.
#:
#: Declared rather than discovered: there is no runtime clock to read on
#: Railway. It exists because a run that never ends blocks every later
#: start silently — see ``_deadline`` for why that is the failure worth
#: guarding.
#:
#: 600s against a measured run: the 2026-09-23 run reached the push in 15
#: seconds — a 3s clone, a 6s export GET, a 1s render. That is a 40x margin,
#: which leaves room for a full-corpus push on a slow day and still fails
#: fast enough to be a signal rather than an afternoon.
_DEFAULT_RUN_TIMEOUT_SECONDS = 600

#: How long the push preflight and the Healthchecks ping may wait.
#:
#: Separate from the run budget and from each other: one is a credential
#: check whose failure stops the run, the other is a ping whose failure is
#: shrugged off. Both are here rather than inline so a test can set them to
#: zero without touching source (TEST-013).
_DEFAULT_PUSH_PREFLIGHT_TIMEOUT_SECONDS = 10.0
_DEFAULT_HEALTHCHECKS_TIMEOUT_SECONDS = 5.0


def _require_api_base_url() -> str:
    """Production reads ``KAIANO_API_BASE_URL``; everywhere else the ``_DEV`` form."""
    v = env_var("KAIANO_API_BASE_URL")
    if not v:
        raise RuntimeError(
            "Missing required environment variable: KAIANO_API_BASE_URL "
            "(or KAIANO_API_BASE_URL_DEV outside production)"
        )
    return v


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number of seconds, got {raw!r}") from exc


def _run_timeout_seconds() -> int:
    raw = os.getenv("RUN_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_RUN_TIMEOUT_SECONDS
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"RUN_TIMEOUT_SECONDS must be an integer number of seconds, got {raw!r}"
        ) from exc


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the wiki renderer."""

    kaiano_api_base_url: str
    wiki_repo_path: Path
    wiki_repo_url: str
    wiki_repo_remote: str
    wiki_branch: str
    wiki_git_author_name: str
    wiki_git_author_email: str
    gh_token: str
    healthchecks_url: str
    sentry_dsn: str
    logging_level: str
    run_timeout_seconds: int
    push_preflight_timeout_seconds: float
    healthchecks_timeout_seconds: float

    @property
    def wiki_repo_is_https(self) -> bool:
        """Whether the wiki remote is an HTTP(S) URL rather than an SSH one."""
        return self.wiki_repo_url.startswith(("http://", "https://"))


def load_config() -> Config:
    """Load and validate environment variables into a Config instance."""
    wiki_repo_path_raw = os.getenv("WIKI_REPO_PATH", _DEFAULT_WIKI_REPO_PATH)
    wiki_repo_path = Path(wiki_repo_path_raw).expanduser().resolve()
    wiki_repo_url = os.getenv("WIKI_REPO_URL", _DEFAULT_WIKI_REPO_URL).strip()
    wiki_branch = os.getenv("WIKI_BRANCH", "main").strip()
    gh_token = os.getenv("GH_TOKEN", "").strip()

    is_https = wiki_repo_url.startswith(("http://", "https://"))
    if is_https and not gh_token and wiki_repo_url != "local-only":
        raise RuntimeError(
            "WIKI_REPO_URL is HTTPS but GH_TOKEN is not set. "
            "Either provide a fine-grained PAT with contents:write on "
            f"{wiki_repo_url}, or switch to an SSH URL with a deploy key."
        )

    return Config(
        kaiano_api_base_url=_require_api_base_url(),
        wiki_repo_path=wiki_repo_path,
        wiki_repo_url=wiki_repo_url,
        wiki_repo_remote=os.getenv("WIKI_REPO_REMOTE", "origin"),
        wiki_branch=wiki_branch,
        wiki_git_author_name=os.getenv("WIKI_GIT_AUTHOR_NAME", "wiki-curator-cog"),
        wiki_git_author_email=os.getenv(
            "WIKI_GIT_AUTHOR_EMAIL", "wiki-curator@kaianolevine.com"
        ),
        gh_token=gh_token,
        healthchecks_url=os.getenv("HEALTHCHECKS_URL_WIKI_CURATOR_COG", ""),
        # SENTRY_DSN_WIKI_CURATOR_COG, not the fleet-wide SENTRY_DSN. The
        # cogs that moved to Lambda each got a function of their own, so a
        # bare name there addresses exactly one cog. This one is still a
        # Railway service sharing a secrets store with its neighbours, where
        # an unsuffixed name is the same name they read — and the failure is
        # silent: events land in another cog's Sentry project and this one
        # simply looks healthy. The suffix is what keeps them apart.
        sentry_dsn=os.getenv("SENTRY_DSN_WIKI_CURATOR_COG", ""),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
        run_timeout_seconds=_run_timeout_seconds(),
        push_preflight_timeout_seconds=_float_env(
            "PUSH_PREFLIGHT_TIMEOUT_SECONDS", _DEFAULT_PUSH_PREFLIGHT_TIMEOUT_SECONDS
        ),
        healthchecks_timeout_seconds=_float_env(
            "HEALTHCHECKS_TIMEOUT_SECONDS", _DEFAULT_HEALTHCHECKS_TIMEOUT_SECONDS
        ),
    )


def assert_wiki_clone_ready(config: Config) -> None:
    """Sanity-check the local wiki clone after ``ensure_wiki_clone`` runs."""
    if not config.wiki_repo_path.exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not exist: {config.wiki_repo_path}. "
            "Did boot.ensure_wiki_clone() run, or set WIKI_REPO_PATH to "
            "an existing local clone?"
        )
    if not (config.wiki_repo_path / "CLAUDE.md").exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not look like a wcs-wiki clone "
            f"(no CLAUDE.md found): {config.wiki_repo_path}"
        )
