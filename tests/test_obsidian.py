from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from playlistpipe.core.models import Playlist, Video
from playlistpipe.exporters.obsidian import ObsidianConfig, ObsidianExporter


def _v(i, **kw):
    defaults = dict(
        video_id=("vid" + str(i).zfill(8))[:11],
        title=f"Video {i}",
        url=f"https://youtu.be/vid{i:08d}",
        channel="Chan",
        duration_seconds=125,
        position=i,
    )
    defaults.update(kw)
    return Video(**defaults)


def _pl(videos, title="My Playlist"):
    return Playlist(
        title=title,
        url="https://www.youtube.com/playlist?list=x",
        videos=tuple(videos),
        scraped_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_creates_one_file_per_video_plus_index(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out = ObsidianExporter(ObsidianConfig(vault_path=vault)).export(
        _pl([_v(1), _v(2)])
    )
    folder = Path(out.artifact)
    md_files = list(folder.glob("*.md"))
    assert len(md_files) == 3   # 2 videos + _index.md
    assert (folder / "_index.md").exists()


def test_preserves_existing_notes(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    pl = _pl([_v(1, title="Original")])

    # First run
    out1 = ObsidianExporter(ObsidianConfig(vault_path=vault)).export(pl)
    folder = Path(out1.artifact)
    note = next(p for p in folder.glob("*.md") if p.name != "_index.md")
    # Simulate the user editing the note
    custom = note.read_text() + "\n\nMY PERSONAL NOTES HERE"
    note.write_text(custom)

    # Second run
    out2 = ObsidianExporter(ObsidianConfig(vault_path=vault)).export(pl)
    assert "MY PERSONAL NOTES HERE" in note.read_text()
    assert "1 existing notes preserved" in out2.notes[1] if len(out2.notes) > 1 else any(
        "preserved" in n for n in out2.notes
    )


def test_yaml_frontmatter_escapes_quotes(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    tricky_title = 'He said "hello" and \\ escaped'
    out = ObsidianExporter(ObsidianConfig(vault_path=vault)).export(
        _pl([_v(1, title=tricky_title)])
    )
    note = next(
        p for p in Path(out.artifact).glob("*.md") if p.name != "_index.md"
    )
    content = note.read_text()
    # The YAML should survive parsing — at minimum, no unescaped quote inside quotes
    import yaml  # dev-only check; skip if not available
    try:
        fm = content.split("---")[1]
        parsed = yaml.safe_load(fm)
        assert parsed["title"] == tricky_title
    except ImportError:
        pytest.skip("pyyaml not installed")


def test_neutralizes_vault_traversal_in_subfolder(tmp_path: Path):
    """A hostile subfolder must not escape the vault. Sanitization is fine
    (the traversal chars get stripped); an actual write outside the vault
    would be a security bug."""
    vault = tmp_path / "vault"
    vault.mkdir()
    out = ObsidianExporter(ObsidianConfig(
        vault_path=vault, subfolder="../../../escaped",
    )).export(_pl([_v(1)]))
    assert Path(out.artifact).resolve().is_relative_to(vault.resolve())
