"""Logging setup.

The redaction filter is the important bit: if anything anywhere accidentally
logs a payload containing a Notion token, we catch it here rather than in
the user's terminal output (or worse, a file).
"""

from __future__ import annotations

import logging
import re

_TOKEN_RE = re.compile(r"(secret_|ntn_)[A-Za-z0-9]{20,}")


class _RedactTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Redact in the formatted message *and* in args before formatting.
        if isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub("<redacted>", record.msg)
        if record.args:
            record.args = tuple(
                _TOKEN_RE.sub("<redacted>", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def configure(*, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(_RedactTokens())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # requests/urllib3 are chatty at DEBUG; keep them at WARNING unless we
    # really asked for it.
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
