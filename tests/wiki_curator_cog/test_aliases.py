"""Tests for the alias map."""

from __future__ import annotations

from pathlib import Path

from wiki_curator_cog.aliases import AliasMap, _normalize_key


def test_normalize_key_lowercases_and_hyphenates() -> None:
    assert _normalize_key("Kaiano") == "kaiano"
    assert _normalize_key("Kate B") == "kate-b"
    assert _normalize_key("  KATE   BENSON  ") == "kate-benson"
    assert _normalize_key("Kaiano_Levine") == "kaiano-levine"


def test_normalize_key_strips_punctuation() -> None:
    assert _normalize_key("O'Brien") == "obrien"
    assert _normalize_key("Jean-Pierre!") == "jean-pierre"


def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    am = AliasMap.load(tmp_path)
    assert am.as_dict() == {}


def test_load_normalizes_keys(wiki_repo_path: Path) -> None:
    aliases_file = wiki_repo_path / "instructors" / "_aliases.yaml"
    aliases_file.write_text("Kate: kate\nKate B: kate\n")
    am = AliasMap.load(wiki_repo_path)
    assert am.to_slug("Kate") == "kate"
    assert am.to_slug("kate b") == "kate"
    assert am.to_slug("KATE  B") == "kate"


def test_add_and_save_round_trips(wiki_repo_path: Path) -> None:
    am = AliasMap.load(wiki_repo_path)
    am.add("Kaiano", "kaiano")
    am.add("Kaiano Levine", "kaiano")
    am.save()

    reloaded = AliasMap.load(wiki_repo_path)
    assert reloaded.to_slug("Kaiano") == "kaiano"
    assert reloaded.to_slug("kaiano-levine") == "kaiano"


def test_save_preserves_header_comments(wiki_repo_path: Path) -> None:
    aliases_file = wiki_repo_path / "instructors" / "_aliases.yaml"
    aliases_file.write_text(
        "# This is a header comment.\n# More header.\n\nkate: kate\n"
    )
    am = AliasMap.load(wiki_repo_path)
    am.add("Robert", "robert")
    am.save()

    contents = aliases_file.read_text()
    assert contents.startswith("# This is a header comment.")
    assert "robert: robert" in contents
    assert "kate: kate" in contents
