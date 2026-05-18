"""Name and vocabulary canonicalization.

Three alias maps share this implementation:

  - ``instructors/_aliases.yaml`` — upstream instructor/student names
    (``Kate``, ``Kate B``, ``Kate Benson``) → canonical instructor slugs.
  - ``concepts/_aliases.yaml`` — concept variant slugs (``anchor``,
    ``anchor-step``, ``anchoring-action``) → canonical concept slugs.
  - ``techniques/_aliases.yaml`` — technique variant slugs (``whip``,
    ``basic-whip``) → canonical technique slugs.

The instructor map is populated automatically as new names appear (the
upstream parser is the authority on identity). The concept/technique
maps are populated manually by Kaiano — vocabulary collapse is a
judgment call per CLAUDE.md "Vocabulary handling".

The map file format is identical across all three: a flat YAML mapping
of normalized variant → canonical slug.
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
    def load(
        cls,
        wiki_repo_path: Path,
        *,
        relative_dir: str = "instructors",
    ) -> AliasMap:
        """Load an alias map from ``<wiki_repo_path>/<relative_dir>/_aliases.yaml``.

        Defaults to ``instructors/_aliases.yaml`` for backward
        compatibility with the original single-map design. Pass
        ``relative_dir="concepts"`` or ``"techniques"`` to load the
        vocabulary maps.
        """
        path = wiki_repo_path / relative_dir / ALIAS_FILE_NAME
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
