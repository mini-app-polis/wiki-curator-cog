"""Small markdown-with-frontmatter parser, focused on what the curator needs.

The derived-page upserts (concept, technique, instructor, terminology)
all share the same shape: a YAML frontmatter block followed by a body
organized into ``## H2`` sections, some of which have ``### H3``
subsections (notably ``## By teacher`` → ``### <Instructor>``). The
curator needs to:

- Read an existing page, modify a specific H2 (or H2→H3) section, and
  write it back without disturbing other sections.
- Update specific frontmatter list fields (``sources``, ``teachers``,
  etc.) without rewriting the whole frontmatter.
- Create a new page when none exists, with a known starter structure.

These helpers are intentionally minimal and structural — no
prose-aware splitting, no smart merging. The caller (``derived_pages``)
holds the per-page-type logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# ── Constants ───────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H2_RE = re.compile(r"^## (.+?)\s*$")
_H3_RE = re.compile(r"^### (.+?)\s*$")


# ── Data model ──────────────────────────────────────────────────────────


@dataclass
class Page:
    """A parsed markdown page with frontmatter and H2-anchored sections.

    ``preamble`` holds any body content that appears between the
    frontmatter and the first ``## H2`` heading (commonly empty or a
    short intro line). ``sections`` is an ordered list of (heading_text,
    body_lines) pairs — body_lines are the lines AFTER the ``## `` line
    and BEFORE the next ``## ``, preserved verbatim (including blank
    lines and any ``### H3`` substructure).

    Round-trip: ``parse(serialize(page)) == page`` (modulo trailing
    whitespace normalization in serialize).
    """

    frontmatter: dict[str, Any] = field(default_factory=dict)
    preamble: list[str] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)

    def get_section(self, heading: str) -> list[str] | None:
        """Return the body_lines for the named H2 section, or None if absent."""
        for h, body in self.sections:
            if h == heading:
                return body
        return None

    def set_section(self, heading: str, body_lines: list[str]) -> None:
        """Replace or append the named H2 section."""
        for i, (h, _) in enumerate(self.sections):
            if h == heading:
                self.sections[i] = (heading, body_lines)
                return
        self.sections.append((heading, body_lines))

    def ensure_section(self, heading: str) -> list[str]:
        """Return the body_lines for the named section, creating an empty one if absent.

        Returned list is the live reference inside ``self.sections`` —
        callers can mutate it in place and the changes persist.
        """
        for h, body in self.sections:
            if h == heading:
                return body
        new_body: list[str] = []
        self.sections.append((heading, new_body))
        return new_body


# ── Parser ──────────────────────────────────────────────────────────────


def parse(text: str) -> Page:
    """Parse a markdown page with optional YAML frontmatter.

    Pages without a frontmatter block parse with ``frontmatter={}``.
    Malformed frontmatter (invalid YAML or non-mapping at top level)
    parses with ``frontmatter={}`` as well — the caller doesn't get a
    silent error, but the rest of the page still parses cleanly.
    """
    frontmatter: dict[str, Any] = {}
    body = text

    m = _FRONTMATTER_RE.match(text)
    if m is not None:
        try:
            parsed = yaml.safe_load(m.group(1))
            if isinstance(parsed, dict):
                frontmatter = parsed
        except yaml.YAMLError:
            pass
        body = text[m.end() :]

    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    for line in body.splitlines():
        h2 = _H2_RE.match(line)
        if h2 is None:
            if current_heading is None:
                preamble.append(line)
            else:
                current_body.append(line)
            continue
        # Flush the previous section (or preamble) and start a new one.
        if current_heading is not None:
            sections.append((current_heading, current_body))
        else:
            # Trim trailing blank lines from preamble so serialize
            # doesn't accumulate them on round-trip.
            while preamble and not preamble[-1].strip():
                preamble.pop()
        current_heading = h2.group(1).strip()
        current_body = []

    if current_heading is not None:
        sections.append((current_heading, current_body))

    return Page(frontmatter=frontmatter, preamble=preamble, sections=sections)


# ── Serializer ──────────────────────────────────────────────────────────


def serialize(page: Page) -> str:
    """Render a Page back to a string. Output always ends with a single newline."""
    parts: list[str] = []

    if page.frontmatter:
        yaml_body = yaml.safe_dump(
            page.frontmatter,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        parts.append(f"---\n{yaml_body}---\n")

    if page.preamble:
        # Separator blank line between frontmatter and preamble prose.
        if parts:
            parts.append("\n")
        parts.append("\n".join(page.preamble).rstrip() + "\n")

    for heading, body in page.sections:
        # Separator blank line before each H2.
        if parts and not parts[-1].endswith("\n\n"):
            parts.append("\n")
        parts.append(f"## {heading}\n")
        # Strip trailing blank lines from each section body so we don't
        # accumulate blanks across re-serializations.
        trimmed = list(body)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        if trimmed:
            parts.append("\n".join(trimmed) + "\n")

    text = "".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text


# ── H3 sub-structure helpers (for ## By teacher → ### Instructor) ───────


def split_h3(body_lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split an H2 section's body into H3-anchored subsections.

    First element has ``heading=None`` for any content before the first
    ``### H3`` (commonly empty in concept pages but possible for prose
    intros to a section).
    """
    chunks: list[tuple[str | None, list[str]]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    for line in body_lines:
        h3 = _H3_RE.match(line)
        if h3 is None:
            current_body.append(line)
            continue
        chunks.append((current_heading, current_body))
        current_heading = h3.group(1).strip()
        current_body = []

    chunks.append((current_heading, current_body))
    return chunks


def join_h3(chunks: list[tuple[str | None, list[str]]]) -> list[str]:
    """Inverse of split_h3. Inserts blank lines between subsections."""
    out: list[str] = []
    for heading, body in chunks:
        if heading is not None:
            if out and out[-1].strip():
                out.append("")
            out.append(f"### {heading}")
        out.extend(body)
    # Drop trailing blanks.
    while out and not out[-1].strip():
        out.pop()
    return out


# ── Frontmatter list helpers ────────────────────────────────────────────


def add_unique(d: dict[str, Any], key: str, value: str) -> bool:
    """Add ``value`` to the list at ``d[key]`` if absent. Returns True if added.

    Creates the list if missing. Coerces non-list existing values to a
    single-item list before appending (defensive — frontmatter from a
    hand-edited file might have a scalar where we expect a list).
    """
    existing = d.get(key)
    if existing is None:
        d[key] = [value]
        return True
    if not isinstance(existing, list):
        existing = [existing]
        d[key] = existing
    if value in existing:
        return False
    existing.append(value)
    return True
