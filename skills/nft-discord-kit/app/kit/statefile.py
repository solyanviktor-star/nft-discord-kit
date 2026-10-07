"""data/state.json: ids of what `kit.setup build` created and where the panels were posted.

{"guild_id": "...", "roles": {name: id}, "categories": {name: id}, "channels": {core name: id},
 "panels": {"verify": {"channel": id, "message": id}, ...}}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
