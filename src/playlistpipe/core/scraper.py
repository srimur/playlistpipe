"""Playlist scraper.

We delegate the actual scraping to yt-dlp. That choice surprises people when
they first see it, so: the original version of this tool rolled its own HTML
scraping, and it broke roughly every time YouTube shipped a frontend change.
yt-dlp has a team of maintainers whose literal job is keeping up with those
changes. Using it means we can spend our time on the export side, which is
where the user-facing value actually lives.

yt-dlp supports `--flat-playlist` mode which gives us everything we need
(titles, ids, channels, durations) in one request without downloading a
single video. It also handles age-gated playlists, region restrictions,
and the occasional auth dance that kills naive scrapers.

If you want to swap this for a pure-HTTP scraper later, all you have to
preserve is the signature: `scrape_playlist(url: str) -> Playlist`.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import Playlist, Video
from .utils import InvalidURLError, extract_video_id

log = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    """The playlist could not be scraped."""


def scrape_playlist(url: str) -> Playlist:
    """Fetch a YouTube playlist and return it as a `Playlist`.

    Raises `ScraperError` on any unrecoverable failure; individual videos
    that fail to parse are skipped with a warning rather than crashing the
    whole scrape.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    except ImportError as e:
        raise ScraperError(
            "yt-dlp is required. Install it with `pip install yt-dlp`."
        ) from e

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",   # don't resolve individual video pages
        "skip_download": True,
        "ignoreerrors": True,            # skip dead videos, don't abort
    }

    log.info("scraping %s", url)
    try:
        with YoutubeDL(opts) as ydl:
            info: dict[str, Any] | None = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp's exception hierarchy is messy
        raise ScraperError(f"yt-dlp failed: {e}") from e

    if not info:
        raise ScraperError("yt-dlp returned no data")
    if info.get("_type") != "playlist":
        raise ScraperError(
            f"URL is not a playlist (got _type={info.get('_type')!r})"
        )

    entries = info.get("entries") or []
    videos: list[Video] = []
    for position, entry in enumerate(entries, start=1):
        if not entry:
            # ignoreerrors=True leaves None in the list for dead videos
            log.debug("skipping entry at position %d (unavailable)", position)
            continue
        video = _entry_to_video(entry, position)
        if video is not None:
            videos.append(video)

    if not videos:
        raise ScraperError("no videos could be extracted from this playlist")

    return Playlist(
        title=info.get("title") or "Untitled Playlist",
        url=url,
        channel=info.get("uploader") or info.get("channel"),
        videos=tuple(videos),
    )


def _entry_to_video(entry: dict[str, Any], position: int) -> Video | None:
    """Translate one yt-dlp entry into our Video. Returns None if unusable."""
    vid = entry.get("id")
    if not vid:
        log.debug("entry at position %d has no id, skipping", position)
        return None

    # Validate the id shape even though yt-dlp gave it to us — defense in
    # depth, and it catches rare edge cases (channels embedded in playlists).
    url = entry.get("url") or entry.get("webpage_url") or f"https://youtu.be/{vid}"
    try:
        validated_id = extract_video_id(url)
    except InvalidURLError:
        # Some entries have non-URL shapes; trust the id field if it looks right
        if len(vid) == 11 and all(c.isalnum() or c in "-_" for c in vid):
            validated_id = vid
            url = f"https://youtu.be/{vid}"
        else:
            log.debug("entry %s failed id validation, skipping", vid)
            return None

    # Duration comes from yt-dlp as an int (seconds) or None
    raw_dur = entry.get("duration")
    duration = int(raw_dur) if isinstance(raw_dur, (int, float)) else None

    return Video(
        video_id=validated_id,
        title=(entry.get("title") or "").strip() or "(untitled)",
        url=url,
        channel=(entry.get("channel") or entry.get("uploader") or "").strip() or "Unknown",
        duration_seconds=duration,
        position=position,
        thumbnail_url=_pick_thumbnail(entry),
    )


def _pick_thumbnail(entry: dict[str, Any]) -> str | None:
    """Prefer a medium-resolution thumbnail; fall back to the first one."""
    thumbs = entry.get("thumbnails") or []
    if not thumbs:
        return entry.get("thumbnail")
    # yt-dlp orders thumbnails roughly by size. The middle of the list is
    # usually a good balance of quality and filesize.
    mid = thumbs[len(thumbs) // 2]
    return mid.get("url") if isinstance(mid, dict) else None
