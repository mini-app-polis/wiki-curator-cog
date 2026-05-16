"""Instructor/student name canonicalization.

Upstream filename parser stores names case-sensitive and unmodified
("Kaiano", "Kate", "Sarah", etc.). The curator maintains an alias map in
the wiki repo at instructors/_aliases.yaml that collapses variants to
canonical slugs.

This module loads, mutates, and saves that file. The curator calls
to_slug() during ingest to translate upstream names into wiki slugs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ALIAS_FILE_NAME = "_aliases.yaml"


def _normalize_key(raw_name: str) -> str:
    """Normalize an upstream name into a candidate alias-map key.

    Lowercase, strip, replace internal whitespace and underscores with
    hyphens, drop characters that aren't alphanumeric or hyphen.
    """
    s = raw_name.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


class AliasMap:
    """Mutable wrapper around the instructors/_aliases.yaml file.

    Loads on construction. Callers can resolve names, add new entries,
    and save. Not thread-safe — single-instance curator concurrency is
    assumed.
    """

    def __init__(self, mapping: dict[str, str], source_path: Path) -> None:
        self._mapping = dict(mapping)
        self._source_path = source_path

    @classmethod
    def load(cls, wiki_repo_path: Path) -> AliasMap:
        """Load the alias map from instructors/_aliases.yaml in the wiki repo."""
        path = wiki_repo_path / "instructors" / ALIAS_FILE_NAME
        if not path.exists():
            return cls({}, source_path=path)
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"{path} must contain a YAML mapping, got {type(raw).__name__}"
            )
        # Normalize all keys for safety; values are canonical slugs and
        # used verbatim.
        normalized: dict[str, str] = {}
        for k, v in raw.items():
            normalized[_normalize_key(str(k))] = str(v).strip()
        return cls(normalized, source_path=path)

    def to_slug(self, raw_name: str) -> str | None:
        """Return the canonical slug for an upstream name, or None if unknown."""
        return self._mapping.get(_normalize_key(raw_name))

    def add(self, raw_name: str, canonical_slug: str) -> None:
        """Add or update a mapping. Caller responsible for save()."""
        self._mapping[_normalize_key(raw_name)] = canonical_slug.strip()

    def contains(self, raw_name: str) -> bool:
        return _normalize_key(raw_name) in self._mapping

    def save(self) -> None:
        """Persist the alias map back to instructors/_aliases.yaml.

        Writes in sorted-key order for stable diffs. Preserves the header
        comment from the original file if one was present.
        """
        # Preserve any leading comment block if the file exists.
        header = ""
        if self._source_path.exists():
            existing = self._source_path.read_text()
            lines: list[str] = []
            for line in existing.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped == "":
                    lines.append(line)
                else:
                    break
            if lines:
                header = "\n".join(lines).rstrip() + "\n\n"

        body = yaml.safe_dump(
            dict(sorted(self._mapping.items())),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        self._source_path.write_text(header + body)

    def as_dict(self) -> dict[str, str]:
        return dict(self._mapping)
