"""Curator-internal state persistence.

Incremental mode needs to know the last-run timestamp so it can ask the
API for notes added since then. State is stored as JSON at the path given
by WIKI_STATE_PATH (default: .wiki-curator-state.json relative to the
curator working directory).

Important: state lives outside the wiki repo. It's curator-internal and
should NOT be committed.
"""

from __future__ import annotations

import datetime as dt

from .config import Config
from .models import CuratorState


def load_state(config: Config) -> CuratorState:
    """Load state from disk, or return defaults if absent."""
    if not config.state_path.exists():
        return CuratorState()
    raw = config.state_path.read_text()
    if not raw.strip():
        return CuratorState()
    return CuratorState.model_validate_json(raw)


def save_state(
    config: Config,
    *,
    last_run_at: dt.datetime,
    curator_version_at_last_run: int,
) -> None:
    """Persist state. Caller passes the new values explicitly."""
    state = CuratorState(
        last_run_at=last_run_at,
        curator_version_at_last_run=curator_version_at_last_run,
    )
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(state.model_dump_json(indent=2))
