"""parallax_engine.casting.bible — read/append-only casting API.

Implements SPEC.md §11.7 (The Casting / Continuity System) and §11.14 step 4.

Public API
----------
CastingBible
    The primary interface.  Instantiate with the path to ``casting.yaml``.
    All writes are atomic (write to temp file, then os.replace).

load_casting_bible(workspace_dir)
    Convenience factory: returns CastingBible(workspace_dir / "casting.yaml").

Design invariants
-----------------
- ``casting.yaml`` contains only standard ``CastingEntry`` fields so that
  ``director.schema.CastingEntry`` (which has ``extra="forbid"``) can parse
  it without errors.
- SHA-256 hashes are stored in a sidecar ``casting.sha256.json`` next to
  ``casting.yaml``.  This file is also written atomically.
- ``append_canonical_svg`` is idempotent: calling it twice with the same
  cast_id and the same path is a no-op (hash matches, path already set).
  Calling it with a different path raises ``RuntimeError`` ("already set").
- ``read_casting_entry`` returns ``None`` for unknown ids; it never raises.
- All dict/list serialisation is deterministic: dicts are sorted by key.

Lifecycle (§11.7.1)
-------------------
1. Director writes the full entry (canonical_svg = null).  Use
   ``write_entries()`` for the initial batch write.
2. First scene-designer reserves the path via ``append_canonical_svg``.
3. Asset-generator produces the SVG.
4. Scene-designer calls ``append_canonical_svg`` to record the path + hash.
5. Every subsequent scene reads the path via ``read_casting_entry``.
6. Variants are transformations of the canonical SVG; call
   ``hash_verify_canonical`` to confirm the canonical hasn't drifted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from parallax_engine.director.schema import CastingEntry

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HASH_SUFFIX = ".sha256.json"


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically (write temp → os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        # Clean up temp file on failure, but re-raise the original error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (write temp → os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CastingBible
# ---------------------------------------------------------------------------


class CastingBible:
    """Read/append-only interface to ``casting.yaml`` + ``casting.sha256.json``.

    Parameters
    ----------
    path:
        Absolute or relative path to ``casting.yaml``.  The file need not
        exist yet; ``write_entries()`` creates it.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._hash_path = self._path.parent / (self._path.stem + _HASH_SUFFIX)

    # ------------------------------------------------------------------
    # Raw YAML I/O
    # ------------------------------------------------------------------

    def _load_raw(self) -> list[dict[str, Any]]:
        """Load casting.yaml as a list of raw dicts.  Returns [] if absent."""
        if not self._path.exists():
            return []
        text = self._path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if data is None:
            return []
        if isinstance(data, dict):
            entries = data.get("casting", [])
        elif isinstance(data, list):
            entries = data
        else:
            return []
        return [e for e in entries if isinstance(e, dict)]

    def _save_raw(self, entries: list[dict[str, Any]]) -> None:
        """Write entries to casting.yaml atomically.

        Entries are sorted by id for determinism.
        """
        sorted_entries = sorted(entries, key=lambda e: e.get("id", ""))
        # Use yaml.safe_dump with explicit settings for determinism.
        content = yaml.safe_dump(
            {"casting": sorted_entries},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=True,
            width=120,
        )
        _atomic_write_text(self._path, content)

    # ------------------------------------------------------------------
    # Hash sidecar I/O
    # ------------------------------------------------------------------

    def _load_hashes(self) -> dict[str, str]:
        """Load the hash sidecar.  Returns {} if absent."""
        if not self._hash_path.exists():
            return {}
        try:
            return json.loads(self._hash_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        """Write the hash sidecar atomically.  Keys are sorted for determinism."""
        content = json.dumps(hashes, sort_keys=True, indent=2, ensure_ascii=False)
        _atomic_write_bytes(self._hash_path, content.encode("utf-8"))

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def read_casting_entry(self, cast_id: str) -> CastingEntry | None:
        """Return the ``CastingEntry`` for *cast_id*, or ``None`` if unknown.

        Never raises for unknown ids.  May raise ``ValidationError`` if the
        YAML on disk is structurally malformed (caller's responsibility).
        """
        entries = self._load_raw()
        for raw in entries:
            if raw.get("id") == cast_id:
                try:
                    return CastingEntry.model_validate(raw)
                except ValidationError:
                    # Re-raise for genuine schema violations, but preserve
                    # the context so the caller can diagnose.
                    raise
        return None

    def all_entries(self) -> list[CastingEntry]:
        """Return all casting entries (validated).  Returns [] if file absent."""
        raw_list = self._load_raw()
        result: list[CastingEntry] = []
        for raw in raw_list:
            result.append(CastingEntry.model_validate(raw))
        return result

    def entry_ids(self) -> list[str]:
        """Return sorted list of all cast ids (no validation)."""
        return sorted(e.get("id", "") for e in self._load_raw() if "id" in e)

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def write_entries(self, entries: list[CastingEntry]) -> None:
        """Batch-write (or overwrite) casting.yaml with *entries*.

        This is the initial write performed by the director.  All entries must
        have ``canonical_svg = None``.

        Parameters
        ----------
        entries:
            List of ``CastingEntry`` objects.  Validated Pydantic models only.
        """
        raw = [e.model_dump(mode="python") for e in entries]
        self._save_raw(raw)

    def append_canonical_svg(
        self,
        cast_id: str,
        svg_path: Path | str,
    ) -> None:
        """Record the canonical SVG path for *cast_id* and store its SHA-256.

        This method is **append-only**: once a canonical SVG path is set,
        calling this method again with the *same* path is a no-op; calling it
        with a *different* path raises ``RuntimeError``.

        The write is atomic: casting.yaml and the hash sidecar are each
        written to a temp file and then replaced atomically via ``os.replace``.

        Parameters
        ----------
        cast_id:
            The casting entry id to update.
        svg_path:
            Path to the canonical SVG file.  Must exist (hash is computed now).

        Raises
        ------
        KeyError
            If *cast_id* is not in casting.yaml.
        FileNotFoundError
            If *svg_path* does not exist.
        RuntimeError
            If the entry already has a *different* canonical_svg path.
        """
        svg_path = Path(svg_path)
        if not svg_path.exists():
            raise FileNotFoundError(f"SVG not found: {svg_path}")

        entries = self._load_raw()

        target: dict[str, Any] | None = None
        for e in entries:
            if e.get("id") == cast_id:
                target = e
                break

        if target is None:
            raise KeyError(f"Cast id not found in casting.yaml: {cast_id!r}")

        # Append-only guard: if already set, must match exactly.
        existing = target.get("canonical_svg")
        svg_str = str(svg_path)
        if existing is not None and existing != svg_str:
            raise RuntimeError(
                f"canonical_svg for {cast_id!r} is already set to "
                f"{existing!r}; cannot overwrite with {svg_str!r}. "
                "The casting bible is append-only."
            )

        if existing == svg_str:
            # Already set to the same path; verify hash consistency and return.
            hashes = self._load_hashes()
            if cast_id not in hashes:
                # Hash missing (edge case): recompute and save.
                sha = _sha256_file(svg_path)
                hashes[cast_id] = sha
                self._save_hashes(hashes)
            return

        # Compute SHA-256 before writing (fail fast if file unreadable).
        sha = _sha256_file(svg_path)

        # Update the entry in-place.
        target["canonical_svg"] = svg_str
        self._save_raw(entries)

        # Update the hash sidecar.
        hashes = self._load_hashes()
        hashes[cast_id] = sha
        self._save_hashes(hashes)

    # ------------------------------------------------------------------
    # Hash verification
    # ------------------------------------------------------------------

    def hash_verify_canonical(
        self,
        cast_id: str,
        svg_path: Path | str | None = None,
    ) -> bool:
        """Verify that the canonical SVG's SHA-256 matches the stored hash.

        Parameters
        ----------
        cast_id:
            The cast member to verify.
        svg_path:
            Path to the SVG file to verify.  If ``None``, the path is read
            from the casting entry's ``canonical_svg`` field.

        Returns
        -------
        bool
            ``True`` if the file's current SHA-256 matches the stored hash;
            ``False`` if the cast_id is unknown, the hash is not stored, the
            file is missing, or the hash does not match.
        """
        hashes = self._load_hashes()
        if cast_id not in hashes:
            return False

        if svg_path is None:
            # Read path from casting.yaml.
            entry = self.read_casting_entry(cast_id)
            if entry is None or entry.canonical_svg is None:
                return False
            svg_path = Path(entry.canonical_svg)
        else:
            svg_path = Path(svg_path)

        if not svg_path.exists():
            return False

        current_sha = _sha256_file(svg_path)
        return current_sha == hashes[cast_id]

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Absolute path to casting.yaml."""
        return self._path

    @property
    def hash_path(self) -> Path:
        """Absolute path to the SHA-256 sidecar file."""
        return self._hash_path

    def exists(self) -> bool:
        """Return True if casting.yaml exists."""
        return self._path.exists()

    def __repr__(self) -> str:
        return f"CastingBible({self._path!r})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def load_casting_bible(workspace_dir: Path | str) -> CastingBible:
    """Return a CastingBible for ``workspace_dir/casting.yaml``.

    Parameters
    ----------
    workspace_dir:
        The project workspace root.  The file need not exist yet.
    """
    return CastingBible(Path(workspace_dir) / "casting.yaml")
