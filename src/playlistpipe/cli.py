"""CLI entry point.

    plp URL --to notion-api --notion-parent PAGE_ID
    plp URL --to notion-md --copy
    plp URL --to obsidian --vault ~/vault
    plp URL --to anki [--thumbnails]

The scraper lives in `core.scraper` (kept separate from this file). If you're
reading this before wiring up your scraper: the only thing it needs to
return is a `Playlist` from `core.models`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import AppConfig
from .core.models import ExportResult, Playlist
from .exporters import (
    AVAILABLE,
    AnkiConfig, AnkiExporter,
    NotionApiExporter, NotionConfig,
    NotionMarkdownExporter,
    ObsidianConfig, ObsidianExporter,
)
from .logging_setup import configure as configure_logging

log = logging.getLogger("playlistpipe")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    # No URL + no --to: the user ran `plp` alone. Hand off to the guided
    # flow. Anything else (including `plp --verbose` with nothing else) is
    # also treated as interactive — verbosity just carries through.
    if args.url is None and args.to is None:
        from .interactive import run as interactive_run
        return interactive_run()
    if args.url is None:
        parser.error("--to requires a URL argument")
    if args.to is None:
        parser.error("--to is required when a URL is given")

    cfg = AppConfig.load()
    output_dir = Path(args.output_dir) if args.output_dir else cfg.output_dir

    try:
        # --- scrape -------------------------------------------------------
        # Import locally so `plp --help` doesn't pay the import cost and so
        # scraper bugs don't block --help or config errors.
        from .core.scraper import scrape_playlist  # noqa: WPS433
        playlist = scrape_playlist(args.url)

        # --- export -------------------------------------------------------
        result = _dispatch(args, playlist, cfg, output_dir)
        _print_result(result)
        return 0

    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001  top-level catch-all is intentional
        # Show the traceback only in verbose mode — regular users want a
        # one-line error, not a wall of Python.
        if args.verbose:
            log.exception("export failed")
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1


def _dispatch(
    args: argparse.Namespace,
    playlist: Playlist,
    cfg: AppConfig,
    output_dir: Path,
) -> ExportResult:
    target = args.to

    if target == "notion-api":
        token = cfg.notion_token
        if not token:
            raise SystemExit(
                "notion-api requires a token. Set NOTION_TOKEN or "
                "configure it in ~/.config/playlistpipe/config.toml"
            )
        database_id = args.notion_db or cfg.notion_default_database
        parent = args.notion_parent or cfg.notion_default_parent
        if not database_id and not parent:
            raise SystemExit(
                "notion-api needs --notion-db (to append to an existing database) "
                "or --notion-parent (to create a new one on a page)"
            )
        return NotionApiExporter(
            NotionConfig(token=token, database_id=database_id, parent_page_id=parent)
        ).export(playlist)

    if target == "notion-md":
        return NotionMarkdownExporter(
            output_dir=output_dir, copy_to_clipboard=args.copy,
        ).export(playlist)

    if target == "obsidian":
        vault = Path(args.vault).expanduser() if args.vault else cfg.obsidian_vault
        if not vault:
            raise SystemExit(
                "obsidian requires --vault or obsidian.vault_path in config.toml"
            )
        if not vault.is_dir():
            raise SystemExit(f"vault path does not exist: {vault}")
        return ObsidianExporter(
            ObsidianConfig(vault_path=vault, subfolder=args.obsidian_subfolder)
        ).export(playlist)

    if target == "anki":
        return AnkiExporter(
            AnkiConfig(
                output_dir=output_dir,
                deck_name=args.deck_name,
                include_thumbnails=args.thumbnails,
            )
        ).export(playlist)

    raise SystemExit(f"unknown --to target: {target}")


def _print_result(r: ExportResult) -> None:
    print(f"✓ {r.exporter}: {r.items_written} items -> {r.artifact}")
    for note in r.notes:
        print(f"  · {note}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plp",
        description="Turn a YouTube playlist into a Notion database, "
                    "Obsidian vault, or Anki deck.",
    )
    p.add_argument(
        "url", nargs="?",
        help="YouTube playlist URL (omit to launch interactive mode)",
    )
    p.add_argument(
        "--to", choices=AVAILABLE,
        help="where to send the playlist (required when a URL is given)",
    )
    p.add_argument("-o", "--output-dir", help="override default output directory")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    notion = p.add_argument_group("notion")
    notion.add_argument("--notion-db", help="existing database id (append/update)")
    notion.add_argument("--notion-parent", help="parent page id (create new database)")
    notion.add_argument("--copy", action="store_true",
                        help="(notion-md) copy output to clipboard")

    obs = p.add_argument_group("obsidian")
    obs.add_argument("--vault", help="path to your Obsidian vault")
    obs.add_argument("--obsidian-subfolder", default="YouTube",
                     help="subfolder inside the vault (default: YouTube)")

    anki = p.add_argument_group("anki")
    anki.add_argument("--deck-name", help="override deck name (default: playlist title)")
    anki.add_argument("--thumbnails", action="store_true",
                      help="embed video thumbnails in cards")

    return p


if __name__ == "__main__":
    raise SystemExit(main())
