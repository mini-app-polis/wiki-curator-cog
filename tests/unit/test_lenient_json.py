"""Model responses with stray text around the JSON still parse."""

from __future__ import annotations

import pytest
from mini_app_polis.llm.errors import LLMValidationError

from wiki_curator_cog import _lenient_json


def test_trailing_text_after_the_object_is_ignored() -> None:
    text = '{"definition": "A stop.", "context": ""}\n\nHope this helps!'
    assert _lenient_json.parse_json(text) == {"definition": "A stop.", "context": ""}


def test_leading_text_before_the_object_is_ignored() -> None:
    text = 'The sources disagree, so here it is:\n{"definition": "A tick."}'
    assert _lenient_json.parse_json(text) == {"definition": "A tick."}


def test_a_brace_in_prose_before_the_object_is_skipped() -> None:
    text = 'Note {not json} then {"ok": true}'
    assert _lenient_json.parse_json(text) == {"ok": True}


def test_no_object_says_what_the_model_returned() -> None:
    with pytest.raises(LLMValidationError, match="I cannot define this"):
        _lenient_json.parse_json("I cannot define this from conflicting notes.")


def test_install_replaces_the_shared_clients_parser() -> None:
    from mini_app_polis.llm import anthropic_client, openai_client

    _lenient_json.install()
    assert anthropic_client.parse_json is _lenient_json.parse_json
    assert openai_client.parse_json is _lenient_json.parse_json
