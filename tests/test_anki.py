"""Anki exporter tests.

We verify the .apkg is produced, is a valid zip, contains an Anki SQLite
collection, and that field values are HTML-escaped. We do NOT test genanki
itself — we test our contract with it.
"""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from playlistpipe.core.models import Playlist, Video
from playlistpipe.exporters.anki import AnkiConfig, AnkiExporter


def _v(i: int, title: str = "Sample", channel: str = "Chan", duration: int | None = 125):
    return Video(
        video_id=("vid" + str(i).zfill(8))[:11],
        title=title,
        url=f"https://youtu.be/vid{i:08d}",
        channel=channel,
        duration_seconds=duration,
        position=i,
    )


def _pl(videos):
    return Playlist(
        title="Deck Test",
        url="https://www.youtube.com/playlist?list=x",
        videos=tuple(videos),
        scraped_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_writes_valid_apkg(tmp_path: Path):
    out = AnkiExporter(AnkiConfig(output_dir=tmp_path)).export(
        _pl([_v(1), _v(2), _v(3)])
    )
    path = Path(out.artifact)
    assert path.exists()
    assert path.suffix == ".apkg"

    # .apkg is a zip
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert any(n in names for n in ("collection.anki2", "collection.anki21"))


def test_html_and_js_in_titles_is_escaped(tmp_path: Path):
    """If a title contains <script>, it must NOT appear un-escaped in the db.

    This is the XSS regression test. Don't delete it.
    """
    evil_title = '<script>alert(1)</script> & "quotes"'
    out = AnkiExporter(AnkiConfig(output_dir=tmp_path)).export(
        _pl([_v(1, title=evil_title)])
    )

    collection_path = _extract_collection(Path(out.artifact), tmp_path)
    fields_blob = _read_first_note_fields(collection_path)

    assert "<script>" not in fields_blob
    assert "&lt;script&gt;" in fields_blob
    assert "&amp;" in fields_blob
    assert "&quot;" in fields_blob


def test_stable_guid_across_runs(tmp_path: Path):
    """Same video id -> same note guid, even if the title changes.

    This is the "re-sync without duplicating" guarantee.
    """
    pl1 = _pl([_v(1, title="Original Title")])
    pl2 = _pl([_v(1, title="Edited Title")])

    r1 = AnkiExporter(AnkiConfig(output_dir=tmp_path / "a")).export(pl1)
    r2 = AnkiExporter(AnkiConfig(output_dir=tmp_path / "b")).export(pl2)

    g1 = _read_first_note_guid(_extract_collection(Path(r1.artifact), tmp_path / "a"))
    g2 = _read_first_note_guid(_extract_collection(Path(r2.artifact), tmp_path / "b"))
    assert g1 == g2


def test_deck_id_deterministic_for_same_name(tmp_path: Path):
    """Same deck name produces the same deck id across runs."""
    pl = _pl([_v(1)])
    r1 = AnkiExporter(AnkiConfig(output_dir=tmp_path / "a")).export(pl)
    r2 = AnkiExporter(AnkiConfig(output_dir=tmp_path / "b")).export(pl)
    # If we got consistent deck ids, both files are the same size-ish.
    # The exact check: inspect the decks table.
    d1 = _read_deck_ids(_extract_collection(Path(r1.artifact), tmp_path / "a"))
    d2 = _read_deck_ids(_extract_collection(Path(r2.artifact), tmp_path / "b"))
    assert d1 == d2


def test_filename_safe_against_traversal(tmp_path: Path):
    pl = Playlist(
        title="../../../etc/passwd",
        url="https://www.youtube.com/playlist?list=x",
        videos=(_v(1),),
    )
    out = AnkiExporter(AnkiConfig(output_dir=tmp_path)).export(pl)
    assert Path(out.artifact).resolve().is_relative_to(tmp_path.resolve())


def test_handles_unknown_duration(tmp_path: Path):
    out = AnkiExporter(AnkiConfig(output_dir=tmp_path)).export(
        _pl([_v(1, duration=None)])
    )
    # Just shouldn't crash; "—" rendered in Duration field
    collection = _extract_collection(Path(out.artifact), tmp_path)
    assert "—" in _read_first_note_fields(collection)


# ---------------------------------------------------------------------------
# helpers: peek into the generated .apkg's SQLite to verify claims
# ---------------------------------------------------------------------------

def _extract_collection(apkg: Path, workdir: Path) -> Path:
    with zipfile.ZipFile(apkg) as zf:
        # Prefer anki21 if present
        for name in ("collection.anki21", "collection.anki2"):
            if name in zf.namelist():
                zf.extract(name, workdir)
                return workdir / name
    raise RuntimeError("no collection found in apkg")


def _read_first_note_fields(collection_path: Path) -> str:
    con = sqlite3.connect(collection_path)
    try:
        row = con.execute("SELECT flds FROM notes LIMIT 1").fetchone()
    finally:
        con.close()
    assert row, "no notes found"
    return row[0]


def _read_first_note_guid(collection_path: Path) -> str:
    con = sqlite3.connect(collection_path)
    try:
        row = con.execute("SELECT guid FROM notes LIMIT 1").fetchone()
    finally:
        con.close()
    assert row
    return row[0]


def _read_deck_ids(collection_path: Path) -> set[int]:
    """Deck ids live in the `col.decks` JSON blob on anki2, or the `decks`
    table on anki21b. Support both by trying the table first."""
    con = sqlite3.connect(collection_path)
    try:
        try:
            rows = con.execute("SELECT id FROM decks").fetchall()
            return {r[0] for r in rows}
        except sqlite3.OperationalError:
            import json
            row = con.execute("SELECT decks FROM col").fetchone()
            return set(int(k) for k in json.loads(row[0]).keys())
    finally:
        con.close()
