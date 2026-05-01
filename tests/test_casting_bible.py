"""Tests for parallax_engine.casting.bible (SPEC.md §11.7).

Covers:
- TestReadCastingEntry:   read/None for known + unknown ids
- TestWriteEntries:       batch write and reload round-trip
- TestAppendCanonicalSvg: atomic write, append-only guard, idempotency
- TestHashVerification:   hash_verify_canonical pass/fail + drift detection
- TestLifecycle:          full §11.7.1 lifecycle (steps 1-6)
- TestEdgeCases:          empty bible, missing file, malformed YAML
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from parallax_engine.casting.bible import CastingBible, load_casting_bible
from parallax_engine.director.schema import CastingEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    cast_id: str,
    kind: str = "motif",
    first_appearance_scene: int = 1,
    canonical_svg: str | None = None,
) -> CastingEntry:
    """Construct a minimal valid CastingEntry."""
    return CastingEntry(
        id=cast_id,
        kind=kind,
        canonical_description=(
            "A small crimson bird with a sharp beak and rounded wings. "
            "Its plumage is uniformly #c84032 with black accents on the wing-tips. "
            "It perches on branches and power lines. "
            "Scale: roughly 15% of frame height."
        ),
        role_in_story="Recurring motif representing longing and freedom.",
        first_appearance_scene=first_appearance_scene,
        canonical_svg=canonical_svg,
    )


def _write_svg(path: Path, content: str = "<svg xmlns='http://www.w3.org/2000/svg'/>") -> Path:
    """Write a minimal SVG to *path* and return *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# TestReadCastingEntry
# ---------------------------------------------------------------------------


class TestReadCastingEntry:
    """read_casting_entry returns None for unknown ids without crashing."""

    def test_unknown_id_returns_none(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        result = bible.read_casting_entry("does_not_exist")
        assert result is None

    def test_unknown_id_on_empty_file(self, tmp_path: Path) -> None:
        casting_path = tmp_path / "casting.yaml"
        casting_path.write_text("casting: []\n", encoding="utf-8")
        bible = CastingBible(casting_path)
        result = bible.read_casting_entry("ghost")
        assert result is None

    def test_unknown_id_when_file_absent(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "nonexistent" / "casting.yaml")
        result = bible.read_casting_entry("anything")
        assert result is None

    def test_known_id_returns_entry(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        entry = _make_entry("red_bird")
        bible.write_entries([entry])
        result = bible.read_casting_entry("red_bird")
        assert result is not None
        assert result.id == "red_bird"
        assert result.kind == "motif"

    def test_known_id_among_multiple(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        entries = [
            _make_entry("red_bird", first_appearance_scene=1),
            _make_entry("silver_clock", kind="prop", first_appearance_scene=2),
            _make_entry("the_bridge", kind="environment_element", first_appearance_scene=3),
        ]
        bible.write_entries(entries)

        result = bible.read_casting_entry("silver_clock")
        assert result is not None
        assert result.id == "silver_clock"
        assert result.kind == "prop"

    def test_read_returns_none_after_checking_all_entries(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("alpha"), _make_entry("beta")])
        assert bible.read_casting_entry("gamma") is None


# ---------------------------------------------------------------------------
# TestWriteEntries
# ---------------------------------------------------------------------------


class TestWriteEntries:
    """write_entries performs atomic initial write; round-trips cleanly."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        casting_path = tmp_path / "casting.yaml"
        bible = CastingBible(casting_path)
        bible.write_entries([_make_entry("red_bird")])
        assert casting_path.exists()

    def test_written_file_is_valid_yaml(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        data = yaml.safe_load((tmp_path / "casting.yaml").read_text())
        assert "casting" in data
        assert isinstance(data["casting"], list)
        assert len(data["casting"]) == 1

    def test_round_trip(self, tmp_path: Path) -> None:
        orig = [
            _make_entry("red_bird"),
            _make_entry("silver_clock", kind="prop", first_appearance_scene=2),
        ]
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries(orig)
        reloaded = bible.all_entries()
        assert len(reloaded) == 2
        ids = {e.id for e in reloaded}
        assert ids == {"red_bird", "silver_clock"}

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "workspace" / "subdir" / "casting.yaml"
        bible = CastingBible(nested)
        bible.write_entries([_make_entry("x")])
        assert nested.exists()

    def test_entries_sorted_by_id_in_file(self, tmp_path: Path) -> None:
        """write_entries sorts entries by id for determinism."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("zebra"),
            _make_entry("alpha"),
            _make_entry("mango"),
        ])
        data = yaml.safe_load((tmp_path / "casting.yaml").read_text())
        ids = [e["id"] for e in data["casting"]]
        assert ids == ["alpha", "mango", "zebra"]

    def test_overwrite_replaces_all_entries(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("original")])
        bible.write_entries([_make_entry("replacement")])
        ids = bible.entry_ids()
        assert ids == ["replacement"]


# ---------------------------------------------------------------------------
# TestAppendCanonicalSvg
# ---------------------------------------------------------------------------


class TestAppendCanonicalSvg:
    """append_canonical_svg is atomic and append-only."""

    def test_sets_canonical_svg(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])

        svg = _write_svg(tmp_path / "assets" / "canon" / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        entry = bible.read_casting_entry("red_bird")
        assert entry is not None
        assert entry.canonical_svg == str(svg)

    def test_creates_hash_sidecar(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        assert bible.hash_path.exists()
        hashes = json.loads(bible.hash_path.read_text())
        assert "red_bird" in hashes
        assert len(hashes["red_bird"]) == 64  # SHA-256 hex

    def test_idempotent_same_path(self, tmp_path: Path) -> None:
        """Calling append_canonical_svg twice with the same path is a no-op."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)
        bible.append_canonical_svg("red_bird", svg)  # must not raise

        entry = bible.read_casting_entry("red_bird")
        assert entry is not None
        assert entry.canonical_svg == str(svg)

    def test_raises_on_different_path(self, tmp_path: Path) -> None:
        """Cannot overwrite an existing canonical_svg with a different path."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg1 = _write_svg(tmp_path / "red_bird_v1.svg")
        svg2 = _write_svg(tmp_path / "red_bird_v2.svg", content="<svg/>")
        bible.append_canonical_svg("red_bird", svg1)
        with pytest.raises(RuntimeError, match="already set"):
            bible.append_canonical_svg("red_bird", svg2)

    def test_raises_for_unknown_id(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "ghost.svg")
        with pytest.raises(KeyError, match="ghost"):
            bible.append_canonical_svg("ghost", svg)

    def test_raises_if_svg_missing(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        with pytest.raises(FileNotFoundError):
            bible.append_canonical_svg("red_bird", tmp_path / "missing.svg")

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path: Path) -> None:
        """After a successful write, no .tmp files should remain."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"

    def test_write_is_valid_yaml_after_append(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        data = yaml.safe_load((tmp_path / "casting.yaml").read_text())
        assert data["casting"][0]["canonical_svg"] == str(svg)

    def test_other_entries_untouched_after_append(self, tmp_path: Path) -> None:
        """append_canonical_svg must not disturb other entries."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("red_bird"),
            _make_entry("silver_clock", kind="prop", first_appearance_scene=2),
        ])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        clock = bible.read_casting_entry("silver_clock")
        assert clock is not None
        assert clock.canonical_svg is None

    def test_multiple_entries_can_be_set(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("alpha"),
            _make_entry("beta", kind="prop", first_appearance_scene=2),
        ])
        svg_a = _write_svg(tmp_path / "alpha.svg", "<svg id='a'/>")
        svg_b = _write_svg(tmp_path / "beta.svg", "<svg id='b'/>")
        bible.append_canonical_svg("alpha", svg_a)
        bible.append_canonical_svg("beta", svg_b)

        assert bible.read_casting_entry("alpha").canonical_svg == str(svg_a)
        assert bible.read_casting_entry("beta").canonical_svg == str(svg_b)


# ---------------------------------------------------------------------------
# TestHashVerification
# ---------------------------------------------------------------------------


class TestHashVerification:
    """hash_verify_canonical compares SHA-256 against stored hash."""

    def test_verify_returns_true_for_unchanged_file(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg", "<svg>original</svg>")
        bible.append_canonical_svg("red_bird", svg)

        assert bible.hash_verify_canonical("red_bird", svg) is True

    def test_verify_returns_false_for_unknown_id(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        assert bible.hash_verify_canonical("ghost", tmp_path / "ghost.svg") is False

    def test_verify_returns_false_if_no_hash_stored(self, tmp_path: Path) -> None:
        """If append_canonical_svg was never called, no hash is stored."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        # Do NOT call append_canonical_svg — no hash stored.
        assert bible.hash_verify_canonical("red_bird", svg) is False

    def test_verify_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)
        svg.unlink()
        assert bible.hash_verify_canonical("red_bird", svg) is False

    def test_verify_detects_drift(self, tmp_path: Path) -> None:
        """If the SVG is modified after registration, hash_verify returns False."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg", "<svg>original</svg>")
        bible.append_canonical_svg("red_bird", svg)

        # Simulate canonical asset drift (e.g. accidental overwrite).
        svg.write_text("<svg>TAMPERED</svg>", encoding="utf-8")

        assert bible.hash_verify_canonical("red_bird", svg) is False

    def test_verify_without_explicit_path_uses_stored_path(self, tmp_path: Path) -> None:
        """hash_verify_canonical(id) without svg_path uses canonical_svg from entry."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        svg = _write_svg(tmp_path / "red_bird.svg")
        bible.append_canonical_svg("red_bird", svg)

        # No svg_path argument — should resolve from the entry.
        assert bible.hash_verify_canonical("red_bird") is True

    def test_verify_returns_false_when_no_canonical_svg_set(self, tmp_path: Path) -> None:
        """Entry exists but canonical_svg is null → False."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("red_bird")])
        assert bible.hash_verify_canonical("red_bird") is False


# ---------------------------------------------------------------------------
# TestLifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Full §11.7.1 lifecycle: steps 1-6."""

    def test_full_lifecycle(self, tmp_path: Path) -> None:
        """Simulate the complete cast member lifecycle per §11.7.1.

        Step 1: Director writes the entry (canonical_svg = null).
        Step 2: First scene-designer reserves path in manifest (not modelled here;
                the reservation is just noting the expected path).
        Step 3: Asset-generator produces the SVG (simulated by writing a file).
        Step 4: Scene-designer calls append_canonical_svg to record path + hash.
        Step 5: Every subsequent scene calls read_casting_entry and reuses path.
        Step 6: A variant is produced (separate file); hash_verify_canonical
                confirms the canonical is unchanged.
        """
        bible = CastingBible(tmp_path / "casting.yaml")

        # Step 1: director writes entry.
        entry = _make_entry("red_bird", first_appearance_scene=1)
        assert entry.canonical_svg is None
        bible.write_entries([entry])

        # Verify null canonical at this point.
        loaded = bible.read_casting_entry("red_bird")
        assert loaded is not None
        assert loaded.canonical_svg is None

        # Step 2: Scene-designer knows the expected path.
        canon_dir = tmp_path / "assets" / "canon"
        expected_path = canon_dir / "red_bird.svg"

        # Step 3: Asset-generator produces the SVG.
        _write_svg(expected_path, "<svg>canonical red bird</svg>")

        # Step 4: Scene-designer records the path.
        bible.append_canonical_svg("red_bird", expected_path)

        # Verify path is now set.
        updated = bible.read_casting_entry("red_bird")
        assert updated is not None
        assert updated.canonical_svg == str(expected_path)

        # Step 5: Subsequent scene reads the path.
        scene2_entry = bible.read_casting_entry("red_bird")
        assert scene2_entry is not None
        assert scene2_entry.canonical_svg == str(expected_path)

        # Simulate scene 3 also reading it.
        scene3_entry = bible.read_casting_entry("red_bird")
        assert scene3_entry is not None
        assert scene3_entry.canonical_svg == str(expected_path)

        # Step 6: A variant is produced (different file; canonical unchanged).
        variant_path = canon_dir / "red_bird_scene3_shadow.svg"
        _write_svg(variant_path, "<svg>variant: smaller + shadow</svg>")

        # The canonical is still intact.
        assert bible.hash_verify_canonical("red_bird", expected_path) is True

        # Simulate accidental canonical drift.
        expected_path.write_text("<svg>ACCIDENTALLY MODIFIED</svg>", encoding="utf-8")
        assert bible.hash_verify_canonical("red_bird", expected_path) is False

    def test_lifecycle_multiple_cast_members(self, tmp_path: Path) -> None:
        """Multiple cast members go through the lifecycle independently."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("red_bird", first_appearance_scene=1),
            _make_entry("silver_clock", kind="prop", first_appearance_scene=2),
            _make_entry("stone_arch", kind="environment_element", first_appearance_scene=1),
        ])

        # Asset-generator produces SVGs in sequence.
        bird_svg = _write_svg(tmp_path / "red_bird.svg", "<svg>bird</svg>")
        clock_svg = _write_svg(tmp_path / "silver_clock.svg", "<svg>clock</svg>")
        arch_svg = _write_svg(tmp_path / "stone_arch.svg", "<svg>arch</svg>")

        bible.append_canonical_svg("red_bird", bird_svg)
        bible.append_canonical_svg("silver_clock", clock_svg)
        bible.append_canonical_svg("stone_arch", arch_svg)

        # All three are now set.
        for cast_id, svg_path in [
            ("red_bird", bird_svg),
            ("silver_clock", clock_svg),
            ("stone_arch", arch_svg),
        ]:
            entry = bible.read_casting_entry(cast_id)
            assert entry is not None
            assert entry.canonical_svg == str(svg_path)
            assert bible.hash_verify_canonical(cast_id, svg_path) is True

    def test_lifecycle_first_appearance_ordering(self, tmp_path: Path) -> None:
        """first_appearance_scene is preserved through the lifecycle."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("early_motif", first_appearance_scene=1),
            _make_entry("late_motif", first_appearance_scene=5),
        ])
        svg = _write_svg(tmp_path / "early_motif.svg")
        bible.append_canonical_svg("early_motif", svg)

        entry = bible.read_casting_entry("early_motif")
        assert entry.first_appearance_scene == 1
        late = bible.read_casting_entry("late_motif")
        assert late.first_appearance_scene == 5


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: empty bible, missing files, helper API surface."""

    def test_empty_bible_all_entries_returns_empty(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([])
        assert bible.all_entries() == []

    def test_entry_ids_sorted(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([
            _make_entry("zebra"),
            _make_entry("alpha"),
        ])
        assert bible.entry_ids() == ["alpha", "zebra"]

    def test_exists_false_before_write(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        assert bible.exists() is False

    def test_exists_true_after_write(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([])
        assert bible.exists() is True

    def test_repr_contains_path(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "casting.yaml")
        assert "casting.yaml" in repr(bible)

    def test_load_casting_bible_factory(self, tmp_path: Path) -> None:
        bible = load_casting_bible(tmp_path)
        assert bible.path == tmp_path / "casting.yaml"

    def test_hash_sidecar_name_derived_from_yaml_stem(self, tmp_path: Path) -> None:
        bible = CastingBible(tmp_path / "my_casting.yaml")
        expected = tmp_path / "my_casting.sha256.json"
        assert bible.hash_path == expected

    def test_flat_list_yaml_format_accepted(self, tmp_path: Path) -> None:
        """Some callers may write casting.yaml as a bare list (no 'casting' key)."""
        casting_path = tmp_path / "casting.yaml"
        data = [
            {
                "id": "red_bird",
                "kind": "motif",
                "canonical_description": "A red bird. It flies. It perches. It sings.",
                "role_in_story": "Motif for freedom.",
                "first_appearance_scene": 1,
                "canonical_svg": None,
            }
        ]
        casting_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        bible = CastingBible(casting_path)
        entry = bible.read_casting_entry("red_bird")
        assert entry is not None
        assert entry.id == "red_bird"

    def test_sha256_is_64_char_hex(self, tmp_path: Path) -> None:
        """SHA-256 digests are 64 lowercase hex chars."""
        bible = CastingBible(tmp_path / "casting.yaml")
        bible.write_entries([_make_entry("x")])
        svg = _write_svg(tmp_path / "x.svg", "<svg>test content for sha256</svg>")
        bible.append_canonical_svg("x", svg)
        hashes = json.loads(bible.hash_path.read_text())
        digest = hashes["x"]
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)
