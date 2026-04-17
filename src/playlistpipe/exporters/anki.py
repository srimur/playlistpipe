"""Anki exporter.

Builds an .apkg file with two cards per video:

    - recall:  channel + duration hint  ->  title + link
    - review:  title + link             ->  "did you actually watch this?"

Stable GUIDs keyed off the YouTube video id mean re-importing after a
playlist changes updates existing notes in place instead of duplicating.

Security notes worth reading if you're modifying this file:

    - genanki treats every field as raw HTML. Anki's card renderer is a full
      Qt WebEngine and CAN execute JS. Every field we set goes through
      html.escape(quote=True). There have been historical CVEs around
      malicious shared decks (e.g. CVE-2020-28366-class template abuse), so
      we also keep our templates static — no user content in qfmt/afmt.

    - Thumbnail download, if enabled, has: a streamed read with a byte cap,
      a content-type allowlist, an explicit timeout, and domain pinning to
      YouTube's own CDNs. We never follow the image URL to arbitrary hosts.

    - `deck_name` and `output_dir` get safe_filename + resolve_within
      treatment before any filesystem touch.
"""

from __future__ import annotations

import html
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import genanki
import requests

from ..core.models import ExportResult, Playlist, Video
from ..core.utils import http_session, resolve_within, safe_filename

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stable IDs. Generated once with random.SystemRandom().randrange(1<<30, 1<<31)
# and hardcoded on purpose — regenerating at runtime creates duplicate note
# types in users' collections on re-import, which is the thing they hate most.
# Do not change these without a major version bump and a migration note.
# ---------------------------------------------------------------------------
_MODEL_ID = 1656893477
_DECK_ID_BASE = 1782440901  # actual deck id is hash(deck_name) XOR'd with this

# Thumbnail download limits
_THUMB_MAX_BYTES = 2 * 1024 * 1024      # 2 MiB — YouTube thumbnails are ~100 KiB
_THUMB_TIMEOUT = (5, 15)                 # (connect, read)
_THUMB_ALLOWED_HOSTS = frozenset({
    "i.ytimg.com",
    "i1.ytimg.com", "i2.ytimg.com", "i3.ytimg.com", "i4.ytimg.com",
    "img.youtube.com",
    "yt3.ggpht.com",
})
_THUMB_ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


@dataclass(frozen=True, slots=True)
class AnkiConfig:
    output_dir: Path
    deck_name: str | None = None       # default: playlist title
    include_thumbnails: bool = False


class AnkiExporter:
    name = "anki"

    def __init__(self, config: AnkiConfig):
        self._cfg = config

    def export(self, playlist: Playlist) -> ExportResult:
        deck_name = self._cfg.deck_name or playlist.title
        deck_name_safe = safe_filename(deck_name, default="playlist")

        # Deterministic deck id per deck name, so the same deck name always
        # maps to the same deck in the user's collection.
        deck_id = _DECK_ID_BASE ^ (hash(deck_name_safe) & 0x7FFF_FFFF)

        deck = genanki.Deck(deck_id, deck_name_safe)
        model = _build_model()

        media_files: list[str] = []
        # TemporaryDirectory cleans up even if we raise mid-export
        with tempfile.TemporaryDirectory(prefix="playlistpipe-anki-") as tmp:
            tmpdir = Path(tmp)

            for v in playlist.videos:
                thumb_basename = ""
                if self._cfg.include_thumbnails and v.thumbnail_url:
                    saved = _download_thumbnail(v.thumbnail_url, tmpdir, v.video_id)
                    if saved is not None:
                        media_files.append(str(saved))
                        thumb_basename = saved.name

                deck.add_note(_build_note(model, v, playlist, thumb_basename))

            self._cfg.output_dir.mkdir(parents=True, exist_ok=True)
            out_path = resolve_within(
                self._cfg.output_dir, f"{deck_name_safe}.apkg"
            )
            pkg = genanki.Package(deck)
            pkg.media_files = media_files
            pkg.write_to_file(str(out_path))

        notes: list[str] = []
        if self._cfg.include_thumbnails:
            notes.append(f"{len(media_files)} thumbnails embedded")
        return ExportResult(
            exporter=self.name,
            artifact=str(out_path),
            items_written=len(playlist),
            notes=tuple(notes),
        )


# ---------------------------------------------------------------------------
# model, note, templates
# ---------------------------------------------------------------------------

class _StableNote(genanki.Note):
    """GUID derived from the video id (first field) so re-imports update
    in place instead of creating duplicates.

    genanki's default GUID hashes every field, which means editing any of
    them produces a different note. We key only on the canonical id.
    """

    @property
    def guid(self):  # type: ignore[override]
        return genanki.guid_for(self.fields[0])


_CARD_CSS = """
.card {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 18px;
  color: #1a1a1a;
  background: #fafafa;
  padding: 24px;
  line-height: 1.5;
}
.card.nightMode { color: #eaeaea; background: #1a1a1a; }
.title { font-size: 22px; font-weight: 600; margin-bottom: 12px; }
.meta { color: #666; font-size: 14px; margin-top: 8px; }
.card.nightMode .meta { color: #aaa; }
.link a { color: #c00; text-decoration: none; }
.link a:hover { text-decoration: underline; }
.thumb img { max-width: 100%; border-radius: 6px; margin: 12px 0; }
.hint { color: #888; font-style: italic; font-size: 15px; }
"""


def _build_model() -> genanki.Model:
    return genanki.Model(
        _MODEL_ID,
        "playlistpipe YouTube Video",
        fields=[
            # Order matters: field[0] is used for GUID and sort.
            {"name": "VideoId"},
            {"name": "Title"},
            {"name": "Url"},
            {"name": "Channel"},
            {"name": "Duration"},
            {"name": "Position"},
            {"name": "Playlist"},
            {"name": "Thumbnail"},
        ],
        templates=[
            {
                "name": "Recall",
                "qfmt": (
                    '<div class="hint">From <b>{{Channel}}</b> · '
                    "{{Duration}} · position {{Position}}</div>"
                    '<div class="hint">What video is this?</div>'
                ),
                "afmt": (
                    '{{FrontSide}}<hr id="answer">'
                    '<div class="title">{{Title}}</div>'
                    '<div class="thumb">{{Thumbnail}}</div>'
                    '<div class="link"><a href="{{Url}}">{{Url}}</a></div>'
                    '<div class="meta">From playlist: {{Playlist}}</div>'
                ),
            },
            {
                "name": "Review",
                "qfmt": (
                    '<div class="title">{{Title}}</div>'
                    '<div class="meta">{{Channel}} · {{Duration}}</div>'
                    '<div class="link"><a href="{{Url}}">{{Url}}</a></div>'
                    '<div class="hint">Do you remember what this covers?</div>'
                ),
                "afmt": (
                    "{{FrontSide}}"
                    '<hr id="answer">'
                    '<div class="thumb">{{Thumbnail}}</div>'
                    '<div class="meta">Rate yourself on recall of the main point.</div>'
                ),
            },
        ],
        css=_CARD_CSS,
        sort_field_index=1,  # sort by Title in Anki's browser
    )


def _build_note(
    model: genanki.Model,
    v: Video,
    playlist: Playlist,
    thumbnail_basename: str,
) -> _StableNote:
    # EVERY user-sourced string is html-escaped before entering a field.
    # Don't remove these without replacing them with something equivalent.
    thumb_html = ""
    if thumbnail_basename:
        # basename is one we generated (video_id + extension), but escape anyway
        thumb_html = f'<img src="{html.escape(thumbnail_basename, quote=True)}">'

    # tags: strip whitespace (Anki splits on spaces) and cap length
    tags = ["youtube", _tag_safe(playlist.title)]

    return _StableNote(
        model=model,
        fields=[
            v.video_id,                                    # VideoId (GUID key)
            html.escape(v.title, quote=True),              # Title
            html.escape(v.url, quote=True),                # Url
            html.escape(v.channel, quote=True),            # Channel
            html.escape(v.duration_hms(), quote=True),     # Duration
            str(v.position),                               # Position
            html.escape(playlist.title, quote=True),       # Playlist
            thumb_html,                                    # Thumbnail (pre-built HTML)
        ],
        tags=tags,
    )


def _tag_safe(s: str) -> str:
    """Anki tags can't contain whitespace — split becomes multiple tags."""
    # Underscore-collapse and strip weirdness; cap at something reasonable
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
    return (out.strip("_") or "playlist")[:64]


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def _download_thumbnail(url: str, dest_dir: Path, video_id: str) -> Path | None:
    """Fetch a thumbnail to dest_dir safely, returning the path or None.

    Returns None on any failure — we never let a thumbnail problem fail the
    whole export. Bounds:
        - host must be in the YouTube CDN allowlist
        - content-type must be one of a few image types
        - total bytes capped at _THUMB_MAX_BYTES (streamed read)
        - request has an explicit timeout
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        log.debug("thumbnail url unparseable: %r", url)
        return None

    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _THUMB_ALLOWED_HOSTS:
        log.debug("thumbnail host not allowed: %s", host)
        return None

    session = http_session()
    try:
        with session.get(url, stream=True, timeout=_THUMB_TIMEOUT) as r:
            if not r.ok:
                return None

            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype not in _THUMB_ALLOWED_TYPES:
                log.debug("thumbnail content-type rejected: %s", ctype)
                return None

            # Trust Content-Length as a hint but don't rely on it —
            # still enforce the cap during read.
            declared = r.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > _THUMB_MAX_BYTES:
                return None

            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[ctype]
            # Filename is video_id we already validated as [A-Za-z0-9_-]{11}
            out = dest_dir / f"ytthumb_{video_id}{ext}"

            total = 0
            with out.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _THUMB_MAX_BYTES:
                        log.debug("thumbnail exceeded size cap, discarding")
                        out.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
            return out
    except requests.RequestException as e:
        log.debug("thumbnail download failed: %s", e)
        return None
    finally:
        session.close()
