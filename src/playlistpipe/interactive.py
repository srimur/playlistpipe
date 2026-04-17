"""Interactive menu-driven flow.

Launched when the user runs ``plp`` with no arguments. Everything scripting-
oriented still goes through argparse in cli.py; this module is the guided
path for humans who'd rather not read --help.

Design rules (worth keeping if you touch this file):

    * Never raise on bad input. A re-prompt beats a traceback every time.
    * Never echo a secret. Notion tokens go through questionary.password.
    * Never write config the user didn't explicitly agree to save.
    * Ctrl+C is a first-class exit path, not an error — handled as 130.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import questionary

from .config import CONFIG_PATH, AppConfig
from .core.models import ExportResult
from .exporters import (
    AnkiConfig,
    AnkiExporter,
    NotionApiExporter,
    NotionConfig,
    NotionMarkdownExporter,
    ObsidianConfig,
    ObsidianExporter,
)

log = logging.getLogger(__name__)

# Mirrors the allowlist in core.utils but scoped to playlist URLs. We
# deliberately accept music.youtube.com too — those URLs share the same
# ?list= shape and yt-dlp handles them fine.
_YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be",
})


def is_valid_playlist_url(url: object) -> bool:
    """Lightweight check used by the interactive URL prompt.

    Looser than core.utils.extract_video_id (that's for single-video URLs).
    We only need: known YouTube host + a non-empty ``list=`` query parameter.
    Anything beyond that is yt-dlp's problem.
    """
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if (parsed.hostname or "").lower() not in _YOUTUBE_HOSTS:
        return False
    list_ids = parse_qs(parsed.query).get("list")
    return bool(list_ids and list_ids[0])


def run() -> int:
    """Entry point called by cli.main when no URL was given on the command line."""
    try:
        return _run()
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130


def _run() -> int:
    url = _prompt_url()
    if url is None:
        return _cancel()

    target = _prompt_target()
    if target is None:
        return _cancel()

    cfg = AppConfig.load()

    exporter = _setup_target(target, cfg)
    if exporter is None:
        return _cancel()

    # Import scraper lazily: keeps `plp` (no args) responsive on slow boxes
    # and mirrors cli.main's own laziness.
    from .core.scraper import scrape_playlist

    try:
        print(f"\nscraping {url} ...")
        playlist = scrape_playlist(url)
        print(f"found {len(playlist)} videos in {playlist.title!r}. exporting ...")
        result = exporter.export(playlist)
    except Exception as e:  # noqa: BLE001  surface friendly errors, no tracebacks
        return _report_error(e)

    _print_result(result)
    _offer_open(result)
    return 0


# --- prompts ----------------------------------------------------------------


def _prompt_url() -> str | None:
    while True:
        answer = questionary.text("Paste a YouTube playlist URL:").ask()
        if answer is None:
            return None
        answer = answer.strip()
        if is_valid_playlist_url(answer):
            return answer
        print(
            "  that doesn't look like a YouTube playlist URL "
            "(needs a youtube.com/youtu.be host and a ?list=... query). try again."
        )


def _prompt_target() -> str | None:
    choice = questionary.select(
        "Where should this go?",
        choices=[
            questionary.Choice(
                "Notion (recommended — full database with progress tracking)",
                value="notion-api",
            ),
            questionary.Choice(
                "Notion (copy-paste markdown, no setup)",
                value="notion-md",
            ),
            questionary.Choice("Obsidian vault", value="obsidian"),
            questionary.Choice("Anki deck", value="anki"),
            questionary.Choice("Cancel", value="cancel"),
        ],
    ).ask()
    if choice in (None, "cancel"):
        return None
    return choice


# --- target setup -----------------------------------------------------------


def _setup_target(target: str, cfg: AppConfig):  # -> Exporter | None
    if target == "notion-api":
        return _setup_notion_api(cfg)
    if target == "notion-md":
        return _setup_notion_md(cfg)
    if target == "obsidian":
        return _setup_obsidian(cfg)
    if target == "anki":
        return _setup_anki(cfg)
    return None


def _setup_notion_api(cfg: AppConfig) -> NotionApiExporter | None:
    token = cfg.notion_token
    if not token:
        print(
            "\nYou'll need a Notion integration token.\n"
            "  Open https://www.notion.so/my-integrations, create a new\n"
            "  internal integration, and paste the token below."
        )
        token = _prompt_notion_token()
        if token is None:
            return None
        if _confirm(
            f"Save this token to {CONFIG_PATH} so you don't have to paste it again?",
            default=True,
        ):
            _try_save({"notion": {"token": token}})

    parent = cfg.notion_default_parent
    database = cfg.notion_default_database

    if not database and not parent:
        print(
            "\nNotion needs a parent page to create the new database under.\n"
            "  The parent page ID is the last 32-char chunk of your page's URL\n"
            "  (the bit after the last dash)."
        )
        answer = questionary.text("Parent page ID:").ask()
        if answer is None:
            return None
        parent = answer.strip() or None
        if not parent:
            print("  no parent id given; aborting.")
            return None
        if _confirm("Save this as your default parent page?", default=True):
            _try_save({"notion": {"default_parent_page_id": parent}})

    try:
        return NotionApiExporter(
            NotionConfig(token=token, database_id=database, parent_page_id=parent),
        )
    except ValueError as e:
        print(f"  notion config rejected: {e}")
        return None


def _prompt_notion_token() -> str | None:
    while True:
        token = questionary.password("Notion integration token:").ask()
        if token is None:
            return None
        token = token.strip()
        if token.startswith(("secret_", "ntn_")):
            return token
        print("  that doesn't look like a Notion token (starts with secret_ or ntn_). try again.")


def _setup_notion_md(cfg: AppConfig) -> NotionMarkdownExporter | None:
    choice = questionary.select(
        "Output:",
        choices=[
            questionary.Choice("Copy to clipboard", value="clipboard"),
            questionary.Choice("Save to file", value="file"),
            questionary.Choice("Both", value="both"),
        ],
    ).ask()
    if choice is None:
        return None

    copy = choice in ("clipboard", "both")
    if copy:
        try:
            import pyperclip  # noqa: F401
        except ImportError:
            print("  pyperclip isn't installed; saving to file only.")
            copy = False

    return NotionMarkdownExporter(output_dir=cfg.output_dir, copy_to_clipboard=copy)


def _setup_obsidian(cfg: AppConfig) -> ObsidianExporter | None:
    vault = cfg.obsidian_vault if cfg.obsidian_vault and cfg.obsidian_vault.is_dir() else None

    if vault is None:
        vault = _prompt_existing_dir("Path to your Obsidian vault:")
        if vault is None:
            return None
        if _confirm("Save this as your default vault path?", default=True):
            _try_save({"obsidian": {"vault_path": str(vault)}})

    subfolder = questionary.text("Subfolder inside the vault?", default="YouTube").ask()
    if subfolder is None:
        return None
    subfolder = subfolder.strip() or "YouTube"
    return ObsidianExporter(ObsidianConfig(vault_path=vault, subfolder=subfolder))


def _setup_anki(cfg: AppConfig) -> AnkiExporter | None:
    thumbs = questionary.confirm(
        "Include video thumbnails? (adds ~100KB per video)",
        default=False,
    ).ask()
    if thumbs is None:
        return None

    default_out = str(cfg.output_dir)
    out_answer = questionary.text("Output directory?", default=default_out).ask()
    if out_answer is None:
        return None
    out_answer = out_answer.strip() or default_out
    output_dir = Path(out_answer).expanduser()

    return AnkiExporter(
        AnkiConfig(output_dir=output_dir, include_thumbnails=bool(thumbs)),
    )


# --- helpers ----------------------------------------------------------------


def _prompt_existing_dir(prompt: str) -> Path | None:
    while True:
        answer = questionary.text(prompt).ask()
        if answer is None:
            return None
        candidate = Path(answer.strip()).expanduser()
        if candidate.is_dir():
            return candidate
        print(f"  {candidate} is not a directory. try again.")


def _confirm(message: str, *, default: bool) -> bool:
    answer = questionary.confirm(message, default=default).ask()
    return bool(answer)


def _try_save(updates: dict) -> None:
    try:
        path = AppConfig.save(updates)
        print(f"  saved to {path}")
    except OSError as e:
        print(f"  couldn't save config: {e}")


def _print_result(r: ExportResult) -> None:
    print(f"\n✓ {r.exporter}: {r.items_written} items -> {r.artifact}")
    for note in r.notes:
        print(f"  · {note}")


def _offer_open(result: ExportResult) -> None:
    artifact = result.artifact
    if not artifact:
        return
    if not _confirm("Open it now?", default=True):
        return
    if artifact.startswith(("http://", "https://")):
        webbrowser.open(artifact)
        return

    path = Path(artifact)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as e:
        print(f"  couldn't open {path}: {e}")


def _cancel() -> int:
    print("cancelled", file=sys.stderr)
    return 130


def _report_error(e: BaseException) -> int:
    """Turn an exception raised during scrape/export into a friendly message.

    The goal is that a user who ran ``plp`` from a GUI terminal never sees a
    traceback — they see one line stating what broke and one line stating how
    to fix it. Set ``PLAYLISTPIPE_DEBUG=1`` to re-raise and get the full trace
    when filing a bug report.
    """
    if os.environ.get("PLAYLISTPIPE_DEBUG"):
        raise e

    # Lazy imports: these types live in modules we may not have loaded yet.
    from .core.scraper import ScraperError
    from .exporters.notion_api import NotionError

    msg = str(e) or type(e).__name__
    low = msg.lower()

    if isinstance(e, NotionError):
        print(f"\nerror: Notion rejected the request.\n  {msg}", file=sys.stderr)
        if "404" in msg and "page" in low:
            print(
                "\n  likely cause: your integration isn't connected to that page.\n"
                "  fix it in Notion:\n"
                "    1. open the parent page in Notion\n"
                "    2. click the '...' menu in the top-right\n"
                "    3. Connections → add your playlistpipe integration\n"
                "  then re-run `plp`.",
                file=sys.stderr,
            )
        elif "401" in msg or "unauthorized" in low:
            print(
                "\n  your Notion token is invalid or has been revoked.\n"
                "  generate a new one at https://www.notion.so/my-integrations\n"
                "  and delete the old `token` line from your config file:\n"
                f"    {CONFIG_PATH}",
                file=sys.stderr,
            )
        elif "429" in msg or "rate" in low:
            print(
                "\n  rate-limited by Notion. wait a minute and re-run `plp`.",
                file=sys.stderr,
            )
        else:
            print(
                "\n  this is usually a schema or payload issue. if it keeps\n"
                "  happening, open an issue with the message above.",
                file=sys.stderr,
            )
        return 1

    if isinstance(e, ScraperError):
        print(f"\nerror: couldn't scrape the playlist.\n  {msg}", file=sys.stderr)
        if "private" in low:
            print(
                "\n  private playlists aren't supported. change the playlist\n"
                "  to unlisted or public in YouTube's playlist settings.",
                file=sys.stderr,
            )
        elif "no videos" in low or "not a playlist" in low:
            print(
                "\n  the URL doesn't look like a real playlist — double-check\n"
                "  it loads for you in a browser (signed out).",
                file=sys.stderr,
            )
        else:
            print(
                "\n  if this looks network-y, check your connection and retry.\n"
                "  YouTube occasionally rate-limits scrapers from residential IPs.",
                file=sys.stderr,
            )
        return 1

    if isinstance(e, PermissionError):
        print(f"\nerror: permission denied.\n  {msg}", file=sys.stderr)
        print(
            "\n  your OS refused a filesystem write. check the target directory\n"
            "  is writable and not open in another program.",
            file=sys.stderr,
        )
        return 1

    if isinstance(e, FileNotFoundError):
        print(f"\nerror: file or directory not found.\n  {msg}", file=sys.stderr)
        print(
            "\n  the path used above doesn't exist. check your config at:\n"
            f"    {CONFIG_PATH}",
            file=sys.stderr,
        )
        return 1

    if isinstance(e, OSError):
        print(f"\nerror: filesystem problem.\n  {msg}", file=sys.stderr)
        print("\n  check disk space and that the output directory is writable.", file=sys.stderr)
        return 1

    if isinstance(e, ValueError):
        # Bad config shape, path traversal attempt, etc.
        print(f"\nerror: invalid input.\n  {msg}", file=sys.stderr)
        return 1

    if isinstance(e, RuntimeError):
        print(f"\nerror: {msg}", file=sys.stderr)
        return 1

    # Fallback — unknown exception. Name the class so a bug report is useful,
    # but still no traceback in the default output.
    print(
        f"\nerror: unexpected {type(e).__name__}: {msg}\n"
        "  re-run with PLAYLISTPIPE_DEBUG=1 for the full traceback, or\n"
        "  open an issue at https://github.com/srimur/playlistpipe/issues",
        file=sys.stderr,
    )
    return 1
