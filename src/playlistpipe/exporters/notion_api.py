"""Notion API exporter.

Creates (or appends to) a Notion database with one row per video. Idempotent:
re-running on the same playlist updates existing rows instead of duplicating
them — we match on the `Video ID` property.

Setup (one-time, per user):

    1. https://www.notion.so/my-integrations  -> "+ New integration"
       Internal integration, copy the secret.
    2. Open the Notion page you want the database to live under, click the
       "..." menu -> Connections -> add your integration.
    3. Grab the 32-char page id from the page's URL (the tail after the
       last dash).
    4. Run:
           export NOTION_TOKEN=secret_...
           plp "https://youtube.com/playlist?list=..." \\
               --to notion-api --notion-parent PAGE_ID

On subsequent runs, pass the returned database id with `--notion-db` to
append/update in the same place.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

from ..core.models import ExportResult, Playlist, Video
from ..core.utils import http_session

log = logging.getLogger(__name__)

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"  # pinned; upgrading this needs a schema review

# Notion enforces ~3 req/sec. We sleep 0.35s between writes and rely on the
# session's Retry-After handling for the 429s we still occasionally hit.
_WRITE_DELAY_SECONDS = 0.35

_HTTP_TIMEOUT = (5, 30)  # (connect, read)

# Notion rejects strings > 2000 chars in rich_text/title fields with a 400.
_NOTION_TEXT_MAX = 2000


class NotionError(RuntimeError):
    """Anything the Notion API told us it didn't like."""


@dataclass(frozen=True, slots=True)
class NotionConfig:
    token: str
    database_id: str | None = None
    parent_page_id: str | None = None

    def __post_init__(self) -> None:
        if not self.token or not self.token.startswith(("secret_", "ntn_")):
            # Don't log the token itself, ever.
            raise ValueError("invalid notion token format")
        if not self.database_id and not self.parent_page_id:
            raise ValueError("provide database_id or parent_page_id")


class NotionApiExporter:
    name = "notion-api"

    def __init__(self, config: NotionConfig, *, session: requests.Session | None = None):
        self._cfg = config
        self._session = session or http_session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        })

    # -- public -------------------------------------------------------------

    def export(self, playlist: Playlist) -> ExportResult:
        db_id = self._cfg.database_id or self._create_database(playlist.title)
        existing = self._index_existing_rows(db_id)

        created = 0
        updated = 0
        # Notion's default database view sorts by creation time, newest
        # first — so creating rows in forward order puts position 1 at the
        # bottom. Insert in reverse so the default view reads 1 → N.
        # (Existing rows get updated by id regardless of order.)
        for v in reversed(playlist.videos):
            page_id = existing.get(v.video_id)
            if page_id:
                self._update_row(page_id, v)
                updated += 1
            else:
                self._create_row(db_id, v)
                created += 1
            time.sleep(_WRITE_DELAY_SECONDS)

        db_url = f"https://notion.so/{db_id.replace('-', '')}"
        return ExportResult(
            exporter=self.name,
            artifact=db_url,
            items_written=created + updated,
            notes=(f"{created} created, {updated} updated",),
        )

    # -- internals ----------------------------------------------------------

    def _create_database(self, title: str) -> str:
        assert self._cfg.parent_page_id  # guarded in NotionConfig
        payload = {
            "parent": {"type": "page_id", "page_id": self._cfg.parent_page_id},
            "title": [{"type": "text", "text": {"content": _clip(title)}}],
            "properties": {
                # The title property is required and must be first
                "Title":    {"title": {}},
                "Video ID": {"rich_text": {}},   # our idempotency key
                "Channel":  {"rich_text": {}},
                "Duration": {"rich_text": {}},
                "Minutes":  {"number": {"format": "number"}},
                "URL":      {"url": {}},
                "Position": {"number": {"format": "number"}},
                "Watched":  {"checkbox": {}},
                "Tags":     {"multi_select": {"options": []}},
                "Notes":    {"rich_text": {}},
            },
        }
        data = self._request("POST", "/databases", payload)
        return data["id"]

    def _index_existing_rows(self, database_id: str) -> dict[str, str]:
        """Return {video_id: notion_page_id} for everything currently in the DB."""
        out: dict[str, str] = {}
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self._request("POST", f"/databases/{database_id}/query", body)
            for row in data.get("results", []):
                vid = _read_rich_text(row["properties"].get("Video ID"))
                if vid:
                    out[vid] = row["id"]
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        log.debug("indexed %d existing rows", len(out))
        return out

    def _create_row(self, database_id: str, v: Video) -> None:
        payload: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": self._properties_for(v),
        }
        if v.thumbnail_url:
            payload["cover"] = {
                "type": "external",
                "external": {"url": v.thumbnail_url},
            }
        self._request("POST", "/pages", payload)

    def _update_row(self, page_id: str, v: Video) -> None:
        # We only update the fields that might have changed on YouTube's side.
        # Leave Watched, Notes, Tags alone — those are the user's.
        payload = {
            "properties": {
                k: self._properties_for(v)[k]
                for k in ("Title", "Channel", "Duration", "Minutes", "URL", "Position")
            }
        }
        self._request("PATCH", f"/pages/{page_id}", payload)

    @staticmethod
    def _properties_for(v: Video) -> dict[str, Any]:
        minutes = (v.duration_seconds or 0) // 60
        return {
            "Title":    {"title":     [{"text": {"content": _clip(v.title)}}]},
            "Video ID": {"rich_text": [{"text": {"content": v.video_id}}]},
            "Channel":  {"rich_text": [{"text": {"content": _clip(v.channel)}}]},
            "Duration": {"rich_text": [{"text": {"content": v.duration_hms()}}]},
            "Minutes":  {"number": minutes if v.duration_seconds is not None else None},
            "URL":      {"url": v.url},
            "Position": {"number": v.position},
        }

    def _request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{_API}{path}"
        r = self._session.request(method, url, json=body, timeout=_HTTP_TIMEOUT)

        # The session's Retry already handled 429 with backoff. If we still
        # got one here it means the retries were exhausted.
        if not r.ok:
            # Notion 400s come with useful messages; surface them without the token.
            msg = _safe_error_message(r)
            raise NotionError(f"{method} {path} -> {r.status_code}: {msg}")
        try:
            return r.json()
        except ValueError as e:
            raise NotionError(f"{method} {path}: non-JSON response") from e


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clip(s: str) -> str:
    """Notion's hard limit on rich_text/title content."""
    if s is None:
        return ""
    return s[:_NOTION_TEXT_MAX]


def _read_rich_text(prop: dict[str, Any] | None) -> str | None:
    """Pull the plain_text out of a rich_text property, tolerating missing keys."""
    if not prop:
        return None
    parts = prop.get("rich_text") or []
    if not parts:
        return None
    return "".join(p.get("plain_text", "") for p in parts) or None


_TOKEN_PATTERN = re.compile(r"(secret_|ntn_)[A-Za-z0-9]+")

def _safe_error_message(r: requests.Response) -> str:
    """Extract a Notion error message, redacting anything that looks like a token."""
    try:
        data = r.json()
        msg = data.get("message") or str(data)
    except ValueError:
        msg = r.text[:500]
    return _TOKEN_PATTERN.sub("<redacted>", msg)
