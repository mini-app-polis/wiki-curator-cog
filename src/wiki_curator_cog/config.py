"""Configuration for wiki-curator-cog.

Environment variables are read once at startup via load_config() and
exposed as an immutable Config dataclass.
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
    """Runtime configuration for the wiki renderer."""

    llm_provider: LLMProvider
    llm_model: str
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

    @property
    def default_models(self) -> dict[str, str]:
        return _DEFAULT_MODELS

    @property
    def wiki_repo_is_https(self) -> bool:
        return self.wiki_repo_url.startswith(("http://", "https://"))


def load_config() -> Config:
    """Load and validate environment variables into a Config instance."""
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
        raise RuntimeError(
            "WIKI_REPO_URL is HTTPS but GH_TOKEN is not set. "
            "Either provide a fine-grained PAT with contents:write on "
            f"{wiki_repo_url}, or switch to an SSH URL with a deploy key."
        )

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
        healthchecks_url=os.getenv("HEALTHCHECKS_URL_WIKI_CURATOR_COG", ""),
        sentry_dsn=os.getenv("SENTRY_DSN_WIKI_CURATOR_COG", ""),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
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
