"""Core data types passed between the scraper and the exporters.

Everything that isn't a `Video` or a `Playlist` is exporter-specific and lives
in the exporter module. Keep this file boring on purpose: it's the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Video:
    """A single video as it came out of the scraper.

    Frozen because exporters shouldn't mutate inputs, and slots because we
    sometimes hold thousands of these in memory for long playlists.

    `video_id` is the canonical 11-char YouTube id. It's the stable identity
    across re-runs — title and channel can change, the id doesn't. Exporters
    should key their idempotency on this, never on the title.
    """

    video_id: str
    title: str
    url: str
    channel: str
    duration_seconds: int | None  # None = live/upcoming/unavailable
    position: int                  # 1-indexed, matches the playlist order
    thumbnail_url: str | None = None

    def duration_hms(self) -> str:
        """'1:02:15' or '12:34' or '—' for unknown."""
        if self.duration_seconds is None:
            return "—"
        h, rem = divmod(self.duration_seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass(frozen=True, slots=True)
class Playlist:
    title: str
    url: str
    videos: tuple[Video, ...]
    channel: str | None = None
    scraped_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __len__(self) -> int:
        return len(self.videos)

    def total_seconds(self) -> int:
        return sum(v.duration_seconds or 0 for v in self.videos)


@runtime_checkable
class Exporter(Protocol):
    """Every exporter implements this. That's the whole plugin interface."""

    name: str  # used as the --to argument, e.g. "notion-api"

    def export(self, playlist: Playlist) -> ExportResult:  # noqa: F821
        ...


@dataclass(frozen=True, slots=True)
class ExportResult:
    """What an exporter tells the CLI after it finishes.

    `artifact` is whatever makes sense for the target: a file path for Anki
    and Obsidian, a URL for Notion, a string for clipboard. The CLI formats
    it for the user; it doesn't interpret it.
    """

    exporter: str
    artifact: str
    items_written: int
    notes: tuple[str, ...] = ()  # human-readable extras, e.g. "3 duplicates skipped"
