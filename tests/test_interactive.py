"""Tests for the interactive layer.

We can't drive questionary's prompts under pytest — the library needs a real
terminal and tries to take over stdin. What we *can* test is the cheap pure
logic that gates those prompts: URL validation, and that the entry point
keeps the right shape so cli.py can still call it.
"""

from __future__ import annotations

import inspect

import pytest

from playlistpipe.interactive import is_valid_playlist_url, run


class TestIsValidPlaylistUrl:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/playlist?list=PLabc123",
        "https://youtube.com/playlist?list=PLx",
        "https://m.youtube.com/playlist?list=PLabc",
        "https://music.youtube.com/playlist?list=PLabc",
        # watch URL carrying a list param — yt-dlp is happy with these.
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc",
        # youtu.be + list is unusual but real.
        "https://youtu.be/dQw4w9WgXcQ?list=PLabc",
        # Whitespace padding is tolerated (user pasted from a terminal).
        "  https://youtube.com/playlist?list=PLabc  ",
    ])
    def test_accepts_playlist_urls(self, url):
        assert is_valid_playlist_url(url) is True

    @pytest.mark.parametrize("url", [
        "",
        "   ",
        "not a url",
        # Single-video URL with no list param — not a playlist.
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        # Empty list param.
        "https://www.youtube.com/playlist?list=",
        # Wrong host.
        "https://evil.com/playlist?list=PLabc",
        # Lookalike host, same trap as extract_video_id guards against.
        "https://foo-youtube.com/playlist?list=PLabc",
        # Scheme tricks.
        "javascript:alert(1)",
    ])
    def test_rejects_non_playlist_urls(self, url):
        assert is_valid_playlist_url(url) is False

    @pytest.mark.parametrize("val", [None, 123, b"https://youtube.com/playlist?list=x"])
    def test_rejects_non_strings(self, val):
        assert is_valid_playlist_url(val) is False


def test_run_is_importable_with_zero_arg_signature():
    """cli.main calls `interactive.run()` with no args — keep it that way."""
    sig = inspect.signature(run)
    assert list(sig.parameters) == []
