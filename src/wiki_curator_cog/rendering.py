"""Render a WcsNote into a source page (frontmatter + markdown body).

Pure functions. All inputs are explicit, no IO. The curator orchestrates
the writes; this module just builds the string.

The output shape is the one specified in ``wcs-wiki/CLAUDE.md`` under
"Source pages". Body sections appear in spec order and are omitted
entirely when the corresponding ``notes_json`` field is missing or empty
— including the heading, so that a sparse note doesn't produce a page
peppered with empty sections.

Three ``notes_json`` fields are deliberately NOT rendered into the
wiki, per the field → page-type mapping in CLAUDE.md:

  - ``student_observations`` — per-student feedback, stays in the notes UI.
  - ``action_items`` — per-session homework, stays in the notes UI.
  - ``off_topic_notes`` / ``suggested_new_sections`` — operational
    signals for the upstream pipeline and the lint pass, not wiki content.

If those fields are present, they're silently dropped here — surfacing
them is the curator's job, not the renderer's.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import yaml

from .models import WcsNote

# ── Frontmatter ─────────────────────────────────────────────────────────


def render_source_frontmatter(
    note: WcsNote,
    *,
    canonical_instructors: list[str],
    canonical_students: list[str],
    instructors_raw: list[str],
    students_raw: list[str],
    contributed_to: dict[str, list[str]],
    curator_version: int,
    ingested_at: dt.date,
) -> str:
    """Build the YAML frontmatter block, including the surrounding ``---`` fences.

    Field order matches the "Source pages" spec in CLAUDE.md. We pass
    ``sort_keys=False`` to ``yaml.safe_dump`` and rely on dict-insertion
    order (Python 3.7+) to preserve it. UUIDs and dates are stringified
    here so the YAML output is plain scalars rather than tagged values.
    """
    session_date_s = (
        note.session_date.isoformat() if note.session_date is not None else None
    )

    data: dict[str, Any] = {
        "type": "source",
        "note_id": str(note.id),
        "transcript_id": str(note.transcript_id),
        "session_type": note.session_type,
        "instructors": list(canonical_instructors),
        "instructors_raw": list(instructors_raw),
        "students": list(canonical_students),
        "students_raw": list(students_raw),
        "organization": note.organization or "",
        "session_date": session_date_s,
        "title": note.title,
        "is_default_visible": bool(note.is_default_visible),
        "visibility": note.visibility,
        "contributed_to": {
            "concepts": list(contributed_to.get("concepts", [])),
            "techniques": list(contributed_to.get("techniques", [])),
            "instructors": list(contributed_to.get("instructors", [])),
            "terminology": list(contributed_to.get("terminology", [])),
        },
        "curator_version": int(curator_version),
        "ingested_at": ingested_at.isoformat(),
    }

    body = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{body}---\n"


# ── Body sections ───────────────────────────────────────────────────────


def _as_str(value: Any) -> str:
    """Coerce any notes_json value to a stripped string. Empty → ''."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_list(value: Any) -> list[Any]:
    """Coerce a notes_json field to a list. Non-list inputs → []."""
    if isinstance(value, list):
        return value
    return []


def _heading(title: str) -> str:
    return f"## {title}\n\n"


def render_summary(notes_json: dict[str, Any]) -> str:
    text = _as_str(notes_json.get("summary"))
    if not text:
        return ""
    return _heading("Summary") + text + "\n\n"


def render_key_concepts(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("key_concepts"))
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            concept = _as_str(item.get("concept"))
            detail = _as_str(item.get("detail"))
            if concept and detail:
                lines.append(f"- **{concept}**: {detail}")
            elif concept:
                lines.append(f"- **{concept}**")
            elif detail:
                lines.append(f"- {detail}")
        else:
            s = _as_str(item)
            if s:
                lines.append(f"- {s}")
    if not lines:
        return ""
    return _heading("Key concepts") + "\n".join(lines) + "\n\n"


def render_vocabulary(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("vocabulary_terms"))
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            term = _as_str(item.get("term"))
            definition = _as_str(item.get("definition"))
            if term and definition:
                lines.append(f"- **{term}**: {definition}")
            elif term:
                lines.append(f"- **{term}**")
        else:
            s = _as_str(item)
            if s:
                lines.append(f"- {s}")
    if not lines:
        return ""
    return _heading("Vocabulary") + "\n".join(lines) + "\n\n"


def render_drills(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("drills"))
    if not items:
        return ""
    blocks: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = _as_str(item.get("name"))
            goal = _as_str(item.get("goal"))
            steps = _as_list(item.get("steps"))
            header = f"### {name}" if name else "### (unnamed drill)"
            chunk = [header, ""]
            if goal:
                chunk.append(f"**Goal:** {goal}")
                chunk.append("")
            if steps:
                for i, step in enumerate(steps, start=1):
                    step_s = _as_str(step)
                    if step_s:
                        chunk.append(f"{i}. {step_s}")
                chunk.append("")
            blocks.append("\n".join(chunk).rstrip() + "\n")
        else:
            s = _as_str(item)
            if s:
                blocks.append(f"- {s}\n")
    if not blocks:
        return ""
    return _heading("Drills") + "\n".join(blocks) + "\n"


def render_common_mistakes(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("common_mistakes"))
    if not items:
        return ""
    blocks: list[str] = []
    for item in items:
        if isinstance(item, dict):
            mistake = _as_str(item.get("mistake"))
            correction = _as_str(item.get("correction"))
            if mistake and correction:
                blocks.append(
                    f"- **Mistake:** {mistake}\n  **Correction:** {correction}"
                )
            elif mistake:
                blocks.append(f"- **Mistake:** {mistake}")
            elif correction:
                blocks.append(f"- **Correction:** {correction}")
        else:
            s = _as_str(item)
            if s:
                blocks.append(f"- {s}")
    if not blocks:
        return ""
    return _heading("Common mistakes") + "\n".join(blocks) + "\n\n"


def render_patterns_and_sequences(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("patterns_and_sequences"))
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = _as_str(item.get("name"))
            description = _as_str(item.get("description"))
            if name and description:
                lines.append(f"- **{name}**: {description}")
            elif name:
                lines.append(f"- **{name}**")
            elif description:
                lines.append(f"- {description}")
        else:
            s = _as_str(item)
            if s:
                lines.append(f"- {s}")
    if not lines:
        return ""
    return _heading("Patterns and sequences") + "\n".join(lines) + "\n\n"


def render_competition_notes(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("competition_notes"))
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            note = _as_str(item.get("note"))
            context = _as_str(item.get("context"))
            if note and context:
                lines.append(f"- {note} _(context: {context})_")
            elif note:
                lines.append(f"- {note}")
            elif context:
                lines.append(f"- _(context: {context})_")
        else:
            s = _as_str(item)
            if s:
                lines.append(f"- {s}")
    if not lines:
        return ""
    return _heading("Competition notes") + "\n".join(lines) + "\n\n"


def render_quotes(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("quotes"))
    if not items:
        return ""
    blocks: list[str] = []
    for item in items:
        if isinstance(item, dict):
            quote = _as_str(item.get("quote"))
            speaker = _as_str(item.get("speaker"))
            context = _as_str(item.get("context"))
            if not quote:
                continue
            chunk = [f"> {quote}"]
            attribution_parts: list[str] = []
            if speaker:
                attribution_parts.append(f"— {speaker}")
            if context:
                attribution_parts.append(f"({context})")
            if attribution_parts:
                chunk.append("> " + " ".join(attribution_parts))
            blocks.append("\n".join(chunk))
        else:
            s = _as_str(item)
            if s:
                blocks.append(f"> {s}")
    if not blocks:
        return ""
    return _heading("Quotes") + "\n\n".join(blocks) + "\n\n"


def render_references(notes_json: dict[str, Any]) -> str:
    items = _as_list(notes_json.get("references"))
    if not items:
        return ""
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = _as_str(item.get("name"))
            ref_type = _as_str(item.get("type"))
            context = _as_str(item.get("context"))
            if not name:
                # A reference with no name is just stray context; skip.
                continue
            line = f"- **{name}**"
            if ref_type:
                line += f" _({ref_type})_"
            if context:
                line += f" — {context}"
            lines.append(line)
        else:
            s = _as_str(item)
            if s:
                lines.append(f"- {s}")
    if not lines:
        return ""
    return _heading("References") + "\n".join(lines) + "\n\n"


def render_notes(notes: list[str]) -> str:
    """Render the ``## Notes`` section. Empty list → no section."""
    cleaned = [n.strip() for n in notes if n and n.strip()]
    if not cleaned:
        return ""
    return _heading("Notes") + "\n".join(f"- {n}" for n in cleaned) + "\n\n"


# ── Top-level renderer ──────────────────────────────────────────────────


# Body sections in spec order. Each entry: (renderer function,
# notes_json field name used only by detect_quality_issues).
_BODY_RENDERERS = (
    render_summary,
    render_key_concepts,
    render_vocabulary,
    render_drills,
    render_common_mistakes,
    render_patterns_and_sequences,
    render_competition_notes,
    render_quotes,
    render_references,
)


def render_source_page(
    note: WcsNote,
    *,
    canonical_instructors: list[str],
    canonical_students: list[str],
    instructors_raw: list[str],
    students_raw: list[str],
    contributed_to: dict[str, list[str]],
    curator_version: int,
    ingested_at: dt.date,
    extra_notes: list[str] | None = None,
) -> str:
    """Render the full source page (frontmatter + body) as a single string.

    ``extra_notes`` is appended to the ``## Notes`` section. The curator
    uses this to record source-quality observations detected during
    ingest (e.g., a totally empty notes_json), without conflating them
    with content from the source itself.
    """
    parts: list[str] = [
        render_source_frontmatter(
            note,
            canonical_instructors=canonical_instructors,
            canonical_students=canonical_students,
            instructors_raw=instructors_raw,
            students_raw=students_raw,
            contributed_to=contributed_to,
            curator_version=curator_version,
            ingested_at=ingested_at,
        ),
        "\n",  # blank line between frontmatter and body
    ]
    for renderer in _BODY_RENDERERS:
        parts.append(renderer(note.notes_json))
    parts.append(render_notes(extra_notes or []))

    # Concatenate, then normalize trailing whitespace so the file ends
    # with exactly one newline.
    return "".join(parts).rstrip() + "\n"


# ── Quality detection ───────────────────────────────────────────────────


def detect_quality_issues(notes_json: dict[str, Any]) -> list[str]:
    """Return a list of human-readable quality observations.

    Used by the curator to (a) populate the source page's ``## Notes``
    section and (b) emit ``wiki.source.quality_issue`` and
    ``wiki.schema.suggested_section`` findings in automated/backfill
    modes. Detection is intentionally conservative — false positives
    here add noise to every source page.
    """
    observations: list[str] = []

    # All-empty notes_json — upstream extraction probably failed.
    has_any_content = any(
        notes_json.get(field)
        for field in (
            "summary",
            "key_concepts",
            "vocabulary_terms",
            "drills",
            "common_mistakes",
            "patterns_and_sequences",
            "competition_notes",
            "quotes",
            "references",
        )
    )
    if not has_any_content:
        observations.append(
            "Upstream notes_json contained no content in any rendered "
            "section. Possible extraction failure — review the source "
            "transcript and consider re-running the upstream extractor."
        )

    # Schema-evolution flag from the upstream LLM.
    suggested = _as_list(notes_json.get("suggested_new_sections"))
    if suggested:
        names = [
            _as_str(s.get("name") if isinstance(s, dict) else s) for s in suggested
        ]
        names = [n for n in names if n]
        if names:
            observations.append(
                "Upstream LLM suggested new notes_json sections: "
                + ", ".join(names)
                + ". Surface for schema review."
            )

    return observations
