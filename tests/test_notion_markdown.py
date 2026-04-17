from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from playlistpipe.core.models import Playlist, Video
from playlistpipe.exporters.notion_markdown import NotionMarkdownExporter


def _playlist(videos=None):
    return Playlist(
        title="Test Playlist",
        url="https://www.youtube.com/playlist?list=ABC",
        channel="Test Channel",
        videos=tuple(videos or []),
        scraped_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )


def _video(i: int, title: str = "Title", duration: int | None = 125):
    return Video(
        video_id=f"vidId{i:05d}X"[:11],
        title=title,
        url=f"https://youtu.be/vidId{i:05d}X"[:30],
        channel="Chan",
        duration_seconds=duration,
        position=i,
    )


def test_renders_checklist(tmp_path: Path):
    pl = _playlist([_video(1), _video(2)])
    out = NotionMarkdownExporter(output_dir=tmp_path).export(pl)
    content = Path(out.artifact).read_text()
    assert content.count("- [ ]") == 2
    assert out.items_written == 2


def test_escapes_markdown_syntax(tmp_path: Path):
    pl = _playlist([_video(1, title="Weird [title] *with* `syntax`")])
    out = NotionMarkdownExporter(output_dir=tmp_path).export(pl)
    content = Path(out.artifact).read_text()
    # Square brackets must be escaped so they don't look like a link
    assert "\\[title\\]" in content
    assert "\\*with\\*" in content
    assert "\\`syntax\\`" in content


def test_handles_empty_playlist(tmp_path: Path):
    out = NotionMarkdownExporter(output_dir=tmp_path).export(_playlist([]))
    content = Path(out.artifact).read_text()
    assert "- [ ]" not in content
    assert out.items_written == 0


def test_totals_unknown_durations_as_zero(tmp_path: Path):
    pl = _playlist([_video(1, duration=60), _video(2, duration=None)])
    out = NotionMarkdownExporter(output_dir=tmp_path).export(pl)
    content = Path(out.artifact).read_text()
    assert "0h 1m" in content or "~0h 1m" in content


def test_filename_is_safe_for_weird_titles(tmp_path: Path):
    pl = Playlist(
        title="../../etc/passwd",
        url="https://www.youtube.com/playlist?list=X",
        videos=(_video(1),),
    )
    out = NotionMarkdownExporter(output_dir=tmp_path).export(pl)
    # The output file must be inside tmp_path
    assert Path(out.artifact).resolve().is_relative_to(tmp_path.resolve())
