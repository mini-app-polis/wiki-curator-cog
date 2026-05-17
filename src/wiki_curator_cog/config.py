"""Configuration for wiki-curator-cog.

Environment variables are read once at startup via load_config() and
exposed as an immutable Config dataclass. Auth env vars
(KAIANO_API_CLERK_MACHINE_SECRET, GH_TOKEN) are read here but only
exposed to the modules that need them — the api client reads the Clerk
secret directly via KaianoApiClient.from_env(); GH_TOKEN is consumed
by ``boot.ensure_wiki_clone`` to build the HTTPS clone URL.

Deployment shape (Railway, ephemeral filesystem):

  WIKI_REPO_URL    — HTTPS or SSH URL to the wcs-wiki repo
                     (default: github.com/mini-app-polis/wcs-wiki)
  WIKI_BRANCH      — branch to checkout and push to (default: main)
  GH_TOKEN         — fine-grained PAT with contents:write on wcs-wiki,
                     required when WIKI_REPO_URL is https://
  WIKI_REPO_PATH   — where the local clone lives. Defaults to
                     /tmp/wcs-wiki on Railway; can be overridden for
                     local development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LLMProvider = Literal["anthropic", "openai"]

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}

_DEFAULT_WIKI_REPO_URL = "https://github.com/mini-app-polis/wcs-wiki.git"
_DEFAULT_WIKI_REPO_PATH = "/tmp/wcs-wiki"


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


@dataclass(frozen=True)
class Config:
    """Runtime configuration for the wiki curator.

    All values are loaded once at process start. Mutable state (last-run
    timestamp, name aliases, wiki page inventory) lives elsewhere.

    Secrets policy: ``gh_token`` is the only secret on this dataclass.
    It is consumed exclusively by ``boot.ensure_wiki_clone`` and never
    logged. Do not include it in repr — but the dataclass is frozen so
    Python's default repr already includes it; callers that log Config
    should redact before emitting.
    """

    # LLM
    llm_provider: LLMProvider
    llm_model: str

    # Upstream API
    kaiano_api_base_url: str

    # Wiki repo (local clone path the curator writes to)
    wiki_repo_path: Path
    wiki_repo_url: str
    wiki_repo_remote: str
    wiki_branch: str
    wiki_git_author_name: str
    wiki_git_author_email: str
    gh_token: str  # may be empty for SSH-based remotes or local-only runs

    # Curator behavior
    curator_version: int
    backfill_page_size: int
    state_path: Path  # incremental-mode state file (relative to repo or absolute)

    # Observability
    healthchecks_url: str
    sentry_dsn: str
    logging_level: str

    @property
    def default_models(self) -> dict[str, str]:
        return _DEFAULT_MODELS

    @property
    def wiki_repo_is_https(self) -> bool:
        return self.wiki_repo_url.startswith(("http://", "https://"))


def load_config() -> Config:
    """Load and validate environment variables into a Config instance.

    The WIKI_REPO_PATH existence check is deliberately deferred —
    ``boot.ensure_wiki_clone`` runs first and populates the path. If
    that helper isn't called (e.g., the operator is pointing at an
    existing local clone), the curator will still raise when it tries
    to load the inventory and finds nothing.

    Raises:
        RuntimeError: If a required environment variable is missing or
        if HTTPS clone is configured without GH_TOKEN.
    """
    provider_raw = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
    if provider_raw not in ("anthropic", "openai"):
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider_raw!r}. "
            "Must be 'anthropic' or 'openai'."
        )
    provider: LLMProvider = provider_raw  # type: ignore[assignment]
    model = os.getenv("LLM_MODEL", _DEFAULT_MODELS[provider])

    wiki_repo_path_raw = os.getenv("WIKI_REPO_PATH", _DEFAULT_WIKI_REPO_PATH)
    wiki_repo_path = Path(wiki_repo_path_raw).expanduser().resolve()
    wiki_repo_url = os.getenv("WIKI_REPO_URL", _DEFAULT_WIKI_REPO_URL).strip()
    wiki_branch = os.getenv("WIKI_BRANCH", "main").strip()
    gh_token = os.getenv("GH_TOKEN", "").strip()

    is_https = wiki_repo_url.startswith(("http://", "https://"))
    if is_https and not gh_token and wiki_repo_url != "local-only":
        # On Railway / any remote deployment, an HTTPS URL without a
        # token means the cog can't push. Block early with a clear
        # error rather than letting `git push` fail mid-backfill.
        raise RuntimeError(
            "WIKI_REPO_URL is HTTPS but GH_TOKEN is not set. "
            "Either provide a fine-grained PAT with contents:write on "
            f"{wiki_repo_url}, or switch to an SSH URL with a deploy key."
        )

    state_path_raw = os.getenv("WIKI_STATE_PATH", ".wiki-curator-state.json")
    state_path = Path(state_path_raw)
    if not state_path.is_absolute():
        # Relative paths resolve against the curator working directory,
        # NOT against the wiki repo (state is curator-internal, never
        # committed to the wiki).
        state_path = Path.cwd() / state_path

    return Config(
        llm_provider=provider,
        llm_model=model,
        kaiano_api_base_url=_require("KAIANO_API_BASE_URL"),
        wiki_repo_path=wiki_repo_path,
        wiki_repo_url=wiki_repo_url,
        wiki_repo_remote=os.getenv("WIKI_REPO_REMOTE", "origin"),
        wiki_branch=wiki_branch,
        wiki_git_author_name=os.getenv("WIKI_GIT_AUTHOR_NAME", "wiki-curator-cog"),
        wiki_git_author_email=os.getenv(
            "WIKI_GIT_AUTHOR_EMAIL", "wiki-curator@kaianolevine.com"
        ),
        gh_token=gh_token,
        curator_version=int(os.getenv("WIKI_CURATOR_VERSION", "4")),
        backfill_page_size=int(os.getenv("WIKI_BACKFILL_PAGE_SIZE", "100")),
        state_path=state_path,
        healthchecks_url=os.getenv("HEALTHCHECKS_URL_WIKI_CURATOR_COG", ""),
        sentry_dsn=os.getenv("SENTRY_DSN_WIKI_CURATOR_COG", ""),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
    )


def assert_wiki_clone_ready(config: Config) -> None:
    """Sanity-check the local wiki clone after ``ensure_wiki_clone`` runs.

    Verifies the path exists, looks like a wcs-wiki clone, and is on
    the configured branch. Raises with an actionable message otherwise.
    """
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
