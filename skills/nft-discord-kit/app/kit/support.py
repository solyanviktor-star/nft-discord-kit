"""Support tickets without Discord: category ids, thread names, archive times and transcripts."""
from __future__ import annotations

import re
import time
from typing import Callable, Mapping, Sequence

ARCHIVE_STEPS = (60, 1440, 4320, 10080)  # the auto-archive minutes Discord accepts


def archive_minutes(hours: int) -> int:
    """The smallest Discord auto-archive step that is at least `hours` long."""
    return next((m for m in ARCHIVE_STEPS if hours * 60 <= m), ARCHIVE_STEPS[-1])


def thread_name(category: str, display_name: str) -> str:
    return f"{category} | {display_name}"[:100]


def transcript(header: str, rows: Sequence[Mapping], name_of: Callable[[str], str]) -> str:
    """Plain-text transcript; user mentions become @names so the file reads on its own.

    rows: {"at": unix seconds, "name": author, "text": message text, "files": [attachment urls]}
    """
    def names(text: str) -> str:
        return re.sub(r"<@!?(\d+)>", lambda m: "@" + name_of(m.group(1)), text)

    lines = [header, ""]
    for row in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(row.get("at") or 0)))
        files = " ".join(row.get("files") or [])
        lines.append(f"[{stamp}] {row.get('name', '?')}: {names(str(row.get('text') or ''))}"
                     + (f" {files}" if files else ""))
    return "\n".join(lines) + "\n"
