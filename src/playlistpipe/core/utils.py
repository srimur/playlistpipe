"""Small utilities shared across exporters.

Keep functions here pure and defensively written — most security-relevant
bugs in this project will be input-handling bugs, and they live here.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# YouTube video ids are exactly 11 chars from [A-Za-z0-9_-]. Anchored match.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Characters we refuse in any filename we generate. Windows is the strict one;
# '/' and '\0' would bite us on Unix too.
_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Cap filenames at something safe across filesystems. 200 leaves headroom for
# extensions and the OS's own limits (255 on most, 143 on eCryptfs).
_MAX_FILENAME_LEN = 200


class InvalidURLError(ValueError):
    """The input wasn't a URL we can safely process."""


def extract_video_id(url: str) -> str:
    """Pull the 11-char video id out of any YouTube URL shape we've seen.

    Accepts:
        https://www.youtube.com/watch?v=dQw4w9WgXcQ
        https://youtu.be/dQw4w9WgXcQ
        https://www.youtube.com/embed/dQw4w9WgXcQ
        https://www.youtube.com/shorts/dQw4w9WgXcQ
        https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=...

    Rejects anything else with InvalidURLError. We never fall back to "the
    last path segment" because that's how you end up with an id of
    "watch?v=foo" and mysterious breakage three layers down.
    """
    if not isinstance(url, str) or not url:
        raise InvalidURLError("empty url")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise InvalidURLError(f"could not parse: {e}") from e

    host = (parsed.hostname or "").lower()
    if not host:
        raise InvalidURLError("no host in url")

    # Allowlist hosts; don't trust endswith() alone (foo-youtube.com etc.)
    allowed = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "music.youtube.com", "youtu.be",
    }
    if host not in allowed:
        raise InvalidURLError(f"not a youtube host: {host}")

    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/", 1)[0]
    else:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
        else:
            # /embed/ID, /shorts/ID, /live/ID
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
                candidate = parts[1]

    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise InvalidURLError(f"no valid video id in {url!r}")
    return candidate


def parse_duration_hms(text: str) -> int | None:
    """'12:34' -> 754, '1:02:15' -> 3735, anything weird -> None.

    YouTube occasionally serves junk here (empty string, '—'); return None
    rather than raising so the scraper can keep going.
    """
    if not text or not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if not all(p.isdigit() for p in parts):
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        m, s = nums
        if s >= 60:
            return None
        return m * 60 + s
    if len(nums) == 3:
        h, m, s = nums
        if m >= 60 or s >= 60:
            return None
        return h * 3600 + m * 60 + s
    return None


def safe_filename(name: str, *, default: str = "playlist") -> str:
    """Normalize arbitrary text into something safe to use as a filename.

    Strips control chars, path separators, and reserved Windows names; NFC-
    normalizes Unicode; trims to a reasonable length; falls back to `default`
    if the result is empty. Never returns '.', '..', or a reserved name.
    """
    if not isinstance(name, str):
        name = str(name)

    # NFC so visually-identical strings hash the same across platforms
    name = unicodedata.normalize("NFC", name)
    # Collapse whitespace BEFORE the bad-char substitution, otherwise tabs
    # get rewritten to "_" and the whitespace collapse never sees them.
    name = re.sub(r"\s+", " ", name)
    name = _BAD_FILENAME_CHARS.sub("_", name).strip("._ ")
    # Collapse runs of underscores from the substitution above.
    name = re.sub(r"_+", "_", name)[:_MAX_FILENAME_LEN]

    reserved = {
        "", ".", "..",
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if name.upper() in reserved:
        return default
    return name


def resolve_within(directory: Path, child: str) -> Path:
    """Return directory/child, guaranteed to not escape `directory`.

    This is the thing that stops ``--name "../../etc/passwd"`` from being
    interesting. We resolve both sides and use is_relative_to.
    """
    base = directory.expanduser().resolve()
    candidate = (base / child).resolve()
    # is_relative_to is 3.9+; we're on 3.10+ so this is fine
    if not candidate.is_relative_to(base):
        raise ValueError(
            f"path {candidate} escapes base directory {base}"
        )
    return candidate


def http_session(
    *,
    user_agent: str = "playlistpipe/0.1 (+https://github.com/srimur/playlistpipe)",
    total_retries: int = 3,
) -> requests.Session:
    """A requests.Session with sane retry/backoff for the exporters to reuse.

    Every caller must still pass an explicit `timeout=` on each request —
    retries don't help if the first call hangs forever.
    """
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    retry = Retry(
        total=total_retries,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PATCH"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
