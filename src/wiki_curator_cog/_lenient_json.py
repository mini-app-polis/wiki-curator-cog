"""Lenient JSON parsing for model responses, until common-python-utils has it.

The shared LLM clients parse a response with ``json.loads`` on the whole
text, so a model that writes one valid JSON object and then a stray line —
or a sentence before it — fails the call, and with it the entity or batch.
Seen in the full-corpus runs: "Extra data" after a complete object, and a
response that did not start with JSON at all.

This reads the first JSON object in the text and ignores anything around
it. When there is no object, the error carries the start of the text, so
the failure says what the model actually returned.

It is installed over the shared clients' ``parse_json`` by
:func:`install`. The right home for it is
``mini_app_polis.llm._json.parse_json`` in common-python-utils; once that
release is in, this module and the call to :func:`install` go.
"""

from __future__ import annotations

import json
from typing import Any

from mini_app_polis.llm.errors import LLMValidationError

_DECODER = json.JSONDecoder()


def parse_json(text: str) -> dict[str, Any]:
    """The first JSON object in ``text``; stray text before or after is ignored."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = _DECODER.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    preview = " ".join(text.split())[:200]
    raise LLMValidationError(f"No JSON object in model response: {preview!r}")


def install() -> None:
    """Use :func:`parse_json` in the shared Anthropic and OpenAI clients."""
    from mini_app_polis.llm import anthropic_client, openai_client

    anthropic_client.parse_json = parse_json  # type: ignore[attr-defined]
    openai_client.parse_json = parse_json  # type: ignore[attr-defined]
