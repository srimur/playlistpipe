"""Notion markdown exporter.

For users who don't want to set up the API. Produces markdown that Notion
parses cleanly when pasted into a page: checklist with title/channel/duration
and a summary footer. We keep this output stable — people diff it.
"""

from __future__ import annotations

from pathlib import Path

from ..core.models import ExportResult, Playlist
from ..core.utils import resolve_within, safe_filename


class NotionMarkdownExporter:
    name = "notion-md"

    def __init__(self, *, output_dir: Path, copy_to_clipboard: bool = False):
        self._output_dir = output_dir
        self._copy = copy_to_clipboard

    def export(self, playlist: Playlist) -> ExportResult:
        md = _render(playlist)

        if self._copy:
            # pyperclip is optional — don't make everyone install it just for
            # an exporter they might not use.
            try:
                import pyperclip  # type: ignore[import-not-found]
            except ImportError:
                raise RuntimeError(
                    "--copy requires pyperclip; install with `pip install playlistpipe[clipboard]`"
                )
            pyperclip.copy(md)

        self._output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{safe_filename(playlist.title)}.md"
        out = resolve_within(self._output_dir, filename)
        out.write_text(md, encoding="utf-8")

        notes = ("copied to clipboard",) if self._copy else ()
        return ExportResult(
            exporter=self.name,
            artifact=str(out),
            items_written=len(playlist),
            notes=notes,
        )


def _render(playlist: Playlist) -> str:
    lines: list[str] = [f"# {_md_escape(playlist.title)}", ""]
    if playlist.channel:
        lines.append(f"_by {_md_escape(playlist.channel)}_")
        lines.append("")

    for v in playlist.videos:
        # Link text and description get markdown-escaped; URL goes through
        # unchanged (Notion's parser handles it and escaping would break it).
        title = _md_escape(v.title)
        channel = _md_escape(v.channel)
        lines.append(
            f"- [ ]  [{title}]({v.url}) — *{channel}* · `{v.duration_hms()}`"
        )

    total_s = playlist.total_seconds()
    hours, rem = divmod(total_s, 3600)
    minutes = rem // 60
    lines += [
        "",
        f"_Total: {len(playlist)} videos · ~{hours}h {minutes}m_",
        "",
    ]
    return "\n".join(lines)


def _md_escape(text: str) -> str:
    """Escape the characters Notion's markdown parser treats as syntax.

    We're deliberately conservative — only the ones that break link text or
    list items. Over-escaping makes output ugly; under-escaping breaks it.
    """
    if not text:
        return ""
    # Order matters: escape backslash first so we don't double-escape.
    for ch in ("\\", "[", "]", "*", "_", "`"):
        text = text.replace(ch, "\\" + ch)
    return text
