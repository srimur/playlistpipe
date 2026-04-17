"""Config loading.

Order of precedence (highest first):
    1. CLI flags
    2. Environment variables
    3. ~/.config/playlistpipe/config.toml
    4. hardcoded defaults

Secrets (Notion token) only ever come from env or the config file. We never
accept tokens on the command line — they end up in shell history.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "playlistpipe"
    return Path.home() / ".config" / "playlistpipe"


CONFIG_PATH = _config_home() / "config.toml"


@dataclass(frozen=True, slots=True)
class AppConfig:
    notion_token: str | None
    notion_default_parent: str | None
    notion_default_database: str | None
    obsidian_vault: Path | None
    output_dir: Path

    @classmethod
    def load(cls) -> "AppConfig":
        data: dict = {}
        if CONFIG_PATH.is_file():
            try:
                with CONFIG_PATH.open("rb") as fh:
                    data = tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError):
                # Broken config shouldn't crash the tool; log once and move on.
                import logging
                logging.getLogger(__name__).warning(
                    "could not read %s, using defaults", CONFIG_PATH
                )
                data = {}

        notion = data.get("notion", {}) or {}
        obsidian = data.get("obsidian", {}) or {}
        defaults = data.get("defaults", {}) or {}

        return cls(
            notion_token=(
                os.environ.get("PLAYLISTPIPE_NOTION_TOKEN")
                or os.environ.get("NOTION_TOKEN")
                or notion.get("token")
            ),
            notion_default_parent=notion.get("default_parent_page_id"),
            notion_default_database=notion.get("default_database_id"),
            obsidian_vault=(
                Path(os.environ["PLAYLISTPIPE_OBSIDIAN_VAULT"]).expanduser()
                if "PLAYLISTPIPE_OBSIDIAN_VAULT" in os.environ
                else Path(obsidian["vault_path"]).expanduser()
                if obsidian.get("vault_path")
                else None
            ),
            output_dir=Path(
                os.environ.get("PLAYLISTPIPE_OUTPUT_DIR")
                or defaults.get("output_dir")
                or "./playlistpipe-out"
            ).expanduser(),
        )

    @classmethod
    def save(cls, updates: dict[str, dict[str, object]]) -> Path:
        """Merge ``updates`` into ``CONFIG_PATH``, preserving unrelated keys.

        Shape is ``{"section": {"key": value}}`` — e.g.
        ``{"notion": {"token": "secret_..."}}``. Keys already present in other
        sections (or other keys in the same section) are left alone. The file
        and its parent directory are created if they don't yet exist.

        We tighten permissions to 0o600 on POSIX because this file routinely
        stores a Notion integration token. No-op on Windows where chmod's
        meaning doesn't carry over.
        """
        import tomli_w

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if CONFIG_PATH.is_file():
            with CONFIG_PATH.open("rb") as fh:
                existing = tomllib.load(fh)

        for section, values in updates.items():
            if isinstance(values, dict):
                current = existing.get(section)
                merged = dict(current) if isinstance(current, dict) else {}
                merged.update(values)
                existing[section] = merged
            else:
                existing[section] = values

        with CONFIG_PATH.open("wb") as fh:
            tomli_w.dump(existing, fh)

        if os.name == "posix":
            try:
                os.chmod(CONFIG_PATH, 0o600)
            except OSError:
                pass
        return CONFIG_PATH
