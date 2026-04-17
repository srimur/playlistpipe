"""Obsidian exporter.

Writes one markdown file per video into a subfolder of the user's vault, plus
an index note with a Dataview-compatible table. Frontmatter uses the fields
Dataview and Obsidian plugins recognize out of the box (title, url, tags,
date, watched).

We don't overwrite files that already exist — if the user edited their notes,
we respect that. Re-running adds new videos and updates the index. Behavior
was chosen over "clobber and sync" because this is the contract every
Obsidian user expects and breaking it loses the user forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..core.models import ExportResult, Playlist, Video
from ..core.utils import resolve_within, safe_filename

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ObsidianConfig:
    vault_path: Path
    subfolder: str = "YouTube"  # relative to vault root

    def target_dir(self, playlist_title: str) -> Path:
        # vault / subfolder / playlist-title
        subfolder = safe_filename(self.subfolder, default="YouTube")
        playlist_dir = safe_filename(playlist_title)
        # Two-step resolve so both path components are individually validated
        base = resolve_within(self.vault_path, subfolder)
        return resolve_within(base, playlist_dir)


class ObsidianExporter:
    name = "obsidian"

    def __init__(self, config: ObsidianConfig):
        self._cfg = config

    def export(self, playlist: Playlist) -> ExportResult:
        target = self._cfg.target_dir(playlist.title)
        target.mkdir(parents=True, exist_ok=True)

        created = 0
        skipped = 0
        for v in playlist.videos:
            path = target / f"{_note_filename(v)}.md"
            if path.exists():
                skipped += 1
                continue
            path.write_text(_render_video_note(v, playlist), encoding="utf-8")
            created += 1

        # Always rewrite the index — it's generated, never hand-edited.
        index = target / "_index.md"
        index.write_text(_render_index(playlist), encoding="utf-8")

        notes: list[str] = [f"{created} new notes"]
        if skipped:
            notes.append(f"{skipped} existing notes preserved")
        return ExportResult(
            exporter=self.name,
            artifact=str(target),
            items_written=created,
            notes=tuple(notes),
        )


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _note_filename(v: Video) -> str:
    """`001 - Title` so files sort by playlist order in the file explorer."""
    prefix = f"{v.position:03d}"
    # We use the video_id as a disambiguator in the filename so two videos
    # with the same title don't collide. 8 chars of the id is plenty.
    slug = safe_filename(v.title, default=v.video_id)
    return f"{prefix} - {slug} [{v.video_id}]"


def _render_video_note(v: Video, playlist: Playlist) -> str:
    # YAML frontmatter. Quote everything — video titles contain colons.
    fm = [
        "---",
        f'title: {_yaml_quote(v.title)}',
        f'url: "{v.url}"',
        f'channel: {_yaml_quote(v.channel)}',
        f'video_id: "{v.video_id}"',
        f"duration: {v.duration_seconds if v.duration_seconds is not None else '~'}",
        f"position: {v.position}",
        f'playlist: {_yaml_quote(playlist.title)}',
        f'playlist_url: "{playlist.url}"',
        f"added: {playlist.scraped_at.date().isoformat()}",
        "watched: false",
        "tags: [youtube]",
        "---",
        "",
        f"# {v.title}",
        "",
        f"**Channel:** {v.channel}  ",
        f"**Duration:** `{v.duration_hms()}`  ",
        f"**Link:** {v.url}",
        "",
        "## Notes",
        "",
        "",
    ]
    return "\n".join(fm)


def _render_index(playlist: Playlist) -> str:
    lines = [
        "---",
        f'playlist: {_yaml_quote(playlist.title)}',
        f'url: "{playlist.url}"',
        f"videos: {len(playlist)}",
        f"updated: {playlist.scraped_at.date().isoformat()}",
        "tags: [youtube, index]",
        "---",
        "",
        f"# {playlist.title}",
        "",
        f"{len(playlist)} videos · [source]({playlist.url})",
        "",
        "## Progress",
        "",
        "```dataview",
        "TABLE channel, duration, watched",
        'FROM "" ',
        f'WHERE playlist = {_yaml_quote(playlist.title)}',
        "SORT position ASC",
        "```",
        "",
        "## Videos",
        "",
    ]
    for v in playlist.videos:
        name = _note_filename(v)
        lines.append(f"- [ ] [[{name}|{v.position:03d}. {v.title}]] · `{v.duration_hms()}`")
    lines.append("")
    return "\n".join(lines)


def _yaml_quote(s: str) -> str:
    """Produce a YAML-safe double-quoted string.

    We escape backslashes and double-quotes; that's sufficient for YAML 1.2's
    double-quoted scalar grammar, which is what Obsidian uses.
    """
    if s is None:
        return '""'
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
