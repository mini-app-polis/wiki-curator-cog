"""One unit of work per job: what the worker runs.

Each job runs one unit against an explicit context, writes through the state
store, costs nothing when its watermark says there is nothing to do, and
names the jobs that should follow.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mini_app_polis.llm import LLMResult

import wiki_curator_cog.flow as flow
import wiki_curator_cog.main as main
from wiki_curator_cog import consolidate, state_backend, units

from .test_link import _export, _id
from .test_state_store import FakeApi

SOURCE = _id("s1")


class RouterLLM:
    """Answers each pass by its schema; records the calls per pass."""

    def __init__(
        self,
        links: dict[str, list[dict[str, Any]]] | None = None,
        parents: dict[str, str] | None = None,
    ) -> None:
        self.links = links or {}
        self.parents = parents or {}
        self.calls: dict[str, int] = {"links": 0, "facets": 0, "prose": 0}

    def generate_json(self, *, messages, json_schema, schema_name="output"):
        self.calls[schema_name] += 1
        user = messages[-1].content
        if schema_name == "links":
            keys = [
                s["id"] for s in json.loads(user.split("Statements (JSON):\n", 1)[1])
            ]
            out: dict[str, Any] = {
                "statements": [
                    {"id": k, "references": self.links.get(k, [])} for k in keys
                ]
            }
        elif schema_name == "facets":
            ids = [
                c["id"] for c in json.loads(user.split("Candidates (JSON):\n", 1)[1])
            ]
            out = {
                "entries": [
                    {
                        "id": i,
                        "reason": "test",
                        "fold": i in self.parents,
                        "parent": self.parents.get(i),
                        "same": False,
                    }
                    for i in ids
                ]
            }
        else:
            out = {"definition": "A definition.", "context": ""}
        return LLMResult(provider="f", model="f", output_json=out, raw_text="")


def _ctx(tmp_path: Path, llm: RouterLLM, store=None) -> units.RunContext:
    return units.build_context(
        _export(),
        store or state_backend.FileState(),
        units.StatePaths.under(tmp_path),
        llm=llm,
        model="fake/m",
    )


def test_a_link_job_links_one_source_and_names_what_follows(tmp_path: Path) -> None:
    key = f"attribution:{_id('a1')}"
    llm = RouterLLM(links={key: [{"name": "connection", "polarity": "assert"}]})
    result = units.handle(
        {"job": "link", "source_id": str(SOURCE)}, _ctx(tmp_path, llm)
    )

    assert result["statements"] == 4 and result["linked"] == 4
    assert result["next"] == [
        {
            "job": "consolidate",
            "entities": ["anchor-step", "stretch", "wall-drill"],
            "origin_source_id": str(SOURCE),
        },
        # what it taught, and what it referred to — a new concept included —
        # in one job
        {
            "job": "generate",
            "entities": ["anchor-step", "connection", "stretch", "wall-drill"],
            "origin_source_id": str(SOURCE),
        },
    ]


def test_a_repeated_job_costs_nothing(tmp_path: Path) -> None:
    llm = RouterLLM()
    job = {"job": "link", "source_id": str(SOURCE)}
    units.handle(job, _ctx(tmp_path, llm))
    calls = dict(llm.calls)
    again = units.handle(job, _ctx(tmp_path, llm))
    assert llm.calls == calls
    assert again["linked"] == 0 and again["skipped"] == 4
    # nothing newly linked, nothing to regenerate; the decisions are still owed
    assert [j["job"] for j in again["next"]] == ["consolidate"]


def test_a_source_with_nothing_in_the_corpus_is_a_no_op(tmp_path: Path) -> None:
    llm = RouterLLM()
    result = units.handle(
        {"job": "link", "source_id": str(uuid.uuid4())}, _ctx(tmp_path, llm)
    )
    assert result["statements"] == 0 and result["next"] == []
    assert llm.calls["links"] == 0


def test_a_consolidate_job_decides_only_what_it_names(tmp_path: Path) -> None:
    llm = RouterLLM(parents={"anchor-step": "stretch"})
    result = units.handle(
        {"job": "consolidate", "entities": ["anchor-step"]}, _ctx(tmp_path, llm)
    )
    facets = consolidate.FacetsFile.load(tmp_path / "facets.json")
    assert set(facets.entities) == {"anchor-step"}
    assert facets.parents() == {"anchor-step": "stretch"}
    assert result["decided"] == 1
    # the page that gained a facet is regenerated with it
    assert result["next"] == [{"job": "generate", "entities": ["stretch"]}]


def test_a_generate_job_writes_one_entity_once(tmp_path: Path) -> None:
    llm = RouterLLM()
    job = {"job": "generate", "entity": "stretch"}
    first = units.handle(job, _ctx(tmp_path, llm))
    assert first["generated"] == 1
    prose = json.loads((tmp_path / "prose.json").read_text())
    assert set(prose["entities"]) == {"stretch"}
    again = units.handle(job, _ctx(tmp_path, llm))
    assert again["generated"] == 0 and llm.calls["prose"] == 1


def test_a_folded_entity_is_generated_as_its_page(tmp_path: Path) -> None:
    facets = consolidate.FacetsFile()
    facets.record("anchor-step", "stretch", "test", "m")
    facets.save(tmp_path / "facets.json")
    result = units.handle(
        {"job": "generate", "entity": "anchor-step"}, _ctx(tmp_path, RouterLLM())
    )
    assert result["entities"] == ["stretch"]


def test_an_unknown_entity_is_skipped(tmp_path: Path) -> None:
    result = units.handle(
        {"job": "generate", "entities": ["gone", "stretch"]},
        _ctx(tmp_path, RouterLLM()),
    )
    assert result["skipped"] == ["gone"] and result["entities"] == ["stretch"]


@pytest.mark.parametrize(
    "job",
    [
        {"job": "dance"},
        {"job": "link"},
        {"job": "consolidate", "entities": []},
        {"job": "generate"},
    ],
)
def test_a_bad_job_is_refused(tmp_path: Path, job: dict) -> None:
    with pytest.raises(units.JobError):
        units.handle(job, _ctx(tmp_path, RouterLLM()))


def test_jobs_write_through_the_api_store(tmp_path: Path) -> None:
    api = FakeApi()
    llm = RouterLLM()
    units.handle(
        {"job": "link", "source_id": str(SOURCE)},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert len(api.docs["links"]["statements"]) == 4
    units.handle(
        {"job": "generate", "entity": "stretch"},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert "stretch" in api.docs["prose"]["entities"]


def test_the_job_command_runs_one_unit_dry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    (tmp_path / ".curator").mkdir()
    with patch.object(flow, "_fetch_export", return_value=_export()):
        code = main.main(
            [
                "job",
                "--job",
                json.dumps({"job": "link", "source_id": str(SOURCE)}),
                "--state",
                "files",
                "--dry-run",
            ]
        )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["usage"]["calls"] == 1
    assert not (tmp_path / ".curator" / "links.json").exists()


def test_the_job_command_refuses_bad_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    assert main.main(["job", "--job", "not json", "--state", "files"]) == 2
    with patch.object(flow, "_fetch_export", return_value=_export()):
        assert main.main(["job", "--job", '{"job": "dance"}', "--state", "files"]) == 2


def test_follow_on_generate_jobs_come_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(units, "GENERATE_PER_JOB", 2)
    assert units._generate_jobs({"c", "a", "b"}) == [
        {"job": "generate", "entities": ["a", "b"]},
        {"job": "generate", "entities": ["c"]},
    ]


# ── Where a chain of jobs came from ──

ORIGIN = str(SOURCE)


def test_the_origin_is_carried_down_the_chain(tmp_path: Path) -> None:
    """link names itself; consolidate and generate pass on what they were given."""
    llm = RouterLLM(parents={"anchor-step": "stretch"})
    linked = units.handle({"job": "link", "source_id": ORIGIN}, _ctx(tmp_path, llm))
    assert {j["origin_source_id"] for j in linked["next"]} == {ORIGIN}

    consolidate_job = next(j for j in linked["next"] if j["job"] == "consolidate")
    decided = units.handle(consolidate_job, _ctx(tmp_path, llm))
    assert decided["next"] == [
        {"job": "generate", "entities": ["stretch"], "origin_source_id": ORIGIN}
    ]
    generated = units.handle(decided["next"][0], _ctx(tmp_path, llm))
    assert generated["next"] == [
        {"job": "materialise", "entities": ["stretch"], "origin_source_id": ORIGIN}
    ]


def test_materialise_stores_the_origin_and_the_run_on_every_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_backend, "PAGES_PER_POST", 1)
    api = FakeApi()
    api.pages["gone"] = {}
    ctx = _ctx(tmp_path, RouterLLM(), state_backend.ApiState(api))
    ctx.job_id = "msg-1"
    units.handle(
        {
            "job": "materialise",
            "entities": ["stretch", "wall-drill", "gone"],
            "origin_source_id": ORIGIN,
        },
        ctx,
    )
    assert len(api.page_posts) == 3  # two pages, then the removal
    assert {(str(p.origin_source_id), p.origin_job_id) for p in api.page_posts} == {
        (ORIGIN, "msg-1")
    }


def test_work_an_admin_started_has_no_origin(tmp_path: Path) -> None:
    api = FakeApi()
    llm = RouterLLM(parents={"anchor-step": "stretch"})

    def ctx() -> units.RunContext:
        return _ctx(tmp_path, llm, state_backend.ApiState(api))

    decided = units.handle({"job": "consolidate", "entities": ["anchor-step"]}, ctx())
    generated = units.handle(decided["next"][0], ctx())
    assert all("origin_source_id" not in j for j in decided["next"] + generated["next"])
    units.handle({"job": "materialise"}, ctx())
    assert api.page_posts
    assert all(p.origin_source_id is None for p in api.page_posts)


def test_an_origin_that_is_no_uuid_is_refused(tmp_path: Path) -> None:
    with pytest.raises(units.JobError, match="origin_source_id"):
        units.validate({"job": "materialise", "origin_source_id": "nope"})


# ── The thin list ──


def test_a_consolidate_job_stores_the_thin_list_for_the_whole_corpus(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    llm = RouterLLM(parents={"anchor-step": "stretch"})
    store = state_backend.ApiState(api)
    # an earlier job decided another entity: the list covers it too
    units.handle(
        {"job": "consolidate", "entities": ["wall-drill"]},
        _ctx(tmp_path, llm, store),
    )
    result = units.handle(
        {"job": "consolidate", "entities": ["anchor-step"]},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert set(api.thin) >= {"anchor-step", "wall-drill"}
    assert api.thin["anchor-step"]["page"] == "stretch"
    assert result["thin"]["total"] == len(api.thin)
    # nothing about the corpus changed: nothing is sent again
    again = units.handle(
        {"job": "consolidate", "entities": ["anchor-step"]},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert (again["thin"]["written"], again["thin"]["removed"]) == (0, 0)


def test_a_link_job_refreshes_the_thin_list(tmp_path: Path) -> None:
    """New references change what is thin, so a link that linked refreshes it."""
    api = FakeApi()
    llm = RouterLLM()
    linked = units.handle(
        {"job": "link", "source_id": ORIGIN},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert linked["linked"] > 0
    assert "thin" in linked and linked["thin"]["total"] == len(api.thin)
    posts = len(api.thin_posts)
    # Delivered again: nothing newly linked, nothing read or written.
    again = units.handle(
        {"job": "link", "source_id": ORIGIN},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert "thin" not in again
    assert len(api.thin_posts) == posts


def test_rebuilding_every_page_refreshes_the_thin_list(tmp_path: Path) -> None:
    """The /admin/wiki rebuild is the free way to bring the list up to date."""
    api = FakeApi()
    llm = RouterLLM(parents={"anchor-step": "stretch"})
    units.handle(
        {"job": "consolidate", "entities": ["anchor-step", "wall-drill"]},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    api.thin.clear()
    named = units.handle(
        {"job": "materialise", "entities": ["stretch"]},
        _ctx(tmp_path, llm, state_backend.ApiState(api)),
    )
    assert "thin" not in named and not api.thin
    every = units.handle(
        {"job": "materialise"}, _ctx(tmp_path, llm, state_backend.ApiState(api))
    )
    assert every["thin"]["total"] == len(api.thin) > 0
    assert api.thin["anchor-step"]["page"] == "stretch"


def test_with_the_files_as_the_store_no_thin_list_is_sent(tmp_path: Path) -> None:
    result = units.handle(
        {"job": "consolidate", "entities": ["anchor-step"]},
        _ctx(tmp_path, RouterLLM()),
    )
    assert "thin" not in result
