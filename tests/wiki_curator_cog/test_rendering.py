"""Tests for source-page rendering from notes_json."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import yaml

from wiki_curator_cog.models import WcsNote
from wiki_curator_cog.rendering import (
    detect_quality_issues,
    render_common_mistakes,
    render_competition_notes,
    render_drills,
    render_key_concepts,
    render_patterns_and_sequences,
    render_quotes,
    render_references,
    render_source_frontmatter,
    render_source_page,
    render_summary,
    render_vocabulary,
)


def _make_note(notes_json: dict[str, Any], **overrides: Any) -> WcsNote:
    defaults = dict(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        transcript_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        title="Anchor step quality",
        session_date=dt.date(2025, 9, 15),
        session_type="private_lesson",
        instructors=["Kate"],
        students=["Kaiano"],
        organization="Studio West",
        is_default_visible=True,
        visibility="private",
        model="claude-sonnet-4-6",
        provider="anthropic",
        notes_json=notes_json,
        created_at=dt.datetime(2025, 9, 15, 18, 0, tzinfo=dt.UTC),
    )
    defaults.update(overrides)
    return WcsNote.model_validate(defaults)


# ── Section renderers ───────────────────────────────────────────────────


def test_render_summary_present() -> None:
    out = render_summary({"summary": "Kate worked on anchor step quality."})
    assert out.startswith("## Summary\n\n")
    assert "anchor step quality" in out
    assert out.endswith("\n\n")


def test_render_summary_missing_returns_empty() -> None:
    assert render_summary({}) == ""
    assert render_summary({"summary": ""}) == ""
    assert render_summary({"summary": None}) == ""


def test_render_key_concepts_dict_items() -> None:
    out = render_key_concepts(
        {
            "key_concepts": [
                {"concept": "Anchor step", "detail": "Closing pattern."},
                {"concept": "Settle"},
                {"detail": "Lone detail with no concept."},
            ]
        }
    )
    assert "## Key concepts" in out
    assert "- **Anchor step**: Closing pattern." in out
    assert "- **Settle**" in out
    assert "- Lone detail with no concept." in out


def test_render_key_concepts_string_items() -> None:
    out = render_key_concepts({"key_concepts": ["Connection", "Frame"]})
    assert "- Connection" in out
    assert "- Frame" in out


def test_render_key_concepts_empty() -> None:
    assert render_key_concepts({}) == ""
    assert render_key_concepts({"key_concepts": []}) == ""
    assert render_key_concepts({"key_concepts": [None, "", "  "]}) == ""


def test_render_vocabulary() -> None:
    out = render_vocabulary(
        {
            "vocabulary_terms": [
                {"term": "Anchor step", "definition": "The closing pattern..."},
                {"term": "Compression"},
            ]
        }
    )
    assert "- **Anchor step**: The closing pattern..." in out
    assert "- **Compression**" in out


def test_render_drills_full() -> None:
    out = render_drills(
        {
            "drills": [
                {
                    "name": "Wall drill",
                    "goal": "Feel the anchor against resistance.",
                    "steps": [
                        "Stand 3 feet from wall.",
                        "Settle into the anchor.",
                    ],
                }
            ]
        }
    )
    assert "### Wall drill" in out
    assert "**Goal:** Feel the anchor against resistance." in out
    assert "1. Stand 3 feet from wall." in out
    assert "2. Settle into the anchor." in out


def test_render_drills_unnamed_falls_back() -> None:
    out = render_drills({"drills": [{"goal": "Connection feel"}]})
    assert "### (unnamed drill)" in out


def test_render_drills_empty() -> None:
    assert render_drills({}) == ""


def test_render_common_mistakes() -> None:
    out = render_common_mistakes(
        {
            "common_mistakes": [
                {
                    "mistake": "Bouncing on anchor.",
                    "correction": "Sink instead.",
                },
                {"mistake": "Rushing the count."},
            ]
        }
    )
    assert "- **Mistake:** Bouncing on anchor." in out
    assert "**Correction:** Sink instead." in out
    assert "- **Mistake:** Rushing the count." in out


def test_render_patterns_and_sequences() -> None:
    out = render_patterns_and_sequences(
        {
            "patterns_and_sequences": [
                {"name": "Sugar push", "description": "6-count basic."},
                {"name": "Whip"},
            ]
        }
    )
    assert "- **Sugar push**: 6-count basic." in out
    assert "- **Whip**" in out


def test_render_competition_notes() -> None:
    out = render_competition_notes(
        {
            "competition_notes": [
                {"note": "Don't fight the lead.", "context": "Jack and Jill"},
                "Cold open the routine.",
            ]
        }
    )
    assert "Don't fight the lead. _(context: Jack and Jill)_" in out
    assert "- Cold open the routine." in out


def test_render_quotes_with_speaker_and_context() -> None:
    out = render_quotes(
        {
            "quotes": [
                {
                    "quote": "Anchors are about sinking, not staying.",
                    "speaker": "Kate",
                    "context": "demonstrating sugar push",
                },
                {"quote": "Frame is a conversation."},
            ]
        }
    )
    assert "> Anchors are about sinking, not staying." in out
    assert "> — Kate (demonstrating sugar push)" in out
    assert "> Frame is a conversation." in out


def test_render_references() -> None:
    out = render_references(
        {
            "references": [
                {
                    "name": "Robert Royston",
                    "type": "instructor",
                    "context": "Kate cited his anchor framing.",
                },
                {"name": "Westies in Space", "type": "event"},
                {"context": "no-name ref — dropped"},
            ]
        }
    )
    assert "- **Robert Royston**" in out
    assert "_(instructor)_" in out
    assert "- **Westies in Space** _(event)_" in out
    assert "no-name ref" not in out


# ── Frontmatter ─────────────────────────────────────────────────────────


def test_frontmatter_is_valid_yaml_with_required_fields() -> None:
    note = _make_note({"summary": "hello"})
    block = render_source_frontmatter(
        note,
        canonical_instructors=["kate"],
        canonical_students=["kaiano"],
        instructors_raw=["Kate"],
        students_raw=["Kaiano"],
        contributed_to={
            "concepts": [],
            "techniques": [],
            "instructors": [],
            "terminology": [],
        },
        curator_version=1,
        ingested_at=dt.date(2026, 5, 16),
    )
    assert block.startswith("---\n")
    assert block.endswith("---\n")
    body = block.strip().strip("-").strip()
    parsed = yaml.safe_load(body)
    assert parsed["type"] == "source"
    assert parsed["note_id"] == str(note.id)
    assert parsed["transcript_id"] == str(note.transcript_id)
    assert parsed["instructors"] == ["kate"]
    assert parsed["instructors_raw"] == ["Kate"]
    assert parsed["students"] == ["kaiano"]
    assert parsed["students_raw"] == ["Kaiano"]
    assert parsed["organization"] == "Studio West"
    assert parsed["session_date"] == "2025-09-15"
    assert parsed["title"] == "Anchor step quality"
    assert parsed["is_default_visible"] is True
    assert parsed["visibility"] == "private"
    assert parsed["contributed_to"] == {
        "concepts": [],
        "techniques": [],
        "instructors": [],
        "terminology": [],
    }
    assert parsed["curator_version"] == 1
    assert parsed["ingested_at"] == "2026-05-16"


def test_frontmatter_handles_null_title_and_missing_date() -> None:
    note = _make_note({}, title=None, session_date=None)
    block = render_source_frontmatter(
        note,
        canonical_instructors=["kate"],
        canonical_students=[],
        instructors_raw=["Kate"],
        students_raw=[],
        contributed_to={
            "concepts": [],
            "techniques": [],
            "instructors": [],
            "terminology": [],
        },
        curator_version=1,
        ingested_at=dt.date(2026, 5, 16),
    )
    parsed = yaml.safe_load(block.strip().strip("-").strip())
    assert parsed["title"] is None
    assert parsed["session_date"] is None


# ── Full page ───────────────────────────────────────────────────────────


def test_render_source_page_smoke() -> None:
    notes_json: dict[str, Any] = {
        "summary": "Kate worked anchor step quality.",
        "key_concepts": [
            {"concept": "Anchor step", "detail": "Closing pattern."},
        ],
        "vocabulary_terms": [{"term": "anchor step", "definition": "..."}],
        "drills": [
            {
                "name": "Wall drill",
                "goal": "Feel the anchor.",
                "steps": ["Step 1", "Step 2"],
            }
        ],
        "common_mistakes": [
            {"mistake": "Bouncing.", "correction": "Sink."},
        ],
        "patterns_and_sequences": [{"name": "Sugar push"}],
        "competition_notes": [],
        "quotes": [{"quote": "Sink.", "speaker": "Kate"}],
        "references": [],
        # These three should NOT appear in the rendered body.
        "student_observations": [{"observation": "Kaiano is improving."}],
        "action_items": [{"action": "Practice anchors", "rationale": "."}],
        "off_topic_notes": ["chatter about food"],
    }
    note = _make_note(notes_json)
    out = render_source_page(
        note,
        canonical_instructors=["kate"],
        canonical_students=["kaiano"],
        instructors_raw=["Kate"],
        students_raw=["Kaiano"],
        contributed_to={
            "concepts": [],
            "techniques": [],
            "instructors": [],
            "terminology": [],
        },
        curator_version=1,
        ingested_at=dt.date(2026, 5, 16),
    )
    assert "## Summary" in out
    assert "## Key concepts" in out
    assert "## Vocabulary" in out
    assert "## Drills" in out
    assert "## Common mistakes" in out
    assert "## Patterns and sequences" in out
    assert "## Quotes" in out
    # These three excluded sections must NOT appear:
    assert "Kaiano is improving" not in out
    assert "Practice anchors" not in out
    assert "chatter about food" not in out
    # Spec order: Summary before Key concepts, etc.
    assert out.index("## Summary") < out.index("## Key concepts")
    assert out.index("## Key concepts") < out.index("## Vocabulary")
    assert out.index("## Vocabulary") < out.index("## Drills")


def test_render_source_page_empty_notes_json_still_writes_header() -> None:
    note = _make_note({})
    out = render_source_page(
        note,
        canonical_instructors=["kate"],
        canonical_students=[],
        instructors_raw=["Kate"],
        students_raw=[],
        contributed_to={
            "concepts": [],
            "techniques": [],
            "instructors": [],
            "terminology": [],
        },
        curator_version=1,
        ingested_at=dt.date(2026, 5, 16),
    )
    # Frontmatter is always present.
    assert out.startswith("---\n")
    # No body sections because notes_json is empty.
    assert "## Summary" not in out
    assert "## Key concepts" not in out


def test_render_source_page_with_extra_notes() -> None:
    note = _make_note({"summary": "ok"})
    out = render_source_page(
        note,
        canonical_instructors=["kate"],
        canonical_students=[],
        instructors_raw=["Kate"],
        students_raw=[],
        contributed_to={
            "concepts": [],
            "techniques": [],
            "instructors": [],
            "terminology": [],
        },
        curator_version=1,
        ingested_at=dt.date(2026, 5, 16),
        extra_notes=["Possible extraction issue.", "Slug collision avoided."],
    )
    assert "## Notes" in out
    assert "- Possible extraction issue." in out
    assert "- Slug collision avoided." in out


# ── Quality detection ───────────────────────────────────────────────────


def test_detect_quality_issues_empty_notes_json() -> None:
    obs = detect_quality_issues({})
    assert any("no content" in o.lower() for o in obs)


def test_detect_quality_issues_with_content_is_clean() -> None:
    assert detect_quality_issues({"summary": "Anything at all."}) == []


def test_detect_quality_issues_surfaces_suggested_sections() -> None:
    obs = detect_quality_issues(
        {
            "summary": "ok",
            "suggested_new_sections": [
                {"name": "judges_comments"},
                "music_choices",
            ],
        }
    )
    assert any("judges_comments" in o for o in obs)
    assert any("music_choices" in o for o in obs)


@pytest.mark.parametrize("field", [None, "", [], "garbage"])
def test_detect_quality_issues_tolerates_malformed_suggested(field) -> None:
    # Should not raise on any of these.
    detect_quality_issues({"summary": "ok", "suggested_new_sections": field})
