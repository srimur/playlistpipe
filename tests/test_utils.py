"""The adversarial cases. If any of these break, users will hit it."""

from __future__ import annotations

from pathlib import Path

import pytest

from playlistpipe.core.utils import (
    InvalidURLError,
    extract_video_id,
    parse_duration_hms,
    resolve_within,
    safe_filename,
)


class TestExtractVideoId:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=x", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # 11-char ids with underscores and hyphens
        ("https://youtu.be/_-aAa1BbCc2", "_-aAa1BbCc2"),
    ])
    def test_valid(self, url, expected):
        assert extract_video_id(url) == expected

    @pytest.mark.parametrize("bad", [
        "",
        "not a url",
        "https://evil.com/watch?v=dQw4w9WgXcQ",    # wrong host
        "https://foo-youtube.com/watch?v=dQw4w9WgXcQ",  # lookalike host
        "https://youtube.com/watch?v=tooShort",    # 8 chars
        "https://youtube.com/watch?v=wayTooLongId12345",
        "https://youtube.com/watch",               # no v
        "https://youtu.be/",                       # empty path
        "javascript:alert(1)",                     # scheme tricks
    ])
    def test_invalid(self, bad):
        with pytest.raises(InvalidURLError):
            extract_video_id(bad)

    def test_non_string_raises(self):
        with pytest.raises(InvalidURLError):
            extract_video_id(None)  # type: ignore[arg-type]


class TestParseDuration:
    @pytest.mark.parametrize("text,expected", [
        ("0:00", 0),
        ("12:34", 754),
        ("1:02:15", 3735),
        ("10:00:00", 36000),
    ])
    def test_valid(self, text, expected):
        assert parse_duration_hms(text) == expected

    @pytest.mark.parametrize("text", [
        "", None, "abc", "1:2:3:4", "12:99", "1:99:00", "12", "-1:00",
    ])
    def test_invalid_returns_none(self, text):
        assert parse_duration_hms(text) is None


class TestSafeFilename:
    def test_strips_path_separators(self):
        assert "/" not in safe_filename("../../etc/passwd")
        assert "\\" not in safe_filename("C:\\Windows\\System32")

    def test_strips_control_chars(self):
        assert "\x00" not in safe_filename("foo\x00bar")
        assert "\x1b" not in safe_filename("foo\x1b[31mred")

    def test_reserved_windows_names(self):
        for name in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9"):
            assert safe_filename(name) == "playlist"

    def test_empty_and_dots(self):
        assert safe_filename("") == "playlist"
        assert safe_filename(".") == "playlist"
        assert safe_filename("..") == "playlist"
        assert safe_filename("...") == "playlist"

    def test_preserves_unicode(self):
        assert safe_filename("日本語プレイリスト") == "日本語プレイリスト"

    def test_length_capped(self):
        result = safe_filename("x" * 500)
        assert len(result) <= 200

    def test_collapses_whitespace(self):
        assert safe_filename("foo   bar\t\tbaz") == "foo bar baz"


class TestResolveWithin:
    def test_accepts_child(self, tmp_path: Path):
        out = resolve_within(tmp_path, "ok.txt")
        assert out.parent == tmp_path.resolve()

    def test_rejects_traversal(self, tmp_path: Path):
        with pytest.raises(ValueError):
            resolve_within(tmp_path, "../escaped.txt")

    def test_rejects_absolute(self, tmp_path: Path):
        # Path("/etc/passwd") resolved against tmp_path still evaluates to
        # /etc/passwd because it's absolute — resolve_within must catch it.
        with pytest.raises(ValueError):
            resolve_within(tmp_path, "/etc/passwd")
