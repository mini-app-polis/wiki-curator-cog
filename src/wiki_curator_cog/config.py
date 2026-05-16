"""Configuration for wiki-curator-cog.

Environment variables are read once at startup via load_config() and
exposed as an immutable Config dataclass. Auth env vars
(KAIANO_API_CLERK_MACHINE_SECRET) are read directly by KaianoApiClient
in api_client.py — validated there rather than duplicated into Config.
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
    """

    # LLM
    llm_provider: LLMProvider
    llm_model: str

    # Upstream API
    kaiano_api_base_url: str

    # Wiki repo (local clone path the curator writes to)
    wiki_repo_path: Path
    wiki_repo_remote: str
    wiki_git_author_name: str
    wiki_git_author_email: str

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


def load_config() -> Config:
    """Load and validate environment variables into a Config instance.

    Raises:
        RuntimeError: If a required environment variable is missing or invalid.
    """
    provider_raw = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
    if provider_raw not in ("anthropic", "openai"):
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider_raw!r}. "
            "Must be 'anthropic' or 'openai'."
        )
    provider: LLMProvider = provider_raw  # type: ignore[assignment]
    model = os.getenv("LLM_MODEL", _DEFAULT_MODELS[provider])

    wiki_repo_path = Path(_require("WIKI_REPO_PATH")).expanduser().resolve()
    if not wiki_repo_path.exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not exist: {wiki_repo_path}. "
            "Clone the wcs-wiki repo locally and point WIKI_REPO_PATH at it."
        )
    if not (wiki_repo_path / "CLAUDE.md").exists():
        raise RuntimeError(
            f"WIKI_REPO_PATH does not look like a wcs-wiki clone "
            f"(no CLAUDE.md found): {wiki_repo_path}"
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
        wiki_repo_remote=os.getenv("WIKI_REPO_REMOTE", "origin"),
        wiki_git_author_name=os.getenv(
            "WIKI_GIT_AUTHOR_NAME", "wiki-curator-cog"
        ),
        wiki_git_author_email=os.getenv(
            "WIKI_GIT_AUTHOR_EMAIL", "wiki-curator@kaianolevine.com"
        ),
        curator_version=int(os.getenv("WIKI_CURATOR_VERSION", "1")),
        backfill_page_size=int(os.getenv("WIKI_BACKFILL_PAGE_SIZE", "100")),
        state_path=state_path,
        healthchecks_url=os.getenv("HEALTHCHECKS_URL_WIKI_CURATOR_COG", ""),
        sentry_dsn=os.getenv("SENTRY_DSN_WIKI_CURATOR_COG", ""),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
    )
