"""Exporter registry.

Keeping it a plain dict for now. If someone ships a third-party exporter we
switch to `importlib.metadata` entry points; until then, YAGNI.
"""

from __future__ import annotations

from .anki import AnkiConfig, AnkiExporter
from .notion_api import NotionApiExporter, NotionConfig
from .notion_markdown import NotionMarkdownExporter
from .obsidian import ObsidianConfig, ObsidianExporter

# The CLI imports this and nothing else from the exporters package.
AVAILABLE = ("notion-api", "notion-md", "obsidian", "anki")

__all__ = [
    "AVAILABLE",
    "AnkiConfig", "AnkiExporter",
    "NotionApiExporter", "NotionConfig",
    "NotionMarkdownExporter",
    "ObsidianConfig", "ObsidianExporter",
]
