"""What a run's model calls cost: measured on a real run, estimated on a dry run.

``LLMResult`` from ``common-python-utils`` does not carry the API's token
counts, so :class:`MeteredLLM` reads them off each SDK response on the way
past: it wraps the provider client's ``create`` methods. That belongs in the
shared client eventually; until then it lives here and touches nothing else.

:class:`DryRunLLM` makes no call at all. Each command runs its normal code
path against it — the same batching, the same watermarks, the same prompts —
and the dry client counts the calls and measures the prompts instead of
sending them. It answers every call with an empty result, which the passes
treat as "nothing decided", so in-memory state is left as it was; the
commands also skip every save in a dry run.

Dollars are an estimate from a per-model price (input and output per million
tokens). Set ``LLM_PRICE_INPUT_PER_MTOK`` and ``LLM_PRICE_OUTPUT_PER_MTOK``
to override, or when the model is not one the defaults know.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from mini_app_polis.llm import LLMMessage, LLMResult

#: Default prices, US dollars per million tokens (input, output), matched
#: against the model name. A guide, not a bill: the environment overrides
#: them (module docstring), and prices change.
_DEFAULT_PRICES: tuple[tuple[str, float, float], ...] = (
    ("opus", 5.0, 25.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
)
#: Cache reads and writes are billed as a multiple of the input price.
_CACHE_READ = 0.1
_CACHE_WRITE = 1.25

#: A dry run's guess at the output, in tokens: per candidate or statement
#: in the batch (an ``"id"`` in the prompt), or per call when there are none
#: (one entity's prose). Rough on purpose; a real run reports the real count.
_DRY_OUTPUT_PER_ITEM = 50
_DRY_OUTPUT_PER_CALL = 300
#: Characters per token, for estimating a prompt's size without calling.
_CHARS_PER_TOKEN = 4
_ID = re.compile(r'"id":')


def _price(model: str) -> tuple[float, float] | None:
    raw_in = os.getenv("LLM_PRICE_INPUT_PER_MTOK", "").strip()
    raw_out = os.getenv("LLM_PRICE_OUTPUT_PER_MTOK", "").strip()
    if raw_in and raw_out:
        return float(raw_in), float(raw_out)
    name = model.lower()
    for key, price_in, price_out in _DEFAULT_PRICES:
        if key in name:
            return price_in, price_out
    return None


@dataclass
class Usage:
    """Calls and tokens for one run, safe to add to from worker threads."""

    model: str
    estimated: bool = False
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        *,
        calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        """Add calls and token counts to the totals, under the lock."""
        with self._lock:
            self.calls += calls
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cache_read_tokens += cache_read
            self.cache_write_tokens += cache_write

    def add_response(self, resp: Any) -> None:
        """Tokens from an Anthropic or OpenAI response, whichever it is."""
        u = getattr(resp, "usage", None)
        if u is None:
            return

        def n(*names: str) -> int:
            for name in names:
                value = getattr(u, name, None)
                if isinstance(value, int):
                    return value
            return 0

        self.add(
            input_tokens=n("input_tokens", "prompt_tokens"),
            output_tokens=n("output_tokens", "completion_tokens"),
            cache_read=n("cache_read_input_tokens"),
            cache_write=n("cache_creation_input_tokens"),
        )

    def cost_usd(self) -> float | None:
        """The run's cost in US dollars, or None when the model has no known price."""
        price = _price(self.model)
        if price is None:
            return None
        price_in, price_out = price
        dollars = (
            self.input_tokens * price_in
            + self.cache_read_tokens * price_in * _CACHE_READ
            + self.cache_write_tokens * price_in * _CACHE_WRITE
            + self.output_tokens * price_out
        ) / 1_000_000
        return round(dollars, 2)

    def summary(self) -> dict[str, Any]:
        """For the command's JSON summary."""
        out: dict[str, Any] = {
            "estimated": self.estimated,
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.cache_read_tokens or self.cache_write_tokens:
            out["cache_read_tokens"] = self.cache_read_tokens
            out["cache_write_tokens"] = self.cache_write_tokens
        out["cost_usd"] = self.cost_usd()
        return out


class MeteredLLM:
    """A client that records calls, and tokens from each SDK response."""

    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self.usage = Usage(model=model)
        self._hook()

    def _hook(self) -> None:
        sdk = getattr(self._inner, "_client", None)
        if sdk is None:
            return
        targets = [
            getattr(sdk, "messages", None),  # Anthropic
            getattr(sdk, "responses", None),  # OpenAI Responses
            getattr(getattr(sdk, "chat", None), "completions", None),  # OpenAI chat
        ]
        for resource in targets:
            create = getattr(resource, "create", None)
            if create is None:
                continue
            resource.create = self._wrap(create)  # type: ignore[union-attr]

    def _wrap(self, create: Any) -> Any:
        usage = self.usage

        def metered(*args: Any, **kwargs: Any) -> Any:
            resp = create(*args, **kwargs)
            usage.add_response(resp)
            return resp

        return metered

    def generate_json(
        self,
        *,
        messages: list[LLMMessage],
        json_schema: dict[str, Any],
        schema_name: str = "output",
    ) -> LLMResult:
        """Count the call, then make it through the wrapped client."""
        self.usage.add(calls=1)
        result: LLMResult = self._inner.generate_json(
            messages=messages, json_schema=json_schema, schema_name=schema_name
        )
        return result


#: What a dry run answers, by schema: the empty result each pass reads as
#: "nothing decided this time".
_EMPTY: dict[str, dict[str, Any]] = {
    "links": {"statements": []},
    "facets": {"entries": []},
}


class DryRunLLM:
    """Counts and measures each call instead of making it."""

    def __init__(self, model: str) -> None:
        self.usage = Usage(model=model, estimated=True)

    def generate_json(
        self,
        *,
        messages: list[LLMMessage],
        json_schema: dict[str, Any],
        schema_name: str = "output",
    ) -> LLMResult:
        """Record the call's estimated tokens and answer the schema's empty result."""
        _ = json_schema
        chars = sum(len(m.content) for m in messages)
        items = sum(len(_ID.findall(m.content)) for m in messages if m.role == "user")
        self.usage.add(
            calls=1,
            input_tokens=chars // _CHARS_PER_TOKEN,
            output_tokens=items * _DRY_OUTPUT_PER_ITEM
            if items
            else _DRY_OUTPUT_PER_CALL,
        )
        empty = _EMPTY.get(schema_name, {})
        return LLMResult(
            provider="dry-run",
            model=self.usage.model,
            output_json=json.loads(json.dumps(empty)),
            raw_text="",
        )


def usage_of(llm: Any) -> dict[str, Any] | None:
    """The usage summary of a metered or dry-run client, else None."""
    usage = getattr(llm, "usage", None)
    return usage.summary() if isinstance(usage, Usage) else None
