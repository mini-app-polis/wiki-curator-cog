"""Cost visibility: the metered client, dry runs, and the price estimate."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from mini_app_polis.llm import LLMMessage, LLMResult

from wiki_curator_cog import consolidate, flow, link, usage

from .export_builders import WcsAttribution, WcsEntity, WcsSource, WcsWikiExport


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, name)


class _Messages:
    def __init__(self) -> None:
        self.sent = 0

    def create(self, **kwargs: Any) -> Any:
        self.sent += 1
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1_000,
                output_tokens=200,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )
        )


class _Inner:
    """Shaped like the shared Anthropic client: an SDK client in ``_client``."""

    def __init__(self) -> None:
        self._client = SimpleNamespace(messages=_Messages())

    def generate_json(self, *, messages, json_schema, schema_name="output"):
        self._client.messages.create(model="m", messages=[])
        return LLMResult(provider="p", model="m", output_json={}, raw_text="")


def _call(llm: Any, schema: str = "facets", user: str = "x") -> LLMResult:
    result: LLMResult = llm.generate_json(
        messages=[
            LLMMessage(role="system", content="s" * 400),
            LLMMessage(role="user", content=user),
        ],
        json_schema={},
        schema_name=schema,
    )
    return result


@pytest.fixture(autouse=True)
def _no_price_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PRICE_INPUT_PER_MTOK", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUTPUT_PER_MTOK", raising=False)


# ── the meter ──


def test_the_meter_reads_tokens_off_each_response() -> None:
    llm = usage.MeteredLLM(_Inner(), "anthropic/claude-sonnet-4-6")
    _call(llm)
    _call(llm)
    out = llm.usage.summary()
    assert out["calls"] == 2
    assert out["input_tokens"] == 2_000 and out["output_tokens"] == 400
    assert out["estimated"] is False
    # 2,000 × $3/M + 400 × $15/M
    assert out["cost_usd"] == round(0.006 + 0.006, 2)


def test_prices_come_from_the_environment_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    u = usage.Usage(model="someone/unknown-model", input_tokens=1_000_000)
    assert u.cost_usd() is None
    monkeypatch.setenv("LLM_PRICE_INPUT_PER_MTOK", "2")
    monkeypatch.setenv("LLM_PRICE_OUTPUT_PER_MTOK", "8")
    assert u.cost_usd() == 2.0


def test_cache_tokens_are_priced_as_a_share_of_input() -> None:
    u = usage.Usage(
        model="anthropic/claude-sonnet-4-6",
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert u.cost_usd() == round(3 * 0.1 + 3 * 1.25, 2)


def test_a_client_without_an_sdk_inside_still_counts_calls() -> None:
    class Bare:
        def generate_json(self, **kwargs: Any) -> LLMResult:
            return LLMResult(provider="p", model="m", output_json={}, raw_text="")

    llm = usage.MeteredLLM(Bare(), "x")
    _call(llm)
    assert llm.usage.calls == 1 and llm.usage.input_tokens == 0


def test_llm_from_env_dry_run_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm, model = link.llm_from_env(dry_run=True)
    assert isinstance(llm, usage.DryRunLLM)
    assert model.startswith("anthropic/")


# ── the dry client ──


def test_the_dry_client_measures_and_answers_empty() -> None:
    llm = usage.DryRunLLM("anthropic/claude-sonnet-4-6")
    result = _call(llm, "facets", json.dumps([{"id": "a"}, {"id": "b"}]))
    assert result.output_json == {"entries": []}
    assert llm.usage.calls == 1
    assert llm.usage.output_tokens == 2 * usage._DRY_OUTPUT_PER_ITEM
    assert llm.usage.input_tokens > 100
    prose = _call(llm, "prose", "one entity")
    assert prose.output_json == {}
    assert llm.usage.output_tokens == 2 * usage._DRY_OUTPUT_PER_ITEM + 300
    assert llm.usage.summary()["estimated"] is True


# ── dry runs end to end ──

SOURCE = WcsSource(
    id=_id("s"),
    transcript_id=_id("t"),
    title="lesson",
    session_date=dt.date(2026, 1, 1),
    session_type="private",
    visibility="private",
    is_default_visible=False,
    created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
)


def _export() -> WcsWikiExport:
    names = ["anchor", "stationary-anchor", "anchor-drill", "whip"]
    kinds = ["concept", "concept", "drill", "pattern"]
    return WcsWikiExport(
        entities=[
            WcsEntity(id=_id(n), slug=n, canonical_name=n.replace("-", " "), kind=k)
            for n, k in zip(names, kinds, strict=True)
        ],
        sources=[SOURCE],
        attributions=[
            WcsAttribution(
                id=_id(f"a-{n}"),
                source_id=SOURCE.id,
                entity_id=_id(n),
                attribution_kind="taught",
                prose=f"About {n}.",
            )
            for n in names
        ],
    )


def test_a_dry_link_run_counts_calls_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    with patch.object(flow, "_fetch_export", return_value=_export()):
        summary = flow.link_local(path, dry_run=True, batch_size=3)
    assert summary["dry_run"] is True
    assert summary["pending"] == 4
    assert summary["usage"]["calls"] == 2  # 4 statements, 3 a batch
    assert summary["usage"]["cost_usd"] is not None
    assert list(tmp_path.iterdir()) == []


def test_a_dry_consolidate_run_counts_each_group(tmp_path: Path) -> None:
    path = tmp_path / "facets.json"
    with patch.object(flow, "_fetch_export", return_value=_export()):
        summary = flow.consolidate_local(path, dry_run=True)
    assert summary["pending_by_group"] == {"concept": 2, "drill": 1, "pattern": 1}
    assert summary["usage"]["calls"] == 3
    assert list(tmp_path.iterdir()) == []


def test_a_dry_generate_run_writes_nothing(tmp_path: Path) -> None:
    prose_path = tmp_path / "prose.json"
    with patch.object(flow, "_fetch_export", return_value=_export()):
        summary = flow.generate_local(prose_path, dry_run=True)
    assert summary["pending"] == 4
    assert summary["usage"]["calls"] == 4
    assert list(tmp_path.iterdir()) == []


def test_a_real_run_reports_its_usage(tmp_path: Path) -> None:
    class Metered(usage.MeteredLLM):
        pass

    inner = _Inner()
    inner.generate_json = lambda **kw: LLMResult(  # type: ignore[method-assign]
        provider="p", model="m", output_json={"entries": []}, raw_text=""
    )
    llm = Metered(inner, "anthropic/claude-sonnet-4-6")
    with (
        patch.object(flow, "_fetch_export", return_value=_export()),
        patch.object(link, "llm_from_env", return_value=(llm, "fake/m")),
    ):
        summary = flow.consolidate_local(tmp_path / "facets.json")
    assert summary["usage"]["calls"] == 3
    assert summary["consolidator_versions"] == consolidate.CONSOLIDATOR_VERSIONS
