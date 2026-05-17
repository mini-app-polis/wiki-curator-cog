"""View regeneration — renders the four required views/ pages.

Per ``wcs-wiki/CLAUDE.md`` "Views", four view pages must exist:

  - ``views/kaiano-teaching-kate.md`` — sources where ``instructors``
    contains ``kaiano`` AND ``students`` contains ``kate``.
  - ``views/kaianos-canon.md`` — sources where ``instructors`` contains
    ``kaiano``.
  - ``views/roberts-canon.md`` — sources where ``instructors`` contains
    ``robert``.
  - ``views/full-model.md`` — the whole wiki at a glance.

Views are pre-rendered, derived artifacts. They never carry their own
claims — every fact on a view page traces back to a source page via
wikilink. The spec says "structured as a curated tour, not a dump";
without an LLM in the loop the curator can't actually curate prose, so
this module produces a deterministic structural rendering: sources
listed in chronological order, plus an aggregated set of
concepts/techniques touched (drawn from the source pages'
``contributed_to`` frontmatter). When LLM synthesis lands later, it
replaces the rendered body but the filter logic stays here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import markdown_utils as md

VIEWS_DIR_NAME: str = "views"
SOURCES_DIR_NAME: str = "sources"


@dataclass(frozen=True)
class _ViewSpec:
    """Declarative filter + render hints for one view page."""

    slug: str
    filter_description: str
    instructors_includes: tuple[str, ...] = ()
    students_includes: tuple[str, ...] = ()
    session_type: str | None = None

    def matches(self, source_fm: dict[str, Any]) -> bool:
        """Return True iff this source's frontmatter satisfies the filter."""
        instructors = _as_lower_set(source_fm.get("instructors"))
        students = _as_lower_set(source_fm.get("students"))
        if any(req.lower() not in instructors for req in self.instructors_includes):
            return False
        if any(req.lower() not in students for req in self.students_includes):
            return False
        if self.session_type and source_fm.get("session_type") != self.session_type:
            return False
        return True


# The four required views, per CLAUDE.md "Views" v0 spec. Stable order
# so the views directory is deterministic across runs.
REQUIRED_VIEWS: tuple[_ViewSpec, ...] = (
    _ViewSpec(
        slug="kaiano-teaching-kate",
        filter_description="sources where instructors contains kaiano AND students contains kate",
        instructors_includes=("kaiano",),
        students_includes=("kate",),
    ),
    _ViewSpec(
        slug="kaianos-canon",
        filter_description="sources where instructors contains kaiano",
        instructors_includes=("kaiano",),
    ),
    _ViewSpec(
        slug="roberts-canon",
        filter_description="sources where instructors contains robert",
        instructors_includes=("robert",),
    ),
    _ViewSpec(
        slug="full-model",
        filter_description="all sources, chronologically",
    ),
)


def _as_lower_set(value: Any) -> set[str]:
    """Coerce a frontmatter list field to a lowercased set of strings."""
    if value is None:
        return set()
    if not isinstance(value, list):
        value = [value]
    return {str(x).strip().lower() for x in value if str(x).strip()}


@dataclass
class _SourceEntry:
    """A row in a view: one source page's identity + metadata for rendering."""

    bucket: str
    slug: str
    title: str | None
    session_date: dt.date | None
    session_type: str
    instructors: list[str] = field(default_factory=list)
    students: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    techniques: list[str] = field(default_factory=list)
    instructors_path: str = ""  # "kaiano, kate"

    @property
    def wikilink(self) -> str:
        return f"sources/{self.bucket}/{self.slug}"

    @property
    def sort_key(self) -> tuple[dt.date, str]:
        """Chronological by session_date, then slug for stable ordering."""
        return (self.session_date or dt.date.min, self.slug)


def _parse_date(raw: Any) -> dt.date | None:
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return dt.date.fromisoformat(raw.strip())
        except ValueError:
            return None
    return None


def _load_source_entries(wiki_repo_path: Path) -> list[_SourceEntry]:
    """Scan ``sources/**/*.md`` and return one ``_SourceEntry`` per source page.

    Silently skips pages with missing or malformed frontmatter — views
    are best-effort summaries and a single bad source shouldn't break
    the whole regeneration.
    """
    sources_dir = wiki_repo_path / SOURCES_DIR_NAME
    if not sources_dir.is_dir():
        return []

    entries: list[_SourceEntry] = []
    for path in sorted(sources_dir.rglob("*.md")):
        try:
            page = md.parse(path.read_text())
        except OSError:
            continue
        fm = page.frontmatter or {}
        if fm.get("type") != "source":
            continue
        bucket = path.parent.name
        slug = path.stem
        contributed = fm.get("contributed_to") or {}
        if not isinstance(contributed, dict):
            contributed = {}
        instructors_list = [str(x) for x in (fm.get("instructors") or []) if x]
        students_list = [str(x) for x in (fm.get("students") or []) if x]
        entries.append(
            _SourceEntry(
                bucket=bucket,
                slug=slug,
                title=(str(fm.get("title")).strip() if fm.get("title") else None),
                session_date=_parse_date(fm.get("session_date")),
                session_type=str(fm.get("session_type") or "other"),
                instructors=instructors_list,
                students=students_list,
                concepts=[str(x) for x in (contributed.get("concepts") or [])],
                techniques=[str(x) for x in (contributed.get("techniques") or [])],
                instructors_path=", ".join(instructors_list),
            )
        )
    return entries


def _entry_matches_spec(entry: _SourceEntry, spec: _ViewSpec) -> bool:
    """Wrap _ViewSpec.matches with the _SourceEntry's frontmatter-equivalent dict."""
    fake_fm = {
        "instructors": entry.instructors,
        "students": entry.students,
        "session_type": entry.session_type,
    }
    return spec.matches(fake_fm)


def _render_view_body(spec: _ViewSpec, matched: list[_SourceEntry]) -> str:
    """Render the body of a view page from its matched source entries."""
    if not matched:
        return (
            "_No sources currently match this view._ "
            "When the curator next ingests a matching source, this page "
            "regenerates with content.\n"
        )

    # Aggregate concept and technique slugs across all matched sources
    # so the view exposes the cross-source surface area at a glance.
    concept_set: set[str] = set()
    technique_set: set[str] = set()
    for e in matched:
        concept_set.update(e.concepts)
        technique_set.update(e.techniques)

    lines: list[str] = []
    lines.append(f"_{len(matched)} matching sources._")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    for e in sorted(matched, key=lambda x: x.sort_key):
        date_str = e.session_date.isoformat() if e.session_date else "(undated)"
        title = e.title or e.slug
        who = e.instructors_path or "(unknown instructor)"
        students_part = f" → {', '.join(e.students)}" if e.students else ""
        lines.append(
            f"- **{date_str}** — [[{e.wikilink}|{title}]] · {who}{students_part}"
        )
    lines.append("")

    if concept_set:
        lines.append("## Concepts touched")
        lines.append("")
        for slug in sorted(concept_set):
            lines.append(f"- [[concepts/{slug}]]")
        lines.append("")

    if technique_set:
        lines.append("## Techniques touched")
        lines.append("")
        for slug in sorted(technique_set):
            lines.append(f"- [[techniques/{slug}]]")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_view_page(
    spec: _ViewSpec,
    matched: list[_SourceEntry],
    *,
    curator_version: str,
    regenerated_at: dt.date,
) -> str:
    """Render a full view page (frontmatter + body) as a string."""
    filter_query: dict[str, Any] = {}
    if spec.instructors_includes:
        filter_query["instructors_includes"] = list(spec.instructors_includes)
    if spec.students_includes:
        filter_query["students_includes"] = list(spec.students_includes)
    if spec.session_type:
        filter_query["session_type"] = spec.session_type

    fm_dict: dict[str, Any] = {
        "type": "view",
        "slug": spec.slug,
        "filter": spec.filter_description,
    }
    if filter_query:
        fm_dict["filter_query"] = filter_query
    fm_dict["regenerated_at"] = regenerated_at.isoformat()
    fm_dict["curator_version"] = curator_version

    fm_yaml = yaml.safe_dump(
        fm_dict, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    body = _render_view_body(spec, matched)
    return f"---\n{fm_yaml}---\n\n{body}"


def regenerate_views(
    wiki_repo_path: Path,
    *,
    curator_version: str,
    regenerated_at: dt.date | None = None,
) -> list[Path]:
    """Render all four required view pages. Returns the absolute paths written.

    Idempotent: re-running produces byte-identical output for the same
    inputs and ``regenerated_at`` date. Callers are responsible for
    staging + committing the returned paths.
    """
    when = regenerated_at or dt.date.today()
    entries = _load_source_entries(wiki_repo_path)
    views_dir = wiki_repo_path / VIEWS_DIR_NAME
    views_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for spec in REQUIRED_VIEWS:
        matched = [e for e in entries if _entry_matches_spec(e, spec)]
        rendered = _render_view_page(
            spec,
            matched,
            curator_version=curator_version,
            regenerated_at=when,
        )
        out_path = views_dir / f"{spec.slug}.md"
        out_path.write_text(rendered)
        written.append(out_path)
    return written
