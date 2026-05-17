"""One-time alias-map bootstrap from existing instructor pages.

The wiki's name canonicalization story (see ``aliases.py`` and
``wcs-wiki/CLAUDE.md`` "Name canonicalization") assumes
``instructors/_aliases.yaml`` already collapses variants of the same
person to one canonical slug. In practice, after a v3/v4 backfill the
alias file is empty and the wiki has many duplicates: ``benji`` +
``benji-schremmer`` + ``benji-schumer`` + ``benji-schwimmer``,
``brandi`` + ``brandi-gill`` + ``brandi-guild`` + ``brandy`` +
``brandy-guild``, ``abby`` + ``abby-white``, etc.

This module scans ``instructors/*.md``, groups likely-duplicates, and
writes a proposal:

  - SAFE merges (high confidence) are written directly into
    ``instructors/_aliases.yaml`` so the next ingest picks them up.
  - AMBIGUOUS groups are written to
    ``instructors/_aliases.review.md`` for Kaiano to resolve by hand.

What counts as "safe":

  - Pure case/whitespace differences (handled implicitly by slugify;
    nothing to do).
  - First-token-only slug + exactly one multi-token slug that starts
    with the same first token: ``benji`` + ``benji-schwimmer`` →
    canonical ``benji-schwimmer``.
  - Multi-token slugs that differ by 1-2 character edits (likely
    misspellings of the same name): ``benji-schremmer`` /
    ``benji-schumer`` / ``benji-schwimmer`` → canonical = the one with
    the most contributing sources.

What counts as "ambiguous":

  - First-token-only slug + multiple multi-token candidates
    (``kate`` + ``kate-benson`` + ``kate-marsden``): can't pick a
    canonical without human input.
  - Multi-token slugs that differ by more than 2 character edits
    (probably distinct people).

Run via ``python -m wiki_curator_cog.main bootstrap-aliases``.
"""

from __future__ import annotations

import dataclasses as _dc
import datetime as dt
from pathlib import Path
from typing import Any

from . import markdown_utils as md
from .aliases import ALIAS_FILE_NAME, AliasMap

_INSTRUCTORS_DIR_NAME: str = "instructors"
_REVIEW_FILE_NAME: str = "_aliases.review.md"

# Levenshtein distance threshold for two same-base-name slugs to be
# considered the same person via fuzzy match. 2 catches typical
# misspellings (Schwimmer / Schremmer / Schumer) without bridging
# obviously-different surnames.
_FUZZY_DISTANCE_THRESHOLD: int = 2


@_dc.dataclass(frozen=True)
class _InstructorPage:
    """One instructor page's identity + popularity signal."""

    slug: str
    name: str  # frontmatter ``name``, falling back to slug
    sources_count: int
    references_count: int

    @property
    def first_token(self) -> str:
        return self.slug.split("-", 1)[0]

    @property
    def popularity(self) -> int:
        """Total references to this instructor across the wiki.

        Used as a tiebreaker when choosing the canonical slug among
        merge candidates: the most-sourced variant wins.
        """
        return self.sources_count + self.references_count


@_dc.dataclass
class _Proposal:
    """Either a safe auto-merge or an ambiguous group needing review."""

    canonical: str  # the slug to merge into
    variants: list[str]  # other slugs that should redirect to canonical
    reason: str
    safe: bool


# ── Loading ─────────────────────────────────────────────────────────────


def _load_instructor_pages(wiki_repo_path: Path) -> list[_InstructorPage]:
    """Scan ``instructors/*.md`` and return one ``_InstructorPage`` per page.

    Excludes ``_aliases.yaml`` and the bootstrap review file.
    """
    instr_dir = wiki_repo_path / _INSTRUCTORS_DIR_NAME
    pages: list[_InstructorPage] = []
    if not instr_dir.is_dir():
        return pages
    for path in sorted(instr_dir.glob("*.md")):
        if path.name == _REVIEW_FILE_NAME:
            continue
        try:
            page = md.parse(path.read_text())
        except OSError:
            continue
        fm = page.frontmatter or {}
        slug = str(fm.get("slug") or path.stem)
        name = str(fm.get("name") or slug)
        pages.append(
            _InstructorPage(
                slug=slug,
                name=name,
                sources_count=_int_or_zero(fm.get("sources_count")),
                references_count=_int_or_zero(fm.get("references_count")),
            )
        )
    return pages


def _int_or_zero(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


# ── Fuzzy matching (small, no deps) ─────────────────────────────────────


def _levenshtein(a: str, b: str) -> int:
    """Classic edit distance. Inlined to avoid pulling in a fuzzy-match dep."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


# ── Proposal generation ────────────────────────────────────────────────


def _group_by_first_token(
    pages: list[_InstructorPage],
) -> dict[str, list[_InstructorPage]]:
    """Bucket pages by the first hyphen-separated token of their slug."""
    groups: dict[str, list[_InstructorPage]] = {}
    for p in pages:
        groups.setdefault(p.first_token, []).append(p)
    return groups


def _propose_first_token_merges(
    group: list[_InstructorPage], token: str
) -> list[_Proposal]:
    """Handle slug == first_token + slugs starting with that token.

    Cases:
      - 1 page total → nothing to do.
      - Single-token only ([token]) → real person, no merges.
      - Single-token + 1 multi-token → safe merge into multi-token.
      - Single-token + N multi-token (N>=2) → ambiguous, defer.
      - No single-token, N>=2 multi-tokens → fuzzy-merge step
        elsewhere.
    """
    single_token = [p for p in group if p.slug == token]
    multi_token = [p for p in group if p.slug != token]
    if not single_token or not multi_token:
        return []
    if len(multi_token) == 1:
        canonical = multi_token[0]
        return [
            _Proposal(
                canonical=canonical.slug,
                variants=[p.slug for p in single_token],
                reason=(
                    f"single-token slug '{token}' has exactly one expansion "
                    f"'{canonical.slug}' in the wiki"
                ),
                safe=True,
            )
        ]
    # Multiple expansions — ambiguous.
    candidates = ", ".join(p.slug for p in multi_token)
    return [
        _Proposal(
            canonical="",  # unknown
            variants=[p.slug for p in single_token] + [p.slug for p in multi_token],
            reason=(
                f"single-token slug '{token}' could be any of {{{candidates}}} "
                "— needs human to pick the canonical"
            ),
            safe=False,
        )
    ]


def _propose_fuzzy_merges(group: list[_InstructorPage]) -> list[_Proposal]:
    """Merge multi-token slugs that look like misspellings of each other.

    Strategy: within the group (same first token), find pairs of
    multi-token slugs whose Levenshtein distance is small. Connect
    them transitively into clusters. Each cluster's most-popular
    member is the canonical.
    """
    multi = [p for p in group if "-" in p.slug]
    if len(multi) < 2:
        return []
    # Union-find by slug.
    parent: dict[str, str] = {p.slug: p.slug for p in multi}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for i, a in enumerate(multi):
        for b in multi[i + 1 :]:
            if _levenshtein(a.slug, b.slug) <= _FUZZY_DISTANCE_THRESHOLD:
                union(a.slug, b.slug)

    clusters: dict[str, list[_InstructorPage]] = {}
    for p in multi:
        clusters.setdefault(find(p.slug), []).append(p)

    proposals: list[_Proposal] = []
    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        # Pick most-popular member; tie-break by alphabetical slug for
        # stable output.
        canonical = max(cluster, key=lambda p: (p.popularity, -len(p.slug), p.slug))
        variants = [p.slug for p in cluster if p.slug != canonical.slug]
        proposals.append(
            _Proposal(
                canonical=canonical.slug,
                variants=variants,
                reason=(
                    f"slug variants within edit-distance {_FUZZY_DISTANCE_THRESHOLD} "
                    f"of each other; '{canonical.slug}' has the most contributions"
                ),
                safe=True,
            )
        )
    return proposals


def generate_proposals(pages: list[_InstructorPage]) -> list[_Proposal]:
    """Run all proposal heuristics over the loaded instructor pages."""
    all_proposals: list[_Proposal] = []
    for token, group in sorted(_group_by_first_token(pages).items()):
        all_proposals.extend(_propose_first_token_merges(group, token))
        all_proposals.extend(_propose_fuzzy_merges(group))
    return all_proposals


# ── Writing outputs ────────────────────────────────────────────────────


def _apply_safe_proposals(
    proposals: list[_Proposal], aliases: AliasMap
) -> tuple[int, int]:
    """Add safe proposals to the alias map. Returns (groups, entries) added."""
    groups = 0
    entries = 0
    for prop in proposals:
        if not prop.safe or not prop.canonical:
            continue
        groups += 1
        for variant in prop.variants:
            aliases.add(variant, prop.canonical)
            entries += 1
        # Also add canonical → canonical so to_slug returns it for the
        # canonical form too (otherwise the upstream raw name "Benji
        # Schwimmer" wouldn't resolve to anything until first ingest).
        aliases.add(prop.canonical, prop.canonical)
        entries += 1
    return groups, entries


def _write_review_file(
    proposals: list[_Proposal], path: Path, *, generated_at: dt.datetime
) -> int:
    """Write ambiguous proposals to a human-readable markdown file. Returns count."""
    ambiguous = [p for p in proposals if not p.safe]
    if not ambiguous:
        # Leave a marker file so a previous review file gets cleared if
        # the ambiguity was resolved upstream.
        path.write_text(
            f"# Alias review queue\n\n"
            f"_Generated {generated_at.isoformat()} — no ambiguities detected._\n"
        )
        return 0

    lines: list[str] = []
    lines.append("# Alias review queue")
    lines.append("")
    lines.append(f"_Generated {generated_at.isoformat()}._")
    lines.append("")
    lines.append(
        "Each block below is a group of instructor slugs the bootstrap "
        "heuristic could NOT confidently merge. Pick a canonical slug "
        "for each group (or decide they're genuinely different people) "
        "and add the mappings to `_aliases.yaml`."
    )
    lines.append("")
    for i, prop in enumerate(ambiguous, start=1):
        lines.append(f"## {i}. Variants: {', '.join(prop.variants)}")
        lines.append("")
        lines.append(f"- Reason: {prop.reason}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n")
    return len(ambiguous)


@_dc.dataclass
class BootstrapResult:
    """Summary of what the bootstrap pass did. Returned for logging."""

    instructor_pages_scanned: int
    safe_merge_groups: int
    safe_alias_entries_added: int
    ambiguous_groups: int
    aliases_path: Path
    review_path: Path


def bootstrap_aliases(wiki_repo_path: Path) -> BootstrapResult:
    """Scan instructors/, write safe merges to _aliases.yaml, ambiguous to review file.

    Idempotent: re-running over a wiki whose duplicates have already
    been collapsed produces no new mappings and a no-ambiguity review
    file. Caller is responsible for committing the result.
    """
    pages = _load_instructor_pages(wiki_repo_path)
    proposals = generate_proposals(pages)
    aliases = AliasMap.load(wiki_repo_path)
    safe_groups, safe_entries = _apply_safe_proposals(proposals, aliases)
    aliases.save()

    review_path = wiki_repo_path / _INSTRUCTORS_DIR_NAME / _REVIEW_FILE_NAME
    ambiguous_count = _write_review_file(
        proposals, review_path, generated_at=dt.datetime.now(dt.UTC)
    )

    aliases_path = wiki_repo_path / _INSTRUCTORS_DIR_NAME / ALIAS_FILE_NAME
    return BootstrapResult(
        instructor_pages_scanned=len(pages),
        safe_merge_groups=safe_groups,
        safe_alias_entries_added=safe_entries,
        ambiguous_groups=ambiguous_count,
        aliases_path=aliases_path,
        review_path=review_path,
    )
